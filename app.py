import os
import time
from math import atan2, asin, degrees
from uuid import uuid4

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from airsim import AirSimClient, ImageRequest, ImageType
from drone_agent import run_drone_agent


load_dotenv()

app = Flask(__name__)

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"
MODEL_OPTIONS = [
    "deepseek-ai/DeepSeek-V3.2",
    "Qwen/Qwen3.6-27B",
]
URL_OPTIONS = {
    "global": os.getenv("SILICONFLOW_URL", ""),
    "cn": os.getenv("SILICONFLOW_URL_CN", ""),
}
DEFAULT_BASE_URL = URL_OPTIONS["cn"] or URL_OPTIONS["global"]
AIRSIM_HOST = os.getenv("AIRSIM_HOST", "127.0.0.1")
AIRSIM_PORT = int(os.getenv("AIRSIM_PORT", "41451"))
AIRSIM_CAMERA = os.getenv("AIRSIM_CAMERA", "Front")
AIRSIM_VEHICLE = os.getenv("AIRSIM_VEHICLE", "")
AIRSIM_VIEW_CAMERAS = {
    "front": AIRSIM_CAMERA,
    "down": os.getenv("AIRSIM_DOWN_CAMERA", "Down"),
}


@app.after_request
def disable_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")

    return ChatOpenAI(
        model=model or DEFAULT_MODEL,
        api_key=api_key,
        base_url=base_url or DEFAULT_BASE_URL,
        temperature=0.7,
    )


def build_messages(history: list[dict]) -> list:
    messages = []
    for item in history:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def stringify_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def normalize_content(content) -> str:
    return stringify_content(content).lstrip("\r\n")


def nested_get(data, *keys, default=None):
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def quaternion_to_euler(orientation: dict) -> tuple[float, float, float]:
    x = as_float(orientation.get("x_val"))
    y = as_float(orientation.get("y_val"))
    z = as_float(orientation.get("z_val"))
    w = as_float(orientation.get("w_val"), 1.0)

    roll = atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch_input = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = asin(pitch_input)
    yaw = atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return degrees(roll), degrees(pitch), (degrees(yaw) + 360.0) % 360.0


