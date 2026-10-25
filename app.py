from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from math import asin, atan2, degrees
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from airsim import AirSimClient, ImageRequest, ImageType
from control import ControlAuthority, FlightControlService
from domain import CapabilityRegistry
from mcp_server import MCPToolExecutor, create_mcp_server
from mission.events import EventBroker
from mission.planner import MissionPlanner
from mission.service import MissionService
from mission.store import MissionStore


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"
MODEL_OPTIONS = ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3.6-27B"]
URL_OPTIONS = {"global": os.getenv("SILICONFLOW_URL", ""), "cn": os.getenv("SILICONFLOW_URL_CN", "")}
DEFAULT_BASE_URL = URL_OPTIONS["cn"] or URL_OPTIONS["global"]
AIRSIM_HOST = os.getenv("AIRSIM_HOST", "127.0.0.1")
AIRSIM_PORT = int(os.getenv("AIRSIM_PORT", "41451"))
AIRSIM_CAMERA = os.getenv("AIRSIM_CAMERA", "Front")
AIRSIM_VEHICLE = os.getenv("AIRSIM_VEHICLE", "")
AIRSIM_VIEW_CAMERAS = {"front": AIRSIM_CAMERA, "down": os.getenv("AIRSIM_DOWN_CAMERA", "Down")}
DATA_DIR = Path(os.getenv("UAV_DATA_DIR", str(ROOT / "data")))


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")
    return ChatOpenAI(model=model or DEFAULT_MODEL, api_key=api_key, base_url=base_url or DEFAULT_BASE_URL, temperature=0.2)


events = EventBroker()
control = FlightControlService()
registry = CapabilityRegistry(ROOT / "config" / "capabilities.yaml", control)
mission_store = MissionStore(DATA_DIR / "missions.sqlite3")
planner = MissionPlanner(registry, build_llm)
mcp = create_mcp_server(registry)
mcp_executor = MCPToolExecutor(mcp)
missions = MissionService(
    mission_store,
    planner,
    registry,
    control,
    DATA_DIR / "checkpoints.sqlite3",
    events,
    mcp_executor,
)
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", json_response=False, stateless_http=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="无人机 HITL 控制平台", version="1.0.0", lifespan=lifespan)
(ROOT / "static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


class MissionCreateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL


class MissionReviseRequest(BaseModel):
    requirements: str = Field(min_length=1)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL


class ManualTakeoffRequest(BaseModel):
    altitude_m: float = Field(default=1.5, ge=0.5, le=20.0)


def validate_model_endpoint(model: str, base_url: str) -> None:
    if model not in MODEL_OPTIONS:
        raise HTTPException(400, "unsupported model")
    valid_urls = {value for value in URL_OPTIONS.values() if value}
    if base_url not in valid_urls:
        raise HTTPException(400, "unsupported url")


def mission_payload(mission_id: str) -> dict[str, Any]:
    state = missions.get(mission_id)
    payload = state.model_dump(mode="json")
    payload["events"] = mission_store.events(mission_id)
    return payload


@app.middleware("http")
async def disable_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})
    return response


@app.exception_handler(KeyError)
async def key_error_handler(_: Request, exc: KeyError):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"default_model": DEFAULT_MODEL, "model_options": MODEL_OPTIONS, "global_url": URL_OPTIONS["global"], "cn_url": URL_OPTIONS["cn"], "default_url": DEFAULT_BASE_URL},
    )


@app.get("/api/config")
async def config():
    return {"defaultModel": DEFAULT_MODEL, "modelOptions": MODEL_OPTIONS, "urlOptions": URL_OPTIONS, "defaultUrl": DEFAULT_BASE_URL, "capabilities": registry.catalog()}


@app.post("/api/missions", status_code=201)
async def create_mission(body: MissionCreateRequest):
    validate_model_endpoint(body.model, body.base_url)
    try:
        state = await asyncio.to_thread(missions.create, body.prompt.strip(), body.model, body.base_url)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return mission_payload(state.mission_id)


@app.get("/api/missions")
async def list_missions(limit: int = 20):
    return {"missions": [item.model_dump(mode="json") for item in mission_store.list_recent(limit)]}


@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: str):
    return mission_payload(mission_id)


@app.post("/api/missions/{mission_id}/approve", status_code=202)
async def approve_mission(mission_id: str):
    return missions.approve(mission_id).model_dump(mode="json")


@app.post("/api/missions/{mission_id}/revise")
async def revise_mission(mission_id: str, body: MissionReviseRequest):
    validate_model_endpoint(body.model, body.base_url)
    state = await asyncio.to_thread(missions.revise, mission_id, body.requirements.strip(), body.model, body.base_url)
    return state.model_dump(mode="json")


@app.post("/api/missions/{mission_id}/cancel")
async def cancel_mission(mission_id: str):
    return (await asyncio.to_thread(missions.cancel, mission_id)).model_dump(mode="json")


@app.get("/api/control/status")
async def control_status():
    snapshot = control.arbiter.snapshot()
    return {**snapshot.__dict__, "authority": snapshot.authority.value}


@app.post("/api/control/takeover")
async def takeover():
    previous = control.arbiter.snapshot()
    result = await asyncio.to_thread(control.takeover)
    if previous.authority == ControlAuthority.AGENT and previous.holder:
        missions.mark_taken_over(previous.holder)
    events.publish({"type": "control_changed", "control": result["control"]})
    return result


