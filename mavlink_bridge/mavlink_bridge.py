import argparse
import json
import math
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from pymavlink import mavutil


DEFAULT_CONNECTION = "udpin:192.168.240.1:18570"


@dataclass
class Telemetry:
    connected: bool = False
    system_id: Optional[int] = None
    component_id: Optional[int] = None
    armed: bool = False
    mode: str = "UNKNOWN"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None
    relative_altitude_m: Optional[float] = None
    distance_from_home_m: Optional[float] = None
    horizontal_speed_m_s: Optional[float] = None
    vertical_speed_m_s: Optional[float] = None
    vx_m_s: Optional[float] = None
    vy_m_s: Optional[float] = None
    vz_m_s: Optional[float] = None
    airspeed_m_s: Optional[float] = None
    groundspeed_m_s: Optional[float] = None
    climb_m_s: Optional[float] = None
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    yaw_deg: Optional[float] = None
    battery_voltage_v: Optional[float] = None
    battery_current_a: Optional[float] = None
    battery_remaining_pct: Optional[int] = None
    gps_fix_type: Optional[int] = None
    satellites_visible: Optional[int] = None
    last_status_text: Optional[str] = None
    last_status_severity: Optional[int] = None
    last_message_type: Optional[str] = None
    last_heartbeat_age_s: Optional[float] = None


