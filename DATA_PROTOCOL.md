# Sensor Read 原始数据协议与采集现状

版本：`sensor-read-data-protocol-v1`  
适用工程：Sensor Read iOS/watchOS 多模态采集器  
目标：让没有当前对话上下文的分析环境能够理解原始数据的来源、文件组织、字段含义、时间戳语义、
采样情况和当前已知限制。本文只描述采集端直接保存的数据，不包含对齐、重采样、滤波、特征提取或
任何模型适配流程。

## 1. 采集架构

一次采集由 iPhone 创建唯一 `sessionID`（UUID）和会话目录：

```text
yyyy-MM-dd_HH-mm-ss_<sessionID 前 8 位>/
```

iPhone 负责：

1. 创建会话目录与 `session-info.json`；
2. 采集 iPhone IMU、姿态、位置、气压、UWB、AirPods 等数据；
3. 录制 iPhone 麦克风 WAV；
4. 通过 WatchConnectivity 给 Apple Watch 下发相同的会话 ID、目录名和音频计划起点；
5. 接收 Watch 的事件文件和 WAV，并放入同一个会话目录。

Apple Watch 负责：

1. 在本地采集 Watch IMU、姿态、位置、UWB、心率/步态等数据；
2. 录制 Watch 麦克风 WAV；
3. 将 Watch 事件 NDJSON 和 WAV 可靠传回 iPhone。

电脑端 UDP 接收器只用于实时遥测和可视化。原始音频不通过 UDP 传输；UDP 可能丢包，离线分析应以设备本地会话文件为准。

## 2. 会话目录中的文件

完整会话通常包含：

```text
session-info.json
iphone-events-<sessionID>.ndjson
iphone-audio-<sessionID>.wav
apple_watch-events-<sessionID>.ndjson
apple_watch-audio-<sessionID>.wav
```

`session-info.json` 是会话清单，包含 `sessionID`、目录名、开始/结束时间、计划音频起点、持续时间、
预期文件名和状态。文件名中的完整 UUID 应保持不变，以便可靠配对同一次会话的事件和音频。

## 3. NDJSON 事件格式

每个 `.ndjson` 文件是一行一个 JSON 对象，不是一个包裹数组。典型事件：

```json
{
  "schemaVersion": 2,
  "sessionID": "19C7D74C-D154-462A-B07F-D865F52F8AEF",
  "source": "apple_watch",
  "sensor": "accelerometer",
  "sequenceNumber": 1234,
  "timestampUnixNs": 1789634101234567890,
  "timestampMonotonicS": 12345.678,
  "values": {
    "x_g": 0.01,
    "y_g": -0.02,
    "z_g": 0.98
  }
}
```

