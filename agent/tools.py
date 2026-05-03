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
        "yaw_rate_deg_s",
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
    """Read the current drone state, including PX4 local NED position, relative altitude, speed, and attitude."""
    state = get_controller().get_state()
    _record_action("get_state", {}, None, state)
    return json.dumps({"ok": True, "state": _compact_state(state)}, ensure_ascii=False)



def reset_airsim() -> str:
    """Clear the current session flight action history. MAVLink/PX4 does not provide a safe generic simulator reset."""
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
    """Take off and climb by altitude_m meters relative to the current altitude, then hold position."""
    result = get_controller().takeoff(float(altitude_m))
    entry = _record_action("takeoff", {"altitude_m": float(altitude_m)}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def land() -> str:
    """Land the drone and try to disarm after it is close to the ground."""
    result = get_controller().land()
    entry = _record_action("land", {}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def fly_to(x: float, y: float, altitude_m: float, velocity_mps: float = DEFAULT_VELOCITY) -> str:
    """Fly to PX4 local NED x/y coordinates and a positive height above ground. x points north and y points east."""
    altitude = float(altitude_m)
    if altitude < 0:
        return json.dumps(
            {
                "ok": False,
                "error": "altitude_m must be a positive height above ground. For PX4 NED dz, convert it to altitude_m before calling fly_to.",
            },
            ensure_ascii=False,
        )
    velocity = float(velocity_mps)
    result = get_controller().fly_to(float(x), float(y), altitude, velocity_mps=velocity)
    entry = _record_action(
        "fly_to",
        {"x": float(x), "y": float(y), "altitude_m": altitude, "velocity_mps": velocity},
        result.get("before"),
        result.get("after"),
    )
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def set_yaw(yaw_deg: float) -> str:
    """Turn the drone to an absolute yaw angle in degrees. 0 is north, 90 is east, 180 is south, and 270 is west."""
    result = get_controller().set_yaw(float(yaw_deg))
    entry = _record_action("set_yaw", {"yaw_deg": float(yaw_deg) % 360.0}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


DRONE_TOOLS = [takeoff, land, fly_to, set_yaw, get_state]
