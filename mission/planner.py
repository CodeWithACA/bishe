from __future__ import annotations

import json
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from domain.registry import CapabilityRegistry

from .models import MissionPlan, PlanStep


class StepDraft(BaseModel):
    title: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_result: str


class PlanDraft(BaseModel):
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[StepDraft]


class MissionPlanner:
    def __init__(self, registry: CapabilityRegistry, llm_factory) -> None:
        self.registry = registry
        self.llm_factory = llm_factory

    def create_plan(
        self,
        mission_id: str,
        prompt: str,
        model: str,
        base_url: str,
        extra_requirements: str | None = None,
    ) -> MissionPlan:
        llm: ChatOpenAI = self.llm_factory(model, base_url)
        structured = llm.with_structured_output(PlanDraft)
        catalog = self.registry.catalog()
        planning_prompt = f"""你是无人机任务规划器。只生成计划，绝对不要执行任何工具。

用户任务：{prompt}
附加要求：{extra_requirements or '无'}

可用领域能力与地点：
{json.dumps(catalog, ensure_ascii=False, indent=2)}

规则：
- 每个步骤的 tool 必须来自 capabilities。
- 命名地点必须使用 flight.goto_location 和 location_id。
- 起飞、降落及配置中声明 requires_confirmation 的能力必须保留确认标记。
- arguments 必须只包含实际工具所需参数。
- expected_result 应使用用户可核验的中文描述。
- 不要添加用户未要求的降落、返航或设备动作。
"""
        draft = structured.invoke(planning_prompt)
        if not isinstance(draft, PlanDraft):
            draft = PlanDraft.model_validate(draft)
        steps: list[PlanStep] = []
        for index, raw_step in enumerate(draft.steps, start=1):
            capability = self.registry.capability(raw_step.tool)
            steps.append(PlanStep(
                id=f"step-{index}",
                title=raw_step.title,
                tool=raw_step.tool,
                arguments=raw_step.arguments,
                expected_result=raw_step.expected_result,
                risk_level=capability.get("risk_level", "low"),
                requires_confirmation=bool(capability.get("requires_confirmation", False)),
            ))
        return MissionPlan(
            mission_id=mission_id,
            summary=draft.summary,
            assumptions=draft.assumptions,
            steps=steps,
        )
