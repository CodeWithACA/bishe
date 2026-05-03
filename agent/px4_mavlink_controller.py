from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from pymavlink import mavutil


DEFAULT_CONNECTION = os.getenv("MAVLINK_CONNECTION", "udpin:127.0.0.1:14550")
DEFAULT_BAUD = int(os.getenv("MAVLINK_BAUD", "115200"))
DEFAULT_TIMEOUT = float(os.getenv("MAVLINK_COMMAND_TIMEOUT", "30.0"))
ALTITUDE_TOLERANCE_M = float(os.getenv("MAVLINK_ALTITUDE_TOLERANCE_M", "0.15"))
POSITION_TOLERANCE_M = float(os.getenv("MAVLINK_POSITION_TOLERANCE_M", "0.35"))
YAW_TOLERANCE_DEG = float(os.getenv("MAVLINK_YAW_TOLERANCE_DEG", "1.0"))
HEARTBEAT_HZ = float(os.getenv("MAVLINK_GCS_HEARTBEAT_HZ", "1.0"))
SETPOINT_HZ = float(os.getenv("PX4_SETPOINT_HZ", "10.0"))
STABLE_SAMPLE_COUNT = int(os.getenv("MAVLINK_STABLE_SAMPLE_COUNT", "5"))
STABLE_SAMPLE_INTERVAL_S = float(os.getenv("MAVLINK_STABLE_SAMPLE_INTERVAL_S", "0.1"))
MOTION_SETTLE_SPEED_MPS = float(os.getenv("MAVLINK_MOTION_SETTLE_SPEED_MPS", "0.12"))
VERTICAL_SETTLE_SPEED_MPS = float(os.getenv("MAVLINK_VERTICAL_SETTLE_SPEED_MPS", "0.08"))
YAW_RATE_SETTLE_DEGPS = float(os.getenv("MAVLINK_YAW_RATE_SETTLE_DEGPS", "1.0"))
SETPOINT_MAX_SPEED_MPS = float(os.getenv("MAVLINK_SETPOINT_MAX_SPEED_MPS", "0.8"))
SETPOINT_MIN_SPEED_MPS = float(os.getenv("MAVLINK_SETPOINT_MIN_SPEED_MPS", "0.08"))
SETPOINT_SLOW_RADIUS_M = float(os.getenv("MAVLINK_SETPOINT_SLOW_RADIUS_M", "3.0"))
SETPOINT_LOOKAHEAD_S = float(os.getenv("MAVLINK_SETPOINT_LOOKAHEAD_S", "0.8"))
MOVEMENT_TIMEOUT_SCALE = float(os.getenv("MAVLINK_MOVEMENT_TIMEOUT_SCALE", "4.0"))
MOVEMENT_SETTLE_TIMEOUT_S = float(os.getenv("MAVLINK_MOVEMENT_SETTLE_TIMEOUT_S", "20.0"))
VELOCITY_CONTROL_HZ = float(os.getenv("MAVLINK_VELOCITY_CONTROL_HZ", "20.0"))
VELOCITY_CONTROL_KP = float(os.getenv("MAVLINK_VELOCITY_CONTROL_KP", "0.9"))
VELOCITY_CONTROL_KD = float(os.getenv("MAVLINK_VELOCITY_CONTROL_KD", "0.25"))
VELOCITY_BRAKE_RADIUS_M = float(os.getenv("MAVLINK_VELOCITY_BRAKE_RADIUS_M", "0.7"))
YAW_CONTROL_HZ = float(os.getenv("MAVLINK_YAW_CONTROL_HZ", "20.0"))
YAW_CONTROL_KP = float(os.getenv("MAVLINK_YAW_CONTROL_KP", "2.0"))
YAW_CONTROL_KD = float(os.getenv("MAVLINK_YAW_CONTROL_KD", "0.2"))
YAW_CONTROL_MAX_RATE_DEGPS = float(os.getenv("MAVLINK_YAW_CONTROL_MAX_RATE_DEGPS", "45.0"))


