# PX4/AirSim 无人机 HITL 控制与评测平台

本仓库实现一个基于 LangGraph、MCP、PX4 和 AirSim 的无人机人机协同控制平台，并支持 CLGSCE 风格任务评测。

当前网页提供两种控制模式：

- **人工操控**：WASD 连续水平飞行，滚轮升降，松开或链路超时后悬停。
- **AI 任务**：模型先生成结构化任务概要，用户批准后逐步执行，高风险步骤会再次等待确认。

控制权由服务端仲裁，固定优先级为 `SAFETY > MANUAL > AGENT`。人工接管不依赖模型判断：服务端会直接取消当前 AI 动作、建立悬停并终止原任务。

平台的主要能力包括：

1. 通过 MCP 资源与工具管理命名地点、飞行动作和设备能力。
2. 使用 LangGraph 检查点和中断实现任务级与步骤级 HITL。
3. 通过 WebSocket 提供带断连悬停保护的人工连续控制。
4. 通过 MAVLink 控制 PX4 SITL，并由 AirSim 提供仿真环境和画面。
5. 保存任务、审批、步骤结果和审计事件，并运行 CLGSCE 风格评测。

## Related Paper And Repository

Reference paper:

- **Large Language Model-Driven Closed-Loop UAV Operation With Semantic Observations**
- arXiv PDF used during development: `https://arxiv.org/pdf/2507.01930`

Reference repository:

- `https://github.com/ai-uavsec/CLGSCE`

CLGSCE evaluates generated UAV operation code by comparing observed state transitions against manually defined ground-truth transitions. Each expected action is represented as:

```text
action_flag dx dy dz dyaw
```

For example:

```text
1 0 0 -5 0
1 0 0 4 0
```

means:

- fly 5 meters up: `dz = -5` in NED coordinates
- fly 4 meters down: `dz = 4`

This project converts CLGSCE's `task_sets/*.txt` and `ground_truth/*.txt` into JSONL files and evaluates our PX4/MAVLink agent with the same transition-comparison idea.

## Current Evaluation Data

Converted CLGSCE task sets are stored in:

```text
experiments/task_sets/clgsce_basic.jsonl
experiments/task_sets/clgsce_advanced.jsonl
```

Current imported task counts:

- `clgsce_basic.jsonl`: 44 tasks
- `clgsce_advanced.jsonl`: 20 tasks

The original CLGSCE repository is not vendored into this repo. Use `experiments/import_clgsce_tasks.py` to regenerate these JSONL files from a local CLGSCE clone.

## Project Structure

```text
agent/
  drone_agent.py              LangGraph agent loop and trace runner
  prompt.py                   System prompt for the drone agent
  tools.py                    LLM tools and compact flight history
  px4_mavlink_controller.py   PX4/MAVLink control implementation

control/
  arbiter.py                  控制权租约与优先级仲裁
  service.py                  人工接管、悬停和连续速度控制

domain/
  registry.py                 地点、设备与飞行能力注册表

mission/
  planner.py                  只规划不执行的结构化任务生成器
  service.py                  LangGraph 审批与逐步执行工作流
  store.py                    SQLite 任务和审计记录

config/capabilities.yaml      地点、设备、风险和确认策略
mcp_server.py                 MCP v2 Streamable HTTP 服务

airsim/
  client.py                   Direct MessagePack-RPC AirSim client
  capture_image.py            Capture an AirSim camera frame
  stream_video.py             Stream AirSim camera video

experiments/
  task_sets/                  Converted CLGSCE tasks and ground truth
  fixtures/                   Small evaluator test fixture
  runs/                       Generated experiment output, ignored by Git
  import_clgsce_tasks.py      Convert CLGSCE task/ground-truth files
  run_experiment.py           Run one task and evaluate it
  run_batch.py                Run selected tasks in batch
  evaluate_experiment.py      Compare flight history with ground truth

utils/
  drone_cli.py                Terminal trace CLI for one natural-language task
  reset_airsim.py             Reset AirSim through RPC
  start_px4_wsl.py            Older direct WSL PX4 launcher

app.py                        FastAPI/ASGI 应用、REST、WebSocket 和 AirSim 接口
templates/index.html          网页结构
static/                       控制台样式与交互逻辑
mavlink_bridge/               MAVLink utility bridge and notes
```

## Environment

Use the `langgraphenv` Conda environment from the repository root:

```powershell
conda activate langgraphenv
pip install -r requirements.txt
```

The project reads `.env` from the repository root. Required LLM settings:

```env
SILICONFLOW_API_KEY=your_key
SILICONFLOW_URL=https://api.siliconflow.com/v1
SILICONFLOW_URL_CN=https://api.siliconflow.cn/v1
DRONE_AGENT_MODEL=deepseek-ai/DeepSeek-V3.2
```

Common simulator settings:

```env
AIRSIM_HOST=127.0.0.1
AIRSIM_PORT=41451
AIRSIM_CAMERA=Front
AIRSIM_DOWN_CAMERA=Down
AIRSIM_VEHICLE=

MAVLINK_CONNECTION=udpin:0.0.0.0:14540
MAVLINK_COMMAND_TIMEOUT=30
PX4_SERVER_URL=http://localhost:8765
PX4_DIR=/home/hw/px4v1.15.2
UAV_DATA_DIR=./data
```

