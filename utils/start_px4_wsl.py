from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time


DEFAULT_PX4_DIR = "~/px4v1.15.2"
DEFAULT_MAVLINK_COMMAND = "mavlink start -x -u 14581 -r 4000000 -f -m onboard -o 14540 -t 192.168.240.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start PX4 SITL in WSL and inject a MAVLink command into the PX4 shell.")
    parser.add_argument("--distro", default="", help="Optional WSL distro name.")
    parser.add_argument("--px4-dir", default=DEFAULT_PX4_DIR)
    parser.add_argument("--startup-delay", type=float, default=5.0, help="Seconds to wait before sending the MAVLink command.")
    parser.add_argument("--mavlink-command", default=DEFAULT_MAVLINK_COMMAND)
    parser.add_argument("--no-cleanup", action="store_true", help="Do not run cleanup commands in WSL after Ctrl+C.")
    return parser.parse_args()


def build_wsl_command(args: argparse.Namespace) -> list[str]:
    command = [
        "wsl.exe",
    ]
    if args.distro:
        command.extend(["-d", args.distro])
    command.extend(["--", "bash", "-lc", f"cd {args.px4_dir} && make px4_sitl_default none_iris"])
    return command


def cleanup_wsl(args: argparse.Namespace) -> None:
    if args.no_cleanup:
        return
    cleanup_command = "pkill -INT -f 'px4.*none_iris|make px4_sitl_default' || true"
    command = ["wsl.exe"]
    if args.distro:
        command.extend(["-d", args.distro])
    command.extend(["--", "bash", "-lc", cleanup_command])
    subprocess.run(command, check=False)


def stop_process(process: subprocess.Popen, args: argparse.Namespace) -> None:
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=8)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    finally:
        cleanup_wsl(args)


def main() -> int:
    args = parse_args()
    command = build_wsl_command(args)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    print("Starting PX4 SITL in WSL...")
    print(" ".join(command))
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=None,
        stderr=None,
        text=True,
        creationflags=creationflags,
    )

    try:
        time.sleep(max(args.startup_delay, 0.0))
        if process.poll() is not None:
            return process.returncode or 1
        if process.stdin is None:
            raise RuntimeError("PX4 process stdin is not available; cannot inject MAVLink command.")

        print(f"\nSending PX4 shell command after {args.startup_delay:g}s:")
        print(args.mavlink_command)
        process.stdin.write(args.mavlink_command + "\n")
        process.stdin.flush()

        print("\nPX4 is running. Press Ctrl+C in this terminal to stop it.")
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping PX4...")
        stop_process(process, args)
        print("PX4 stop requested.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
