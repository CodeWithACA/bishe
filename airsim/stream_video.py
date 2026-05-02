from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

try:
    from client import AirSimClient, ImageRequest, ImageType
except ImportError:
    from .client import AirSimClient, ImageRequest, ImageType


VIEW_CAMERAS = {
    "front": "Front",
    "down": "Down",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show an AirSim camera stream using manual RPC calls.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41451)
    parser.add_argument("--camera", default="Front")
    parser.add_argument("--view", choices=sorted(VIEW_CAMERAS), help="Named view; overrides --camera.")
    parser.add_argument("--vehicle", default="")
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--window", default="AirSim stream")
    parser.add_argument("--record", default="", help="Optional output video path, for example frames/stream.mp4")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    interval = 1.0 / max(args.fps, 1.0)
    writer: cv2.VideoWriter | None = None

    camera = VIEW_CAMERAS.get(args.view, args.camera)
    request = ImageRequest(camera, ImageType.SCENE, pixels_as_float=False, compress=False)
    with AirSimClient(args.host, args.port) as client:
        client.confirm_connection()
        print("Streaming started. Press q in the video window to stop.")
        while True:
            start = time.monotonic()
            response = client.sim_get_images([request], vehicle_name=args.vehicle)[0]
            width, height = int(response["width"]), int(response["height"])
            image_bytes = response["image_data_uint8"]
            frame = np.frombuffer(image_bytes, dtype=np.uint8).reshape(height, width, 3)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            if args.record and writer is None:
                Path(args.record).parent.mkdir(parents=True, exist_ok=True)
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(args.record, fourcc, args.fps, (width, height))
            if writer is not None:
                writer.write(frame)

            cv2.imshow(args.window, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            elapsed = time.monotonic() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