Do not commit `.env`.

## Simulator Setup

This workflow assumes:

- AirSim runs on Windows.
- PX4 SITL runs inside WSL.
- A small WSL-side PX4 control server is already running on port `8765`.

The WSL-side server is responsible for:

- starting PX4 in a `tmux` session
- sending commands into the PX4 shell
- stopping PX4 reliably

Windows talks to it through:

```text
http://localhost:8765
```

Useful server endpoints:

```text
GET  /px4/status
POST /px4/start
POST /px4/mavlink
POST /px4/stop
```

The MAVLink shell command used by default is:

```bash
mavlink start -x -u 14581 -r 4000000 -f -m onboard -o 14540 -t 192.168.240.1
```

## Manual Utilities

Reset AirSim:

```powershell
conda activate langgraphenv
python -m utils.reset_airsim --no-api-control --no-arm
```

Run one free-form agent task and print every LLM/tool step:

```powershell
python -m utils.drone_cli "Fly 5 meters up, then fly 4 meters down."
```

运行 FastAPI 控制台：

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

控制台包含任务规划、人工操控以及 AirSim 视频/遥测接口：

```text
/api/airsim/views
/api/airsim/stream
/api/airsim/telemetry
```

任务与人工控制接口：

```text
POST /api/missions
POST /api/missions/{id}/approve
POST /api/missions/{id}/revise
POST /api/missions/{id}/cancel
POST /api/control/takeover
POST /api/control/hold
WS   /ws/control
WS   /ws/events
POST /mcp
```

任务数据默认保存在 `data/missions.sqlite3`，LangGraph 检查点保存在 `data/checkpoints.sqlite3`。服务重启时，处于执行中的任务会被标记为失败，不会自动续飞。

## Single-Task Evaluation

Run one CLGSCE task:

```powershell
python experiments\run_experiment.py --task-id clgsce_basic_001
```

This writes:

```text
experiments/runs/<run-id>/
  task.json
  agent_trace.txt
  flight_history.json
  tool_log.jsonl
  final_answer.json
  evaluation.json
```

Evaluate an existing flight history manually:

```powershell
python experiments\evaluate_experiment.py `
  --task-id clgsce_basic_001 `
  --flight-history experiments\runs\<run-id>\flight_history.json
```

## Batch Evaluation

Run all basic tasks:

```powershell
python experiments\run_batch.py --task-set basic --continue-on-error
```

Run all advanced tasks:

```powershell
python experiments\run_batch.py --task-set advanced --continue-on-error
```

Run both task sets:

```powershell
python experiments\run_batch.py --task-set all --continue-on-error
```

Run a specific task:

```powershell
python experiments\run_batch.py --task-set basic --task-id clgsce_basic_001
```

Run a small smoke test:

```powershell
python experiments\run_batch.py --task-set basic --limit 3 --continue-on-error
```

Batch output:

```text
experiments/runs/<batch-id>/
  selected_tasks.json
  summary.json
  summary.jsonl
  001-clgsce_basic_001/
    task.json
    px4_start.json
    agent_trace.txt
    flight_history.json
    tool_log.jsonl
    evaluation.json
    px4_stop.json
```

## Evaluation Method

The evaluator follows the CLGSCE state-transition comparison convention.

For each significant observed tool action:

```text
actual_delta = after_state - before_state
```

It compares:

```text
[dx, dy, dz, dyaw]
```

with the expected ground-truth transition.

Default tolerances:

```text
position error < 1 meter
yaw error < 4 degrees
```

Metrics:

```text
matched_action_count
expected_action_count
completeness = matched_action_count / expected_action_count
success = completeness == 1.0
```

Batch summaries include:

```text
task_count
completed_count
success_count
success_rate
average_completeness
```

## Important Notes

- AI 规划阶段没有可变更工具；用户批准后，任务动作才通过 MCP 工具执行。
- 起飞、降落及 `capabilities.yaml` 中标记为高风险的动作需要步骤级复核。
- 人工连续控制不经过 MCP，而是走 20 Hz WebSocket；两类控制最终使用同一个控制权仲裁器。
- 300 ms 未收到人工速度帧或 WebSocket 断开时，服务端会建立位置悬停。

- The LLM sees compact tool results only, not full telemetry dumps. This keeps context size under control.
- The agent's tools use LLM-friendly arguments such as `takeoff(altitude_m)` and `fly_to(x, y, altitude_m)`.
- `altitude_m` is always a positive height above ground. The controller converts it to PX4 local NED internally.
- Resetting AirSim does not fully reset PX4 estimator/mode state. For evaluation, the batch runner resets AirSim and restarts PX4 for each task.
- `experiments/runs/*` is ignored by Git because it contains generated traces, logs, and evaluation outputs.
- The direct `utils/start_px4_wsl.py` launcher is kept for reference, but the WSL server approach is more reliable for automated experiments.

## Rebuilding CLGSCE Task Files

Clone CLGSCE somewhere locally, then run:

```powershell
python experiments\import_clgsce_tasks.py C:\path\to\CLGSCE --task-set all
```

This regenerates:

```text
experiments/task_sets/clgsce_basic.jsonl
experiments/task_sets/clgsce_advanced.jsonl
```
