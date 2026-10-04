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
"""Keep repository documentation scans focused on maintained project files."""

import ast
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-documentation.py"
_SPEC = importlib.util.spec_from_file_location("orbit_check_documentation", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
documentation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(documentation)
_HEADER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-license-headers.py"
_HEADER_SPEC = importlib.util.spec_from_file_location("orbit_check_license_headers", _HEADER_SCRIPT)
assert _HEADER_SPEC is not None and _HEADER_SPEC.loader is not None
license_headers = importlib.util.module_from_spec(_HEADER_SPEC)
_HEADER_SPEC.loader.exec_module(license_headers)


def test_documentation_scans_ignore_generated_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated tool environments must not be treated as maintained source documentation."""
    root = tmp_path / "repository"
    docs = root / "docs"
    generated = root / ".tox" / "py311" / "site-packages"
    docs.mkdir(parents=True)
    generated.mkdir(parents=True)
    (docs / "index.md").write_text("# Project docs\n", encoding="utf-8")
    (generated / "README.md").write_text("Not a maintained document\n", encoding="utf-8")
    (generated / "module.py").write_text("# generated package file\n", encoding="utf-8")

    monkeypatch.setattr(documentation, "MARKDOWN_ROOTS", (root,))
    monkeypatch.setattr(documentation, "ROOT", root)
    monkeypatch.setattr(documentation, "PYTHON_ROOTS", (root,))
    monkeypatch.setattr(license_headers, "ROOT", root)

    assert documentation.markdown_files() == [docs / "index.md"]
    assert documentation.python_files() == []
    assert license_headers.python_files() == []


def test_documentation_scans_checked_out_sibling_package_sources_and_guides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sibling package code, examples, READMEs, and public APIs share docs policy."""
    root = tmp_path / "Projects" / "orbit-core"
    sibling = root.parent / "orbit-example"
    package = sibling / "src" / "orbit_example"
    package.mkdir(parents=True)
    tests = sibling / "tests"
    examples = sibling / "examples"
    tests.mkdir()
    examples.mkdir()
    root.mkdir(parents=True)
    readme = sibling / "README.md"
    readme.write_text("# Example package\n", encoding="utf-8")
    source = package / "api.py"
    source.write_text('"""Example module."""\n', encoding="utf-8")
    test_source = tests / "test_api.py"
    test_source.write_text('"""Example tests."""\n', encoding="utf-8")
    example_source = examples / "app.py"
    example_source.write_text('"""Runnable example."""\n', encoding="utf-8")

    monkeypatch.setattr(documentation, "ROOT", root)
    monkeypatch.setattr(documentation, "PYTHON_ROOTS", (root / "src",))
    monkeypatch.setattr(documentation, "MARKDOWN_ROOTS", (root,))

    assert readme in documentation.markdown_files()
    assert {source, test_source, example_source}.issubset(documentation.python_files())
    assert package in documentation.public_api_roots()
    assert tests not in documentation.public_api_roots()
    assert examples not in documentation.public_api_roots()


def test_public_documentation_errors_identify_missing_class_method_and_function_docs() -> None:
    """The documentation policy names every undocumented public API object."""
    tree = ast.parse(
        '"""Module docs."""\n'
        "class PublicType:\n"
        '    """Type docs."""\n'
        "    def missing_method(self):\n"
        "        pass\n"
        "def missing_function():\n"
        "    pass\n"
    )

    errors = documentation.public_documentation_errors(Path("api.py"), tree)

    assert any("public method missing_method" in error for error in errors)
    assert any("public function missing_function" in error for error in errors)


def test_documentation_policy_checks_sibling_test_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unresolved markers in sibling package tests are reported by the repository gate."""
    root = tmp_path / "Projects" / "orbit-core"
    sibling_test = root.parent / "orbit-example" / "tests" / "test_api.py"
    sibling_test.parent.mkdir(parents=True)
    sibling_test.write_text('"""Example tests."""\n# TODO remove this marker\n', encoding="utf-8")

    monkeypatch.setattr(documentation, "ROOT", root)
    monkeypatch.setattr(documentation, "PYTHON_ROOTS", ())
    monkeypatch.setattr(documentation, "MARKDOWN_ROOTS", ())

    assert documentation.main() == 1
    assert "unresolved marker in comment" in capsys.readouterr().err
