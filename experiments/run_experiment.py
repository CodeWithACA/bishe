from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from agent.drone_agent import MAX_AGENT_STEPS, run_drone_agent_trace
from agent.tools import get_flight_history
from evaluate_experiment import DEFAULT_TASK_FILE, evaluate, load_observed_actions, load_task


DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"
RUNS_DIR = Path(__file__).resolve().parent / "runs"


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0.2)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one LangGraph drone task and write CLGSCE-style experiment logs.")
    parser.add_argument("--task-file", type=Path, default=DEFAULT_TASK_FILE)
    parser.add_argument("--task-id", default="clgsce_basic_001")
    parser.add_argument("--output-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--model", default=os.getenv("DRONE_AGENT_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_URL_CN") or os.getenv("SILICONFLOW_URL", ""))
    parser.add_argument("--max-steps", type=int, default=MAX_AGENT_STEPS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("Missing SILICONFLOW_URL_CN or SILICONFLOW_URL in .env")

    task = load_task(args.task_file, args.task_id)
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.task_id}-{uuid4().hex[:8]}"
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    session_id = f"experiment-{run_id}"
    write_json(run_dir / "task.json", task)

    llm = build_llm(args.model, args.base_url)
    trace_path = run_dir / "agent_trace.txt"
    with trace_path.open("w", encoding="utf-8") as trace_file:
        tee = Tee(sys.stdout, trace_file)
        with contextlib.redirect_stdout(tee):
            print(f"Run ID: {run_id}")
            print(f"Session ID: {session_id}")
            print(f"Task ID: {task['id']}")
            print(f"Task: {task['prompt']}")
            final_answer = run_drone_agent_trace(
                llm=llm,
                message=task["prompt"],
                history=[],
                session_id=session_id,
                max_steps=args.max_steps,
            )

    history = get_flight_history(session_id, limit=200)
    write_json(run_dir / "flight_history.json", history)
    write_jsonl(run_dir / "tool_log.jsonl", history)
    write_json(run_dir / "final_answer.json", {"final_answer": final_answer})

    observed_actions = load_observed_actions(run_dir / "flight_history.json")
    evaluation = evaluate(task, observed_actions)
    write_json(run_dir / "evaluation.json", evaluation)

    print("\n========== EXPERIMENT OUTPUT ==========")
    print(f"Run directory: {run_dir}")
    print(f"Trace: {trace_path}")
    print(f"Flight history: {run_dir / 'flight_history.json'}")
    print(f"Evaluation: {run_dir / 'evaluation.json'}")
    print(f"Completeness: {evaluation['completeness']:.3f}")
    print(f"Success: {evaluation['success']}")


if __name__ == "__main__":
    main()
