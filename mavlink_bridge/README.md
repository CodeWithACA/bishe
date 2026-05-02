# mavlink_bridge

这是一个用于连接 AirSim/ArduPilot 无人机的 MAVLink 小工具。默认连接地址：

```powershell
udpin:192.168.240.1:18570
```

## 安装

```powershell
cd D:\WorkSpace\works\bishe\mavlink_bridge
pip install -r requirements.txt
```

## 查看遥测

```powershell
python .\mavlink_bridge.py monitor
```

输出包括模式、解锁状态、经纬度、相对高度、水平速度、垂直速度、离起点距离、偏航角、电池和 GPS 状态。

JSON 输出：

```powershell
python .\mavlink_bridge.py monitor --json
```

## 发命令

```powershell
python .\mavlink_bridge.py arm
python .\mavlink_bridge.py takeoff --altitude 3
python .\mavlink_bridge.py mode GUIDED
python .\mavlink_bridge.py land
python .\mavlink_bridge.py rtl
python .\mavlink_bridge.py disarm
```

## 键盘操纵

```powershell
python .\mavlink_bridge.py control
```

按键：

| 键 | 动作 |
|---|---|
| W/S | 前进/后退 |
| A/D | 左移/右移 |
| R/F | 上升/下降 |
| Q/E | 左/右偏航 |
| X | 停止速度指令 |
| G | 切到速度控制模式，ArduPilot 为 GUIDED，PX4/AirSim 为 OFFBOARD |
| T | 解锁并起飞 |
| L | 降落 |
| C 或 Esc | 退出 |

默认水平速度为 1.0 m/s，垂直速度为 0.6 m/s。可调整：

```powershell
python .\mavlink_bridge.py control --speed 0.5 --vertical-speed 0.3 --yaw-rate 15
```

## 连接到其他地址

```powershell
python .\mavlink_bridge.py --connect udpin:0.0.0.0:14550 monitor
python .\mavlink_bridge.py --connect tcp:127.0.0.1:5762 monitor
python .\mavlink_bridge.py --connect COM7 --baud 115200 monitor
```

更多实现说明见 [docs/经验文档.md](docs/经验文档.md)。

## 当前连接实测

在本机用默认地址跑过一次 `snapshot`，可以收到 AirSim 遥测，回传模式为 `POSCTL`，经纬度、电池、GPS、姿态和速度数据均有返回。这说明当前仿真端更像 PX4/AirSim 模式命名；脚本已按心跳里的 autopilot 类型自动选择 ArduPilot 的 `GUIDED` 或 PX4 的 `OFFBOARD` 作为键盘速度控制模式。
