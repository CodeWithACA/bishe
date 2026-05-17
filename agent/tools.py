from __future__ import annotations

import contextvars
import json
import time
import math
from typing import Any

from langchain_core.tools import tool

from .px4_mavlink_controller import get_controller


DEFAULT_VELOCITY = 0.8
DEFAULT_TAKEOFF_ALTITUDE_M = 1.5

current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_drone_session_id",
    default="default",
)
_flight_histories: dict[str, list[dict[str, Any]]] = {}


def _round_number(value: Any, digits: int = 3) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _compact_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    keys = ["x", "y", "z", "relative_altitude_m", "yaw_deg"]
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
    payload = {"ok": bool(result.get("ok", False))}
    if history_entry:
        status = _status_text(history_entry)
        if status:
            payload["message"] = status
    return payload


def _status_text(entry: dict[str, Any]) -> str:
    action = entry.get("action")
    params = entry.get("params") or {}
    after = entry.get("after") or {}
    if action == "set_yaw":
        yaw = after.get("yaw_deg", params.get("yaw_deg"))
        return f"Drone is heading {_round_number(yaw)}° ({_heading_name(float(yaw or 0.0))} direction)."
    if action == "move_body":
        direction = str(params.get("direction", "")).lower()
        distance = _round_number(params.get("distance_m", 0.0))
        return f"Moved {direction} by {distance} meters."
    return ""


def _heading_name(yaw_deg: float) -> str:
    yaw = yaw_deg % 360.0
    headings = [
        (0.0, "north"),
        (90.0, "east"),
        (180.0, "south"),
        (270.0, "west"),
    ]
    return min(headings, key=lambda item: abs(((yaw - item[0] + 180.0) % 360.0) - 180.0))[1]


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
    """Read the current drone position, relative altitude, and yaw heading."""
    state = get_controller().get_state()
    _record_action("get_state", {}, None, state)
    return json.dumps({"ok": True, "state": _compact_state(state)}, ensure_ascii=False)

