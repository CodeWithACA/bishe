# 仓库协作指南

## 项目概述

本仓库是一个面向 PX4/AirSim 的无人机 HITL（Human-in-the-Loop）控制与评测平台。系统同时支持人工飞行和 AI 任务执行，并通过服务端仲裁确保控制优先级始终为：

```text
SAFETY > MANUAL > AGENT
```

首版默认单机、单操作者、单架无人机，主要运行目标为 AirSim 与 PX4 SITL。AI 必须先生成结构化计划，经过用户审批后才能执行；人工接管必须绕过模型并立即取消 AI 动作、建立悬停。

## 文档维护要求

除非另有说明，仓库内新增或修改的文档默认使用中文。

当项目实现、目录结构、运行命令、接口或开发约定发生变化并与本 `AGENTS.md` 不一致时，必须在同一变更中同步更新 `AGENTS.md`。

不要保留已经失效的架构说明、命令或测试要求。修改公共行为时，也应同步检查 `README.md`、相关模块文档和配置示例是否需要更新。

## 目录与模块

- `app.py`：FastAPI/ASGI 应用入口，提供网页、REST、WebSocket、AirSim 接口并挂载 MCP。
- `templates/index.html`：控制台页面结构。
- `static/app.css`、`static/app.js`：控制台样式和浏览器交互逻辑。
- `control/`：控制权租约、优先级仲裁、人工接管、悬停和连续速度控制。
- `mission/`：任务模型、结构化规划、LangGraph 审批工作流、事件和 SQLite 持久化。
- `domain/registry.py`：地点、设备和能力配置注册表。
- `config/capabilities.yaml`：地点、设备、风险等级、确认策略和前置条件。
- `mcp_server.py`：MCP v2 Streamable HTTP 领域能力服务；可变更工具必须经过统一安全控制层。
- `agent/`：底层 LangGraph agent、工具封装和 PX4/MAVLink 控制器。
- `airsim/`：直接使用 MessagePack-RPC 的画面、遥测和视频流工具，不依赖官方 `airsim` Python 包。
- `mavlink_bridge/`：独立 MAVLink 命令行工具及说明。
- `experiments/`：CLGSCE 任务集转换、单任务/批量运行和评测脚本。
- `tests/`：控制仲裁、任务工作流、MCP 和应用接口测试。
- `utils/`：AirSim 重置、任务 CLI 和 PX4/WSL 辅助脚本。
- `data/`：本地 SQLite 和运行数据，不应提交。
- `airsim/frames/`、`experiments/runs/`：生成的画面、轨迹和评测产物，不应提交。

## 开发环境与运行命令

默认使用 PowerShell，并在仓库根目录执行命令。统一使用 `langgraphenv` Conda 环境：

```powershell
conda activate langgraphenv
pip install -r requirements.txt
python app.py
```

控制台默认地址：

```text
http://127.0.0.1:5000
```

运行自动化测试：

```powershell
conda run -n langgraphenv python -m pytest -q
```

常用工具：

```powershell
python -m utils.reset_airsim --no-api-control --no-arm
python -m utils.drone_cli "Fly 5 meters up, then fly 4 meters down."
python experiments\run_experiment.py --task-id clgsce_basic_001
python experiments\run_batch.py --task-set basic --limit 3 --continue-on-error
```

AirSim 工具：

```powershell
python airsim\capture_image.py --view front --output airsim\frames\front.png
python airsim\stream_video.py --view down
```

MAVLink 工具：

```powershell
python mavlink_bridge\mavlink_bridge.py monitor --json
python mavlink_bridge\mavlink_bridge.py control
```

## 核心实现约束

### 控制与安全

- 所有飞控命令必须经过 `ControlArbiter` 和统一控制服务，不得从网页、MCP 或任务代码直接绕过安全层访问 MAVLink。
- 控制优先级不得改变为低优先级覆盖高优先级；人工接管和安全保护不依赖模型同意。
- 长时间飞行动作、转向和等待循环必须支持取消，并防止被取消的旧命令继续发送 setpoint。
- 人工连续速度流使用 `/ws/control`，不通过 MCP。300 ms 超时或 WebSocket 断开必须进入悬停。
- 起飞、降落和高风险设备动作必须保留显式审批，不得为了简化流程跳过 HITL。
- 服务重启后不得自动恢复正在执行的飞行动作。