字段语义：

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` | 当前为 2 |
| `sessionID` | 会话 UUID；用于过滤和配对 |
| `source` | `iphone`、`apple_watch` 或 `airpods` |
| `sensor` | 模态名称 |
| `sequenceNumber` | 同一 source/sensor 内从 0 递增的事件序号 |
| `timestampUnixNs` | Unix wall-clock 时间，纳秒；用于跨设备/音频对齐 |
| `timestampMonotonicS` | 设备单调时钟秒数；只能在同一设备内比较 |
| `values` | 该模态的数值字典，数值被编码为 JSON number |

文件中可能出现的模态：

- IMU：`accelerometer`（g）、`gyroscope`（rad/s）、`magnetometer`（µT）
- 融合运动：`device_motion`、AirPods `head_motion`
- 音频遥测：`audio_start`、`audio_level`、`audio_stop`
- 环境/位置：`barometer`、`absolute_altitude`、`location`、`heading`
- 行为统计：`pedometer`、`motion_activity`
- 近距/视觉：`uwb_ranging`、`uwb_status`、可选 `body_skeleton`
- Watch 生理/步态：`heart_rate`、`active_energy`、`workout_distance`、`workout_steps`、步行/跑步指标
- 状态/控制：`battery`、`capabilities`、`proximity`、`device_orientation`、`headphone_status`、`control_event`、`recording_end`
- 原始 IMU 诊断：`raw_motion_status`（iPhone/Watch 均会定期记录 raw 服务可用性、激活状态、样本计数和启动重试次数）

### 原始与派生的边界

- `accelerometer`、`gyroscope`、`magnetometer` 是 Core Motion 回调的直接数值，应用没有做滤波、插值或归一化。
- `device_motion` 和 `head_motion` 是 Apple 已融合的姿态/运动结果，不是传感器芯片级原始寄存器值。
- `pedometer`、`motion_activity`、HealthKit 步态、UWB 和 ARKit 骨架是 Apple API 的统计/估计/融合结果。
- `audio_level` 是 RMS/峰值等音量统计，不能作为原始音频输入。

## 4. WAV 音频格式与时间语义

`iphone-audio-*.wav` 和 `apple_watch-audio-*.wav` 为：

- 单声道（1 channel）
- 16,000 Hz
- 16-bit little-endian PCM
- 标准 WAV 文件头 + PCM 采样点

WAV 文件没有为每个采样点存储 Unix 时间戳。`audio_start` 事件中的 `timestampUnixNs` 表示 WAV 第 0 个采样点的时间。`audio_stop` 记录时长和样本数。

iPhone 在会话开始后约 1.5 秒计划两端音频同时启动，并把同一个绝对 Unix 计划时间发送给 Watch；实际起点和延迟仍需以各自 `audio_start` 为准。这是采集时的启动同步，不是后处理后的精确对齐。

## 5. 时间戳、帧率与对齐原则

原始会话文件不会：

- 把手机和手表合并成单一时间序列；
- 把不同模态重采样到同一频率；
- 对音频和 IMU 裁剪到共同区间；
- 执行滤波、归一化或 Mel 特征提取。

应用只保存每个事件自己的时间戳，并使用共享 `sessionID` 和音频计划起点帮助后续处理。各模态帧率不同：iPhone IMU 目标约 100 Hz，Watch IMU 目标约 50 Hz，音频为 16 kHz，`audio_level` 约 20 Hz，UWB/HealthKit/位置等由系统自适应。

Watch 本地文件保留高频事件；通过 WatchConnectivity 传给 iPhone 时可能以约 0.1 秒批次发送，但这不会改变文件中事件的原始采样时间戳。

## 6. 最新一次原始会话实测情况

截至 2026-09-17，电脑端最新完整导出的会话是：

```text
recordings/2026-09-17_17-44-45_BAA1F041/
sessionID: BAA1F041-F832-4CC7-929C-3DE9E4B7408D
会话状态: stopped
会话清单时长: 38.847 s
```

目录中的五个预期文件均存在：`session-info.json`、iPhone/Watch 两份 NDJSON 和两份 WAV。两份
NDJSON 共检查到 30,312 条可解析事件，没有发现 JSON 解析错误。iPhone NDJSON 还包含 Watch 经
WatchConnectivity 回传的事件副本，所以不能把两份文件简单合并后计数，否则会重复计算 Watch 数据。

### iPhone 实测主要数据

| 数据 | 事件数 | 覆盖时长 | 实测平均频率/格式 | 当前状态 |
| --- | ---: | ---: | --- | --- |
| 原始加速度 `accelerometer` | 3,858 | 38.641 s | 约 99.82 Hz，三轴 g | 完整产生 |
| 原始角速度 `gyroscope` | 3,859 | 38.651 s | 约 99.82 Hz，三轴 rad/s | 完整产生 |
| 原始磁场 `magnetometer` | 1,956 | 38.636 s | 约 50.60 Hz，三轴 µT | 完整产生 |
| 融合运动 `device_motion` | 3,856 | 38.621 s | 约 99.82 Hz，31 个标量字段 | 完整产生 |
| 航向 `heading` | 1,928 | 38.610 s | 约 49.91 Hz | 完整产生 |
| UWB 距离 `uwb_ranging` | 250 | 37.343 s | 约 6.67 Hz，仅距离 | 完整产生 |
| 音频文件 | 595,798 PCM 帧 | 37.237 s | 16 kHz、单声道、16-bit | WAV 可读取 |

iPhone 的最终 `raw_motion_status` 显示原始加速度计、陀螺仪和磁力计均为 available/active，且三者
样本计数均大于 0。因此本次 iPhone 原始三轴加速度、角速度和磁场数据确实齐全。

### Apple Watch 实测主要数据

| 数据 | 事件数 | 覆盖时长 | 实测平均频率/格式 | 当前状态 |
| --- | ---: | ---: | --- | --- |
| 原始加速度 `accelerometer` | 1,895 | 37.771 s | 约 50.14 Hz，三轴 g | 完整产生 |
| 原始角速度 `gyroscope` | 0 | — | 目标 50 Hz | 本次系统未开放独立 raw 流 |
| 原始磁场 `magnetometer` | 0 | — | 目标 25 Hz | 本次系统未开放独立 raw 流 |
| 融合运动 `device_motion` | 1,894 | 37.751 s | 约 50.14 Hz，31 个标量字段 | 完整产生 |
| 航向 `heading` | 1,893 | 37.471 s | 约 50.49 Hz | 完整产生 |
| UWB 距离 `uwb_ranging` | 249 | 37.196 s | 约 6.67 Hz，仅距离 | 完整产生 |
| 心率 `heart_rate` | 6 | 24.525 s | 非固定频率 | 有数据 |
| 音频文件 | 586,649 PCM 帧 | 36.666 s | 16 kHz、单声道、16-bit | WAV 可读取 |

Watch 的最终 `raw_motion_status` 显示：独立加速度计 available/active 且持续产生样本；独立陀螺仪和
磁力计均为 unavailable/inactive，重试 8 次后样本数仍为 0。因此这不是电脑端 UDP 丢包，也不是文件
传输漏写，而是该设备/系统在本次会话中没有通过公开 Core Motion raw API 提供这两条独立数据流。

Watch 的 `device_motion` 仍包含 `rotation_x/y/z_rad_s`，也包含重力、用户加速度、姿态、四元数、
旋转矩阵和校准磁场；`heading` 也包含磁场相关字段。这些数据已经保存，但属于 Apple 融合或校准后的
输出，必须与独立的 raw `gyroscope`/`magnetometer` 区分，不应混称为芯片级原始流。

### 音频与同步现状

- 两端 WAV 均可按标准 PCM WAV 读取。实际音频帧数与 NDJSON 中 `audio_stop.sample_count` 存在少量
  差异，因此离线读取时应以 WAV 文件头中的实际帧数为准，并保留 `audio_stop` 作为采集日志。
- 两端 `audio_start` 记录了相同的计划起点和实际起点；这是采集端记录的时间锚点，不代表数据已经
  后处理对齐。
- iPhone WAV 比 Watch WAV 长约 0.572 秒。原始文件没有自动裁剪到共同时间区间。
- 本次没有 AirPods `head_motion` 数据；仅记录到 `headphone_diagnostic`，因此不能把该会话视为包含
  AirPods 运动数据的三设备完整序列。

## 7. 原始数据质量检查建议

在使用数据前检查：

1. `session-info.json` 的 `state` 是否为 `stopped`；
2. 四个预期文件是否都存在；
3. WAV 是否为 16 kHz、单声道、16-bit；
4. NDJSON 是否每行可解析，且 `sessionID` 与目录一致；
5. 各 source/sensor 的 `sequenceNumber` 是否有缺口；
6. `audio_start`、IMU 首尾时间与 WAV 时长是否存在重叠；以 WAV 文件头帧数作为实际音频长度；
7. 若使用电脑端 UDP 文件，检查丢包后再决定是否回到设备本地文件。
8. 分别检查 `raw_motion_status` 中三个 raw 传感器的 available、active 和 samples；不要仅凭设备含有
   对应硬件就假定公开 raw 流一定存在。
9. 不要直接拼接 iPhone 与 Watch NDJSON；iPhone 文件可能已经包含 Watch 回传事件，应按
   `source`、`sensor`、`sequenceNumber` 和时间戳识别重复数据，或分别读取。

## 8. 原始数据交接包结构与恢复

`make_handoff_bundle.sh` 会生成如下压缩包：

```text
sensor_read_handoff_<会话>.tar.gz
├── DATA_PROTOCOL.md
├── BUNDLE_README.md
├── SHA256SUMS
└── raw_session/<会话目录>/...
```

接收电脑上：

```bash
tar -xzf sensor_read_handoff_<会话>.tar.gz
cd sensor_read_handoff_<会话>
shasum -a 256 -c SHA256SUMS
```

校验通过后，应先按第 7 节检查文件完整性、事件可解析性、raw 传感器状态和音频格式，再决定具体的
离线处理方式。交接包中的原始会话文件应保持不变；任何对齐、转换或特征文件应输出到新目录，并记录
所使用的原始 `sessionID`。
