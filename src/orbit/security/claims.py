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
"""Shared validation for provider-supplied security claim mappings."""

from typing import Any

from orbit._immutability import validate_mapping

_MAX_CLAIMS = 1_024
_MAX_CLAIM_KEY_LENGTH = 255


def validate_claims(value: object) -> dict[str, Any]:
    """Validate the shape of a verified claim mapping.

    Claim values intentionally remain provider-neutral: different identity
    systems use different JSON-compatible structures. The mapping itself is
    still bounded so an untrusted provider response cannot create an
    arbitrarily large identity or token model. Values are detached and
    recursively frozen by the owning model after this validator returns.
    """
    return validate_mapping(
        value,
        name="claims",
        max_entries=_MAX_CLAIMS,
        max_key_length=_MAX_CLAIM_KEY_LENGTH,
    )


__all__ = ["validate_claims"]