### 任务与 MCP

- 规划阶段只允许读取状态和配置，不能调用可变更工具。
- 任务计划必须保持结构化，包含概要、步骤、工具参数、风险等级、确认要求和预期结果。
- MCP 只封装地点、设备、飞行动作等领域能力；任务编排、审批状态机和控制权仲裁不放入 MCP。
- 地点、设备、风险等级和确认策略应保存在可版本管理的配置中，不要写死在提示词或前端代码里。
- 每一步执行前后应记录参数、结果、耗时、审批信息和必要遥测，确保 SQLite 审计链完整。

### 前端

- 保持深色飞行控制台的设计方向，并优先保证控制权、风险、链路状态和任务审批的可读性。
- 页面结构、样式和逻辑分别维护在 `templates/index.html`、`static/app.css` 和 `static/app.js`，不要重新合并成单文件页面。
- 保留键盘焦点保护：只有人工模式且飞行画面获得焦点时，WASD 和滚轮输入才生效。
- 关键操作必须有文字标签和明确状态，不能仅用颜色或图标表达；交互控件应支持键盘焦点和必要的 ARIA 属性。
- 修改布局后至少检查桌面和窄屏显示，避免任务审批区掉出主要工作区或出现横向滚动。

## 编码规范

- Python 使用 4 空格缩进、`snake_case` 命名和清晰的类型/数据边界。
- 优先编写职责单一的小函数，协议、配置解析和飞控细节不要跨模块复制。
- API 请求与返回使用明确的数据模型；失败应返回可操作的错误信息，不能静默吞掉安全相关异常。
- JavaScript 保持原生模块化写法，避免引入不必要的前端框架或构建步骤。
- 配置项使用描述性名称；新增环境变量时同步更新 README 或示例说明。
- 保留工作区中与当前任务无关的未提交改动，不要使用破坏性 Git 命令覆盖用户文件。

## 测试与验收

所有代码变更都应运行与风险相称的验证。默认至少执行：

```powershell
conda run -n langgraphenv python -m pytest -q
```

按修改范围补充检查：

- 控制层：验证租约优先级、取消令牌、命令代次、接管和超时悬停。
- 任务层：验证未审批不可执行、高风险步骤暂停、修改计划后旧版本失效、重启不续飞。
- MCP：验证资源结构、参数校验、风险策略和所有可变更工具都经过安全控制服务。
- Web/API：验证任务、控制和 AirSim 接口，以及 `/ws/control`、`/ws/events` 的断连行为。
- 前端：启动应用后检查控制权条、任务规划/审批、事件更新、视频、遥测和响应式布局。
- AirSim/PX4：涉及真实控制路径的改动应在仿真环境中单独验收；自动化单元测试不得意外驱动无人机。

如果受环境限制无法完成 AirSim/PX4 集成测试，应在交付说明中明确列出未验证部分，不能将其表述为已经通过。

## 配置与安全

- 不得提交真实 API Key、令牌、密码或仿真环境专用秘密。
- 本地配置保存在仓库根目录 `.env`，常用变量包括 `SILICONFLOW_API_KEY`、`SILICONFLOW_URL`、`SILICONFLOW_URL_CN`、`DRONE_AGENT_MODEL`、`AIRSIM_HOST`、`AIRSIM_PORT`、`MAVLINK_CONNECTION` 和 `UAV_DATA_DIR`。
- 不要提交 `.env`、SQLite 数据库、AirSim 捕获画面、实验运行目录或大体积日志。
- 真实无人机接入必须在现有驱动接口之后扩展，并额外考虑身份认证、地理围栏、硬件急停、链路冗余和实机安全认证。

## 提交与评审

提交信息使用简短的祈使句，一个提交聚焦一个逻辑变更。Pull Request 或交付说明应包含：

- 改动概要及其安全影响。
- 实际执行的测试命令和结果。
- 新增或变化的 `.env`/配置要求。
- 可见界面变化的截图或录屏。
- 尚未完成的仿真或实机验证。

提交前检查代码、测试、`README.md`、模块文档和本 `AGENTS.md` 是否相互一致。