@tool
def takeoff() -> str:
    """Perform a plain takeoff: climb upward by the default 1.5 meters relative to the current height.

    This tool has no arguments. For "take off and climb/fly/ascend up N meters", call takeoff()
    first and then call change_altitude(N). Do not replace those two physical actions with one
    larger climb.
    """
    result = get_controller().takeoff(DEFAULT_TAKEOFF_ALTITUDE_M)
    entry = _record_action("takeoff", {"altitude_m": DEFAULT_TAKEOFF_ALTITUDE_M}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def change_altitude(delta_m: float) -> str:
    """Change altitude by delta_m meters relative to the current height, then hold position.

    Positive delta_m climbs upward. Negative delta_m descends downward. Examples:
    "climb 5 meters" => change_altitude(5); "fly up 4 meters" => change_altitude(4);
    "descend 5 meters" => change_altitude(-5); "fly down 4 meters" => change_altitude(-4).
    This is always the requested additional vertical movement, not a target altitude and not the
    difference from the default takeoff height. After takeoff(), "fly up 5 meters" still means
    change_altitude(5), not change_altitude(3.5). Use land() only when the user explicitly asks
    to land.
    """
    delta = float(delta_m)
    controller = get_controller()
    before = controller.get_state()
    current_altitude = float(before.get("relative_altitude_m") or 0.0)
    target_altitude = max(current_altitude + delta, 0.5)
    result = controller.fly_to(
        float(before.get("x") or 0.0),
        float(before.get("y") or 0.0),
        target_altitude,
        velocity_mps=DEFAULT_VELOCITY,
    )
    if "before" not in result:
        result["before"] = before
    entry = _record_action("change_altitude", {"delta_m": delta}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def land() -> str:
    """Land the drone and try to disarm after it is close to the ground."""
    result = get_controller().land()
    entry = _record_action("land", {}, result.get("before"), result.get("after"))
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def move_body(direction: str, distance_m: float) -> str:
    """Move horizontally in the drone body frame while keeping the current yaw and altitude.

    direction must be one of: forward, backward, right, left. The tool reads the latest yaw and
    position, converts the body-frame movement to PX4 local NED x/y, and flies there.
    With yaw 0/north: forward=north, right=east, backward=south, left=west.
    With yaw 90/east: forward=east, right=south, backward=west, left=north.
    With yaw 180/south: forward=south, right=west, backward=north, left=east.
    With yaw 270/west: forward=west, right=north, backward=east, left=south.
    """
    normalized = direction.strip().lower().replace("-", "_")
    aliases = {
        "front": "forward",
        "forwards": "forward",
        "back": "backward",
        "backwards": "backward",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"forward", "backward", "right", "left"}:
        return json.dumps({"ok": False, "error": "direction must be forward, backward, right, or left."}, ensure_ascii=False)

    distance = float(distance_m)
    if distance < 0:
        return json.dumps({"ok": False, "error": "distance_m must be non-negative."}, ensure_ascii=False)

    controller = get_controller()
    before = controller.get_state()
    yaw_rad = math.radians(float(before.get("yaw_deg") or 0.0))
    forward_dx = math.cos(yaw_rad)
    forward_dy = math.sin(yaw_rad)
    right_dx = -math.sin(yaw_rad)
    right_dy = math.cos(yaw_rad)

    if normalized == "forward":
        dx, dy = distance * forward_dx, distance * forward_dy
    elif normalized == "backward":
        dx, dy = -distance * forward_dx, -distance * forward_dy
    elif normalized == "right":
        dx, dy = distance * right_dx, distance * right_dy
    else:
        dx, dy = -distance * right_dx, -distance * right_dy

    target_x = float(before.get("x") or 0.0) + dx
    target_y = float(before.get("y") or 0.0) + dy
    altitude = float(before.get("relative_altitude_m") or 0.0)
    result = controller.fly_to(target_x, target_y, altitude, velocity_mps=DEFAULT_VELOCITY)
    if "before" not in result:
        result["before"] = before
    entry = _record_action(
        "move_body",
        {"direction": normalized, "distance_m": distance},
        result.get("before"),
        result.get("after"),
    )
    return json.dumps(_compact_action_result(result, entry), ensure_ascii=False)


@tool
def move_body_polar(plane: str, direction: str, angle_deg: float, distance_m: float) -> str:
    """Move in a body-frame plane using polar coordinates while keeping current yaw.

    plane must be one of: xy, xz, yz. distance_m is the path length.
    For plane yz, direction must be one of: top_right, top_left, bottom_right, bottom_left.
    The tool decomposes distance_m into cos(angle_deg) and sin(angle_deg) components.
    """
    normalized_plane = plane.strip().lower()
    normalized_direction = direction.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_plane not in {"xy", "xz", "yz"}:
        return json.dumps({"ok": False, "error": "plane must be xy, xz, or yz."}, ensure_ascii=False)

    distance = float(distance_m)
    if distance < 0:
        return json.dumps({"ok": False, "error": "distance_m must be non-negative."}, ensure_ascii=False)

    angle_rad = math.radians(float(angle_deg))
    first_component_m = distance * math.cos(angle_rad)
    second_component_m = distance * math.sin(angle_rad)
    body_forward_m = 0.0
    body_right_m = 0.0
    altitude_delta_m = 0.0

    if normalized_plane == "yz":
        if normalized_direction not in {"top_right", "top_left", "bottom_right", "bottom_left"}:
            return json.dumps(
                {"ok": False, "error": "for plane yz, direction must be top_right, top_left, bottom_right, or bottom_left."},
                ensure_ascii=False,
            )
        body_right_m = first_component_m if normalized_direction.endswith("right") else -first_component_m
        altitude_delta_m = second_component_m if normalized_direction.startswith("top") else -second_component_m
    elif normalized_plane == "xz":
        if normalized_direction not in {"top_forward", "top_backward", "bottom_forward", "bottom_backward"}:
            return json.dumps(
                {"ok": False, "error": "for plane xz, direction must be top_forward, top_backward, bottom_forward, or bottom_backward."},
                ensure_ascii=False,
            )
        body_forward_m = first_component_m if normalized_direction.endswith("forward") else -first_component_m
        altitude_delta_m = second_component_m if normalized_direction.startswith("top") else -second_component_m
    else:
        if normalized_direction not in {"forward_right", "forward_left", "backward_right", "backward_left"}:
            return json.dumps(
                {"ok": False, "error": "for plane xy, direction must be forward_right, forward_left, backward_right, or backward_left."},
                ensure_ascii=False,
            )
        body_forward_m = first_component_m if normalized_direction.startswith("forward") else -first_component_m
        body_right_m = second_component_m if normalized_direction.endswith("right") else -second_component_m

    controller = get_controller()
    before = controller.get_state()
    yaw_rad = math.radians(float(before.get("yaw_deg") or 0.0))
    forward_dx = math.cos(yaw_rad)
    forward_dy = math.sin(yaw_rad)
    right_dx = -math.sin(yaw_rad)
    right_dy = math.cos(yaw_rad)
    target_x = float(before.get("x") or 0.0) + body_forward_m * forward_dx + body_right_m * right_dx
    target_y = float(before.get("y") or 0.0) + body_forward_m * forward_dy + body_right_m * right_dy
    target_altitude = max(float(before.get("relative_altitude_m") or 0.0) + altitude_delta_m, 0.5)

    result = controller.fly_to(target_x, target_y, target_altitude, velocity_mps=DEFAULT_VELOCITY)
    if "before" not in result:
        result["before"] = before
    entry = _record_action(
        "move_body_polar",
        {"plane": normalized_plane, "direction": normalized_direction, "angle_deg": float(angle_deg), "distance_m": distance},
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


DRONE_TOOLS = [takeoff, change_altitude, land, move_body, move_body_polar, set_yaw, get_state]
