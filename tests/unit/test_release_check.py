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
"""Release checksum and SBOM validation tests."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


def _checker_module():
    path = Path(__file__).parents[2] / "scripts" / "check-release.py"
    spec = importlib.util.spec_from_file_location("orbit_check_release", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load release checker.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_valid_release(directory: Path) -> None:
    artifacts = {
        "orbit_core-0.1.0a1-py3-none-any.whl": b"wheel",
        "orbit_core-0.1.0a1.tar.gz": b"source",
        "dependencies.cdx.json": json.dumps(
            {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": [{"name": "pydantic"}]}
        ).encode(),
    }
    checksums: list[str] = []
    for name, content in artifacts.items():
        (directory / name).write_bytes(content)
        checksums.append(f"{hashlib.sha256(content).hexdigest()}  {name}")
    (directory / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def test_release_checker_accepts_valid_checksums_and_sbom(tmp_path: Path) -> None:
    _write_valid_release(tmp_path)

    assert _checker_module().check_release_directory(tmp_path) == []


def test_release_checker_accepts_workflow_directory_qualified_paths(tmp_path: Path) -> None:
    _write_valid_release(tmp_path)
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        "\n".join(
            f"{hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}  {tmp_path.name}/{name}"
            for name in (
                "orbit_core-0.1.0a1-py3-none-any.whl",
                "orbit_core-0.1.0a1.tar.gz",
                "dependencies.cdx.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert _checker_module().check_release_directory(tmp_path) == []


def test_release_checker_rejects_digest_and_sbom_shape_errors(tmp_path: Path) -> None:
    _write_valid_release(tmp_path)
    (tmp_path / "orbit_core-0.1.0a1-py3-none-any.whl").write_bytes(b"tampered")
    (tmp_path / "dependencies.cdx.json").write_text(
        json.dumps({"bomFormat": "not-cyclonedx", "components": []}), encoding="utf-8"
    )

    errors = _checker_module().check_release_directory(tmp_path)

    assert any("SHA-256 mismatch" in error for error in errors)
    assert any("bomFormat" in error for error in errors)
    assert any("specVersion" in error for error in errors)
    assert any("components" in error for error in errors)


def test_release_checker_rejects_unsafe_or_duplicate_manifest_entries(tmp_path: Path) -> None:
    _write_valid_release(tmp_path)
    digest = hashlib.sha256(b"wheel").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        "\n".join(
            (
                f"{digest}  ../outside.whl",
                f"{digest}  orbit_core-0.1.0a1-py3-none-any.whl",
                f"{digest}  orbit_core-0.1.0a1-py3-none-any.whl",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    errors = _checker_module().check_release_directory(tmp_path)

    assert any("unsafe artifact name" in error for error in errors)
    assert any("duplicate artifact name" in error for error in errors)
    assert any("dependency SBOM" in error for error in errors)


def test_release_checker_rejects_unlisted_direct_artifacts(tmp_path: Path) -> None:
    """Every direct release file must be covered before the set is signed or uploaded."""
    _write_valid_release(tmp_path)
    (tmp_path / "unexpected-artifact.txt").write_bytes(b"not covered")

    errors = _checker_module().check_release_directory(tmp_path)

    assert any("artifact is not covered" in error for error in errors)


def test_release_checker_rejects_symlinked_release_metadata(tmp_path: Path) -> None:
    """Release validation must not follow links for the manifest or dependency inventory."""
    _write_valid_release(tmp_path)
    target = tmp_path / "outside.json"
    target.write_bytes((tmp_path / "dependencies.cdx.json").read_bytes())
    (tmp_path / "dependencies.cdx.json").unlink()
    try:
        os.symlink(target, tmp_path / "dependencies.cdx.json")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    errors = _checker_module().check_release_directory(tmp_path)

    assert any("does not exist" in error for error in errors)
    assert any("missing dependency SBOM" in error for error in errors)
