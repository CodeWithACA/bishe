from __future__ import annotations

import itertools
import socket
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable

import msgpack


class AirSimRpcError(RuntimeError):
    pass


class ImageType(IntEnum):
    SCENE = 0
    DEPTH_PLANAR = 1
    DEPTH_PERSPECTIVE = 2
    DEPTH_VIS = 3
    DISPARITY_NORMALIZED = 4
    SEGMENTATION = 5
    SURFACE_NORMALS = 6
    INFRARED = 7
    OPTICAL_FLOW = 8
    OPTICAL_FLOW_VIS = 9


@dataclass(frozen=True)
class ImageRequest:
    camera_name: str = "0"
    image_type: ImageType = ImageType.SCENE
    pixels_as_float: bool = False
    compress: bool = True

    def to_rpc(self) -> dict[str, Any]:
        return {
            "camera_name": self.camera_name,
            "image_type": int(self.image_type),
            "pixels_as_float": self.pixels_as_float,
            "compress": self.compress,
        }


class AirSimClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 41451, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._unpacker = msgpack.Unpacker(raw=False)
        self._message_ids = itertools.count(1)
        self._lock = threading.Lock()

    def __enter__(self) -> "AirSimClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._socket is not None:
            return
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._socket.settimeout(self.timeout)

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def call(self, method: str, *args: Any) -> Any:
        self.connect()
        message_id = next(self._message_ids)
        request = [0, message_id, method, list(args)]
        payload = msgpack.packb(request, use_bin_type=True)

        with self._lock:
            assert self._socket is not None
            self._socket.sendall(payload)
            while True:
                for message in self._unpacker:
                    if not isinstance(message, list) or len(message) < 4:
                        continue
                    message_type, response_id, error, result = message[:4]
                    if message_type != 1 or response_id != message_id:
                        continue
                    if error:
                        raise AirSimRpcError(str(error))
                    return result

                chunk = self._socket.recv(1024 * 1024)
                if not chunk:
                    raise ConnectionError("AirSim RPC connection closed")
                self._unpacker.feed(chunk)

    def ping(self) -> bool:
        return bool(self.call("ping"))

    def get_server_version(self) -> int:
        return int(self.call("getServerVersion"))

    def confirm_connection(self) -> None:
        if not self.ping():
            raise ConnectionError("AirSim ping failed")

    def reset(self) -> Any:
        return self.call("reset")

    def enable_api_control(self, enabled: bool = True, vehicle_name: str = "") -> Any:
        return self._call_vehicle_optional("enableApiControl", enabled, vehicle_name=vehicle_name)

    def arm_disarm(self, arm: bool = True, vehicle_name: str = "") -> Any:
        return self._call_vehicle_optional("armDisarm", arm, vehicle_name=vehicle_name)

    def get_multirotor_state(self, vehicle_name: str = "") -> dict[str, Any]:
        return self._call_vehicle_optional("getMultirotorState", vehicle_name=vehicle_name)

    def wait_on_last_task(self, timeout_sec: float = 30.0) -> Any:
        return self._call_first_supported("waitOnLastTask", [(timeout_sec,), ()])

    def takeoff(self, timeout_sec: float = 30.0, vehicle_name: str = "") -> Any:
        return self._call_vehicle_optional("takeoff", timeout_sec, vehicle_name=vehicle_name)

    def land(self, timeout_sec: float = 30.0, vehicle_name: str = "") -> Any:
        return self._call_vehicle_optional("land", timeout_sec, vehicle_name=vehicle_name)

    def move_to_z(
        self,
        z: float,
        velocity: float,
        timeout_sec: float = 30.0,
        yaw_mode: dict[str, Any] | None = None,
        lookahead: float = -1.0,
        adaptive_lookahead: float = 1.0,
        vehicle_name: str = "",
    ) -> Any:
        yaw_mode = yaw_mode or {"is_rate": False, "yaw_or_rate": 0.0}
        yaw_mode_as_list = [bool(yaw_mode.get("is_rate", False)), float(yaw_mode.get("yaw_or_rate", 0.0))]
        return self._call_first_supported(
            "moveToZ",
            [
                (z, velocity, timeout_sec, yaw_mode, lookahead, adaptive_lookahead, vehicle_name),
                (z, velocity, timeout_sec, yaw_mode_as_list, lookahead, adaptive_lookahead, vehicle_name),
                (z, velocity, timeout_sec, yaw_mode, lookahead, adaptive_lookahead),
                (z, velocity, timeout_sec, yaw_mode_as_list, lookahead, adaptive_lookahead),
            ],
        )

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        velocity: float,
        timeout_sec: float = 30.0,
        drivetrain: int = 0,
        yaw_mode: dict[str, Any] | None = None,
        lookahead: float = -1.0,
        adaptive_lookahead: float = 1.0,
        vehicle_name: str = "",
    ) -> Any:
        yaw_mode = yaw_mode or {"is_rate": False, "yaw_or_rate": 0.0}
        yaw_mode_as_list = [bool(yaw_mode.get("is_rate", False)), float(yaw_mode.get("yaw_or_rate", 0.0))]
        return self._call_first_supported(
            "moveToPosition",
            [
                (x, y, z, velocity, timeout_sec, drivetrain, yaw_mode, lookahead, adaptive_lookahead, vehicle_name),
                (x, y, z, velocity, timeout_sec, drivetrain, yaw_mode_as_list, lookahead, adaptive_lookahead, vehicle_name),
                (x, y, z, velocity, timeout_sec, drivetrain, yaw_mode, lookahead, adaptive_lookahead),
                (x, y, z, velocity, timeout_sec, drivetrain, yaw_mode_as_list, lookahead, adaptive_lookahead),
            ],
        )

    def rotate_to_yaw(
        self,
        yaw_deg: float,
        timeout_sec: float = 30.0,
        margin: float = 5.0,
        vehicle_name: str = "",
    ) -> Any:
        return self._call_first_supported(
            "rotateToYaw",
            [
                (yaw_deg, timeout_sec, margin, vehicle_name),
                (yaw_deg, timeout_sec, margin),
                (yaw_deg, timeout_sec, vehicle_name),
                (yaw_deg, timeout_sec),
            ],
        )

    def _call_vehicle_optional(self, method: str, *args: Any, vehicle_name: str = "") -> Any:
        candidates = [(*args, vehicle_name), args] if vehicle_name == "" else [(*args, vehicle_name), args]
        return self._call_first_supported(method, candidates)

    def _call_first_supported(self, method: str, candidates: list[tuple[Any, ...]]) -> Any:
        last_error: Exception | None = None
        for args in candidates:
            try:
                return self.call(method, *args)
            except AirSimRpcError as exc:
                message = str(exc)
                if "invalid number of arguments" not in message and "Invalid number of arguments" not in message:
                    raise
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError(f"No RPC candidates provided for {method}")

    def sim_get_image(
        self,
        camera_name: str = "0",
        image_type: ImageType = ImageType.SCENE,
        vehicle_name: str = "",
        external: bool = False,
    ) -> bytes:
        data = self.call("simGetImage", camera_name, int(image_type), vehicle_name, external)
        return _as_bytes(data)

    def sim_get_images(
        self,
        requests: Iterable[ImageRequest],
        vehicle_name: str = "",
        external: bool = False,
    ) -> list[dict[str, Any]]:
        rpc_requests = [request.to_rpc() for request in requests]
        responses = self.call("simGetImages", rpc_requests, vehicle_name, external)
        return [_normalize_image_response(response) for response in responses]


def _as_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("latin1")
    if isinstance(value, list):
        return bytes(value)
    raise TypeError(f"Unsupported image byte payload: {type(value)!r}")


def _normalize_image_response(response: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(response)
    if "image_data_uint8" in normalized:
        normalized["image_data_uint8"] = _as_bytes(normalized["image_data_uint8"])
    return normalized
