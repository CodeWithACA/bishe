from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from agent.drone_agent import MAX_AGENT_STEPS, run_drone_agent_trace
from agent.tools import clear_flight_history, get_flight_history
from airsim import AirSimClient
from evaluate_experiment import evaluate, load_observed_actions
from run_experiment import DEFAULT_INITIAL_ALTITUDE_M, DEFAULT_MODEL, Tee, prepare_initial_flight_state, write_json, write_jsonl


EXPERIMENTS_DIR = Path(__file__).resolve().parent
RUNS_DIR = EXPERIMENTS_DIR / "runs"
TASK_FILES = {
    "basic": EXPERIMENTS_DIR / "task_sets" / "clgsce_basic.jsonl",
    "advanced": EXPERIMENTS_DIR / "task_sets" / "clgsce_advanced.jsonl",
}
DEFAULT_PX4_SERVER_URL = os.getenv("PX4_SERVER_URL", "http://localhost:8765")
DEFAULT_PX4_DIR = os.getenv("PX4_DIR", "/home/hw/px4v1.15.2")
DEFAULT_MAVLINK_COMMAND = os.getenv(
    "PX4_MAVLINK_COMMAND",
    "mavlink start -x -u 14581 -r 4000000 -f -m onboard -o 14540 -t 192.168.240.1",
)


def load_tasks(task_file: Path) -> list[dict[str, Any]]:
    with task_file.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0.2)


