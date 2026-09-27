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
"""Release-artifact metadata contract tests."""

import importlib.util
import tarfile
from email.message import Message
from pathlib import Path


def _checker_module():
    path = Path(__file__).parents[2] / "scripts" / "check-package.py"
    spec = importlib.util.spec_from_file_location("orbit_check_package", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load package checker.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_distribution_metadata_must_match_project_version() -> None:
    checker = _checker_module()
    metadata = Message()
    metadata["Name"] = "orbit-core"
    metadata["License-Expression"] = "Apache-2.0"
    metadata["Requires-Python"] = ">=3.11,<3.15"
    metadata["License-File"] = "LICENSE"
    metadata["Version"] = "0.0.0"

    errors = checker._metadata_errors(metadata, Path("artifact.whl"))  # noqa: SLF001

    assert any("must match the declared project version" in error for error in errors)

    regular = tarfile.TarInfo("orbit_core-0.1.0a1/file.txt")
    regular.type = tarfile.REGTYPE
    link = tarfile.TarInfo("orbit_core-0.1.0a1/link")
    link.type = tarfile.SYMTYPE
    assert checker._safe_tar_member(regular)  # noqa: SLF001
    assert not checker._safe_tar_member(link)  # noqa: SLF001
