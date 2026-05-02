from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_ground_truth(path: Path) -> list[list[dict[str, float]]]:
    tasks: list[list[dict[str, float]]] = []
    current: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            parts = line.strip().split()
            if not parts:
                continue
            values = [float(item) for item in parts]
            if int(values[0]) == 2:
                tasks.append(current)
                current = []
                continue
            current.append(
                {
                    "action_flag": int(values[0]),
                    "dx": values[1],
                    "dy": values[2],
                    "dz": values[3],
                    "dyaw": values[4],
                }
            )
    if current:
        tasks.append(current)
    return tasks


def convert_task_set(clgsce_root: Path, task_name: str) -> list[dict[str, Any]]:
    task_path = clgsce_root / "task_sets" / f"{task_name}.txt"
    ground_truth_path = clgsce_root / "ground_truth" / f"{task_name}.txt"
    prompts = [line.strip() for line in task_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ground_truth = parse_ground_truth(ground_truth_path)
    if len(prompts) != len(ground_truth):
        raise ValueError(f"{task_name}: task count {len(prompts)} != ground truth count {len(ground_truth)}")

    rows = []
    for index, (prompt, expected) in enumerate(zip(prompts, ground_truth), start=1):
        rows.append(
            {
                "id": f"clgsce_{task_name}_{index:03d}",
                "source": f"CLGSCE task_sets/{task_name}.txt line {index}",
                "source_task_index": index - 1,
                "prompt": prompt,
                "ground_truth": expected,
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert CLGSCE task_sets/*.txt and ground_truth/*.txt into experiment JSONL files.")
    parser.add_argument("clgsce_root", type=Path, help="Path to a local CLGSCE repository clone.")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "task_sets")
    parser.add_argument("--task-set", choices=["basic", "advanced", "all"], default="all")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task_names = ["basic", "advanced"] if args.task_set == "all" else [args.task_set]
    for task_name in task_names:
        rows = convert_task_set(args.clgsce_root, task_name)
        output = args.output_dir / f"clgsce_{task_name}.jsonl"
        write_jsonl(output, rows)
        print(f"Wrote {len(rows)} tasks to {output}")


if __name__ == "__main__":
    main()
