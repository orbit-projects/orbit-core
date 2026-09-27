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
"""Shared validation for security role and scope identifiers."""

from collections.abc import Collection

_MAX_ROLE_ENTRIES = 1_024


def validate_role_text(value: object) -> str:
    """Validate one printable, bounded role or scope identifier."""
    if not isinstance(value, str):
        raise TypeError("Role and scope identifiers must be strings.")
    if not 1 <= len(value) <= 255 or any(
        not character.isprintable() or character.isspace() for character in value
    ):
        raise ValueError(
            "Role and scope identifiers must be nonempty and contain at most 255 "
            "non-whitespace printable characters."
        )
    return value


def validate_role_collection(values: Collection[str]) -> frozenset[str]:
    """Validate and normalize a bounded role or scope collection.

    The shared limit applies to principals, tokens, routes, OAuth scopes and authorization
    policies alike. Keeping it here prevents one security surface from accepting a much larger
    identity payload than another surface can safely inspect or serialize.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise TypeError("Roles and scopes must be collections of strings.")
    if len(values) > _MAX_ROLE_ENTRIES:
        raise ValueError(
            f"Roles and scopes cannot contain more than {_MAX_ROLE_ENTRIES:,} entries."
        )
    try:
        normalized = frozenset(values)
    except TypeError as exc:
        raise TypeError("Roles and scopes must be collections of strings.") from exc
    return frozenset(validate_role_text(value) for value in normalized)


__all__ = ["validate_role_collection", "validate_role_text"]
