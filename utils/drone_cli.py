from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from agent.drone_agent import MAX_AGENT_STEPS, run_drone_agent_trace


DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace a LangGraph drone agent run in the terminal.")
    parser.add_argument("task", nargs="*", help="Drone task text.")
    parser.add_argument("--model", default=os.getenv("DRONE_AGENT_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_URL_CN") or os.getenv("SILICONFLOW_URL", ""))
    parser.add_argument("--session-id", default=f"cli-{uuid4()}")
    parser.add_argument("--max-steps", type=int, default=MAX_AGENT_STEPS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task = " ".join(args.task).strip()
    if not task:
        task = input("请输入无人机任务：").strip()
    if not task:
        raise SystemExit("任务不能为空。")
    if not args.base_url:
        raise SystemExit("缺少 SILICONFLOW_URL_CN 或 SILICONFLOW_URL。")

    print(f"Session: {args.session_id}")
    print(f"Model: {args.model}")
    print(f"Base URL: {args.base_url}")
    print(f"Task: {task}")

    llm = build_llm(args.model, args.base_url)
    final_answer = run_drone_agent_trace(
        llm=llm,
        message=task,
        history=[],
        session_id=args.session_id,
        max_steps=args.max_steps,
    )

    print("\n========== FINAL ANSWER ==========")
    print(final_answer)


if __name__ == "__main__":
    main()
