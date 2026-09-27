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
"""Validate the contents and metadata of built Orbit Core distributions.

This check intentionally inspects archives without installing them. It catches a common class
of release mistakes—missing package files, missing typing markers, malformed metadata, or an
unsafe archive member—before an artifact is attested or uploaded. Runtime dependencies remain
defined by ``pyproject.toml``; this script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import ast
import email.message
import email.parser
import posixpath
import stat
import sys
import tarfile
from pathlib import Path
from zipfile import ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
EXPECTED_NAME = "orbit-core"
EXPECTED_LICENSE = "Apache-2.0"
EXPECTED_REQUIRES_PYTHON = {">=3.11,<3.15", "<3.15,>=3.11"}
_VERSION_MODULE = ast.parse(
    (ROOT / "src/orbit/_version.py").read_text(encoding="utf-8"),
    filename="src/orbit/_version.py",
)
EXPECTED_VERSION = next(
    value.value
    for statement in _VERSION_MODULE.body
    if isinstance(statement, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets
    )
    for value in [statement.value]
    if isinstance(value, ast.Constant) and isinstance(value.value, str)
)


def _safe_member(name: str) -> bool:
    """Return whether an archive member stays within its extraction directory."""
    normalized = posixpath.normpath(name)
    return not normalized.startswith("/") and ".." not in normalized.split("/")


def _safe_tar_member(member: tarfile.TarInfo) -> bool:
    """Allow only regular files and directories with safe relative archive names."""
    return _safe_member(member.name) and (member.isfile() or member.isdir())


def _safe_zip_member(member: ZipInfo) -> bool:
    """Reject ZIP symlinks while allowing normal wheel entries without Unix mode bits."""
    mode = (member.external_attr >> 16) & 0o170000
    return _safe_member(member.filename) and mode != stat.S_IFLNK


def _metadata_errors(metadata: email.message.Message, source: Path) -> list[str]:
    """Return errors for the release metadata carried by one archive."""
    errors: list[str] = []
    expected = {"Name": EXPECTED_NAME, "License-Expression": EXPECTED_LICENSE}
    for key, value in expected.items():
        if metadata.get(key) != value:
            errors.append(f"{source}: metadata {key!r} must be {value!r}")
    if metadata.get("Requires-Python") not in EXPECTED_REQUIRES_PYTHON:
        errors.append(f"{source}: metadata 'Requires-Python' must describe Python 3.11–3.14")
    version = metadata.get("Version")
    if not version:
        errors.append(f"{source}: metadata must contain a version")
    elif version != EXPECTED_VERSION:
        errors.append(
            f"{source}: metadata version must match the declared project version "
            f"({EXPECTED_VERSION!r})"
        )
    if not metadata.get("License-File"):
        errors.append(f"{source}: metadata must declare the license file")
    return errors


def _read_metadata(raw: bytes, source: Path) -> list[str]:
    """Parse metadata bytes and return validation errors without raising parser failures."""
    metadata = email.parser.BytesParser().parsebytes(raw)
    return _metadata_errors(metadata, source)


def _check_wheel(path: Path) -> list[str]:
    """Validate the package, typing marker, license, and metadata in a wheel."""
    errors: list[str] = []
    with ZipFile(path) as archive:
        names = archive.namelist()
        errors.extend(
            f"{path}: unsafe archive member {info.filename!r}"
            for info in archive.infolist()
            if not _safe_zip_member(info)
        )
        dist_info = sorted(name for name in names if name.endswith(".dist-info/METADATA"))
        if len(dist_info) != 1:
            errors.append(f"{path}: expected exactly one wheel METADATA file")
        else:
            errors.extend(_read_metadata(archive.read(dist_info[0]), path))
        required = {
            "orbit/__init__.py": "the importable Orbit package",
            "orbit/py.typed": "the PEP 561 typing marker",
        }
        for name, description in required.items():
            if name not in names:
                errors.append(f"{path}: missing {description}: {name}")
        if not any(name.endswith(".dist-info/WHEEL") for name in names):
            errors.append(f"{path}: missing wheel metadata")
        if not any(name.endswith(".dist-info/RECORD") for name in names):
            errors.append(f"{path}: missing wheel record")
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            errors.append(f"{path}: missing packaged LICENSE file")
    return errors


def _check_sdist(path: Path) -> list[str]:
    """Validate the source package, license, typing marker, and metadata in an sdist."""
    errors: list[str] = []
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        errors.extend(
            f"{path}: unsafe archive member {member.name!r}"
            for member in members
            if not _safe_tar_member(member)
        )
        pkg_info = sorted(name for name in names if name.endswith("/PKG-INFO"))
        if len(pkg_info) != 1:
            errors.append(f"{path}: expected exactly one source PKG-INFO file")
        else:
            member = archive.extractfile(pkg_info[0])
            if member is None:
                errors.append(f"{path}: unable to read {pkg_info[0]}")
            else:
                errors.extend(_read_metadata(member.read(), path))
        required_suffixes = {
            "/src/orbit/__init__.py": "the importable Orbit package",
            "/src/orbit/py.typed": "the PEP 561 typing marker",
            "/LICENSE": "the project license",
        }
        for suffix, description in required_suffixes.items():
            if not any(name.endswith(suffix) for name in names):
                errors.append(f"{path}: missing {description} ({suffix})")
    return errors


def check_distribution_directory(directory: Path) -> list[str]:
    """Return package-integrity errors for the wheel and sdist in ``directory``."""
    if not directory.is_dir():
        return [f"distribution directory does not exist: {directory}"]
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    errors: list[str] = []
    if len(wheels) != 1:
        errors.append(f"expected exactly one wheel in {directory}, found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist in {directory}, found {len(sdists)}")
    if len(wheels) == 1:
        expected_prefix = f"{EXPECTED_NAME.replace('-', '_')}-{EXPECTED_VERSION}-"
        if not wheels[0].name.startswith(expected_prefix):
            errors.append(
                f"{wheels[0]}: filename must start with the project name and version "
                f"({expected_prefix!r})"
            )
        errors.extend(_check_wheel(wheels[0]))
    if len(sdists) == 1:
        expected_name = f"{EXPECTED_NAME.replace('-', '_')}-{EXPECTED_VERSION}.tar.gz"
        if sdists[0].name != expected_name:
            errors.append(f"{sdists[0]}: filename must be {expected_name!r}")
        errors.extend(_check_sdist(sdists[0]))
    return errors


def main() -> int:
    """Validate the requested distribution directory and return a shell status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_DIST,
        help="directory containing exactly one wheel and one source archive (default: dist)",
    )
    args = parser.parse_args()
    errors = check_distribution_directory(args.directory)
    if errors:
        print("Package integrity checks failed:", file=sys.stderr)
        print("\n".join(f"  {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"Package integrity checks passed for {args.directory}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
