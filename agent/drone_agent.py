from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from .prompt import SYSTEM_PROMPT
from .tools import DRONE_TOOLS, current_session_id, get_flight_history


MAX_AGENT_STEPS = 12


class DroneAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _build_graph(llm: ChatOpenAI):
    tool_llm = llm.bind_tools(DRONE_TOOLS)

    def call_model(state: DroneAgentState) -> dict[str, list[BaseMessage]]:
        response = tool_llm.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(DroneAgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(DRONE_TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def build_drone_messages(
    message: str,
    history: list[dict[str, str]],
    session_id: str,
) -> list[BaseMessage]:
    recent_history = get_flight_history(session_id)
    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        SystemMessage(content=f"当前会话的飞行动作历史：{json.dumps(recent_history, ensure_ascii=False)}"),
    ]

    for item in history[-12:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=message))
    return messages


def run_drone_agent(
    llm: ChatOpenAI,
    message: str,
    history: list[dict[str, str]],
    session_id: str,
) -> str:
    token = current_session_id.set(session_id or "default")
    try:
        graph = _build_graph(llm)
        messages = build_drone_messages(message, history, session_id)
        result = graph.invoke({"messages": messages}, config={"recursion_limit": MAX_AGENT_STEPS})
        final_message = result["messages"][-1]
        return str(final_message.content).lstrip("\r\n")
    finally:
        current_session_id.reset(token)


def run_drone_agent_trace(
    llm: ChatOpenAI,
    message: str,
    history: list[dict[str, str]],
    session_id: str,
    max_steps: int = MAX_AGENT_STEPS,
) -> str:
    token = current_session_id.set(session_id or "trace")
    tool_llm = llm.bind_tools(DRONE_TOOLS)
    tools_by_name = {item.name: item for item in DRONE_TOOLS}
    messages = build_drone_messages(message, history, session_id)

    try:
        for step in range(1, max_steps + 1):
            print(f"\n========== LLM ROUND {step} INPUT ==========")
            for index, item in enumerate(messages, start=1):
                role = getattr(item, "type", item.__class__.__name__)
                content = item.content
                if isinstance(content, str):
                    printable = content
                else:
                    printable = json.dumps(content, ensure_ascii=False, indent=2)
                print(f"\n[{index}] {role}\n{printable}")

            ai_message = tool_llm.invoke(messages)
            messages.append(ai_message)

            print(f"\n========== LLM ROUND {step} OUTPUT ==========")
            print(ai_message.content or "[no text content]")
            tool_calls = getattr(ai_message, "tool_calls", None) or []
            if tool_calls:
                print("\nTool calls:")
                print(json.dumps(tool_calls, ensure_ascii=False, indent=2))
            else:
                print("\nNo tool calls. Agent finished.")
                return str(ai_message.content).lstrip("\r\n")

            for tool_call in tool_calls:
                name = tool_call["name"]
                args = tool_call.get("args") or {}
                tool_id = tool_call.get("id")
                print(f"\n========== TOOL EXECUTION: {name} ==========")
                print(json.dumps(args, ensure_ascii=False, indent=2))

                try:
                    result = tools_by_name[name].invoke(args)
                except Exception as exc:
                    result = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

                print("\nTool result:")
                print(result)
                messages.append(ToolMessage(content=str(result), tool_call_id=tool_id, name=name))

            print("\n========== FLIGHT HISTORY ==========")
            print(json.dumps(get_flight_history(session_id), ensure_ascii=False, indent=2))

        return "已达到最大追踪步数，agent 停止。"
    finally:
        current_session_id.reset(token)
