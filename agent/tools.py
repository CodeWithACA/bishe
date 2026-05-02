from __future__ import annotations

import contextvars
import json
import time
from typing import Any

from langchain_core.tools import tool

from .px4_mavlink_controller import get_controller


DEFAULT_VELOCITY = 0.8

current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_drone_session_id",
    default="default",
)
_flight_histories: dict[str, list[dict[str, Any]]] = {}


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
    session_id = current_session_id.get()
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
    clear_flight_history(current_session_id.get())
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
