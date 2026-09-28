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
"""Validate and report Orbit Core's fixed, evidence-based release scorecard."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs" / "development" / "completion-scorecard.toml"
COMPLETION_DOC = ROOT / "docs" / "development" / "completion.md"
HANDOFF_DOC = ROOT / "HANDOFF.md"
EXPECTED_AREAS = (
    ("core_contracts", "Core contracts and scope", 25),
    ("reliability_operations", "Reliability and operational behavior", 20),
    ("security_supply_chain", "Security and supply chain", 15),
    ("tests_compatibility", "Tests and compatibility", 20),
    ("documentation_api", "Documentation and API governance", 10),
    ("packaging_release", "Packaging and release operations", 10),
)
EXPECTED_GATES = (
    "local_core_validation",
    "hosted_ci_security",
    "deployment_http_hosting",
    "release_artifacts_provenance",
)
VALID_GATE_STATES = frozenset({"pass", "open"})
VALID_CRITERION_STATES = frozenset({"earned", "open"})


def load_scorecard(path: Path = SCORECARD) -> dict[str, Any]:
    """Load the TOML scorecard and require a mapping at its document root."""
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Scorecard root must be a TOML table.")
    return data


def validate_scorecard(data: dict[str, Any]) -> list[str]:
    """Return structural or arithmetic violations in one scorecard document."""
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("scorecard version must be 1")
    threshold = data.get("stable_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
        errors.append("stable_threshold must be an integer from 0 through 100")

    areas = data.get("areas")
    if not isinstance(areas, dict):
        errors.append("areas must be a TOML table")
        areas = {}
    area_order = data.get("area_order")
    expected_keys = {key for key, _, _ in EXPECTED_AREAS}
    if area_order != [key for key, _, _ in EXPECTED_AREAS]:
        errors.append("area_order must match the fixed scorecard category order")
    if set(areas) != expected_keys:
        errors.append("areas must contain exactly the fixed scorecard categories")

    total_weight = 0
    total_earned = 0
    for key, label, expected_weight in EXPECTED_AREAS:
        area = areas.get(key)
        if not isinstance(area, dict):
            errors.append(f"{key} must be a TOML table")
            continue
        if area.get("label") != label:
            errors.append(f"{key}.label must be {label!r}")
        weight = area.get("weight")
        earned = area.get("earned")
        if weight != expected_weight:
            errors.append(f"{key}.weight must be {expected_weight}")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            errors.append(f"{key}.weight must be a non-negative integer")
        else:
            total_weight += weight
        if isinstance(earned, bool) or not isinstance(earned, int):
            errors.append(f"{key}.earned must be an integer")
        elif not isinstance(weight, int) or not 0 <= earned <= weight:
            errors.append(f"{key}.earned must be between zero and its weight")
        else:
            total_earned += earned
        criteria = area.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{key}.criteria must be a nonempty array")
            continue
        criteria_weight = 0
        criteria_earned = 0
        for index, criterion in enumerate(criteria, start=1):
            prefix = f"{key}.criteria[{index}]"
            if not isinstance(criterion, dict):
                errors.append(f"{prefix} must be a TOML table")
                continue
            points = criterion.get("points")
            if isinstance(points, bool) or not isinstance(points, int) or points <= 0:
                errors.append(f"{prefix}.points must be a positive integer")
            else:
                criteria_weight += points
            status = criterion.get("status")
            if status not in VALID_CRITERION_STATES:
                errors.append(f"{prefix}.status must be one of: earned, open")
            elif status == "earned" and isinstance(points, int) and not isinstance(points, bool):
                criteria_earned += points
            for field in ("requirement", "evidence"):
                value = criterion.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{prefix}.{field} must be nonempty text")
        if isinstance(weight, int) and criteria_weight != weight:
            errors.append(f"{key}.criteria points must total {weight}")
        if isinstance(earned, int) and criteria_earned != earned:
            errors.append(f"{key}.criteria earned points must total {earned}")
    if total_weight != 100:
        errors.append(f"scorecard weights must total 100, got {total_weight}")

    gates = data.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be a TOML table")
        gates = {}
    if set(gates) != set(EXPECTED_GATES):
        errors.append("gates must contain exactly the fixed mandatory release gates")
    for gate in EXPECTED_GATES:
        if gates.get(gate) not in VALID_GATE_STATES:
            errors.append(f"{gate} must be one of: pass, open")
    return errors


def scorecard_result(data: dict[str, Any]) -> tuple[int, bool]:
    """Return the earned score and whether the stable-release gates are all satisfied."""
    areas = data["areas"]
    score = sum(area["earned"] for area in areas.values())
    gates = data["gates"]
    stable = score >= data["stable_threshold"] and all(
        gates[gate] == "pass" for gate in EXPECTED_GATES
    )
    return score, stable


def documented_scorecard_errors(data: dict[str, Any]) -> list[str]:
    """Return errors when the human-readable score summaries drift from the TOML source."""
    score, _ = scorecard_result(data)
    areas = data["areas"]
    completion_summary = (
        f"Current evidence score: **{score} / 100** — "
        f"Core contracts {areas['core_contracts']['earned']}/{areas['core_contracts']['weight']}, "
        f"reliability {areas['reliability_operations']['earned']}/"
        f"{areas['reliability_operations']['weight']}, "
        f"security and supply chain {areas['security_supply_chain']['earned']}/"
        f"{areas['security_supply_chain']['weight']}, "
        f"tests and compatibility {areas['tests_compatibility']['earned']}/"
        f"{areas['tests_compatibility']['weight']}, "
        f"documentation and API governance {areas['documentation_api']['earned']}/"
        f"{areas['documentation_api']['weight']}, and packaging and release operations "
        f"{areas['packaging_release']['earned']}/{areas['packaging_release']['weight']}."
    )
    errors: list[str] = []
    try:
        completion_text = " ".join(COMPLETION_DOC.read_text(encoding="utf-8").split())
        handoff_text = " ".join(HANDOFF_DOC.read_text(encoding="utf-8").split())
    except OSError as exc:
        return [f"unable to read scorecard summary documents: {exc}"]
    if " ".join(completion_summary.split()) not in completion_text:
        errors.append("completion.md score summary does not match completion-scorecard.toml")
    handoff_summary = f"The current evidence score is **{score}/100**."
    if handoff_summary not in handoff_text:
        errors.append("HANDOFF.md score summary does not match completion-scorecard.toml")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Print the fixed score and fail only when the document or requested gate is invalid."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-stable",
        action="store_true",
        help="return failure until the score threshold and every mandatory gate pass",
    )
    args = parser.parse_args(argv)
    try:
        data = load_scorecard()
        errors = validate_scorecard(data)
        if not errors:
            errors.extend(documented_scorecard_errors(data))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"Scorecard invalid: {exc}")
        return 1
    if errors:
        print("Scorecard invalid:")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    score, stable = scorecard_result(data)
    print(f"Orbit Core score: {score}/100")
    for key, label, _ in EXPECTED_AREAS:
        area = data["areas"][key]
        print(f"- {label}: {area['earned']}/{area['weight']}")
    print(f"Stable release gate: {'PASS' if stable else 'OPEN'}")
    if args.require_stable and not stable:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
