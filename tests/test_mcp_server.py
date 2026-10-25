import asyncio

from app import mcp
from control import ControlArbiter, FlightControlService
from domain import CapabilityRegistry
from mcp_server import MCPToolExecutor, create_mcp_server


class DeviceOnlyController:
    def get_state(self):
        return {"connected": True}


def test_mcp_exposes_domain_tools_and_resources():
    tools = asyncio.run(mcp.list_tools())
    resources = asyncio.run(mcp.list_resources())
    names = {tool.name for tool in tools}
    uris = {str(resource.uri) for resource in resources}
    assert {"flight.takeoff", "flight.move", "flight.goto_location", "device.set_state"} <= names
    assert {"location://catalog", "device://catalog"} <= uris


def test_mission_action_crosses_mcp_tool_boundary():
    control = FlightControlService(controller=DeviceOnlyController(), arbiter=ControlArbiter())
    registry = CapabilityRegistry("config/capabilities.yaml", control)
    server = create_mcp_server(registry)
    executor = MCPToolExecutor(server)
    control.acquire_agent("mission-1")
    result = executor.execute(
        "device.set_state",
        {"device_id": "emergency_light", "enabled": True},
        "mission-1",
    )
    assert result["ok"] is True
    assert result["state"] is True
