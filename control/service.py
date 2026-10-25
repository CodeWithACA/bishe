from __future__ import annotations

import threading
from typing import Any

from agent.px4_mavlink_controller import PX4MavlinkController, get_controller

from .arbiter import ControlArbiter, ControlAuthority


class FlightControlService:
    def __init__(
        self,
        controller: PX4MavlinkController | None = None,
        arbiter: ControlArbiter | None = None,
    ) -> None:
        self._controller = controller
        self.arbiter = arbiter or ControlArbiter()
        self._transition_lock = threading.RLock()

    @property
    def controller(self) -> PX4MavlinkController:
        if self._controller is None:
            self._controller = get_controller()
        return self._controller

    def get_state(self) -> dict[str, Any]:
        return self.controller.get_state()

    def acquire_agent(self, mission_id: str) -> dict[str, Any]:
        return self.arbiter.acquire_agent(mission_id).__dict__

    def require_agent(self, mission_id: str) -> None:
        self.arbiter.require(ControlAuthority.AGENT, mission_id)

    def release_agent(self, mission_id: str) -> None:
        snapshot = self.arbiter.snapshot()
        if snapshot.authority == ControlAuthority.AGENT and snapshot.holder == mission_id:
            self.arbiter.release(mission_id)

    def takeover(self, operator_id: str = "local-operator") -> dict[str, Any]:
        with self._transition_lock:
            self.controller.cancel_active_command()
            try:
                hold = self.controller.hold_position()
            except Exception as exc:
                self.arbiter.enter_safety(f"接管悬停失败：{exc}")
                raise
            lease = self.arbiter.acquire_manual(operator_id)
            return {"ok": True, "hold": hold, "control": lease.__dict__}

    def hold_manual(self, operator_id: str = "local-operator") -> dict[str, Any]:
        self.arbiter.require(ControlAuthority.MANUAL, operator_id)
        return self.controller.hold_position()

    def manual_velocity(
        self,
        forward_mps: float,
        right_mps: float,
        up_mps: float,
        operator_id: str = "local-operator",
    ) -> dict[str, Any]:
        self.arbiter.require(ControlAuthority.MANUAL, operator_id)
        limit = 1.5
        return self.controller.set_body_velocity(
            max(-limit, min(limit, float(forward_mps))),
            max(-limit, min(limit, float(right_mps))),
            max(-0.8, min(0.8, float(up_mps))),
        )

    def manual_takeoff(self, altitude_m: float = 1.5, operator_id: str = "local-operator") -> dict[str, Any]:
        self.arbiter.require(ControlAuthority.MANUAL, operator_id)
        return self.controller.takeoff(altitude_m)

    def manual_land(self, operator_id: str = "local-operator") -> dict[str, Any]:
        self.arbiter.require(ControlAuthority.MANUAL, operator_id)
        return self.controller.land()

    def release_manual(self, operator_id: str = "local-operator") -> None:
        with self._transition_lock:
            try:
                self.controller.hold_position()
            finally:
                snapshot = self.arbiter.snapshot()
                if snapshot.authority == ControlAuthority.MANUAL and snapshot.holder == operator_id:
                    self.arbiter.release(operator_id)
