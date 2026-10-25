# AirSim manual image capture

This folder does not use the official `airsim` Python package. It talks to the
AirSim simulator directly through its default MessagePack-RPC TCP endpoint
(`127.0.0.1:41451`).

## Requirements

The current `langgraphenv` environment already has the needed packages:

- `msgpack`
- `numpy`
- `opencv-python`
- `Pillow` is optional for your own post-processing scripts

If another machine is missing them, install only these general-purpose packages:

```powershell
conda run -n langgraphenv python -m pip install msgpack numpy opencv-python pillow
```

Do not install the official `airsim` package for this implementation.

## Capture one PNG

Run from this folder:

```powershell
cd D:\WorkSpace\works\bishe\airsim
conda run -n langgraphenv python capture_image.py --output frames\scene.png
```

Capture the named front camera:

```powershell
conda run -n langgraphenv python capture_image.py --view front --output frames\front.png
```

Capture the downward camera after restarting AirSim with the settings below:

```powershell
conda run -n langgraphenv python capture_image.py --view down --output frames\down.png
```

## Show live video

```powershell
cd D:\WorkSpace\works\bishe\airsim
conda run -n langgraphenv python stream_video.py
```

Press `q` in the video window to stop.

Downward stream:

```powershell
conda run -n langgraphenv python stream_video.py --view down
```

Optional recording:

```powershell
conda run -n langgraphenv python stream_video.py --record frames\stream.mp4
```

## Useful options

- `--host` / `--port`: AirSim RPC address, default `127.0.0.1:41451`
- `--camera`: camera name, default `Front`
- `--view front|down`: named view; overrides `--camera`
- `--vehicle`: vehicle name, leave empty for the default vehicle

## Frontend API views

The FastAPI app supports named views:

- `GET /api/airsim/views`: list supported views
- `GET /api/airsim/stream?view=front`: HD front stream
- `GET /api/airsim/stream?view=down`: downward stream

`view=front` maps to the AirSim camera named `Front`, and `view=down` maps to
`Down`. You can override them with `AIRSIM_CAMERA` and `AIRSIM_DOWN_CAMERA` in
`.env`.

## AirSim camera settings

The settings file is:

```text
C:\Users\admin\Documents\AirSim\settings.json
```

The `PX4` vehicle includes HD cameras named `Front` and `Down`:

```json
"Front": {
  "Enabled": true,
  "CaptureSettings": [
    {
      "ImageType": 0,
      "Width": 1920,
      "Height": 1080,
      "FOV_Degrees": 100
    }
  ],
  "X": 0.4,
  "Y": 0,
  "Z": 0.2,
  "Pitch": 0,
  "Roll": 0,
  "Yaw": 0
}
```

```json
"Down": {
  "Enabled": true,
  "CaptureSettings": [
    {
      "ImageType": 0,
      "Width": 1920,
      "Height": 1080,
      "FOV_Degrees": 100
    }
  ],
  "X": 0,
  "Y": 0,
  "Z": 0.2,
  "Pitch": -90,
  "Roll": 0,
  "Yaw": 0
}
```

AirSim must be restarted after editing `settings.json`.
