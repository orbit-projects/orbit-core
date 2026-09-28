# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Validate the complete release set, checksums, and dependency SBOM before signing artifacts.

The release workflow creates a checksum manifest before Sigstore adds its own signing
metadata. This checker validates every direct file in the release directory is covered by that
manifest and confirms that the dependency inventory is a CycloneDX document with components. It
deliberately does not attempt to verify hosted signatures or provenance; those checks require the
identity and attestation services available only during a hosted release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_DIST = Path(__file__).resolve().parents[1] / "dist"
_CHECKSUM_LINE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64}) (?P<marker>[ *])(?P<name>.+)$")
_REQUIRED_ARCHIVE_SUFFIXES = (".whl", ".tar.gz")
_SBOM_NAME = "dependencies.cdx.json"
_CHECKSUMS_NAME = "SHA256SUMS"


def _safe_filename(name: str) -> bool:
    """Return whether a manifest entry names one direct regular file."""
    path = PurePosixPath(name)
    return (
        bool(name)
        and path.name == name
        and "\\" not in name
        and name not in {".", "..", _CHECKSUMS_NAME}
    )


def _manifest_artifact_name(name: str, directory: Path) -> str | None:
    """Normalize a direct or workflow-style ``directory/file`` manifest entry."""
    prefix = f"{directory.name}/"
    candidate = name.removeprefix(prefix)
    if candidate == name and "/" in name:
        return None
    return candidate if _safe_filename(candidate) else None


def _sha256(path: Path) -> str:
    """Hash one artifact incrementally so release verification does not buffer it in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_errors(directory: Path) -> list[str]:
    """Return errors for the release checksum manifest and every listed file."""
    manifest = directory / _CHECKSUMS_NAME
    if manifest.is_symlink() or not manifest.is_file():
        return [f"missing release checksum manifest: {manifest}"]
    errors: list[str] = []
    seen: set[str] = set()
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"unable to read release checksum manifest: {exc}"]
    for line_number, line in enumerate(lines, start=1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            errors.append(f"{manifest}:{line_number}: invalid SHA-256 manifest line")
            continue
        digest = match.group("digest").lower()
        manifest_name = match.group("name")
        name = _manifest_artifact_name(manifest_name, directory)
        if name is None:
            errors.append(f"{manifest}:{line_number}: unsafe artifact name {manifest_name!r}")
            continue
        if name in seen:
            errors.append(f"{manifest}:{line_number}: duplicate artifact name {name!r}")
            continue
        seen.add(name)
        artifact = directory / name
        if not artifact.is_file() or artifact.is_symlink():
            errors.append(f"{manifest}:{line_number}: artifact does not exist: {name}")
            continue
        try:
            actual = _sha256(artifact)
        except OSError as exc:
            errors.append(f"{manifest}:{line_number}: unable to read {name}: {exc}")
            continue
        if actual != digest:
            errors.append(f"{manifest}:{line_number}: SHA-256 mismatch for {name}")
    for suffix in _REQUIRED_ARCHIVE_SUFFIXES:
        if not any(name.endswith(suffix) for name in seen):
            errors.append(f"{manifest}: no release archive with suffix {suffix!r} is covered")
    if _SBOM_NAME not in seen:
        errors.append(f"{manifest}: dependency SBOM {_SBOM_NAME!r} is not covered")
    direct_files = {
        entry.name
        for entry in directory.iterdir()
        if entry.name != _CHECKSUMS_NAME and (entry.is_file() or entry.is_symlink())
    }
    for name in sorted(direct_files - seen):
        errors.append(f"{manifest}: artifact is not covered: {name}")
    return errors


def _sbom_errors(directory: Path) -> list[str]:
    """Return errors for the generated dependency CycloneDX inventory."""
    source = directory / _SBOM_NAME
    if source.is_symlink() or not source.is_file():
        return [f"missing dependency SBOM: {source}"]
    try:
        document: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"{source}: invalid JSON: {exc}"]
    if not isinstance(document, dict):
        return [f"{source}: SBOM root must be a JSON object"]
    errors: list[str] = []
    if document.get("bomFormat") != "CycloneDX":
        errors.append(f"{source}: bomFormat must be 'CycloneDX'")
    if not isinstance(document.get("specVersion"), str) or not document["specVersion"]:
        errors.append(f"{source}: specVersion must be nonempty text")
    components = document.get("components")
    if not isinstance(components, list) or not components:
        errors.append(f"{source}: components must be a nonempty array")
    return errors


def check_release_directory(directory: Path) -> list[str]:
    """Return checksum and SBOM validation errors for one release directory."""
    if not directory.is_dir():
        return [f"release directory does not exist: {directory}"]
    return [*_checksum_errors(directory), *_sbom_errors(directory)]


def main() -> int:
    """Validate a release directory and return a shell-compatible status code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_DIST,
        help="directory containing distributions, SHA256SUMS, and dependencies.cdx.json",
    )
    args = parser.parse_args()
    errors = check_release_directory(args.directory)
    if errors:
        print("Release integrity checks failed:", file=sys.stderr)
        print("\n".join(f"  {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"Release integrity checks passed for {args.directory}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
