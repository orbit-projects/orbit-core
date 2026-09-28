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
"""Administrative audit diagnostic identifier tests."""

from types import SimpleNamespace

from orbit.admin.application import _audit_error_code


def test_admin_audit_error_code_rejects_untrusted_problem_metadata() -> None:
    """An arbitrary exception cannot make audit recording emit an invalid identifier."""
    unsafe_error = type("private\n" + "x" * 128, (RuntimeError,), {})
    failure = unsafe_error("private detail")
    failure.problem = SimpleNamespace(code="bad code")

    assert _audit_error_code(failure) == "Exception"

    failure.problem = SimpleNamespace(code="runtime.operation-failed")
    assert _audit_error_code(failure) == "runtime.operation-failed"
