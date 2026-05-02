# CLGSCE-Style Experiments

This folder contains the evaluation pipeline for the PX4/AirSim LangGraph drone agent.

The goal is to run CLGSCE natural-language UAV tasks, record the agent's tool-level flight actions, and compare observed state transitions against CLGSCE-style ground truth.

## Data

Converted task files:

```text
task_sets/clgsce_basic.jsonl
task_sets/clgsce_advanced.jsonl
```

Current contents:

- `clgsce_basic.jsonl`: 44 tasks
- `clgsce_advanced.jsonl`: 20 tasks

Each JSONL row contains:

```json
{
  "id": "clgsce_basic_001",
  "source": "CLGSCE task_sets/basic.txt line 1",
  "source_task_index": 0,
  "prompt": "Fly 5 meters up, then fly 4 meters down.",
  "ground_truth": [
    {"action_flag": 1, "dx": 0.0, "dy": 0.0, "dz": -5.0, "dyaw": 0.0},
    {"action_flag": 1, "dx": 0.0, "dy": 0.0, "dz": 4.0, "dyaw": 0.0}
  ]
}
```

The original CLGSCE repository is not vendored here. To rebuild the converted files from a local CLGSCE clone:

```powershell
python experiments\import_clgsce_tasks.py C:\path\to\CLGSCE --task-set all
```

## Files

```text
import_clgsce_tasks.py      Convert CLGSCE task_sets/*.txt and ground_truth/*.txt into JSONL
evaluate_experiment.py      Compare one flight_history.json against a task's ground truth
run_experiment.py           Run one selected task with the agent and write logs
run_batch.py                Run basic, advanced, all, a range, or one specific task

task_sets/                  Converted CLGSCE tasks and ground truth
fixtures/                   Small fake flight log used to test evaluator logic
runs/                       Generated experiment outputs; ignored by Git
```

## Prerequisites

Before running real experiments:

1. Start AirSim on Windows.
2. Start the WSL-side PX4 controller server on port `8765`.
3. Activate the Windows Python environment:

```powershell
conda activate langgraphenv
```

The batch runner talks to the WSL server through:

```text
http://localhost:8765
```

Default PX4 directory:

```text
/home/hw/px4v1.15.2
```

Default MAVLink command sent into the PX4 shell:

```bash
mavlink start -x -u 14581 -r 4000000 -f -m onboard -o 14540 -t 192.168.240.1
```

These defaults can be overridden with CLI flags or `.env`.

## Single Task

Run one task:

```powershell
python experiments\run_experiment.py --task-id clgsce_basic_001
```

This assumes PX4/AirSim are already ready. It does not manage PX4 startup and shutdown by itself.

Output:

```text
runs/<run-id>/
  task.json
  agent_trace.txt
  flight_history.json
  tool_log.jsonl
  final_answer.json
  evaluation.json
```

## Batch Runs

The batch runner manages the full per-task loop:

```text
reset AirSim
start PX4 through WSL server
wait
send MAVLink command
run agent
evaluate flight history
stop PX4
write summary
```

Run all basic tasks:

```powershell
python experiments\run_batch.py --task-set basic --continue-on-error
```

Run all advanced tasks:

```powershell
python experiments\run_batch.py --task-set advanced --continue-on-error
```

Run both:

```powershell
python experiments\run_batch.py --task-set all --continue-on-error
```

Run one task:

```powershell
python experiments\run_batch.py --task-set basic --task-id clgsce_basic_001
```

Run a quick smoke test:

```powershell
python experiments\run_batch.py --task-set basic --limit 3 --continue-on-error
```

Run a range:

```powershell
python experiments\run_batch.py --task-set basic --start-index 5 --end-index 10 --continue-on-error
```

## Batch Output

Each batch writes:

```text
runs/<batch-id>/
  selected_tasks.json
  summary.json
  summary.jsonl
  001-clgsce_basic_001/
    task.json
    px4_start.json
    agent_trace.txt
    flight_history.json
    tool_log.jsonl
    final_answer.json
    evaluation.json
    px4_stop.json
```

`summary.json` contains:

```text
task_count
completed_count
success_count
success_rate
average_completeness
results
```

## Evaluation Logic

The evaluator extracts significant tool actions from `flight_history.json`.

For each action with both `before` and `after` states:

```text
actual_delta = after_state - before_state
```

It compares:

```text
[dx, dy, dz, dyaw]
```

against the task's ground truth.

Default tolerances:

```text
position error < 1 meter
yaw error < 4 degrees
```

Per-task metrics:

```text
matched_action_count
expected_action_count
completeness = matched_action_count / expected_action_count
success = completeness == 1.0
```

## Notes

- `runs/*` is ignored by Git. Keep only useful summaries elsewhere if they need to be shared.
- `fixtures/clgsce_basic_001_flight_history.json` is not a real flight; it is a small deterministic evaluator fixture.
- `run_experiment.py` is useful for debugging one task after the simulator is already running.
- `run_batch.py` is the preferred entry point for automated experiments because it resets AirSim and restarts PX4 for every task.
