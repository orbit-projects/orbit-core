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
"""Shared bounded protocol and numeric limits used by Core boundaries."""

import math
import re
from datetime import datetime
from typing import TypeGuard

MAX_PATH_BYTES: int = 16 * 1024
MAX_PATH_PARAMETERS: int = 128
MAX_PATH_PARAMETER_NAME_LENGTH: int = 127
_MAX_CORE_CAPACITY: int = 1_000_000
_MAX_RELATION_ENTRIES: int = 1_024
_MAX_CONFIG_FILE_BYTES: int = 64 * 1024 * 1024
_EXCEPTION_TYPE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")


def is_finite_number(value: object) -> TypeGuard[int | float]:
    """Return whether a value is a finite non-boolean integer or floating-point number.

    ``math.isfinite`` can raise ``OverflowError`` for integers too large to convert to a
    platform float. Core treats those values as invalid policy input rather than leaking an
    implementation-level exception from a public constructor.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def is_aware_datetime(value: object) -> TypeGuard[datetime]:
    """Return whether a datetime has a usable UTC offset for cross-process comparisons.

    ``tzinfo`` implementations are application-provided code. Core treats any ordinary
    exception raised while asking one for its offset as invalid input and fails closed.
    """
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except Exception:
        return False


def safe_exception_type_name(error: BaseException) -> str:
    """Return a bounded exception type suitable for public diagnostics.

    Exception class names are application-controlled: ``type()`` can create names with control
    characters or arbitrary length. Core's structured records use identifier-shaped text, so an
    unsafe name is deliberately generalized instead of letting diagnostics mask the real failure.
    """
    name = type(error).__name__
    return name if _EXCEPTION_TYPE_PATTERN.fullmatch(name) is not None else "Exception"


__all__ = [
    "MAX_PATH_BYTES",
    "MAX_PATH_PARAMETER_NAME_LENGTH",
    "MAX_PATH_PARAMETERS",
    "is_aware_datetime",
    "is_finite_number",
    "safe_exception_type_name",
]
