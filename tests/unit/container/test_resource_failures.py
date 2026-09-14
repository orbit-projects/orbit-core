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
"""Every resource exit receives a deadline and failures remain observable."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from orbit.container import Container


async def test_cleanup_aggregates_timeout_and_errors_and_attempts_every_exit():
    root = Container(cleanup_timeout=0.01)
    exits = []

    @asynccontextmanager
    async def first(container):
        yield "first"
        exits.append("first")
        raise ValueError("first")

    @asynccontextmanager
    async def last(container):
        yield "last"
        exits.append("last")
        await asyncio.Event().wait()

    root.register_resource("first", first)
    root.register_resource("last", last)
    await root.aresolve("first")
    await root.aresolve("last")
    with pytest.raises(ExceptionGroup) as error:
        await root.aclose()
    assert exits == ["last", "first"]
    assert {type(exc) for exc in error.value.exceptions} == {TimeoutError, ValueError}
    with pytest.raises(ExceptionGroup):
        await root.aclose()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_cleanup_deadline_must_be_positive_finite(timeout):
    with pytest.raises(ValueError):
        Container(cleanup_timeout=timeout)
