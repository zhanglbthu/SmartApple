# SmartApple Sensor Receiver

SmartApple 的电脑端接收、实时可视化和离线处理工具。它接收 Sensor Read iPhone/Apple Watch
采集软件通过 UDP 发出的 JSON 事件，可视化指定设备的一种模态，并将收到的完整事件流保存为
NDJSON。仓库不包含 iOS/watchOS App 源码，也不包含任何受试者或真机录制数据。

## 代码结构

| 文件 | 作用 |
| --- | --- |
| `receiver_visualizer.py` | UDP 接收、NDJSON 保存、单模态实时可视化和丢包统计 |
| `run.sh` | 使用 `mobileposer` Conda 环境启动接收器 |
| `DATA_PROTOCOL.md` | 会话目录、NDJSON、WAV、时间戳和原始/融合数据边界 |
| `make_handoff_bundle.sh` | 把原始会话和数据协议打包，并生成 SHA-256 校验文件 |
| `environment.yml` | 可复现的 Python/Conda 环境 |

## 1. 软件使用

### 1.1 环境安装

需要 Python 3、NumPy 和 Matplotlib。推荐使用 Conda：

```bash
git clone git@github.com:zhanglbthu/SmartApple.git
cd SmartApple
conda env create -f environment.yml
conda activate mobileposer
```

如果本机已经存在 `mobileposer` 环境，只需确认依赖：

```bash
conda install -n mobileposer numpy matplotlib
```

### 1.2 确定电脑 IP

Mac 可运行：

```bash
ipconfig getifaddr en0
```

Windows 可在 PowerShell 或命令提示符运行：

```powershell
ipconfig
```

确保电脑、iPhone 和 Apple Watch 网络互通。在 Sensor Read iPhone App 中填写电脑的局域网 IP，
端口与接收器保持一致，默认是 `9000`。macOS 或 Windows 首次询问 Python 的入站网络权限时需要允许。

### 1.3 启动接收器

先查看支持的数据源和模态：

```bash
./run.sh --list-modalities
```

启动 iPhone 加速度可视化：

```bash
./run.sh --source iphone --modality accelerometer --port 9000
```

随后在手机或手表上点击“开始采集”。程序会同时：

1. 监听 `0.0.0.0:9000` 的 UDP 数据；
2. 显示所选 source/modality；
3. 将电脑实际收到的所有模态保存到 `recordings/sensor-<时间>.ndjson`。

关闭图表窗口或按 `Control-C` 停止。只接收、不显示图表：

```bash
./run.sh --source any --modality capabilities --no-gui
```

指定保存文件和运行时长：

```bash
./run.sh --source apple_watch --modality device_motion \
  --output recordings/trial-001.ndjson --duration 60 --no-gui
```

相关实现：`receiver_visualizer.py` 中的 `UDPReceiver` 负责网络与落盘，`main()` 负责参数和运行模式。

## 2. 数据可视化

一次只显示一个设备来源和一个模态，通过 `--source` 与 `--modality` 选择：

```bash
# iPhone 融合运动
./run.sh --source iphone --modality device_motion

# Apple Watch 心率
./run.sh --source apple_watch --modality heart_rate

# Apple Watch 与 iPhone 的 UWB 距离
./run.sh --source apple_watch --modality uwb_ranging

# AirPods 融合头部运动
./run.sh --source airpods --modality head_motion

# 手机或手表的实时音量统计（不是原始波形）
./run.sh --source iphone --modality audio_level
```

常用模态包括：

- IMU/姿态：`accelerometer`、`gyroscope`、`magnetometer`、`device_motion`、`head_motion`
- 音频遥测：`audio_level`、`audio_start`、`audio_stop`
- 环境/位置：`barometer`、`absolute_altitude`、`location`、`heading`
- 近距/视觉：`uwb_ranging`、`body_skeleton`
- Watch 健康/运动：`heart_rate`、`active_energy`、`workout_distance`、`workout_steps` 等
- 状态：`battery`、`capabilities`、`raw_motion_status`、`control_event`