def post_json(url: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def reset_airsim(host: str, port: int, timeout: float = 5.0, settle_sec: float = 1.0) -> None:
    with AirSimClient(host, port, timeout=timeout) as client:
        client.confirm_connection()
        client.reset()
    if settle_sec > 0:
        time.sleep(settle_sec)


def start_px4(server_url: str, px4_dir: str, mavlink_command: str, startup_delay: float) -> dict[str, Any]:
    start_result = post_json(
        f"{server_url}/px4/start",
        {"px4_dir": px4_dir, "restart": True},
        timeout=30,
    )
    time.sleep(max(startup_delay, 0.0))
    mavlink_result = post_json(
        f"{server_url}/px4/mavlink",
        {"command": mavlink_command},
        timeout=30,
    )
    status = get_json(f"{server_url}/px4/status", timeout=10)
    return {
        "start": start_result,
        "mavlink": mavlink_result,
        "status": status,
    }


def stop_px4(server_url: str) -> dict[str, Any]:
    try:
        return post_json(f"{server_url}/px4/stop", {"force": True}, timeout=30)
    except URLError as exc:
        return {"ok": False, "error": str(exc)}


def run_one_task(
    llm: ChatOpenAI,
    task: dict[str, Any],
    run_dir: Path,
    max_steps: int,
) -> dict[str, Any]:
    session_id = f"batch-{run_dir.name}"
    clear_flight_history(session_id)
    write_json(run_dir / "task.json", task)

    trace_path = run_dir / "agent_trace.txt"
    with trace_path.open("w", encoding="utf-8") as trace_file:
        tee = Tee(sys.stdout, trace_file)
        with contextlib.redirect_stdout(tee):
            print(f"Run directory: {run_dir}")
            print(f"Session ID: {session_id}")
            print(f"Task ID: {task['id']}")
            print(f"Task: {task['prompt']}")
            final_answer = run_drone_agent_trace(
                llm=llm,
                message=task["prompt"],
                history=[],
                session_id=session_id,
                max_steps=max_steps,
            )

    history = get_flight_history(session_id, limit=300)
    write_json(run_dir / "flight_history.json", history)
    write_jsonl(run_dir / "tool_log.jsonl", history)
    write_json(run_dir / "final_answer.json", {"final_answer": final_answer})

    observed_actions = load_observed_actions(run_dir / "flight_history.json")
    evaluation = evaluate(task, observed_actions)
    write_json(run_dir / "evaluation.json", evaluation)
    return evaluation


def select_tasks(args: argparse.Namespace) -> list[dict[str, Any]]:
    task_sets = ["basic", "advanced"] if args.task_set == "all" else [args.task_set]
    tasks: list[dict[str, Any]] = []
    for task_set in task_sets:
        tasks.extend(load_tasks(TASK_FILES[task_set]))

    if args.task_id:
        tasks = [task for task in tasks if task["id"] == args.task_id]
        if not tasks:
            raise ValueError(f"Task id not found in selected task set(s): {args.task_id}")

    if args.start_index is not None or args.end_index is not None:
        start = args.start_index or 1
        end = args.end_index or len(tasks)
        tasks = tasks[start - 1 : end]

    if args.limit is not None:
        tasks = tasks[: args.limit]

    return tasks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run batch CLGSCE-style drone experiments.")
    parser.add_argument("--task-set", choices=["basic", "advanced", "all"], default="basic")
    parser.add_argument("--task-id", default="", help="Run only one task id, such as clgsce_basic_001.")
    parser.add_argument("--start-index", type=int, help="1-based start index after task-set selection.")
    parser.add_argument("--end-index", type=int, help="1-based end index after task-set selection.")
    parser.add_argument("--limit", type=int, help="Run only the first N selected tasks.")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--output-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--px4-server-url", default=DEFAULT_PX4_SERVER_URL)
    parser.add_argument("--px4-dir", default=DEFAULT_PX4_DIR)
    parser.add_argument("--mavlink-command", default=DEFAULT_MAVLINK_COMMAND)
    parser.add_argument("--px4-startup-delay", type=float, default=5.0)
    parser.add_argument("--airsim-host", default=os.getenv("AIRSIM_HOST", "127.0.0.1"))
    parser.add_argument("--airsim-port", type=int, default=int(os.getenv("AIRSIM_PORT", "41451")))
    parser.add_argument("--model", default=os.getenv("DRONE_AGENT_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_URL_CN") or os.getenv("SILICONFLOW_URL", ""))
    parser.add_argument("--max-steps", type=int, default=MAX_AGENT_STEPS)
    parser.add_argument("--initial-altitude-m", type=float, default=DEFAULT_INITIAL_ALTITUDE_M)
    parser.add_argument("--skip-initial-state", action="store_true", help="Do not move to the safe initial altitude before task logging.")
    parser.add_argument("--skip-reset", action="store_true", help="Do not reset AirSim before each task.")
    parser.add_argument("--skip-px4", action="store_true", help="Do not start/stop PX4 around each task.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep running later tasks after a failure.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("Missing SILICONFLOW_URL_CN or SILICONFLOW_URL in .env")

    tasks = select_tasks(args)
    if not tasks:
        raise SystemExit("No tasks selected.")

    batch_id = args.batch_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.task_set}"
    batch_dir = args.output_dir / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_json(batch_dir / "selected_tasks.json", tasks)

    llm = build_llm(args.model, args.base_url)
    results: list[dict[str, Any]] = []

    for index, task in enumerate(tasks, start=1):
        task_run_dir = batch_dir / f"{index:03d}-{task['id']}"
        task_run_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n========== TASK {index}/{len(tasks)}: {task['id']} ==========")

        record: dict[str, Any] = {
            "task_id": task["id"],
            "prompt": task["prompt"],
            "run_dir": str(task_run_dir),
            "ok": False,
        }

        try:
            if not args.skip_reset:
                print("Resetting AirSim...")
                reset_airsim(args.airsim_host, args.airsim_port)

            if not args.skip_px4:
                print("Starting PX4...")
                px4_start = start_px4(args.px4_server_url.rstrip("/"), args.px4_dir, args.mavlink_command, args.px4_startup_delay)
                write_json(task_run_dir / "px4_start.json", px4_start)

            if not args.skip_initial_state:
                print(f"Preparing initial flight state at {args.initial_altitude_m:g}m...")
                setup = prepare_initial_flight_state(args.initial_altitude_m)
                write_json(task_run_dir / "initial_state_setup.json", setup)

            evaluation = run_one_task(llm, task, task_run_dir, args.max_steps)
            record.update(
                {
                    "ok": True,
                    "success": evaluation["success"],
                    "completeness": evaluation["completeness"],
                    "matched_action_count": evaluation["matched_action_count"],
                    "expected_action_count": evaluation["expected_action_count"],
                }
            )
            print(f"Result: success={evaluation['success']} completeness={evaluation['completeness']:.3f}")
        except Exception as exc:
            record["error"] = str(exc)
            write_json(task_run_dir / "error.json", record)
            print(f"Task failed: {exc}")
            if not args.continue_on_error:
                raise
        finally:
            if not args.skip_px4:
                print("Stopping PX4...")
                stop_result = stop_px4(args.px4_server_url.rstrip("/"))
                write_json(task_run_dir / "px4_stop.json", stop_result)
                print(f"PX4 stop ok={stop_result.get('ok')}")

        results.append(record)
        write_json(batch_dir / "summary.json", summarize_batch(batch_id, results))
        write_jsonl(batch_dir / "summary.jsonl", results)

    summary = summarize_batch(batch_id, results)
    write_json(batch_dir / "summary.json", summary)
    print("\n========== BATCH SUMMARY ==========")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Batch directory: {batch_dir}")


def summarize_batch(batch_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("ok")]
    successes = [item for item in completed if item.get("success")]
    completeness_values = [float(item.get("completeness", 0.0)) for item in completed]
    return {
        "batch_id": batch_id,
        "task_count": len(results),
        "completed_count": len(completed),
        "success_count": len(successes),
        "success_rate": len(successes) / len(completed) if completed else 0.0,
        "average_completeness": sum(completeness_values) / len(completeness_values) if completeness_values else 0.0,
        "results": results,
    }


if __name__ == "__main__":
    main()