@dataclass
class PX4State:
    connected: bool = False
    armed: bool = False
    mode: str = "UNKNOWN"
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    vx: Optional[float] = None
    vy: Optional[float] = None
    vz: Optional[float] = None
    altitude_m: Optional[float] = None
    relative_altitude_m: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    yaw_deg: Optional[float] = None
    yaw_rate_deg_s: Optional[float] = None
    speed_mps: Optional[float] = None
    vertical_speed_mps: Optional[float] = None
    last_status_text: Optional[str] = None
    timestamp: float = 0.0


class PX4MavlinkController:
    def __init__(self, connection: str = DEFAULT_CONNECTION, baud: int = DEFAULT_BAUD):
        self.connection = connection
        self.baud = baud
        self.master = None
        self.state = PX4State()
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._connected = False
        self._heartbeat_thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._setpoint_thread: threading.Thread | None = None
        self._hold_lock = threading.Lock()
        self._hold_enabled = False
        self._hold_target: tuple[float, float, float, float, bool] | None = None

    def connect(self, wait_timeout: float = 30.0) -> None:
        if self._connected:
            return
        self.master = mavutil.mavlink_connection(self.connection, baud=self.baud)
        self._send_heartbeat()
        heartbeat = self.master.wait_heartbeat(timeout=wait_timeout)
        deadline = time.time() + wait_timeout
        while heartbeat is not None and not self._is_vehicle_heartbeat(heartbeat) and time.time() < deadline:
            heartbeat = self.master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        if heartbeat is None:
            raise TimeoutError(f"No MAVLink heartbeat on {self.connection} within {wait_timeout}s")
        self.master.target_system = heartbeat.get_srcSystem()
        self.master.target_component = heartbeat.get_srcComponent()
        self._connected = True
        with self._state_lock:
            self.state.connected = True
        self._request_streams()
        self._start_threads()
        self.wait_for_state(lambda state: state.z is not None or state.relative_altitude_m is not None, timeout=wait_timeout)

    def close(self) -> None:
        self._stop_event.set()
        for thread in [self._heartbeat_thread, self._reader_thread, self._setpoint_thread]:
            if thread:
                thread.join(timeout=1.5)
        if self.master:
            self.master.close()
        self._connected = False

    def get_state(self) -> dict:
        self.connect()
        with self._state_lock:
            data = asdict(self.state)
        data["timestamp"] = int(time.time())
        return data

    def takeoff(self, altitude_m: float = 5.0, timeout: float = DEFAULT_TIMEOUT) -> dict:
        self.connect()
        altitude_m = max(0.5, float(altitude_m))
        before = self.get_state()
        start_z = self._require_local_z()
        current = self._snapshot()
        start_x = current.x or 0.0
        start_y = current.y or 0.0
        yaw_rad = math.radians(current.yaw_deg or 0.0)
        target_z = start_z - altitude_m
        movement_timeout = self._movement_timeout(altitude_m, timeout)

        self._enter_offboard(start_x, start_y, start_z, yaw_rad)
        self.arm()
        self._set_hold_target(start_x, start_y, target_z, yaw_rad)

        reached = self.wait_for_stable_state(
            lambda state: state.z is not None
            and abs(state.z - target_z) <= ALTITUDE_TOLERANCE_M
            and self._motion_is_settled(state),
            timeout=movement_timeout,
        )
        after_state = self._snapshot()
        if after_state.z is not None:
            self._set_hold_target(after_state.x or start_x, after_state.y or start_y, after_state.z, yaw_rad, shaped=False)

        after = self.get_state()
        delta = None if after_state.z is None else round(start_z - after_state.z, 3)
        return {
            "ok": reached,
            "target_reached": reached,
            "target_delta_altitude_m": altitude_m,
            "actual_delta_altitude_m": delta,
            "before": before,
            "after": after,
        }

    def land(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        self.connect()
        before = self.get_state()
        self._hold_enabled = False
        self.set_mode("LAND")
        reached = self.wait_for_stable_state(
            lambda state: (
                (state.relative_altitude_m is not None and state.relative_altitude_m <= ALTITUDE_TOLERANCE_M)
                or (state.z is not None and abs(state.z) <= ALTITUDE_TOLERANCE_M)
            )
            and self._motion_is_settled(state),
            timeout=timeout,
        )
        if reached:
            try:
                self.disarm()
            except Exception:
                pass
        return {"ok": reached, "target_reached": reached, "before": before, "after": self.get_state()}

    def fly_to(self, x: float, y: float, altitude_m: float, velocity_mps: float = SETPOINT_MAX_SPEED_MPS, timeout: float = DEFAULT_TIMEOUT) -> dict:
        self.connect()
        before = self.get_state()
        current = self._snapshot()
        yaw_rad = math.radians(current.yaw_deg or 0.0)
        target_x = float(x)
        target_y = float(y)
        target_z = -abs(float(altitude_m))
        max_speed = max(SETPOINT_MIN_SPEED_MPS, min(float(velocity_mps), SETPOINT_MAX_SPEED_MPS))
        start_x = current.x or 0.0
        start_y = current.y or 0.0
        start_z = current.z if current.z is not None else target_z
        distance = math.sqrt((target_x - start_x) ** 2 + (target_y - start_y) ** 2 + (target_z - start_z) ** 2)
        movement_timeout = self._movement_timeout(distance, timeout, max_speed)

        self._enter_offboard(start_x, start_y, start_z, yaw_rad)
        self.arm()
        self._hold_enabled = False
        self._fly_velocity_profile(target_x, target_y, target_z, yaw_rad, max_speed, movement_timeout)
        self._set_hold_target(target_x, target_y, target_z, yaw_rad)

        reached = self.wait_for_stable_state(
            lambda state: state.x is not None
            and state.y is not None
            and state.z is not None
            and math.sqrt((state.x - target_x) ** 2 + (state.y - target_y) ** 2 + (state.z - target_z) ** 2) <= POSITION_TOLERANCE_M
            and self._motion_is_settled(state),
            timeout=movement_timeout,
        )
        return {"ok": reached, "target_reached": reached, "before": before, "after": self.get_state()}

    def set_yaw(self, yaw_deg: float, timeout: float = DEFAULT_TIMEOUT) -> dict:
        self.connect()
        before = self.get_state()
        current = self._snapshot()
        target_yaw = float(yaw_deg) % 360.0
        target_yaw_rad = math.radians(target_yaw)
        x = current.x or 0.0
        y = current.y or 0.0
        z = current.z if current.z is not None else -max(current.relative_altitude_m or 0.0, 0.0)

        self._enter_offboard(x, y, z, target_yaw_rad)
        self.arm()
        self._hold_enabled = False
        self._turn_yaw_rate_profile(x, y, z, target_yaw, timeout)
        self._set_hold_target(x, y, z, target_yaw_rad)

        reached = self.wait_for_stable_state(
            lambda state: state.yaw_deg is not None
            and yaw_error_deg(state.yaw_deg, target_yaw) <= YAW_TOLERANCE_DEG
            and self._motion_is_settled(state)
            and self._yaw_is_settled(state),
            timeout=timeout,
        )
        return {
            "ok": reached,
            "target_reached": reached,
            "yaw_error_deg": yaw_error_deg((self._snapshot().yaw_deg or 0.0), target_yaw),
            "before": before,
            "after": self.get_state(),
        }

    def arm(self) -> None:
        self._command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        )

    def disarm(self) -> None:
        self._command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )

    def set_mode(self, mode: str) -> None:
        assert self.master is not None
        with self._send_lock:
            self.master.set_mode(mode.upper())

    def wait_for_state(self, predicate, timeout: float = DEFAULT_TIMEOUT, interval: float = 0.05) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(self._snapshot()):
                return True
            time.sleep(interval)
        return False

    def wait_for_stable_state(
        self,
        predicate,
        timeout: float = DEFAULT_TIMEOUT,
        interval: float = STABLE_SAMPLE_INTERVAL_S,
        samples: int = STABLE_SAMPLE_COUNT,
    ) -> bool:
        deadline = time.time() + timeout
        consecutive = 0
        while time.time() < deadline:
            if predicate(self._snapshot()):
                consecutive += 1
                if consecutive >= samples:
                    return True
            else:
                consecutive = 0
            time.sleep(interval)
        return False

    def _start_threads(self) -> None:
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._setpoint_thread = threading.Thread(target=self._setpoint_loop, daemon=True)
        self._heartbeat_thread.start()
        self._reader_thread.start()
        self._setpoint_thread.start()

    def _heartbeat_loop(self) -> None:
        interval = 1.0 / max(HEARTBEAT_HZ, 0.1)
        while not self._stop_event.is_set():
            try:
                self._send_heartbeat()
            except Exception:
                pass
            time.sleep(interval)

    def _reader_loop(self) -> None:
        assert self.master is not None
        while not self._stop_event.is_set():
            msg = self.master.recv_match(blocking=True, timeout=0.1)
            if msg:
                self._update_state(msg)

    def _setpoint_loop(self) -> None:
        interval = 1.0 / max(SETPOINT_HZ, 2.0)
        while not self._stop_event.is_set():
            with self._hold_lock:
                target = self._hold_target
                enabled = self._hold_enabled
            if enabled and target:
                try:
                    x, y, z, yaw_rad, shaped = target
                    if shaped:
                        x, y, z = self._shaped_position_target(x, y, z)
                    self._send_position_target(x, y, z, yaw_rad)
                except Exception:
                    pass
            time.sleep(interval)

    def _send_heartbeat(self) -> None:
        if not self.master:
            return
        with self._send_lock:
            self.master.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                0,
            )

    def _request_streams(self) -> None:
        assert self.master is not None
        with self._send_lock:
            self.master.mav.request_data_stream_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                20,
                1,
            )

    def _enter_offboard(self, x: float, y: float, z: float, yaw_rad: float) -> None:
        self._set_hold_target(x, y, z, yaw_rad, shaped=False)
        for _ in range(15):
            self._send_position_target(x, y, z, yaw_rad)
            time.sleep(0.08)
        self.set_mode("OFFBOARD")

    def _set_hold_target(self, x: float, y: float, z: float, yaw_rad: float, shaped: bool = True) -> None:
        with self._hold_lock:
            self._hold_target = (float(x), float(y), float(z), float(yaw_rad), bool(shaped))
            self._hold_enabled = True

    def _shaped_position_target(self, target_x: float, target_y: float, target_z: float) -> tuple[float, float, float]:
        state = self._snapshot()
        if state.x is None or state.y is None or state.z is None:
            return target_x, target_y, target_z
        dx = target_x - state.x
        dy = target_y - state.y
        dz = target_z - state.z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance <= POSITION_TOLERANCE_M:
            return target_x, target_y, target_z

        speed = SETPOINT_MAX_SPEED_MPS * min(1.0, distance / max(SETPOINT_SLOW_RADIUS_M, 0.01))
        speed = max(SETPOINT_MIN_SPEED_MPS, min(SETPOINT_MAX_SPEED_MPS, speed))
        lookahead = max(POSITION_TOLERANCE_M, speed * SETPOINT_LOOKAHEAD_S)
        step = min(distance, lookahead)
        scale = step / distance
        return state.x + dx * scale, state.y + dy * scale, state.z + dz * scale

    def _movement_timeout(self, distance_m: float, requested_timeout: float, speed_mps: float = SETPOINT_MAX_SPEED_MPS) -> float:
        cruise_time = max(0.0, float(distance_m)) / max(speed_mps, 0.1)
        shaped_time = cruise_time * max(MOVEMENT_TIMEOUT_SCALE, 1.0) + MOVEMENT_SETTLE_TIMEOUT_S
        return max(float(requested_timeout), shaped_time)

    def _fly_velocity_profile(
        self,
        target_x: float,
        target_y: float,
        target_z: float,
        yaw_rad: float,
        max_speed: float,
        timeout: float,
    ) -> None:
        interval = 1.0 / max(VELOCITY_CONTROL_HZ, 2.0)
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._snapshot()
            if state.x is None or state.y is None or state.z is None:
                time.sleep(interval)
                continue

            ex = target_x - state.x
            ey = target_y - state.y
            ez = target_z - state.z
            distance = math.sqrt(ex * ex + ey * ey + ez * ez)
            if distance <= POSITION_TOLERANCE_M:
                break

            vx = VELOCITY_CONTROL_KP * ex - VELOCITY_CONTROL_KD * (state.vx or 0.0)
            vy = VELOCITY_CONTROL_KP * ey - VELOCITY_CONTROL_KD * (state.vy or 0.0)
            vz = VELOCITY_CONTROL_KP * ez - VELOCITY_CONTROL_KD * (state.vz or 0.0)
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if speed > 0.0:
                speed_limit = max_speed * min(1.0, distance / max(VELOCITY_BRAKE_RADIUS_M, POSITION_TOLERANCE_M))
                speed_limit = max(SETPOINT_MIN_SPEED_MPS, min(max_speed, speed_limit))
                if speed > speed_limit:
                    scale = speed_limit / speed
                    vx *= scale
                    vy *= scale
                    vz *= scale
            self._send_local_velocity(vx, vy, vz, yaw_rad)
            time.sleep(interval)
        self._send_local_velocity(0.0, 0.0, 0.0, yaw_rad)

    def _turn_yaw_rate_profile(self, x: float, y: float, z: float, target_yaw_deg: float, timeout: float) -> None:
        interval = 1.0 / max(YAW_CONTROL_HZ, 2.0)
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._snapshot()
            if state.yaw_deg is None:
                time.sleep(interval)
                continue

            error = signed_yaw_error_deg(target_yaw_deg, state.yaw_deg)
            if abs(error) <= YAW_TOLERANCE_DEG and self._yaw_is_settled(state):
                break

            yaw_rate = YAW_CONTROL_KP * error - YAW_CONTROL_KD * (state.yaw_rate_deg_s or 0.0)
            yaw_rate = max(-YAW_CONTROL_MAX_RATE_DEGPS, min(YAW_CONTROL_MAX_RATE_DEGPS, yaw_rate))
            self._send_position_yaw_rate(x, y, z, math.radians(yaw_rate))
            time.sleep(interval)
        self._send_position_yaw_rate(x, y, z, 0.0)

    def _send_position_target(self, x: float, y: float, z: float, yaw_rad: float) -> None:
        assert self.master is not None
        type_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        with self._send_lock:
            self.master.mav.set_position_target_local_ned_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                x,
                y,
                z,
                0,
                0,
                0,
                0,
                0,
                0,
                yaw_rad,
                0,
            )

    def _send_local_velocity(self, vx: float, vy: float, vz: float, yaw_rad: float) -> None:
        assert self.master is not None
        type_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        with self._send_lock:
            self.master.mav.set_position_target_local_ned_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                0,
                0,
                0,
                vx,
                vy,
                vz,
                0,
                0,
                0,
                yaw_rad,
                0,
            )

    def _send_position_yaw_rate(self, x: float, y: float, z: float, yaw_rate_rad_s: float) -> None:
        assert self.master is not None
        type_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        )
        with self._send_lock:
            self.master.mav.set_position_target_local_ned_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                type_mask,
                x,
                y,
                z,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                yaw_rate_rad_s,
            )

    def _command_long(self, command: int, *params: float) -> None:
        assert self.master is not None
        padded = list(params[:7]) + [0] * (7 - len(params))
        with self._send_lock:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                command,
                0,
                *padded[:7],
            )

    def _update_state(self, msg) -> None:
        msg_type = msg.get_type()
        with self._state_lock:
            self.state.timestamp = time.time()
            if msg_type == "HEARTBEAT":
                if msg.type != mavutil.mavlink.MAV_TYPE_GCS:
                    self.state.armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    self.state.mode = self.master.flightmode if self.master else self.state.mode
            elif msg_type == "LOCAL_POSITION_NED":
                self.state.x = msg.x
                self.state.y = msg.y
                self.state.z = msg.z
                self.state.vx = msg.vx
                self.state.vy = msg.vy
                self.state.vz = msg.vz
                self.state.relative_altitude_m = -msg.z
                self.state.speed_mps = math.sqrt(msg.vx * msg.vx + msg.vy * msg.vy + msg.vz * msg.vz)
                self.state.vertical_speed_mps = -msg.vz
            elif msg_type == "GLOBAL_POSITION_INT":
                self.state.latitude = msg.lat / 1e7
                self.state.longitude = msg.lon / 1e7
                self.state.altitude_m = msg.alt / 1000.0
                self.state.relative_altitude_m = msg.relative_alt / 1000.0
            elif msg_type == "ATTITUDE":
                self.state.roll_deg = math.degrees(msg.roll)
                self.state.pitch_deg = math.degrees(msg.pitch)
                self.state.yaw_deg = (math.degrees(msg.yaw) + 360.0) % 360.0
                self.state.yaw_rate_deg_s = math.degrees(msg.yawspeed)
            elif msg_type == "STATUSTEXT":
                text = msg.text.decode(errors="replace") if isinstance(msg.text, bytes) else str(msg.text)
                self.state.last_status_text = text.rstrip("\x00")

    def _is_vehicle_heartbeat(self, msg) -> bool:
        if msg is None or msg.get_type() != "HEARTBEAT":
            return False
        if msg.type == mavutil.mavlink.MAV_TYPE_GCS:
            return False
        if msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            return False
        return True

    def _snapshot(self) -> PX4State:
        with self._state_lock:
            return PX4State(**asdict(self.state))

    def _require_local_z(self) -> float:
        if not self.wait_for_state(lambda state: state.z is not None, timeout=10.0):
            raise TimeoutError("No LOCAL_POSITION_NED.z telemetry available")
        state = self._snapshot()
        assert state.z is not None
        return state.z

    def _motion_is_settled(self, state: PX4State) -> bool:
        return (state.speed_mps is None or state.speed_mps <= MOTION_SETTLE_SPEED_MPS) and (
            state.vertical_speed_mps is None or abs(state.vertical_speed_mps) <= VERTICAL_SETTLE_SPEED_MPS
        )

    def _yaw_is_settled(self, state: PX4State) -> bool:
        return state.yaw_rate_deg_s is None or abs(state.yaw_rate_deg_s) <= YAW_RATE_SETTLE_DEGPS


def yaw_error_deg(current: float, target: float) -> float:
    return abs((current - target + 180.0) % 360.0 - 180.0)


def signed_yaw_error_deg(target: float, current: float) -> float:
    return (target - current + 180.0) % 360.0 - 180.0


_controller: PX4MavlinkController | None = None
_controller_lock = threading.Lock()


def get_controller() -> PX4MavlinkController:
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = PX4MavlinkController(
                connection=os.getenv("MAVLINK_CONNECTION", DEFAULT_CONNECTION),
                baud=int(os.getenv("MAVLINK_BAUD", str(DEFAULT_BAUD))),
            )
        _controller.connect()
        return _controller
