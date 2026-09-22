#!/usr/bin/env python3
"""Receive Sensor Read UDP events, save NDJSON, and visualize one modality."""

import argparse
import json
import math
import queue
import socket
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple


PlotGroup = Tuple[str, str, Sequence[Tuple[str, str]]]

# title, unit, [(JSON field, display label)]
MODALITIES: Dict[str, Sequence[PlotGroup]] = {
    "accelerometer": (("Acceleration", "g", (("x_g", "X"), ("y_g", "Y"), ("z_g", "Z"))),),
    "gyroscope": (("Angular velocity", "rad/s", (("x_rad_s", "X"), ("y_rad_s", "Y"), ("z_rad_s", "Z"))),),
    "magnetometer": (("Magnetic field", "µT", (("x_uT", "X"), ("y_uT", "Y"), ("z_uT", "Z"))),),
    "device_motion": (
        ("Attitude", "rad", (("attitude_roll", "Roll"), ("attitude_pitch", "Pitch"), ("attitude_yaw", "Yaw"))),
        ("Quaternion", "", (("quaternion_x", "X"), ("quaternion_y", "Y"), ("quaternion_z", "Z"), ("quaternion_w", "W"))),
        ("Gravity", "g", (("gravity_x", "X"), ("gravity_y", "Y"), ("gravity_z", "Z"))),
        ("User acceleration", "g", (("user_accel_x_g", "X"), ("user_accel_y_g", "Y"), ("user_accel_z_g", "Z"))),
        ("Rotation rate", "rad/s", (("rotation_x_rad_s", "X"), ("rotation_y_rad_s", "Y"), ("rotation_z_rad_s", "Z"))),
        ("Magnetic field", "µT", (("magnetic_x_uT", "X"), ("magnetic_y_uT", "Y"), ("magnetic_z_uT", "Z"))),
        ("Rotation matrix", "", tuple((f"rotation_m{row}{column}", f"M{row}{column}") for row in range(1, 4) for column in range(1, 4))),
    ),
    "head_motion": (
        ("Head attitude", "rad", (("attitude_roll", "Roll"), ("attitude_pitch", "Pitch"), ("attitude_yaw", "Yaw"))),
        ("Head quaternion", "", (("quaternion_x", "X"), ("quaternion_y", "Y"), ("quaternion_z", "Z"), ("quaternion_w", "W"))),
        ("Head gravity", "g", (("gravity_x", "X"), ("gravity_y", "Y"), ("gravity_z", "Z"))),
        ("Head acceleration", "g", (("user_accel_x_g", "X"), ("user_accel_y_g", "Y"), ("user_accel_z_g", "Z"))),
        ("Head rotation rate", "rad/s", (("rotation_x_rad_s", "X"), ("rotation_y_rad_s", "Y"), ("rotation_z_rad_s", "Z"))),
    ),
    "barometer": (
        ("Relative altitude", "m", (("relative_altitude_m", "Altitude"),)),
        ("Air pressure", "kPa", (("pressure_kPa", "Pressure"),)),
    ),
    "location": (
        ("Coordinates", "degree", (("latitude_deg", "Latitude"), ("longitude_deg", "Longitude"))),
        ("Altitude", "m", (("altitude_m", "MSL"), ("ellipsoidal_altitude_m", "WGS84"))),
        ("Movement", "", (("speed_m_s", "Speed (m/s)"), ("course_deg", "Course (deg)"))),
        ("Accuracy", "m", (("horizontal_accuracy_m", "Horizontal"), ("vertical_accuracy_m", "Vertical"))),
    ),
    "absolute_altitude": (
        ("Absolute altitude", "m", (("altitude_m", "Altitude"),)),
        ("Altitude quality", "m", (("accuracy_m", "Accuracy"), ("precision_m", "Precision"))),
    ),
    "pedometer": (
        ("Steps", "count", (("steps", "Steps"),)),
        ("Distance", "m", (("distance_m", "Distance"),)),
        ("Cadence", "steps/s", (("cadence_steps_s", "Cadence"),)),
        ("Pace", "s/m", (("current_pace_s_m", "Current"), ("average_active_pace_s_m", "Average"))),
        ("Floors", "count", (("floors_ascended", "Ascended"), ("floors_descended", "Descended"))),
    ),
    "motion_activity": (("Activity classification", "0/1", (("stationary", "Stationary"), ("walking", "Walking"), ("running", "Running"), ("cycling", "Cycling"), ("automotive", "Automotive"), ("unknown", "Unknown"))),),
    "headphone_activity": (("Headphone activity", "0/1", (("stationary", "Stationary"), ("walking", "Walking"), ("running", "Running"), ("cycling", "Cycling"), ("automotive", "Automotive"), ("unknown", "Unknown"))),),
    "heading": (
        ("Compass heading", "degree", (("magnetic_heading_deg", "Magnetic"), ("true_heading_deg", "True"))),
        ("Raw magnetic vector", "µT", (("raw_x_uT", "X"), ("raw_y_uT", "Y"), ("raw_z_uT", "Z"))),
    ),
    "uwb_ranging": (("Phone–Watch distance", "m", (("distance_m", "Distance"),)),),
    "uwb_status": (
        ("UWB handshake stage", "code", (("stage", "Stage"),)),
        ("UWB handshake attempts", "count", (("attempt", "Attempt"),)),
        ("Connectivity", "0/1", (("watch_reachable", "Watch"), ("phone_reachable", "Phone"))),
        ("UWB errors", "code", (("error_code", "Error"), ("reason", "Removal"))),
    ),
    "body_skeleton": (
        ("Body root", "m", (("root_x_m", "X"), ("root_y_m", "Y"), ("root_z_m", "Z"))),
        ("Tracking state", "0/1", (("is_tracked", "Tracked"),)),
    ),
    "proximity": (("Screen proximity", "0/1", (("is_near", "Near"),)),),
    "device_orientation": (("Device orientation", "code", (("orientation", "Orientation"),)),),
    "headphone_status": (("Headphone status", "code", (("status", "Status"),)),),
    "audio_level": (
        ("Audio level", "dBFS", (("rms_dbfs", "RMS"), ("peak_dbfs", "Peak"))),
        ("Audio amplitude", "linear", (("linear_rms", "RMS"),)),
    ),
    "audio_start": (
        ("Audio format", "", (("sample_rate_hz", "Sample rate"), ("channels", "Channels"),
                                ("bits_per_sample", "Bits"))),
        ("Start synchronization", "ms", (("schedule_lateness_ms", "Lateness"),)),
    ),
    "audio_stop": (
        ("Audio duration", "s", (("duration_s", "Duration"),)),
        ("Audio samples", "count", (("sample_count", "Samples"),)),
    ),
    "control_event": (
        ("Watch system control", "0/1", (("action_start", "Start"), ("action_stop", "Stop"),
                                          ("accepted", "Accepted"),
                                          ("phone_was_recording", "Was recording"))),
    ),
    "capabilities": (("Runtime capabilities", "0/1", (("accelerometer", "Accel"), ("gyroscope", "Gyro"), ("magnetometer", "Mag"), ("device_motion", "Motion"), ("pedometer", "Steps"), ("uwb_precise_distance", "UWB"))),),
    "heart_rate": (("Heart rate", "bpm", (("bpm", "BPM"),)),),
    "active_energy": (("Active energy", "kcal", (("kilocalories", "Energy"),)),),
    "workout_distance": (("Workout distance", "m", (("distance_m", "Distance"),)),),
    "workout_steps": (("Workout steps", "count", (("steps", "Steps"),)),),
    "walking_speed": (("Walking speed", "m/s", (("speed_m_s", "Speed"),)),),
    "walking_step_length": (("Walking step length", "m", (("length_m", "Length"),)),),
    "walking_asymmetry": (("Walking asymmetry", "%", (("percent", "Asymmetry"),)),),
    "walking_double_support": (("Double support", "%", (("percent", "Support"),)),),
    "stair_ascent_speed": (("Stair ascent speed", "m/s", (("speed_m_s", "Speed"),)),),
    "stair_descent_speed": (("Stair descent speed", "m/s", (("speed_m_s", "Speed"),)),),
    "running_speed": (("Running speed", "m/s", (("speed_m_s", "Speed"),)),),
    "running_power": (("Running power", "W", (("watts", "Power"),)),),
    "running_vertical_oscillation": (("Vertical oscillation", "m", (("distance_m", "Oscillation"),)),),
    "running_ground_contact": (("Ground contact", "s", (("duration_s", "Duration"),)),),
    "running_stride_length": (("Running stride", "m", (("length_m", "Length"),)),),
    "battery": (
        ("Battery level", "0–1", (("level", "Level"),)),
        ("Battery state", "code", (("state", "State"),)),
    ),
}

