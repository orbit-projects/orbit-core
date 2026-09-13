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
"""Lifecycle validation tests."""

import pytest

from orbit.errors import LifecycleError
from orbit.lifecycle import Lifecycle, LifecyclePhase


async def test_lifecycle_rejects_skipped_phases() -> None:
    """The lifecycle state machine rejects an invalid direct startup transition."""
    lifecycle = Lifecycle()

    with pytest.raises(LifecycleError, match="Cannot transition"):
        await lifecycle.transition(LifecyclePhase.RUNNING)
