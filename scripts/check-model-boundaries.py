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
"""Reject implicit primitive coercion in Core Pydantic model fields."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "orbit"
PRIMITIVES = frozenset({"bool", "float", "int", "str"})


def _contains_unstrict_primitive(annotation: ast.expr) -> bool:
    """Return whether an annotation contains a primitive that Pydantic may coerce."""
    if isinstance(annotation, ast.Name):
        return annotation.id in PRIMITIVES
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _contains_unstrict_primitive(annotation.left) or _contains_unstrict_primitive(
            annotation.right
        )
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name) and annotation.value.id.startswith("Strict"):
            return False
        if isinstance(annotation.value, ast.Name) and annotation.value.id == "Annotated":
            elements = (
                annotation.slice.elts
                if isinstance(annotation.slice, ast.Tuple)
                else (annotation.slice,)
            )
            return bool(elements) and _contains_unstrict_primitive(elements[0])
        if isinstance(annotation.value, ast.Name) and annotation.value.id in {
            "frozenset",
            "list",
            "set",
            "tuple",
        }:
            return _contains_unstrict_primitive(annotation.slice)
    if isinstance(annotation, ast.Tuple):
        return any(_contains_unstrict_primitive(element) for element in annotation.elts)
    return False


def _is_mapping_annotation(annotation: ast.expr) -> bool:
    """Return whether a field is a typed dictionary whose keys need pre-validation."""
    return (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and (annotation.value.id == "dict")
    )


def _before_validated_fields(node: ast.ClassDef) -> set[str]:
    """Return fields with an explicit ``field_validator(..., mode='before')`` hook."""
    fields: set[str] = set()
    for member in node.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in member.decorator_list:
            if not isinstance(decorator, ast.Call) or not (
                isinstance(decorator.func, ast.Name) and decorator.func.id == "field_validator"
            ):
                continue
            mode = next(
                (keyword.value for keyword in decorator.keywords if keyword.arg == "mode"),
                None,
            )
            if not isinstance(mode, ast.Constant) or mode.value != "before":
                continue
            fields.update(
                argument.value
                for argument in decorator.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            )
    return fields


def _validates_defaults(node: ast.ClassDef) -> bool:
    """Return whether a Pydantic model validates declared defaults at construction."""
    for member in node.body:
        value: ast.expr | None = None
        if (
            isinstance(member, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in member.targets
            )
        ) or (
            isinstance(member, ast.AnnAssign)
            and isinstance(member.target, ast.Name)
            and member.target.id == "model_config"
        ):
            value = member.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "ConfigDict"
        ):
            continue
        for keyword in value.keywords:
            if (
                keyword.arg == "validate_default"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                return True
    return False


def _violations(path: Path) -> list[str]:
    """Return direct BaseModel field annotations that need an explicit strict type."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not any(
            isinstance(base, ast.Name) and base.id == "BaseModel" for base in node.bases
        ):
            continue
        before_validated = _before_validated_fields(node)
        if not _validates_defaults(node):
            violations.append(
                f"{path.relative_to(ROOT)}:{node.lineno}:{node.name} "
                "must set ConfigDict(validate_default=True)"
            )
        for field in node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            if _contains_unstrict_primitive(field.annotation):
                violations.append(
                    f"{path.relative_to(ROOT)}:{field.lineno}:{node.name}.{field.target.id} "
                    f"uses {ast.unparse(field.annotation)}; use a strict Pydantic type"
                )
            if _is_mapping_annotation(field.annotation) and field.target.id not in before_validated:
                violations.append(
                    f"{path.relative_to(ROOT)}:{field.lineno}:{node.name}.{field.target.id} "
                    "is a mapping field without a mode='before' validator"
                )
    return violations


def main() -> int:
    """Check every Core Pydantic model and return a process-style status code."""
    violations = [
        violation for path in sorted(SOURCE.rglob("*.py")) for violation in _violations(path)
    ]
    if violations:
        print("\n".join(violations))
        return 1
    print("Core Pydantic model fields use explicit strict primitive types.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
