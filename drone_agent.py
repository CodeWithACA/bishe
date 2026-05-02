from __future__ import annotations

import contextvars
import json
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from px4_mavlink_controller import get_controller


MAX_AGENT_STEPS = 12
DEFAULT_VELOCITY = 0.8

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_drone_session_id",
    default="default",
)
_flight_histories: dict[str, list[dict[str, Any]]] = {}


class DroneAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _round_number(value: Any, digits: int = 3) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _compact_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    keys = [
        "connected",
        "armed",
        "mode",
        "x",
        "y",
        "z",
        "relative_altitude_m",
        "yaw_deg",
        "speed_mps",
        "vertical_speed_mps",
        "last_status_text",
    ]
    compact: dict[str, Any] = {}
    for key in keys:
        if key in state and state[key] is not None:
            compact[key] = _round_number(state[key])
    return compact


def _compact_history_ref(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": entry["index"],
        "action": entry["action"],
        "params": entry["params"],
    }


def _compact_action_result(result: dict[str, Any], history_entry: dict[str, Any] | None = None) -> dict[str, Any]:
    compact: dict[str, Any] = {"ok": bool(result.get("ok", False))}
    for key in ("target_reached", "error", "height_delta_m", "distance_error_m", "yaw_error_deg"):
        if key in result:
            compact[key] = _round_number(result[key])
    if history_entry:
        compact["history"] = _compact_history_ref(history_entry)
    after = _compact_state(result.get("after"))
    if after:
        compact["state"] = after
    return compact


def _record_action(action: str, params: dict[str, Any], before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    session_id = _current_session_id.get()
    entry = {
        "index": len(_flight_histories.setdefault(session_id, [])) + 1,
        "action": action,
        "params": params,
        "before": _compact_state(before),
        "after": _compact_state(after),
        "created_at": int(time.time()),
    }
    _flight_histories[session_id].append(entry)
    return entry


def get_flight_history(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return _flight_histories.get(session_id, [])[-limit:]


def clear_flight_history(session_id: str) -> None:
    _flight_histories.pop(session_id, None)


@tool
def get_state() -> str:
    """读取当前无人机状态，包括 PX4 本地 NED 坐标、离地高度、速度和姿态角。"""
    state = get_controller().get_state()
    _record_action("get_state", {}, None, state)
    return json.dumps({"ok": True, "state": _compact_state(state)}, ensure_ascii=False)


@tool
def reset_airsim() -> str:
    """清空当前会话的飞行动作历史。MAVLink/PX4 不提供通用仿真重置，仿真重置应在仿真器侧执行。"""
    before = get_controller().get_state()
    clear_flight_history(_current_session_id.get())
    after = get_controller().get_state()
    entry = _record_action("reset_airsim", {}, before, after)
    return json.dumps(
        {
            "ok": False,
            "error": "PX4/MAVLink does not provide a safe generic simulator reset; flight history was cleared only.",
            "history": _compact_history_ref(entry),
            "state": entry["after"],
        },
        ensure_ascii=False,
    )


@tool
def takeoff(altitude_m: float = 3.0) -> str:
    """起飞并相对当前高度上升指定米数，默认 3 米。工具会循环读取高度，高度差达到目标后进入位置保持。"""
    result = get_controller().takeoff(float(altitude_m))
    entry = _record_action("takeoff", {"altitude_m": float(altitude_m)}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def land() -> str:
    """降落无人机，并在接近地面后尝试上锁。"""
    result = get_controller().land()
    entry = _record_action("land", {}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def fly_to(x: float, y: float, altitude_m: float, velocity_mps: float = DEFAULT_VELOCITY) -> str:
    """飞到 PX4 本地 NED 坐标 x/y 和指定离地高度。x 向北，y 向东，altitude_m 为正数离地高度。"""
    result = get_controller().fly_to(float(x), float(y), abs(float(altitude_m)))
    entry = _record_action(
        "fly_to",
        {"x": float(x), "y": float(y), "altitude_m": abs(float(altitude_m)), "velocity_mps": float(velocity_mps)},
        result.get("before"),
        result.get("after"),
    )
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def set_yaw(yaw_deg: float) -> str:
    """把机头转到指定航向角，单位度。0 度约为北，90 度约为东。"""
    result = get_controller().set_yaw(float(yaw_deg))
    entry = _record_action("set_yaw", {"yaw_deg": float(yaw_deg) % 360.0}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


DRONE_TOOLS = [takeoff, land, fly_to, set_yaw, get_state, reset_airsim]


SYSTEM_PROMPT = """你是一个 PX4/MAVLink 无人机操作 agent。

你可以使用工具一步步控制无人机。每次控制前先结合用户目标、已有飞行历史和当前状态规划下一步。

重要规则：
1. 真实改变无人机状态的任务必须调用工具，不要只用文字回答已经执行。
2. 一次只执行必要步骤；复杂任务可以连续调用多个工具，但每步都要依赖前一步结果。
3. 坐标采用 PX4 本地 NED 坐标：x 向北，y 向东，z 向下；工具 fly_to 使用正数 altitude_m 表示离地高度。
4. 用户说“起飞到 8 米”时，调用 takeoff(8)，表示相对当前高度上升 8 米；用户说“上升 8 米”也调用 takeoff(8)；用户说“降落”时，调用 land()。
5. 如果任务缺少关键参数，先读取状态并提出需要补充的信息；不要编造危险动作参数。
6. 最终回复要用中文，简要说明已执行动作、当前状态和下一步建议。
"""


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
    token = _current_session_id.set(session_id or "default")
    try:
        graph = _build_graph(llm)
        messages = build_drone_messages(message, history, session_id)
        result = graph.invoke({"messages": messages}, config={"recursion_limit": MAX_AGENT_STEPS})
        final_message = result["messages"][-1]
        return str(final_message.content).lstrip("\r\n")
    finally:
        _current_session_id.reset(token)


def run_drone_agent_trace(
    llm: ChatOpenAI,
    message: str,
    history: list[dict[str, str]],
    session_id: str,
    max_steps: int = MAX_AGENT_STEPS,
) -> str:
    token = _current_session_id.set(session_id or "trace")
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
        _current_session_id.reset(token)
