from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import MCPServer

from domain.registry import CapabilityRegistry


class MCPToolExecutor:
    """Execute mission actions through the same registered MCP tool boundary."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    def execute(self, tool_name: str, arguments: dict[str, Any], mission_id: str) -> dict[str, Any]:
        payload = dict(arguments)
        if tool_name not in {"flight.get_state", "device.get_state"}:
            payload["mission_id"] = mission_id
        result = asyncio.run(self.server.call_tool(tool_name, payload))
        structured = getattr(result, "structured_content", None)
        if structured is None:
            raise RuntimeError(f"MCP tool {tool_name} did not return structured output")
        return structured


def create_mcp_server(registry: CapabilityRegistry) -> MCPServer:
    mcp = MCPServer(
        "uav-domain-capabilities",
        title="无人机领域能力",
        description="提供地点、飞行与设备能力；控制权和审批由宿主安全层管理。",
        version="1.0.0",
    )

    @mcp.resource("location://catalog", name="地点目录", mime_type="application/json")
    def location_catalog() -> dict[str, Any]:
        return {"locations": list(registry.locations.values())}

    @mcp.resource("location://{location_id}", name="地点详情", mime_type="application/json")
    def location_detail(location_id: str) -> dict[str, Any]:
        return registry.location(location_id)

    @mcp.resource("device://catalog", name="设备目录", mime_type="application/json")
    def device_catalog() -> dict[str, Any]:
        return {"devices": list(registry.devices.values())}

    @mcp.resource("device://{device_id}", name="设备详情", mime_type="application/json")
    def device_detail(device_id: str) -> dict[str, Any]:
        return registry.device_state(device_id)

    @mcp.tool(name="flight.get_state", structured_output=True)
    def flight_get_state() -> dict[str, Any]:
        """读取无人机当前状态；只读操作。"""
        return {"ok": True, "state": registry.control.get_state()}

    @mcp.tool(name="flight.takeoff", structured_output=True)
    def flight_takeoff(mission_id: str, altitude_m: float = 1.5) -> dict[str, Any]:
        """在已批准任务的 AGENT 控制租约下起飞。"""
        return registry.execute("flight.takeoff", {"altitude_m": altitude_m}, mission_id)

    @mcp.tool(name="flight.land", structured_output=True)
    def flight_land(mission_id: str) -> dict[str, Any]:
        """在已批准任务的 AGENT 控制租约下降落。"""
        return registry.execute("flight.land", {}, mission_id)

    @mcp.tool(name="flight.move", structured_output=True)
    def flight_move(mission_id: str, direction: str, distance_m: float) -> dict[str, Any]:
        """按机体系方向移动指定距离。"""
        return registry.execute("flight.move", {"direction": direction, "distance_m": distance_m}, mission_id)

    @mcp.tool(name="flight.set_yaw", structured_output=True)
    def flight_set_yaw(mission_id: str, yaw_deg: float) -> dict[str, Any]:
        """设置绝对航向角。"""
        return registry.execute("flight.set_yaw", {"yaw_deg": yaw_deg}, mission_id)

    @mcp.tool(name="flight.goto_location", structured_output=True)
    def flight_goto_location(mission_id: str, location_id: str) -> dict[str, Any]:
        """飞往配置中的命名地点。"""
        return registry.execute("flight.goto_location", {"location_id": location_id}, mission_id)

    @mcp.tool(name="device.get_state", structured_output=True)
    def device_get_state(device_id: str) -> dict[str, Any]:
        """读取设备状态；只读操作。"""
        return registry.execute("device.get_state", {"device_id": device_id}, "read-only")

    @mcp.tool(name="device.set_state", structured_output=True)
    def device_set_state(mission_id: str, device_id: str, enabled: bool) -> dict[str, Any]:
        """在已批准任务的 AGENT 控制租约下切换设备。"""
        return registry.execute("device.set_state", {"device_id": device_id, "enabled": enabled}, mission_id)

    return mcp
