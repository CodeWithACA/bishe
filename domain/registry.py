from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import yaml

from control.service import FlightControlService


class CapabilityRegistry:
    def __init__(self, config_path: str | Path, control: FlightControlService) -> None:
        self.config_path = Path(config_path)
        self.control = control
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.version = int(raw.get("version", 1))
        self.locations = {item["id"]: item for item in raw.get("locations", [])}
        self.devices = {item["id"]: item for item in raw.get("devices", [])}
        self.capabilities = raw.get("capabilities", {})
        self._device_states = {
            item_id: item.get("initial_state", False) for item_id, item in self.devices.items()
        }
        for location in self.locations.values():
            frame = location.get("frame")
            if frame not in {"local_ned", "global_gps"}:
                raise ValueError(f"unsupported coordinate frame: {frame}")

    def catalog(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "locations": list(self.locations.values()),
            "devices": list(self.devices.values()),
            "capabilities": self.capabilities,
        }

    def capability(self, tool_name: str) -> dict[str, Any]:
        if tool_name not in self.capabilities:
            raise ValueError(f"unsupported capability: {tool_name}")
        return self.capabilities[tool_name]

    def location(self, location_id: str) -> dict[str, Any]:
        try:
            return self.locations[location_id]
        except KeyError as exc:
            raise ValueError(f"unknown location: {location_id}") from exc

    def device_state(self, device_id: str) -> dict[str, Any]:
        if device_id not in self.devices:
            raise ValueError(f"unknown device: {device_id}")
        return {"device": self.devices[device_id], "state": self._device_states[device_id]}

    def execute(self, tool_name: str, arguments: dict[str, Any], mission_id: str) -> dict[str, Any]:
        self.capability(tool_name)
        if tool_name == "flight.get_state":
            return {"ok": True, "state": self.control.get_state()}
        if tool_name == "device.get_state":
            return {"ok": True, **self.device_state(str(arguments["device_id"]))}

        self.control.require_agent(mission_id)
        controller = self.control.controller
        if tool_name == "flight.takeoff":
            return controller.takeoff(float(arguments.get("altitude_m", 1.5)))
        if tool_name == "flight.land":
            return controller.land()
        if tool_name == "flight.set_yaw":
            return controller.set_yaw(float(arguments["yaw_deg"]))
        if tool_name == "flight.move":
            return self._move(arguments)
        if tool_name == "flight.goto_location":
            return self._goto_location(str(arguments["location_id"]))
        if tool_name == "device.set_state":
            device_id = str(arguments["device_id"])
            if device_id not in self.devices:
                raise ValueError(f"unknown device: {device_id}")
            with self._lock:
                self._device_states[device_id] = bool(arguments["enabled"])
            return {"ok": True, **self.device_state(device_id)}
        raise ValueError(f"unsupported capability: {tool_name}")

    def _move(self, arguments: dict[str, Any]) -> dict[str, Any]:
        direction = str(arguments["direction"]).lower()
        if direction not in {"forward", "backward", "left", "right", "up", "down"}:
            raise ValueError("direction must be forward, backward, left, right, up, or down")
        distance = float(arguments["distance_m"])
        if distance < 0:
            raise ValueError("distance_m must be non-negative")
        state = self.control.get_state()
        altitude = float(state.get("relative_altitude_m") or 0.0)
        if direction in {"up", "down"}:
            target_altitude = max(0.5, altitude + (distance if direction == "up" else -distance))
            return self.control.controller.fly_to(
                float(state.get("x") or 0.0),
                float(state.get("y") or 0.0),
                target_altitude,
            )
        yaw = math.radians(float(state.get("yaw_deg") or 0.0))
        forward = (math.cos(yaw), math.sin(yaw))
        right = (-math.sin(yaw), math.cos(yaw))
        vectors = {
            "forward": forward,
            "backward": (-forward[0], -forward[1]),
            "right": right,
            "left": (-right[0], -right[1]),
        }
        dx, dy = vectors[direction]
        return self.control.controller.fly_to(
            float(state.get("x") or 0.0) + dx * distance,
            float(state.get("y") or 0.0) + dy * distance,
            max(0.5, altitude),
        )

    def _goto_location(self, location_id: str) -> dict[str, Any]:
        location = self.location(location_id)
        if location["frame"] != "local_ned":
            raise ValueError("global_gps locations require a real-vehicle coordinate adapter")
        position = location["position"]
        result = self.control.controller.fly_to(
            float(position["x"]),
            float(position["y"]),
            float(position["altitude_m"]),
        )
        if result.get("ok") and location.get("yaw_deg") is not None:
            result["yaw"] = self.control.controller.set_yaw(float(location["yaw_deg"]))
        return result
