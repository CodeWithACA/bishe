from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from airsim import AirSimClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset the AirSim simulator through the local MessagePack-RPC client.")
    parser.add_argument("--host", default=os.getenv("AIRSIM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AIRSIM_PORT", "41451")))
    parser.add_argument("--vehicle", default=os.getenv("AIRSIM_VEHICLE", ""))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--no-api-control", action="store_true", help="Do not re-enable API control after reset.")
    parser.add_argument("--no-arm", action="store_true", help="Do not arm the vehicle after reset.")
    parser.add_argument("--settle-sec", type=float, default=1.0, help="Seconds to wait after reset before reinitializing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with AirSimClient(args.host, args.port, timeout=args.timeout) as client:
        client.confirm_connection()
        print(f"Connected to AirSim RPC at {args.host}:{args.port}")

        client.reset()
        print("AirSim reset command sent.")

        if args.settle_sec > 0:
            time.sleep(args.settle_sec)

        if not args.no_api_control:
            client.enable_api_control(True, vehicle_name=args.vehicle)
            print("API control enabled.")

        if not args.no_arm:
            client.arm_disarm(True, vehicle_name=args.vehicle)
            print("Vehicle armed.")

    print("Done.")


if __name__ == "__main__":
    main()
