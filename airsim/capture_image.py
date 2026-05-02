from __future__ import annotations

import argparse
from pathlib import Path

try:
    from client import AirSimClient, ImageRequest, ImageType
except ImportError:
    from .client import AirSimClient, ImageRequest, ImageType


VIEW_CAMERAS = {
    "front": "Front",
    "down": "Down",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one image from an AirSim camera.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41451)
    parser.add_argument("--camera", default="Front")
    parser.add_argument("--view", choices=sorted(VIEW_CAMERAS), help="Named view; overrides --camera.")
    parser.add_argument("--vehicle", default="")
    parser.add_argument("--output", default="frames/airsim_scene.png")
    parser.add_argument("--raw-rgb", action="store_true", help="Save raw RGB bytes instead of PNG bytes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with AirSimClient(args.host, args.port) as client:
        client.confirm_connection()
        camera = VIEW_CAMERAS.get(args.view, args.camera)
        if args.raw_rgb:
            response = client.sim_get_images(
                [ImageRequest(camera, ImageType.SCENE, pixels_as_float=False, compress=False)],
                vehicle_name=args.vehicle,
            )[0]
            output.write_bytes(response["image_data_uint8"])
            print(f"Saved {response['width']}x{response['height']} raw RGB bytes to {output}")
        else:
            image = client.sim_get_image(camera, ImageType.SCENE, vehicle_name=args.vehicle)
            output.write_bytes(image)
            print(f"Saved PNG image to {output}")


if __name__ == "__main__":
    main()
