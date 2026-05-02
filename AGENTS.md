# Repository Guidelines

## Project Structure & Module Organization

This repository contains a small Python web app plus simulator tooling.

- `app.py` is the main Flask application for the AI chat UI and AirSim API endpoints.
- `templates/index.html` contains the browser UI.
- `airsim/` contains direct MessagePack-RPC helpers for image capture and live video streaming. It intentionally does not use the official `airsim` Python package.
- `mavlink_bridge/` contains the MAVLink command-line bridge and related documentation.
- `requirements.txt`, `airsim/requirements.txt`, and `mavlink_bridge/requirements.txt` define dependencies for each area.
- `airsim/frames/` is for generated captures and recordings; avoid committing generated media.

## Build, Test, and Development Commands

Use PowerShell from the repository root unless noted.

```powershell
conda activate langgraphenv
pip install -r requirements.txt
python app.py
```

`python app.py` starts the local web app at `http://127.0.0.1:5000`.

AirSim utilities:

```powershell
cd airsim
python capture_image.py --view front --output frames\front.png
python stream_video.py --view down
```

MAVLink utilities:

```powershell
cd mavlink_bridge
pip install -r requirements.txt
python .\mavlink_bridge.py monitor --json
python .\mavlink_bridge.py control
```

## Coding Style & Naming Conventions

Write Python using 4-space indentation, clear function names, and `snake_case` for variables, functions, and module-level helpers. Keep command-line options explicit and descriptive, such as `--vertical-speed` or `--output`. Prefer small helper functions around protocol or simulator details instead of duplicating request logic across scripts.

Keep HTML, CSS, and JavaScript in `templates/index.html` consistent with the existing single-page structure unless the UI grows enough to justify splitting assets.

## Testing Guidelines

There is currently no dedicated automated test suite. For changes to `app.py`, run the web app and manually verify chat, model selection, API URL switching, `/api/airsim/views`, `/api/airsim/stream`, and `/api/airsim/telemetry` as applicable.

For AirSim or MAVLink changes, verify against the relevant simulator connection before merging. Use `monitor --json` for machine-readable telemetry checks and save any temporary images or recordings under `airsim/frames/`.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Improve streaming chat UX` and `Add simple AI chat site`. Follow that style: start with a verb, keep the subject concise, and describe one logical change.

Pull requests should include a brief summary, the commands or simulator checks run, any required `.env` changes, and screenshots or recordings for visible UI or stream changes. Link related issues when available.

## Security & Configuration Tips

Do not commit real API keys or simulator-specific secrets. Keep local configuration in `.env`, including `SILICONFLOW_API_KEY`, `SILICONFLOW_URL`, `SILICONFLOW_URL_CN`, `AIRSIM_HOST`, `AIRSIM_PORT`, `AIRSIM_CAMERA`, and `AIRSIM_DOWN_CAMERA`.
