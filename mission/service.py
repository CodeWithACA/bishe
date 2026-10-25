from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from control.service import FlightControlService
from domain.registry import CapabilityRegistry

from .events import EventBroker
from .models import MissionPlan, MissionState, MissionStatus
from .planner import MissionPlanner
from .store import MissionStore


TERMINAL_STATUSES = {
    MissionStatus.COMPLETED,
    MissionStatus.FAILED,
    MissionStatus.CANCELLED,
    MissionStatus.TAKEN_OVER,
}


class WorkflowState(TypedDict, total=False):
    mission_id: str
    decision: str


class MissionService:
    def __init__(
        self,
        store: MissionStore,
        planner: MissionPlanner,
        registry: CapabilityRegistry,
        control: FlightControlService,
        checkpoint_path: str | Path,
        events: EventBroker | None = None,
        tool_executor: Any | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.registry = registry
        self.control = control
        self.events = events or EventBroker()
        self.tool_executor = tool_executor or registry
        self._run_lock = threading.RLock()
        connection = sqlite3.connect(Path(checkpoint_path), check_same_thread=False)
        self._checkpointer = SqliteSaver(connection)
        self._graph = self._build_graph().compile(checkpointer=self._checkpointer)

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("overall_review", self._overall_review)
        graph.add_node("step_gate", self._step_gate)
        graph.add_node("execute_step", self._execute_step)
        graph.add_edge(START, "overall_review")
        graph.add_conditional_edges(
            "overall_review",
            lambda state: "step_gate" if state.get("decision") == "approve" else END,
            {"step_gate": "step_gate", END: END},
        )
        graph.add_conditional_edges(
            "step_gate",
            lambda state: "execute_step" if state.get("decision") == "execute" else END,
            {"execute_step": "execute_step", END: END},
        )
        graph.add_conditional_edges(
            "execute_step",
            self._after_step_route,
            {"step_gate": "step_gate", END: END},
        )
        return graph

    def create(self, prompt: str, model: str, base_url: str) -> MissionState:
        mission_id = str(uuid4())
        now = time.time()
        state = MissionState(
            mission_id=mission_id,
            prompt=prompt,
            status=MissionStatus.DRAFTING,
            created_at=now,
            updated_at=now,
        )
        self._save(state, "mission_created", {"prompt": prompt})
        try:
            state.plan = self.planner.create_plan(mission_id, prompt, model, base_url)
            state.status = MissionStatus.AWAITING_APPROVAL
            state.updated_at = time.time()
            self._save(state, "plan_created", state.plan.model_dump(mode="json"))
            self._graph.invoke(
                {"mission_id": mission_id},
                config=self._config(state),
            )
            return self._require(mission_id)
        except Exception as exc:
            state.status = MissionStatus.FAILED
            state.error = str(exc)
            state.updated_at = time.time()
            self._save(state, "planning_failed", {"error": str(exc)})
            raise

    def approve(self, mission_id: str) -> MissionState:
        mission = self._require(mission_id)
        if mission.status not in {MissionStatus.AWAITING_APPROVAL, MissionStatus.AWAITING_STEP_APPROVAL}:
            raise ValueError(f"mission cannot be approved from {mission.status}")
        self._save(mission, "approval_submitted", {"status": mission.status})
        threading.Thread(
            target=self._resume,
            args=(mission, {"action": "approve"}),
            daemon=True,
            name=f"mission-{mission_id[:8]}",
        ).start()
        return mission

    def revise(self, mission_id: str, requirements: str, model: str, base_url: str) -> MissionState:
        mission = self._require(mission_id)
        if mission.status != MissionStatus.AWAITING_APPROVAL:
            raise ValueError("only a mission awaiting overall approval can be revised")
        mission.revision += 1
        mission.plan = self.planner.create_plan(
            mission_id,
            mission.prompt,
            model,
            base_url,
            extra_requirements=requirements,
        )
        mission.next_step_index = 0
        mission.approved = False
        mission.approved_step_id = None
        mission.status = MissionStatus.AWAITING_APPROVAL
        mission.updated_at = time.time()
        self._save(mission, "plan_revised", {"requirements": requirements, "plan": mission.plan.model_dump(mode="json")})
        self._graph.invoke({"mission_id": mission_id}, config=self._config(mission))
        return self._require(mission_id)

    def cancel(self, mission_id: str, reason: str = "用户取消任务") -> MissionState:
        mission = self._require(mission_id)
        if mission.status in TERMINAL_STATUSES:
            return mission
        self.control.controller.cancel_active_command()
        try:
            self.control.controller.hold_position()
        except Exception:
            pass
        mission.status = MissionStatus.CANCELLED
        mission.error = reason
        mission.updated_at = time.time()
        self.control.release_agent(mission_id)
        return self._save(mission, "mission_cancelled", {"reason": reason})

    def mark_taken_over(self, mission_id: str) -> MissionState:
        mission = self._require(mission_id)
        if mission.status not in TERMINAL_STATUSES:
            mission.status = MissionStatus.TAKEN_OVER
            mission.error = "用户已接管；继续任务需要基于当前位置重新规划并确认。"
            mission.updated_at = time.time()
            self._save(mission, "human_takeover", {"next_step_index": mission.next_step_index})
        return mission

    def get(self, mission_id: str) -> MissionState:
        return self._require(mission_id)

    def _resume(self, mission: MissionState, decision: dict[str, Any]) -> None:
        try:
            with self._run_lock:
                self._graph.invoke(Command(resume=decision), config=self._config(mission))
        except Exception as exc:
            current = self._require(mission.mission_id)
            if current.status not in TERMINAL_STATUSES:
                current.status = MissionStatus.FAILED
                current.error = str(exc)
                current.updated_at = time.time()
                self.control.release_agent(current.mission_id)
                self._save(current, "mission_failed", {"error": str(exc)})

    def _overall_review(self, state: WorkflowState) -> WorkflowState:
        mission = self._require(state["mission_id"])
        mission.status = MissionStatus.AWAITING_APPROVAL
        mission.updated_at = time.time()
        self._save(mission, "approval_required", {"scope": "mission"})
        answer = interrupt({"type": "mission_approval", "mission": mission.model_dump(mode="json")})
        if answer.get("action") != "approve":
            mission.status = MissionStatus.CANCELLED
            mission.error = "任务未获批准"
            mission.updated_at = time.time()
            self._save(mission, "mission_rejected", answer)
            return {"mission_id": mission.mission_id, "decision": "stop"}
        mission.approved = True
        mission.status = MissionStatus.EXECUTING
        mission.updated_at = time.time()
        self._save(mission, "mission_approved", {})
        return {"mission_id": mission.mission_id, "decision": "approve"}

    def _step_gate(self, state: WorkflowState) -> WorkflowState:
        mission = self._require(state["mission_id"])
        if mission.status in TERMINAL_STATUSES:
            return {"mission_id": mission.mission_id, "decision": "stop"}
        assert mission.plan is not None
        if mission.next_step_index >= len(mission.plan.steps):
            mission.status = MissionStatus.COMPLETED
            mission.updated_at = time.time()
            self.control.release_agent(mission.mission_id)
            self._save(mission, "mission_completed", {})
            return {"mission_id": mission.mission_id, "decision": "stop"}
        step = mission.plan.steps[mission.next_step_index]
        if step.requires_confirmation and mission.approved_step_id != step.id:
            mission.status = MissionStatus.AWAITING_STEP_APPROVAL
            mission.updated_at = time.time()
            self._save(mission, "approval_required", {"scope": "step", "step": step.model_dump(mode="json")})
            answer = interrupt({"type": "step_approval", "step": step.model_dump(mode="json")})
            if answer.get("action") != "approve":
                mission.status = MissionStatus.CANCELLED
                mission.error = f"步骤 {step.id} 未获批准"
                mission.updated_at = time.time()
                self.control.release_agent(mission.mission_id)
                self._save(mission, "step_rejected", {"step_id": step.id})
                return {"mission_id": mission.mission_id, "decision": "stop"}
            mission.approved_step_id = step.id
        return {"mission_id": mission.mission_id, "decision": "execute"}

    def _execute_step(self, state: WorkflowState) -> WorkflowState:
        mission = self._require(state["mission_id"])
        if mission.status in TERMINAL_STATUSES:
            return {"mission_id": mission.mission_id, "decision": "stop"}
        assert mission.plan is not None
        step = mission.plan.steps[mission.next_step_index]
        self.control.acquire_agent(mission.mission_id)
        mission.status = MissionStatus.EXECUTING
        step.status = "RUNNING"
        mission.updated_at = time.time()
        self._save(mission, "step_started", {"step": step.model_dump(mode="json")})
        started = time.time()
        try:
            before = self.control.get_state()
            result = self.tool_executor.execute(step.tool, step.arguments, mission.mission_id)
            after = self.control.get_state()
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            after = None
            before = None
        current = self._require(mission.mission_id)
        if current.status in {MissionStatus.TAKEN_OVER, MissionStatus.CANCELLED}:
            return {"mission_id": mission.mission_id, "decision": "stop"}
        mission = current
        assert mission.plan is not None
        step = mission.plan.steps[mission.next_step_index]
        step.result = {
            **result,
            "duration_s": round(time.time() - started, 3),
            "before": before,
            "after": after,
        }
        if not result.get("ok", False):
            step.status = "FAILED"
            mission.status = MissionStatus.FAILED
            mission.error = str(result.get("error") or "飞行动作未达到目标")
            self.control.release_agent(mission.mission_id)
            decision = "stop"
        else:
            step.status = "COMPLETED"
            mission.next_step_index += 1
            mission.approved_step_id = None
            decision = "continue"
        mission.updated_at = time.time()
        self._save(mission, "step_finished", {"step": step.model_dump(mode="json")})
        return {"mission_id": mission.mission_id, "decision": decision}

    def _after_step_route(self, state: WorkflowState) -> str:
        return "step_gate" if state.get("decision") == "continue" else END

    def _config(self, mission: MissionState) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"{mission.mission_id}:r{mission.revision}"}}

    def _save(self, mission: MissionState, event_type: str, payload: dict[str, Any]) -> MissionState:
        self.store.save(mission)
        self.store.append_event(mission.mission_id, event_type, payload)
        self.events.publish(
            {
                "type": event_type,
                "mission_id": mission.mission_id,
                "mission": mission.model_dump(mode="json"),
            }
        )
        return mission

    def _require(self, mission_id: str) -> MissionState:
        mission = self.store.get(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        return mission