class MavlinkBridge:
    def __init__(self, connection: str = DEFAULT_CONNECTION, baud: int = 115200):
        self.connection = connection
        self.baud = baud
        self.master = None
        self.telemetry = Telemetry()
        self.home_lat = None
        self.home_lon = None
        self.last_heartbeat_time = None
        self._stop_event = threading.Event()
        self._heartbeat_thread = None

    def connect(self, wait_heartbeat_timeout: int = 30) -> None:
        self.master = mavutil.mavlink_connection(self.connection, baud=self.baud)
        self._send_gcs_heartbeat()
        print(f"Waiting for heartbeat on {self.connection} ...")
        heartbeat = self.master.wait_heartbeat(timeout=wait_heartbeat_timeout)
        if heartbeat is None:
            raise TimeoutError(f"No heartbeat received within {wait_heartbeat_timeout}s")
        if not self._is_vehicle_heartbeat(heartbeat):
            deadline = time.time() + wait_heartbeat_timeout
            while time.time() < deadline and not self._is_vehicle_heartbeat(heartbeat):
                heartbeat = self.master.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if not self._is_vehicle_heartbeat(heartbeat):
                raise TimeoutError("Heartbeat received, but no vehicle/autopilot heartbeat was found")
        self.telemetry.connected = True
        self._remember_target_from_message(heartbeat, force=True)
        self.telemetry.armed = bool(
            heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )
        self.telemetry.mode = self.master.flightmode
        self.last_heartbeat_time = time.time()
        print(
            "Connected: "
            f"system={self.master.target_system}, component={self.master.target_component}, "
            f"mode={self.master.flightmode}"
        )
        self._request_telemetry_streams()
        self._start_heartbeat()

    def close(self) -> None:
        self._stop_event.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=1.5)
        if self.master:
            self.master.close()

    def _start_heartbeat(self) -> None:
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._send_gcs_heartbeat()
            except Exception:
                pass
            time.sleep(1)

    def _send_gcs_heartbeat(self) -> None:
        if not self.master:
            return
        self.master.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            0,
        )

    def _request_telemetry_streams(self) -> None:
        self.master.mav.request_data_stream_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL,
            20,
            1,
        )
        message_rates = {
            mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT: 1,
            mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 5,
            mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 2,
            mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 2,
        }
        for message_id, hz in message_rates.items():
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                0,
                message_id,
                int(1_000_000 / hz),
                0,
                0,
                0,
                0,
                0,
            )

    def receive_once(self, timeout: float = 0.1):
        msg = self.master.recv_match(blocking=True, timeout=timeout)
        if msg is not None:
            self.update_telemetry(msg)
        return msg

    def update_telemetry(self, msg) -> None:
        msg_type = msg.get_type()
        self.telemetry.last_message_type = msg_type

        if msg_type == "HEARTBEAT":
            if self._is_vehicle_heartbeat(msg):
                self._remember_target_from_message(msg)
                self.telemetry.armed = bool(
                    msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )
                self.telemetry.mode = self.master.flightmode
            self.last_heartbeat_time = time.time()
        elif msg_type == "GLOBAL_POSITION_INT":
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            self.telemetry.latitude = lat
            self.telemetry.longitude = lon
            self.telemetry.altitude_m = msg.alt / 1000.0
            self.telemetry.relative_altitude_m = msg.relative_alt / 1000.0
            self.telemetry.vx_m_s = msg.vx / 100.0
            self.telemetry.vy_m_s = msg.vy / 100.0
            self.telemetry.vz_m_s = msg.vz / 100.0
            self.telemetry.horizontal_speed_m_s = math.hypot(msg.vx, msg.vy) / 100.0
            self.telemetry.vertical_speed_m_s = -msg.vz / 100.0
            if (self.home_lat is None or self.home_lon is None) and lat != 0.0 and lon != 0.0:
                self.home_lat = lat
                self.home_lon = lon
            if self.home_lat is not None and self.home_lon is not None:
                self.telemetry.distance_from_home_m = haversine_m(
                    self.home_lat, self.home_lon, lat, lon
                )
        elif msg_type == "LOCAL_POSITION_NED":
            self.telemetry.vx_m_s = msg.vx
            self.telemetry.vy_m_s = msg.vy
            self.telemetry.vz_m_s = msg.vz
            self.telemetry.horizontal_speed_m_s = math.hypot(msg.vx, msg.vy)
            self.telemetry.vertical_speed_m_s = -msg.vz
        elif msg_type == "VFR_HUD":
            self.telemetry.airspeed_m_s = msg.airspeed
            self.telemetry.groundspeed_m_s = msg.groundspeed
            self.telemetry.climb_m_s = msg.climb
            if self.telemetry.horizontal_speed_m_s is None:
                self.telemetry.horizontal_speed_m_s = msg.groundspeed
            self.telemetry.vertical_speed_m_s = msg.climb
        elif msg_type == "ATTITUDE":
            self.telemetry.roll_deg = math.degrees(msg.roll)
            self.telemetry.pitch_deg = math.degrees(msg.pitch)
            self.telemetry.yaw_deg = math.degrees(msg.yaw)
        elif msg_type == "SYS_STATUS":
            if msg.voltage_battery != 65535:
                self.telemetry.battery_voltage_v = msg.voltage_battery / 1000.0
            if msg.current_battery != -1:
                self.telemetry.battery_current_a = msg.current_battery / 100.0
            if msg.battery_remaining != -1:
                self.telemetry.battery_remaining_pct = msg.battery_remaining
        elif msg_type == "GPS_RAW_INT":
            self.telemetry.gps_fix_type = msg.fix_type
            self.telemetry.satellites_visible = msg.satellites_visible
        elif msg_type == "STATUSTEXT":
            text = msg.text
            if isinstance(text, bytes):
                text = text.decode(errors="replace")
            self.telemetry.last_status_text = text.rstrip("\x00")
            self.telemetry.last_status_severity = msg.severity

        if self.last_heartbeat_time:
            self.telemetry.last_heartbeat_age_s = time.time() - self.last_heartbeat_time

    def arm(self) -> None:
        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        self._print_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, "ARM_DISARM")

    def disarm(self, force: bool = False) -> None:
        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,
            21196 if force else 0,
            0,
            0,
            0,
            0,
            0,
        )
        self._print_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, "ARM_DISARM")

    def set_mode(self, mode: str) -> None:
        self.master.set_mode(mode.upper())
        self._print_ack(mavutil.mavlink.MAV_CMD_DO_SET_MODE, "DO_SET_MODE")

    def takeoff(self, altitude_m: float) -> None:
        if self.is_px4:
            self.arm()
            time.sleep(0.5)
            self.set_mode("TAKEOFF")
            time.sleep(0.5)
        else:
            self.set_mode("GUIDED")
            time.sleep(0.5)
            self.arm()
            time.sleep(1.0)
        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            altitude_m,
        )
        self._print_ack(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, "NAV_TAKEOFF")

    @property
    def is_px4(self) -> bool:
        autopilot = self.master.field("HEARTBEAT", "autopilot", None)
        return autopilot == mavutil.mavlink.MAV_AUTOPILOT_PX4

    @property
    def guided_velocity_mode(self) -> str:
        return "OFFBOARD" if self.is_px4 else "GUIDED"

    def prepare_velocity_control_mode(self) -> None:
        mode = self.guided_velocity_mode
        if mode == "OFFBOARD":
            for _ in range(12):
                self.send_body_velocity(0, 0, 0, 0)
                self.receive_once(timeout=0.02)
                time.sleep(0.08)
        self.set_mode(mode)

    def land(self) -> None:
        self.set_mode("LAND")

    def rtl(self) -> None:
        self.set_mode("RTL")

    def send_body_velocity(
        self, forward_m_s: float, right_m_s: float, down_m_s: float, yaw_rate_rad_s: float
    ) -> None:
        type_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        )
        self.master.mav.set_position_target_local_ned_send(
            int(time.time() * 1000) & 0xFFFFFFFF,
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_FRAME_BODY_NED,
            type_mask,
            0,
            0,
            0,
            forward_m_s,
            right_m_s,
            down_m_s,
            0,
            0,
            0,
            0,
            yaw_rate_rad_s,
        )

    def _print_ack(self, command_id: int, command_name: str, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.receive_once(timeout=0.2)
            if msg and msg.get_type() == "COMMAND_ACK" and msg.command == command_id:
                result_name = mavutil.mavlink.enums["MAV_RESULT"].get(msg.result)
                readable = result_name.name if result_name else str(msg.result)
                print(f"ACK {command_name}: {readable}")
                return
        print(f"ACK {command_name}: no ACK received within {timeout:.1f}s")

    def _remember_target_from_message(self, msg, force: bool = False) -> None:
        if msg is None:
            return
        if not force and not self._is_vehicle_heartbeat(msg):
            return
        src_system = msg.get_srcSystem()
        src_component = msg.get_srcComponent()
        if src_system:
            self.master.target_system = src_system
            self.telemetry.system_id = src_system
        if src_component:
            self.master.target_component = src_component
            self.telemetry.component_id = src_component

    def _is_vehicle_heartbeat(self, msg) -> bool:
        if msg is None or msg.get_type() != "HEARTBEAT":
            return False
        if msg.type == mavutil.mavlink.MAV_TYPE_GCS:
            return False
        if msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            return False
        return True


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def telemetry_line(t: Telemetry) -> str:
    return (
        f"mode={t.mode} armed={t.armed} "
        f"lat={fmt(t.latitude, 7)} lon={fmt(t.longitude, 7)} "
        f"rel_alt={fmt(t.relative_altitude_m)}m "
        f"hspd={fmt(t.horizontal_speed_m_s)}m/s vspd={fmt(t.vertical_speed_m_s)}m/s "
        f"home={fmt(t.distance_from_home_m)}m "
        f"yaw={fmt(t.yaw_deg)}deg batt={fmt(t.battery_voltage_v)}V "
        f"gps={t.gps_fix_type}/{t.satellites_visible}"
    )


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def monitor(args) -> None:
    bridge = MavlinkBridge(args.connect, args.baud)
    bridge.connect(args.wait)
    last_print = 0.0
    try:
        while True:
            bridge.receive_once(timeout=0.2)
            if time.time() - last_print >= args.interval:
                if args.json:
                    print(json.dumps(asdict(bridge.telemetry), ensure_ascii=False))
                else:
                    print(telemetry_line(bridge.telemetry))
                last_print = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()


def command(args) -> None:
    bridge = MavlinkBridge(args.connect, args.baud)
    bridge.connect(args.wait)
    try:
        if args.command == "arm":
            bridge.arm()
        elif args.command == "disarm":
            bridge.disarm(force=args.force)
        elif args.command == "takeoff":
            bridge.takeoff(args.altitude)
        elif args.command == "land":
            bridge.land()
        elif args.command == "rtl":
            bridge.rtl()
        elif args.command == "mode":
            bridge.set_mode(args.mode)
        elif args.command == "snapshot":
            end_time = time.time() + args.seconds
            while time.time() < end_time:
                bridge.receive_once(timeout=0.2)
            print(json.dumps(asdict(bridge.telemetry), ensure_ascii=False, indent=2))
    finally:
        bridge.close()


def control(args) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Keyboard control currently uses Windows msvcrt; use monitor/command on other systems.")

    import msvcrt

    bridge = MavlinkBridge(args.connect, args.baud)
    bridge.connect(args.wait)
    print_control_help(args.speed, args.vertical_speed, args.yaw_rate)
    bridge.prepare_velocity_control_mode()

    forward = right = down = yaw_rate = 0.0
    last_key_time = 0.0
    last_print = 0.0

    try:
        while True:
            bridge.receive_once(timeout=0.02)
            if msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                last_key_time = time.time()
                if key == "\x03":
                    break
                if key == "x":
                    forward = right = down = yaw_rate = 0.0
                elif key == "w":
                    forward = args.speed
                elif key == "s":
                    forward = -args.speed
                elif key == "a":
                    right = -args.speed
                elif key == "d":
                    right = args.speed
                elif key == "r":
                    down = -args.vertical_speed
                elif key == "f":
                    down = args.vertical_speed
                elif key == "q":
                    yaw_rate = -math.radians(args.yaw_rate)
                elif key == "e":
                    yaw_rate = math.radians(args.yaw_rate)
                elif key == "g":
                    bridge.prepare_velocity_control_mode()
                elif key == "l":
                    bridge.land()
                elif key == "t":
                    bridge.takeoff(args.takeoff_altitude)
                elif key == "h":
                    print_control_help(args.speed, args.vertical_speed, args.yaw_rate)
                elif key in ("\x1b", "c"):
                    break

            if time.time() - last_key_time > args.key_timeout:
                forward = right = down = yaw_rate = 0.0

            bridge.send_body_velocity(forward, right, down, yaw_rate)

            if time.time() - last_print >= 1.0:
                print(telemetry_line(bridge.telemetry))
                last_print = time.time()

            time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.send_body_velocity(0, 0, 0, 0)
        bridge.close()


def print_control_help(speed: float, vertical_speed: float, yaw_rate: float) -> None:
    print(
        "\nKeyboard control:\n"
        f"  W/S forward/back ({speed} m/s), A/D left/right ({speed} m/s)\n"
        f"  R/F up/down ({vertical_speed} m/s), Q/E yaw ({yaw_rate} deg/s)\n"
        "  X stop, G guided velocity mode, T arm+takeoff, L land, H help, C or Esc quit\n"
        "Keys act like short pulses. Hold or tap repeatedly for continuous motion.\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small MAVLink bridge for AirSim/ArduPilot telemetry and control.")
    parser.add_argument("--connect", default=DEFAULT_CONNECTION, help=f"MAVLink connection string. Default: {DEFAULT_CONNECTION}")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate when using a serial connection.")
    parser.add_argument("--wait", type=int, default=30, help="Seconds to wait for the first heartbeat.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_monitor = subparsers.add_parser("monitor", help="Print live telemetry.")
    p_monitor.add_argument("--interval", type=float, default=1.0, help="Print interval in seconds.")
    p_monitor.add_argument("--json", action="store_true", help="Print telemetry as JSON lines.")
    p_monitor.set_defaults(func=monitor)

    p_control = subparsers.add_parser("control", help="Control the drone with keyboard velocity commands.")
    p_control.add_argument("--speed", type=float, default=1.0, help="Horizontal speed in m/s.")
    p_control.add_argument("--vertical-speed", type=float, default=0.6, help="Vertical speed in m/s.")
    p_control.add_argument("--yaw-rate", type=float, default=25.0, help="Yaw rate in deg/s.")
    p_control.add_argument("--key-timeout", type=float, default=0.45, help="Seconds before a key command decays to stop.")
    p_control.add_argument("--takeoff-altitude", type=float, default=3.0, help="Takeoff altitude for T key.")
    p_control.set_defaults(func=control)

    for name in ["arm", "land", "rtl"]:
        subparsers.add_parser(name).set_defaults(func=command)

    p_disarm = subparsers.add_parser("disarm")
    p_disarm.add_argument("--force", action="store_true", help="Force disarm. Use only when it is safe.")
    p_disarm.set_defaults(func=command)

    p_takeoff = subparsers.add_parser("takeoff")
    p_takeoff.add_argument("--altitude", type=float, default=3.0, help="Target takeoff altitude in meters.")
    p_takeoff.set_defaults(func=command)

    p_mode = subparsers.add_parser("mode")
    p_mode.add_argument("mode", help="ArduPilot Copter mode, such as GUIDED, LOITER, ALT_HOLD, LAND, RTL.")
    p_mode.set_defaults(func=command)

    p_snapshot = subparsers.add_parser("snapshot")
    p_snapshot.add_argument("--seconds", type=float, default=3.0, help="Collect telemetry for this many seconds before printing.")
    p_snapshot.set_defaults(func=command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