@app.post("/api/control/hold")
async def hold():
    return await asyncio.to_thread(control.hold_manual)


@app.post("/api/control/takeoff")
async def manual_takeoff(body: ManualTakeoffRequest):
    return await asyncio.to_thread(control.manual_takeoff, body.altitude_m)


@app.post("/api/control/land")
async def manual_land():
    return await asyncio.to_thread(control.manual_land)


@app.websocket("/ws/control")
async def control_socket(websocket: WebSocket):
    await websocket.accept()
    stale = False
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.3)
                stale = False
            except asyncio.TimeoutError:
                if not stale and control.arbiter.snapshot().authority == ControlAuthority.MANUAL:
                    await asyncio.to_thread(control.hold_manual)
                    stale = True
                continue
            message_type = message.get("type", "velocity")
            if message_type == "velocity":
                result = await asyncio.to_thread(control.manual_velocity, message.get("forward", 0.0), message.get("right", 0.0), message.get("up", 0.0))
                await websocket.send_json(result)
            elif message_type == "heartbeat":
                await websocket.send_json({"ok": True, "type": "heartbeat"})
            elif message_type == "release":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if control.arbiter.snapshot().authority == ControlAuthority.MANUAL:
            await asyncio.to_thread(control.release_manual)


@app.websocket("/ws/events")
async def event_socket(websocket: WebSocket):
    await websocket.accept()
    subscription = events.subscribe()
    _, queue = subscription
    try:
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        events.unsubscribe(subscription)


def nested_get(data: dict[str, Any], *keys: str, default=None):
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def as_float(value: Any, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def quaternion_to_euler(orientation: dict[str, Any]) -> tuple[float, float, float]:
    x, y, z = (as_float(orientation.get(key)) for key in ("x_val", "y_val", "z_val"))
    w = as_float(orientation.get("w_val"), 1.0)
    roll = atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return degrees(roll), degrees(pitch), (degrees(yaw) + 360.0) % 360.0


def parse_telemetry(state: dict[str, Any]) -> dict[str, Any]:
    gps = nested_get(state, "gps_location", default={}) or {}
    kinematics = nested_get(state, "kinematics_estimated", default={}) or {}
    position = nested_get(kinematics, "position", default={}) or {}
    velocity = nested_get(kinematics, "linear_velocity", default={}) or {}
    orientation = nested_get(kinematics, "orientation", default={}) or {}
    vx, vy, vz = (as_float(velocity.get(key)) for key in ("x_val", "y_val", "z_val"))
    roll, pitch, yaw = quaternion_to_euler(orientation)
    return {"ok": True, "latitude": as_float(gps.get("latitude"), None), "longitude": as_float(gps.get("longitude"), None), "altitude": as_float(gps.get("altitude"), -as_float(position.get("z_val"))), "speed": (vx * vx + vy * vy + vz * vz) ** 0.5, "verticalSpeed": -vz, "heading": yaw, "roll": roll, "pitch": pitch, "yaw": yaw, "timestamp": int(time.time())}


@app.get("/api/airsim/views")
async def airsim_views():
    return {"default": "front", "views": [{"id": view, "camera": camera} for view, camera in AIRSIM_VIEW_CAMERAS.items()]}


@app.get("/api/airsim/telemetry")
async def airsim_telemetry(vehicle: str = AIRSIM_VEHICLE):
    try:
        def read():
            with AirSimClient(AIRSIM_HOST, AIRSIM_PORT, timeout=3.0) as client:
                return parse_telemetry(client.call("getMultirotorState", vehicle))
        return await asyncio.to_thread(read)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "timestamp": int(time.time())}


def status_frame(message: str) -> bytes:
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    frame[:] = (18, 25, 34)
    cv2.putText(frame, "AirSim video unavailable", (64, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (214, 232, 240), 2)
    cv2.putText(frame, message[:72], (64, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (127, 149, 163), 1)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return encoded.tobytes() if ok else b""


def encode_frame(client: AirSimClient, camera: str, vehicle: str) -> bytes:
    response = client.sim_get_images([ImageRequest(camera, ImageType.SCENE, pixels_as_float=False, compress=False)], vehicle_name=vehicle)[0]
    frame = np.frombuffer(response["image_data_uint8"], dtype=np.uint8).reshape(int(response["height"]), int(response["width"]), 3)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
    if not ok:
        raise RuntimeError("Failed to encode AirSim frame")
    return encoded.tobytes()


@app.get("/api/airsim/stream")
async def airsim_stream(view: str = "front", camera: str | None = None, vehicle: str = AIRSIM_VEHICLE, fps: float = 15.0):
    selected_camera = AIRSIM_VIEW_CAMERAS.get(view.lower(), camera or AIRSIM_CAMERA)
    interval = 1.0 / max(1.0, min(30.0, fps))

    def generate():
        try:
            with AirSimClient(AIRSIM_HOST, AIRSIM_PORT, timeout=3.0) as client:
                client.confirm_connection()
                while True:
                    started = time.monotonic()
                    frame = encode_frame(client, selected_camera, vehicle)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    time.sleep(max(0.0, interval - (time.monotonic() - started)))
        except Exception as exc:
            frame = status_frame(str(exc))
            while True:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                time.sleep(1.0)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


app.mount("/", mcp_app, name="mcp")


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=False)
