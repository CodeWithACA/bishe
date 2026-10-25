from __future__ import annotations

import time

from control import ControlArbiter, FlightControlService
from domain import CapabilityRegistry
from mission.events import EventBroker
from mission.models import MissionPlan, MissionStatus, PlanStep
from mission.service import MissionService
from mission.store import MissionStore


class FakeController:
    def __init__(self):
        self.actions = []
        self.cancelled = False
        self.state = {
            "connected": True,
            "armed": True,
            "mode": "OFFBOARD",
            "x": 0.0,
            "y": 0.0,
            "z": -1.5,
            "relative_altitude_m": 1.5,
            "yaw_deg": 0.0,
        }

    def get_state(self):
        return dict(self.state)

    def takeoff(self, altitude_m=1.5):
        self.actions.append(("takeoff", altitude_m))
        self.state["relative_altitude_m"] = altitude_m
        self.state["z"] = -altitude_m
        return {"ok": True, "before": {}, "after": self.get_state()}

    def land(self):
        self.actions.append(("land",))
        return {"ok": True, "before": {}, "after": self.get_state()}

    def fly_to(self, x, y, altitude_m):
        self.actions.append(("fly_to", x, y, altitude_m))
        self.state.update({"x": x, "y": y, "z": -altitude_m, "relative_altitude_m": altitude_m})
        return {"ok": True, "before": {}, "after": self.get_state()}

    def set_yaw(self, yaw_deg):
        self.actions.append(("set_yaw", yaw_deg))
        self.state["yaw_deg"] = yaw_deg
        return {"ok": True, "before": {}, "after": self.get_state()}

    def cancel_active_command(self):
        self.cancelled = True
        return 1

    def hold_position(self):
        self.actions.append(("hold",))
        return {"ok": True, "state": self.get_state()}

    def set_body_velocity(self, forward, right, up):
        self.actions.append(("velocity", forward, right, up))
        return {"ok": True}


class FakePlanner:
    def create_plan(self, mission_id, prompt, model, base_url, extra_requirements=None):
        return MissionPlan(
            mission_id=mission_id,
            summary="测试任务",
            steps=[
                PlanStep(
                    id="step-1",
                    title="起飞",
                    tool="flight.takeoff",
                    arguments={"altitude_m": 2.0},
                    risk_level="high",
                    requires_confirmation=True,
                    expected_result="悬停在 2 米",
                ),
                PlanStep(
                    id="step-2",
                    title="向前飞",
                    tool="flight.move",
                    arguments={"direction": "forward", "distance_m": 1.0},
                    risk_level="low",
                    expected_result="向前移动 1 米",
                ),
            ],
        )


def wait_for(service, mission_id, status, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        mission = service.get(mission_id)
        if mission.status == status:
            return mission
        time.sleep(0.02)
    raise AssertionError(f"mission did not reach {status}: {service.get(mission_id).status}")


def build_service(tmp_path):
    controller = FakeController()
    control = FlightControlService(controller=controller, arbiter=ControlArbiter())
    registry = CapabilityRegistry("config/capabilities.yaml", control)
    store = MissionStore(tmp_path / "missions.sqlite3")
    service = MissionService(
        store,
        FakePlanner(),
        registry,
        control,
        tmp_path / "checkpoints.sqlite3",
        EventBroker(),
    )
    return service, controller, control


def test_no_action_before_both_approvals(tmp_path):
    service, controller, _ = build_service(tmp_path)
    mission = service.create("起飞并向前飞", "model", "url")
    assert mission.status == MissionStatus.AWAITING_APPROVAL
    assert controller.actions == []

    service.approve(mission.mission_id)
    wait_for(service, mission.mission_id, MissionStatus.AWAITING_STEP_APPROVAL)
    assert controller.actions == []

    service.approve(mission.mission_id)
    completed = wait_for(service, mission.mission_id, MissionStatus.COMPLETED)
    assert completed.next_step_index == 2
    assert [action[0] for action in controller.actions] == ["takeoff", "fly_to"]


def test_takeover_terminates_mission(tmp_path):
    service, controller, control = build_service(tmp_path)
    mission = service.create("起飞", "model", "url")
    service.approve(mission.mission_id)
    wait_for(service, mission.mission_id, MissionStatus.AWAITING_STEP_APPROVAL)
    control.acquire_agent(mission.mission_id)
    result = control.takeover()
    service.mark_taken_over(mission.mission_id)
    assert result["ok"] is True
    assert controller.cancelled is True
    assert service.get(mission.mission_id).status == MissionStatus.TAKEN_OVER


def test_restart_marks_executing_mission_failed(tmp_path):
    path = tmp_path / "missions.sqlite3"
    store = MissionStore(path)
    now = time.time()
    from mission.models import MissionState
    state = MissionState(mission_id="m1", prompt="x", status=MissionStatus.EXECUTING, created_at=now, updated_at=now)
    store.save(state)
    restarted = MissionStore(path)
    recovered = restarted.get("m1")
    assert recovered.status == MissionStatus.FAILED
    assert "重新规划" in recovered.error