def make_status_frame(message: str) -> bytes:
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    frame[:] = (18, 25, 34)
    cv2.putText(frame, "AirSim video unavailable", (64, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (214, 232, 240), 2)
    cv2.putText(frame, message[:72], (64, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (127, 149, 163), 1)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return encoded.tobytes() if ok else b""


def encode_airsim_frame(client: AirSimClient, camera: str, vehicle: str) -> bytes:
    request_item = ImageRequest(camera, ImageType.SCENE, pixels_as_float=False, compress=False)
    response = client.sim_get_images([request_item], vehicle_name=vehicle)[0]
    width, height = int(response["width"]), int(response["height"])
    image_bytes = response["image_data_uint8"]
    frame = np.frombuffer(image_bytes, dtype=np.uint8).reshape(height, width, 3)
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
    if not ok:
        raise RuntimeError("Failed to encode AirSim frame")
    return encoded.tobytes()


def parse_telemetry(state: dict) -> dict:
    gps = nested_get(state, "gps_location", default={}) or {}
    kinematics = nested_get(state, "kinematics_estimated", default={}) or {}
    position = nested_get(kinematics, "position", default={}) or {}
    velocity = nested_get(kinematics, "linear_velocity", default={}) or {}
    orientation = nested_get(kinematics, "orientation", default={}) or {}

    vx = as_float(velocity.get("x_val"))
    vy = as_float(velocity.get("y_val"))
    vz = as_float(velocity.get("z_val"))
    roll, pitch, yaw = quaternion_to_euler(orientation)

    return {
        "ok": True,
        "latitude": as_float(gps.get("latitude"), None),
        "longitude": as_float(gps.get("longitude"), None),
        "altitude": as_float(gps.get("altitude"), -as_float(position.get("z_val"))),
        "speed": (vx * vx + vy * vy + vz * vz) ** 0.5,
        "verticalSpeed": -vz,
        "heading": yaw,
        "roll": roll,
        "pitch": pitch,
        "yaw": yaw,
        "timestamp": int(time.time()),
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_model=DEFAULT_MODEL,
        model_options=MODEL_OPTIONS,
        global_url=URL_OPTIONS["global"],
        cn_url=URL_OPTIONS["cn"],
        default_url=DEFAULT_BASE_URL,
    )


@app.get("/api/config")
def config():
    return jsonify(
        {
            "defaultModel": DEFAULT_MODEL,
            "modelOptions": MODEL_OPTIONS,
            "urlOptions": URL_OPTIONS,
            "defaultUrl": DEFAULT_BASE_URL,
        }
    )


@app.get("/api/airsim/stream")
def airsim_stream():
    view = request.args.get("view", "").strip().lower()
    camera = AIRSIM_VIEW_CAMERAS.get(view, request.args.get("camera", AIRSIM_CAMERA))
    vehicle = request.args.get("vehicle", AIRSIM_VEHICLE)
    fps = max(1.0, min(30.0, as_float(request.args.get("fps"), 15.0)))
    interval = 1.0 / fps

    def generate():
        try:
            with AirSimClient(AIRSIM_HOST, AIRSIM_PORT, timeout=3.0) as client:
                client.confirm_connection()
                while True:
                    started = time.monotonic()
                    frame = encode_airsim_frame(client, camera, vehicle)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    elapsed = time.monotonic() - started
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
        except Exception as exc:
            frame = make_status_frame(str(exc))
            while True:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                time.sleep(1.0)

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/airsim/views")
def airsim_views():
    return jsonify(
        {
            "default": "front",
            "views": [
                {"id": view, "camera": camera}
                for view, camera in AIRSIM_VIEW_CAMERAS.items()
            ],
        }
    )


@app.get("/api/airsim/telemetry")
def airsim_telemetry():
    vehicle = request.args.get("vehicle", AIRSIM_VEHICLE)
    try:
        with AirSimClient(AIRSIM_HOST, AIRSIM_PORT, timeout=3.0) as client:
            state = client.call("getMultirotorState", vehicle)
            return jsonify(parse_telemetry(state))
    except Exception as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
                "latitude": None,
                "longitude": None,
                "altitude": None,
                "speed": None,
                "verticalSpeed": None,
                "heading": None,
                "roll": 0,
                "pitch": 0,
                "yaw": 0,
                "timestamp": int(time.time()),
            }
        )


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip()
    base_url = (data.get("baseUrl") or DEFAULT_BASE_URL).strip()
    history = data.get("history") or []
    session_id = (data.get("sessionId") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    if model not in MODEL_OPTIONS:
        return jsonify({"error": "unsupported model"}), 400

    valid_urls = {value for value in URL_OPTIONS.values() if value}
    if base_url not in valid_urls:
        return jsonify({"error": "unsupported url"}), 400

    try:
        llm = build_llm(model, base_url)
        content = run_drone_agent(llm, message, history, session_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "id": str(uuid4()),
            "role": "assistant",
            "content": normalize_content(content),
        }
    )


@app.post("/api/chat/stream")
def chat_stream():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip()
    base_url = (data.get("baseUrl") or DEFAULT_BASE_URL).strip()
    history = data.get("history") or []
    session_id = (data.get("sessionId") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    if model not in MODEL_OPTIONS:
        return jsonify({"error": "unsupported model"}), 400

    valid_urls = {value for value in URL_OPTIONS.values() if value}
    if base_url not in valid_urls:
        return jsonify({"error": "unsupported url"}), 400

    def generate():
        try:
            llm = build_llm(model, base_url)
            yield run_drone_agent(llm, message, history, session_id)
        except Exception as exc:
            yield f"\n[ERROR] {exc}"

    return Response(
        stream_with_context(generate()),
        mimetype="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