`--window 20` 可将图表时间窗设为最近 20 秒。UWB 距离图默认固定为 0–4 m，音频电平也使用固定
纵轴，便于不同实验之间比较。图表顶部的 `Dropped` 根据同一 session/source/sensor 的
`sequenceNumber` 缺口统计可确认的 UDP 丢包。

可视化字段、颜色和纵轴设置集中定义在 `receiver_visualizer.py` 的 `MODALITIES`、
`FIXED_Y_LIMITS` 和 `AUDIO_Y_LIMITS` 中，增加新模态时在这些映射中补充字段即可。

## 3. 本地数据与后处理

### 3.1 原始会话结构

从 iPhone 文件共享导出的完整会话通常为：

```text
yyyy-MM-dd_HH-mm-ss_<session前8位>/
├── session-info.json
├── iphone-events-<sessionID>.ndjson
├── iphone-audio-<sessionID>.wav
├── apple_watch-events-<sessionID>.ndjson
└── apple_watch-audio-<sessionID>.wav
```

NDJSON 每行是一个独立 JSON 事件，包含 `sessionID`、`source`、`sensor`、`sequenceNumber`、
`timestampUnixNs`、`timestampMonotonicS` 和 `values`。WAV 为 16 kHz、单声道、16-bit PCM；
`audio_start.timestampUnixNs` 是 WAV 第 0 个采样点的时间锚点。

详细字段语义、帧率、质量检查及原始/融合信号的区别见 `DATA_PROTOCOL.md`。

### 3.2 读取 NDJSON

```python
import json
from pathlib import Path

events = []
path = Path("recordings/<session>/iphone-events-<sessionID>.ndjson")
with path.open(encoding="utf-8") as handle:
    for line in handle:
        event = json.loads(line)
        if event["source"] == "iphone" and event["sensor"] == "accelerometer":
            events.append((event["timestampUnixNs"], event["values"]))
```

不要直接拼接 iPhone 与 Watch 的 NDJSON：iPhone 文件可能包含 Watch 回传事件副本。应按
`sessionID/source/sensor/sequenceNumber` 识别重复数据。用于严格离线分析时，应优先使用设备导出的
会话文件，而不是可能丢包的电脑 UDP 录制。

### 3.3 通用后处理原则

本仓库保留原始数据和时间锚点，不预设特定模型格式。推荐的通用处理顺序是：

1. 根据 `sessionID` 选择同一次会话；
2. 检查 `session-info.json` 状态和五个预期文件；
3. 按 `source/sensor/sequenceNumber` 检查缺帧并去除重复事件；
4. 使用 `timestampUnixNs` 建立跨设备公共时间轴；
5. 使用对应 source 的 `audio_start.timestampUnixNs` 定位 WAV 第 0 个采样点；
6. 根据具体任务确定共同时间区间、目标频率、插值和滤波方法；
7. 将处理结果写入新目录，并保留原始文件、参数与 session ID。

不要混淆独立 raw 流和 Apple 融合结果：`accelerometer`、`gyroscope`、`magnetometer` 是独立传感器
回调；`device_motion` 和 `head_motion` 是融合输出。某模态是否真实可用，应同时检查事件数量及
`raw_motion_status`，不能只看设备是否含有相关硬件。具体字段和检查规则见 `DATA_PROTOCOL.md`。

### 3.4 制作可移交数据包

```bash
./make_handoff_bundle.sh \
  --session-dir recordings/<session> \
  --output exports/sensor_read_handoff_<session>.tar.gz
```

压缩包包含选定原始会话、数据协议、说明文件和 `SHA256SUMS`。接收方可运行：

```bash
shasum -a 256 -c SHA256SUMS
```

确认传输前后文件一致。`make_handoff_bundle.sh` 只复制原始文件，所有对齐和转换结果应写到新目录，
不要覆盖原始会话。

## 数据安全

`recordings/`、WAV、NDJSON、派生文件和交接压缩包默认被 `.gitignore` 排除。原始音频、位置、健康数据
可能包含敏感个人信息，上传或共享前应取得受试者授权并进行必要的去标识化处理。
