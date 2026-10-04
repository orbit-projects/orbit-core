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
"""Fail when maintained Python source lacks Orbit's Apache-2.0 SPDX header."""

from __future__ import annotations

import sys
from pathlib import Path

HEADER = '# Licensed under the Apache License, Version 2.0 (the "License");'
ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }
)


def python_files() -> list[Path]:
    """Return maintained Python files, excluding generated environments and caches."""
    return [
        path for path in ROOT.rglob("*.py") if not GENERATED_DIRECTORIES.intersection(path.parts)
    ]


def main() -> int:
    """Print missing headers and return a nonzero status when policy is violated."""
    missing = [
        path.relative_to(ROOT)
        for path in python_files()
        if HEADER not in path.read_text(encoding="utf-8").splitlines()[:16]
    ]
    if missing:
        print("Missing Apache-2.0 license headers:", file=sys.stderr)
        print("\n".join(f"  {path}" for path in missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