SOURCE_CHOICES = ("iphone", "apple_watch", "airpods", "any")
COLORS = ("#55D6BE", "#7C83FD", "#FFCB77", "#FF6B9D", "#6EE7FF", "#A7F3D0")
BACKGROUND = "#0B1020"
CARD = "#141B2D"
TEXT = "#E8EDF7"
MUTED = "#8E9AAF"
GRID = "#34405A"

# Keep UWB plots visually comparable over time instead of rescaling around
# every small distance change.
FIXED_Y_LIMITS: Dict[str, Tuple[float, float]] = {
    "uwb_ranging": (0.0, 4.0),
}

AUDIO_Y_LIMITS: Dict[str, Tuple[float, float]] = {
    "rms_dbfs": (-80.0, 0.0),
    "peak_dbfs": (-80.0, 0.0),
    "linear_rms": (0.0, 1.0),
}


def source_matches(selected: str, actual: str) -> bool:
    if selected == "any":
        return True
    # Simulator sources deliberately match the corresponding physical source.
    return actual.removesuffix("_simulator") == selected


class UDPReceiver:
    def __init__(self, host: str, port: int, output: Path) -> None:
        self.host = host
        self.port = port
        self.output = output
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.errors: "queue.Queue[str]" = queue.Queue()
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, name="udp-receiver", daemon=True)
        self.thread.start()
        self.ready.wait(timeout=2.0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.bind((self.host, self.port))
            sock.settimeout(0.25)
        except OSError as error:
            self.errors.put(f"Cannot listen on UDP {self.host}:{self.port}: {error}")
            self.ready.set()
            return

        self.ready.set()
        try:
            with self.output.open("ab") as output:
                last_flush = time.monotonic()
                while not self.stop_event.is_set():
                    try:
                        packet, sender = sock.recvfrom(65535)
                    except socket.timeout:
                        continue
                    except OSError as error:
                        self.errors.put(f"UDP receive error: {error}")
                        break

                    for raw_line in packet.splitlines():
                        try:
                            event = json.loads(raw_line)
                            if not isinstance(event, dict) or not isinstance(event.get("values"), dict):
                                raise ValueError("not a Sensor Read event")
                        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                            self.errors.put(f"Invalid packet from {sender[0]}: {error}")
                            continue
                        output.write(raw_line + b"\n")
                        if time.monotonic() - last_flush >= 0.5:
                            output.flush()
                            last_flush = time.monotonic()
                        event["_sender"] = sender[0]
                        event["_received_at"] = time.time()
                        self.events.put(event)
        finally:
            sock.close()


class LiveDashboard:
    def __init__(self, receiver: UDPReceiver, source: str, modality: str, window_seconds: float) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation

        self.plt = plt
        self.receiver = receiver
        self.source = source
        self.modality = modality
        self.groups = MODALITIES[modality]
        self.window_seconds = window_seconds
        self.series: Dict[str, Tuple[Deque[float], Deque[float]]] = {}
        self.lines: Dict[str, Any] = {}
        self.field_axes: Dict[str, Any] = {}
        self.first_timestamp: Optional[float] = None
        self.last_event_time: Optional[float] = None
        self.rate_times: Deque[float] = deque()
        self.received_total = 0
        self.matched_total = 0
        self.dropped_total = 0
        self.out_of_order_total = 0
        self.last_sequences: Dict[Tuple[str, str, str], int] = {}
        self.latest_values: Dict[str, float] = {}
        self.actual_source = "—"
        self.session_id = "—"
        self.last_error = ""

        plt.style.use("dark_background")
        columns = 1 if len(self.groups) == 1 else 2
        rows = math.ceil(len(self.groups) / columns)
        self.figure, axis_grid = plt.subplots(
            rows,
            columns,
            figsize=(14, 7.8),
            squeeze=False,
            facecolor=BACKGROUND,
        )
        self.axes = list(axis_grid.flat)
        for unused in self.axes[len(self.groups):]:
            unused.set_visible(False)

        for index, (axis, (title, unit, fields)) in enumerate(zip(self.axes, self.groups)):
            axis.set_facecolor(CARD)
            axis.set_title(title, color=TEXT, fontsize=12, loc="left", pad=12, weight="semibold")
            axis.set_xlabel("Time (s)", color=MUTED)
            axis.set_ylabel(unit, color=MUTED)
            axis.tick_params(colors=MUTED, labelsize=9)
            axis.grid(True, color=GRID, alpha=0.38, linewidth=0.7)
            for spine in axis.spines.values():
                spine.set_color(GRID)
            for field_index, (field, label) in enumerate(fields):
                self.series[field] = (deque(maxlen=30000), deque(maxlen=30000))
                self.field_axes[field] = axis
                line, = axis.plot([], [], color=COLORS[field_index % len(COLORS)], linewidth=1.8, label=label)
                self.lines[field] = line
            axis.legend(loc="upper left", frameon=False, labelcolor=TEXT, ncol=min(3, len(fields)))
            if modality in FIXED_Y_LIMITS:
                axis.set_ylim(*FIXED_Y_LIMITS[modality])
            elif modality == "audio_level":
                first_field = fields[0][0]
                axis.set_ylim(*AUDIO_Y_LIMITS[first_field])

        self.figure.canvas.manager.set_window_title(f"Sensor Read · {source}/{modality}")
        self.figure.suptitle(
            "SENSOR READ  ·  LIVE TELEMETRY",
            x=0.055,
            y=0.975,
            ha="left",
            fontsize=15,
            color=TEXT,
            weight="bold",
        )
        self.filter_text = self.figure.text(
            0.055, 0.93, f"STREAM  {source.upper()} / {modality.upper()}   ·   UDP {receiver.host}:{receiver.port}",
            color="#6EE7FF", fontsize=10, weight="semibold",
        )
        self.connection_text = self.figure.text(0.55, 0.965, "● WAITING", color="#FFCB77", fontsize=10, weight="bold")
        self.rate_text = self.figure.text(0.67, 0.965, "0.0 events/s", color=TEXT, fontsize=10)
        self.count_text = self.figure.text(0.79, 0.965, "0 matched", color=TEXT, fontsize=10)
        self.loss_text = self.figure.text(0.90, 0.965, "0 dropped", color=TEXT, fontsize=10)
        self.latest_text = self.figure.text(0.055, 0.025, "Waiting for the selected stream…", color=MUTED, fontsize=9)
        self.file_text = self.figure.text(0.99, 0.025, str(receiver.output), ha="right", color=MUTED, fontsize=8)
        self.figure.subplots_adjust(left=0.07, right=0.97, top=0.87, bottom=0.10, hspace=0.34, wspace=0.20)
        self.animation = FuncAnimation(self.figure, self._update, interval=80, cache_frame_data=False)

    @staticmethod
    def _event_time(event: Dict[str, Any]) -> float:
        timestamp_ns = event.get("timestampUnixNs")
        if isinstance(timestamp_ns, (int, float)) and timestamp_ns > 0:
            return float(timestamp_ns) / 1_000_000_000.0
        return float(event.get("_received_at", time.time()))

    def _consume(self, event: Dict[str, Any]) -> None:
        self.received_total += 1
        actual_source = str(event.get("source", "unknown"))
        if event.get("sensor") != self.modality or not source_matches(self.source, actual_source):
            return

        timestamp = self._event_time(event)
        if self.first_timestamp is None:
            self.first_timestamp = timestamp
        relative_time = timestamp - self.first_timestamp
        received_at = float(event.get("_received_at", time.time()))
        self.last_event_time = received_at
        self.rate_times.append(received_at)
        self.matched_total += 1
        self.actual_source = actual_source
        self.session_id = str(event.get("sessionID", "—"))[:8]
        sequence = event.get("sequenceNumber")
        if isinstance(sequence, int):
            sequence_key = (str(event.get("sessionID", "")), actual_source, self.modality)
            previous = self.last_sequences.get(sequence_key)
            if previous is not None:
                if sequence > previous + 1:
                    self.dropped_total += sequence - previous - 1
                elif sequence <= previous:
                    self.out_of_order_total += 1
            self.last_sequences[sequence_key] = max(sequence, previous if previous is not None else sequence)
        values = event.get("values", {})
        self.latest_values = values
        for field, value in values.items():
            if field in self.series and isinstance(value, (int, float)):
                xs, ys = self.series[field]
                xs.append(relative_time)
                ys.append(float(value))

    def _update(self, _frame: int) -> None:
        while True:
            try:
                self._consume(self.receiver.events.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                self.last_error = self.receiver.errors.get_nowait()
            except queue.Empty:
                break

        now = time.time()
        while self.rate_times and self.rate_times[0] < now - 2.0:
            self.rate_times.popleft()
        latest_x = max((xs[-1] for xs, _ in self.series.values() if xs), default=0.0)
        start_x = max(0.0, latest_x - self.window_seconds)

        for field, (xs, ys) in self.series.items():
            if xs:
                first_visible = next((i for i, x in enumerate(xs) if x >= start_x), 0)
                self.lines[field].set_data(list(xs)[first_visible:], list(ys)[first_visible:])
        for axis in self.axes[:len(self.groups)]:
            axis.relim()
            fixed_scale = self.modality in FIXED_Y_LIMITS or self.modality == "audio_level"
            axis.autoscale_view(scalex=False, scaley=not fixed_scale)
            axis.set_xlim(start_x, max(self.window_seconds, latest_x))
            if self.modality in FIXED_Y_LIMITS:
                axis.set_ylim(*FIXED_Y_LIMITS[self.modality])
        if self.modality == "audio_level":
            for axis, (_, _, fields) in zip(self.axes, self.groups):
                axis.set_ylim(*AUDIO_Y_LIMITS[fields[0][0]])

        age = None if self.last_event_time is None else now - self.last_event_time
        if self.last_error:
            state, color = "● ERROR", "#FF6B6B"
        elif age is None:
            state, color = "● WAITING", "#FFCB77"
        elif age < 1.0:
            state, color = "● LIVE", "#55D6BE"
        else:
            state, color = "● STALE", "#FFCB77"
        self.connection_text.set_text(state)
        self.connection_text.set_color(color)
        self.rate_text.set_text(f"{len(self.rate_times) / 2.0:.1f} events/s")
        self.count_text.set_text(f"{self.matched_total:,} matched")
        self.loss_text.set_text(f"{self.dropped_total:,} dropped")
        self.loss_text.set_color("#FF6B6B" if self.dropped_total else TEXT)
        if self.latest_values:
            values = "   ".join(
                f"{name} {value:.4g}" for name, value in list(self.latest_values.items())[:8]
            )
            self.latest_text.set_text(
                f"ACTUAL SOURCE {self.actual_source}   ·   SESSION {self.session_id}   ·   {values}"
            )
        elif self.last_error:
            self.latest_text.set_text(self.last_error)

    def show(self) -> None:
        try:
            self.plt.show()
        finally:
            self.receiver.stop()


def default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parent / "recordings" / f"sensor-{stamp}.ndjson"


def print_modalities() -> None:
    print("可选数据源: " + ", ".join(SOURCE_CHOICES))
    print("可选模态:")
    for name in MODALITIES:
        print(f"  {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sensor Read 单模态 UDP 实时可视化")
    parser.add_argument("--source", choices=SOURCE_CHOICES, default="iphone", help="要显示的数据源")
    parser.add_argument("--modality", choices=tuple(MODALITIES), help="要显示的单个传感器模态")
    parser.add_argument("--list-modalities", action="store_true", help="列出可选数据源和模态")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认监听所有网卡")
    parser.add_argument("--port", type=int, default=9000, help="UDP 端口，默认 9000")
    parser.add_argument("--output", type=Path, default=None, help="完整 NDJSON 保存路径")
    parser.add_argument("--window", type=float, default=10.0, help="曲线显示最近多少秒")
    parser.add_argument("--no-gui", action="store_true", help="只接收和打印所选模态")
    parser.add_argument("--duration", type=float, default=None, help="无界面模式运行秒数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_modalities:
        print_modalities()
        return
    if args.modality is None:
        raise SystemExit("请用 --modality 指定一个模态；运行 --list-modalities 查看选项。")
    if not 1 <= args.port <= 65535:
        raise SystemExit("端口必须在 1 到 65535 之间")

    output = args.output or default_output()
    receiver = UDPReceiver(args.host, args.port, output.resolve())
    receiver.start()
    print(f"正在监听 udp://{args.host}:{args.port}")
    print(f"正在显示 {args.source}/{args.modality}")
    print(f"完整原始数据保存至 {receiver.output}")

    if not args.no_gui:
        LiveDashboard(receiver, args.source, args.modality, max(1.0, args.window)).show()
        return

    started = time.monotonic()
    matched = 0
    try:
        while args.duration is None or time.monotonic() - started < args.duration:
            try:
                event = receiver.events.get(timeout=0.2)
                if event.get("sensor") == args.modality and source_matches(args.source, str(event.get("source", ""))):
                    matched += 1
                    print(f"{event.get('source')}/{event.get('sensor')}: {event.get('values')}")
            except queue.Empty:
                pass
            while not receiver.errors.empty():
                print(receiver.errors.get())
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
        print(f"已停止，共匹配 {matched} 个事件")


if __name__ == "__main__":
    main()
