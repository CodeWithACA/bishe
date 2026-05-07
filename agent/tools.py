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

@tool
def takeoff(altitude_m: float = 1.5) -> str:
    """Climb upward by altitude_m meters relative to the current height, then hold position.

    Use takeoff() for a plain "take off" command. Use takeoff(N) for relative climbs such as
    "fly up 5 meters" or "climb another 5 meters". altitude_m is NOT a target height above
    ground, so do not add the current altitude and do not subtract the default takeoff height.
    If one instruction says "take off and fly/climb/ascend up N meters", call takeoff() first,
    then call takeoff(N) as the next physical action. Do not replace those two actions with
    a single takeoff(N) call.
    """
    result = get_controller().takeoff(float(altitude_m))
    entry = _record_action("takeoff", {"altitude_m": float(altitude_m)}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def descend(distance_m: float) -> str:
    """Descend by distance_m meters relative to the current height, then hold position.

    Use this for commands such as "descend 5 meters" or "fly down 5 meters".
    The tool reads the current state and converts the relative descent into the required
    positive height above ground for fly_to. Use land() only when the user explicitly asks to land.
    """
    distance = float(distance_m)
    if distance < 0:
        return json.dumps({"ok": False, "error": "distance_m must be non-negative."}, ensure_ascii=False)

    controller = get_controller()
    before = controller.get_state()
    current_altitude = float(before.get("relative_altitude_m") or 0.0)
    target_altitude = max(current_altitude - distance, 0.5)
    result = controller.fly_to(
        float(before.get("x") or 0.0),
        float(before.get("y") or 0.0),
        target_altitude,
        velocity_mps=DEFAULT_VELOCITY,
    )
    if "before" not in result:
        result["before"] = before
    entry = _record_action("descend", {"distance_m": distance}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def land() -> str:
    """Land the drone and try to disarm after it is close to the ground."""
    result = get_controller().land()
    entry = _record_action("land", {}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def fly_to(x: float, y: float, altitude_m: float, velocity_mps: float = DEFAULT_VELOCITY) -> str:
    """Fly to absolute PX4 local NED x/y coordinates and a positive height above ground.

    Coordinate rules:
    - x points north, y points east, and altitude_m is positive height above ground.
    - Use the latest returned state as the current state. Do not reset the origin to (0, 0).
    - World-frame directions: north/forward = x+d, south/backward = x-d, east/right = y+d,
      west/left = y-d. West/east never change x by themselves; north/south never change y by
      themselves.
    - Body-frame horizontal movement at yaw theta: forward dx=d*cos(theta), dy=d*sin(theta);
      backward is the negative of forward; right dx=-d*sin(theta), dy=d*cos(theta); left is
      the negative of right. Keep altitude unchanged unless vertical movement is requested.
    - In the drone body's YZ plane, top-right has body_dy=+d*cos(a), ned_dz=-d*sin(a);
      top-left body_dy=-d*cos(a), ned_dz=-d*sin(a); bottom-right body_dy=+d*cos(a),
      ned_dz=+d*sin(a); bottom-left body_dy=-d*cos(a), ned_dz=+d*sin(a). Convert body_dy
      with dx=-body_dy*sin(theta), dy=body_dy*cos(theta), and target altitude=current_altitude-ned_dz.

    Path rules:
    - A square is a closed four-leg path; preserve the requested leg order.
    - A repeated square repeats the same four-leg pattern from the current endpoint.
    - A reverse pass retraces the previous path from the current endpoint using inverse movement
      vectors in reverse chronological order.
    - A mirrored/symmetric square reflects both movement and viewing yaw across the named axis:
      across X, swap east/west; across Y, swap north/south.
    - A square-loop figure-eight has two closed loops sharing the crossing/start point. Complete
      exactly four movement legs for the first loop and four for the second; do not insert a
      repositioning move unless requested.
    - In world-frame figure-eight placement, left=west, right=east, front=north, rear=south.
      For compound regions, the first two legs should enter both named components: left-front
      starting left => west,north,east,south; left-rear starting left => west,south,east,north;
      right-front starting north => north,east,south,west; right-rear starting south =>
      south,east,north,west.
    """
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
    """Turn the drone to an absolute yaw angle in degrees.

    Yaw rules:
    - 0 is north, 90 is east, 180 is south, and 270 or -90 is west.
    - For relative turns, first read current yaw with get_state, then add clockwise degrees or
      subtract counterclockwise degrees.
    - If the drone must face the travel direction, set yaw to the next movement direction before
      that move. If it must face opposite to travel, set yaw 180 degrees from the movement
      direction. If no orientation requirement is stated, keep yaw unchanged.
    - For perpendicular inspection, face the interior of the square/loop and keep the same physical
      viewing side throughout the loop. Common sequences: forward/right/backward/left uses
      east,south,west,north; forward/left/backward/right uses west,south,east,north;
      south/east/north/west uses east,north,west,south.
    - For a right-rear inspected loop starting south, use yaw sequence east,north,west,south.
    - Do not add a final yaw change after the last movement unless explicitly requested.
    """
    result = get_controller().set_yaw(float(yaw_deg))
    entry = _record_action("set_yaw", {"yaw_deg": float(yaw_deg) % 360.0}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


DRONE_TOOLS = [takeoff, descend, land, fly_to, set_yaw, get_state]
