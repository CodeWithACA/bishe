from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_TASK_FILE = Path(__file__).resolve().parent / "task_sets" / "clgsce_basic.jsonl"
DEFAULT_POSITION_TOLERANCE_M = 1.0
DEFAULT_YAW_TOLERANCE_DEG = 4.0


def load_task(task_file: Path, task_id: str) -> dict[str, Any]:
    with task_file.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            task = json.loads(line)
            if task.get("id") == task_id:
                return task
    raise ValueError(f"Task id not found: {task_id}")


def yaw_delta_deg(after: float, before: float) -> float:
    return (after - before + 180.0) % 360.0 - 180.0


def yaw_error_deg(actual: float, expected: float) -> float:
    return abs((actual - expected + 180.0) % 360.0 - 180.0)


def numeric_state(state: dict[str, Any] | None) -> dict[str, float] | None:
    if not state:
        return None
    required = ["x", "y", "z", "yaw_deg"]
    if any(state.get(key) is None for key in required):
        return None
    return {key: float(state[key]) for key in required}


def observed_transition(entry: dict[str, Any]) -> dict[str, Any] | None:
    before = numeric_state(entry.get("before"))
    after = numeric_state(entry.get("after"))
    if before is None or after is None:
        return None

    dx = after["x"] - before["x"]
    dy = after["y"] - before["y"]
    dz = after["z"] - before["z"]
    dyaw = yaw_delta_deg(after["yaw_deg"], before["yaw_deg"])
    return {
        "index": entry.get("index"),
        "action": entry.get("action"),
        "params": entry.get("params", {}),
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "dyaw": dyaw,
    }


def is_significant(change: dict[str, Any]) -> bool:
    return (
        abs(change["dx"]) > 2.0
        or abs(change["dy"]) > 2.0
        or abs(change["dz"]) > 1.0
        or abs(change["dyaw"]) > 30.0
    )


def load_observed_actions(flight_history_path: Path) -> list[dict[str, Any]]:
    with flight_history_path.open("r", encoding="utf-8") as file:
        history = json.load(file)

    actions = []
    for entry in history:
        change = observed_transition(entry)
        if change and is_significant(change):
            actions.append(change)
    return actions


def compare_action(actual: dict[str, Any] | None, expected: dict[str, Any], position_tolerance_m: float, yaw_tolerance_deg: float) -> dict[str, Any]:
    if actual is None:
        return {
            "matched": False,
            "reason": "missing observed action",
            "expected": expected,
            "actual": None,
        }

    errors = {
        "dx": actual["dx"] - float(expected["dx"]),
        "dy": actual["dy"] - float(expected["dy"]),
        "dz": actual["dz"] - float(expected["dz"]),
        "dyaw": yaw_error_deg(actual["dyaw"], float(expected["dyaw"])),
    }
    matched = (
        abs(errors["dx"]) < position_tolerance_m
        and abs(errors["dy"]) < position_tolerance_m
        and abs(errors["dz"]) < position_tolerance_m
        and errors["dyaw"] < yaw_tolerance_deg
    )
    return {
        "matched": matched,
        "expected": expected,
        "actual": {key: round(float(actual[key]), 3) if key in {"dx", "dy", "dz", "dyaw"} else actual[key] for key in actual},
        "errors": {key: round(float(value), 3) for key, value in errors.items()},
    }


def evaluate(
    task: dict[str, Any],
    observed_actions: list[dict[str, Any]],
    position_tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
    yaw_tolerance_deg: float = DEFAULT_YAW_TOLERANCE_DEG,
) -> dict[str, Any]:
    expected_actions = [item for item in task["ground_truth"] if int(item.get("action_flag", 1)) == 1]
    comparisons = []
    matched_count = 0

    for index, expected in enumerate(expected_actions):
        actual = observed_actions[index] if index < len(observed_actions) else None
        comparison = compare_action(actual, expected, position_tolerance_m, yaw_tolerance_deg)
        comparisons.append(comparison)
        if comparison["matched"]:
            matched_count += 1

    completeness = matched_count / len(expected_actions) if expected_actions else 0.0
    return {
        "task_id": task["id"],
        "prompt": task["prompt"],
        "source": task.get("source"),
        "position_tolerance_m": position_tolerance_m,
        "yaw_tolerance_deg": yaw_tolerance_deg,
        "expected_action_count": len(expected_actions),
        "observed_action_count": len(observed_actions),
        "matched_action_count": matched_count,
        "completeness": completeness,
        "success": math.isclose(completeness, 1.0),
        "comparisons": comparisons,
        "extra_observed_actions": observed_actions[len(expected_actions) :],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a drone flight history against CLGSCE-style ground truth.")
    parser.add_argument("--task-file", type=Path, default=DEFAULT_TASK_FILE)
    parser.add_argument("--task-id", default="clgsce_basic_001")
    parser.add_argument("--flight-history", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--position-tolerance-m", type=float, default=DEFAULT_POSITION_TOLERANCE_M)
    parser.add_argument("--yaw-tolerance-deg", type=float, default=DEFAULT_YAW_TOLERANCE_DEG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task = load_task(args.task_file, args.task_id)
    observed_actions = load_observed_actions(args.flight_history)
    result = evaluate(task, observed_actions, args.position_tolerance_m, args.yaw_tolerance_deg)

    output_text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text + "\n", encoding="utf-8")
    print(output_text)


if __name__ == "__main__":
    main()
