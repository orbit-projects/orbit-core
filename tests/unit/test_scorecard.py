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
"""Regression tests for the fixed Orbit Core release scorecard."""

import importlib.util
from pathlib import Path


def _scorecard_module():
    path = Path(__file__).parents[2] / "scripts" / "check-scorecard.py"
    spec = importlib.util.spec_from_file_location("orbit_check_scorecard", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load scorecard checker.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_scorecard_is_valid_and_not_stable_yet() -> None:
    module = _scorecard_module()
    data = module.load_scorecard()
    assert module.validate_scorecard(data) == []
    assert module.documented_scorecard_errors(data) == []
    assert module.scorecard_result(data) == (87, False)


def test_scorecard_rejects_weight_or_gate_drift() -> None:
    module = _scorecard_module()
    data = module.load_scorecard()
    data["areas"]["core_contracts"]["weight"] = 24
    data["gates"]["hosted_ci_security"] = "verified"
    errors = module.validate_scorecard(data)
    assert any("core_contracts.weight" in error for error in errors)
    assert any("hosted_ci_security" in error for error in errors)


def test_scorecard_rejects_point_ledger_drift() -> None:
    """Every reported point must have a named status, requirement, and evidence source."""
    module = _scorecard_module()
    data = module.load_scorecard()
    criterion = data["areas"]["core_contracts"]["criteria"][0]
    criterion["status"] = "verified"
    criterion["evidence"] = ""
    errors = module.validate_scorecard(data)
    assert any("core_contracts.criteria[1].status" in error for error in errors)
    assert any("core_contracts.criteria[1].evidence" in error for error in errors)


def test_scorecard_rejects_human_summary_drift() -> None:
    module = _scorecard_module()
    data = module.load_scorecard()
    data["areas"]["packaging_release"]["earned"] = 6
    errors = module.documented_scorecard_errors(data)
    assert any("completion.md score summary" in error for error in errors)
    assert any("HANDOFF.md score summary" in error for error in errors)
