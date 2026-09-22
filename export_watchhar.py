#!/usr/bin/env python3
"""Align Sensor Read WAV + IMU events and export WatchHAR raw pickle input."""

import argparse
import json
import pickle
import re
import wave
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


ACC_FIELDS = ("x_g", "y_g", "z_g")
GYRO_FIELDS = ("x_rad_s", "y_rad_s", "z_rad_s")
VALID_COMPONENT = re.compile(r"^[A-Za-z0-9_ -]+$")


def event_time(event: Dict[str, Any]) -> float:
    timestamp_ns = event.get("timestampUnixNs")
    if isinstance(timestamp_ns, (int, float)) and timestamp_ns > 0:
        return float(timestamp_ns) / 1_000_000_000.0
    raise ValueError("event is missing timestampUnixNs")


def load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at line {line_number}: {error}") from error
            if isinstance(item, dict):
                events.append(item)
    if not events:
        raise ValueError(f"no events found in {path}")
    return events


def choose_session(events: Sequence[Dict[str, Any]], source: str,
                   requested: Optional[str]) -> str:
    if requested:
        return requested
    starts = [event for event in events
              if event.get("source") == source and event.get("sensor") == "audio_start"]
    session_ids = list(dict.fromkeys(str(event.get("sessionID", "")) for event in starts
                                    if event.get("sessionID")))
    if len(session_ids) != 1:
        raise ValueError("cannot infer one session; pass --session-id")
    return session_ids[0]


def sensor_rows(events: Iterable[Dict[str, Any]], session_id: str, source: str,
                sensor: str, fields: Sequence[str]) -> Tuple[np.ndarray, np.ndarray]:
    rows = []
    for event in events:
        if (event.get("sessionID") != session_id or event.get("source") != source
                or event.get("sensor") != sensor):
            continue
        values = event.get("values", {})
        if not all(isinstance(values.get(field), (int, float)) for field in fields):
            continue
        rows.append((event_time(event), *(float(values[field]) for field in fields)))
    if len(rows) < 2:
        raise ValueError(f"not enough {source}/{sensor} samples for session {session_id}")
    rows.sort(key=lambda row: row[0])
    array = np.asarray(rows, dtype=np.float64)
    unique_times, unique_indices = np.unique(array[:, 0], return_index=True)
    return unique_times, array[unique_indices, 1:]


def audio_start_time(events: Iterable[Dict[str, Any]], session_id: str, source: str) -> float:
    candidates = [event_time(event) for event in events
                  if event.get("sessionID") == session_id
                  and event.get("source") == source
                  and event.get("sensor") == "audio_start"]
    if not candidates:
        raise ValueError(f"missing {source}/audio_start for session {session_id}")
    return min(candidates)


def load_wav(path: Path) -> Tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {sample_width * 8}-bit")
    audio = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        audio = np.rint(audio.reshape(-1, channels).mean(axis=1)).astype(np.int16)
    return sample_rate, audio.copy()


def find_audio(events_path: Path, session_id: str, source: str) -> Path:
    expected = events_path.parent / f"{source}-audio-{session_id}.wav"
    if expected.exists():
        return expected
    matches = sorted(events_path.parent.glob(f"{source}-audio-{session_id}*.wav"))
    if len(matches) == 1:
        return matches[0]
    raise ValueError("cannot find matching WAV; pass --audio")


def interpolate(times: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    return np.column_stack([np.interp(grid, times, values[:, column])
                            for column in range(values.shape[1])])


def validate_component(name: str, value: str) -> str:
    if not value or not VALID_COMPONENT.fullmatch(value) or "---" in value:
        raise ValueError(f"invalid {name}: {value!r}")
    return value


def export(args: argparse.Namespace) -> Tuple[Path, Path]:
    events = load_events(args.events)
    session_id = choose_session(events, args.source, args.session_id)
    audio_path = args.audio or find_audio(args.events, session_id, args.source)
    sample_rate, audio = load_wav(audio_path)
    if sample_rate != 16_000:
        raise ValueError(f"WatchHAR raw input expects 16 kHz audio; WAV is {sample_rate} Hz")

    acc_times, acc = sensor_rows(events, session_id, args.source, "accelerometer", ACC_FIELDS)
    gyro_times, gyro = sensor_rows(events, session_id, args.source, "gyroscope", GYRO_FIELDS)
    wav_start = audio_start_time(events, session_id, args.source)
    wav_end = wav_start + len(audio) / sample_rate

    overlap_start = max(acc_times[0], gyro_times[0], wav_start)
    overlap_end = min(acc_times[-1], gyro_times[-1], wav_end)
    if overlap_end - overlap_start < 1.0:
        raise ValueError(f"aligned overlap is only {overlap_end - overlap_start:.3f}s")

    imu_rate = 50.0
    sample_count = int(np.floor((overlap_end - overlap_start) * imu_rate))
    grid = overlap_start + np.arange(sample_count, dtype=np.float64) / imu_rate
    imu = np.column_stack((interpolate(acc_times, acc, grid),
                           interpolate(gyro_times, gyro, grid))).astype(np.float32)

    audio_first = int(round((overlap_start - wav_start) * sample_rate))
    aligned_audio_count = int(round(sample_count / imu_rate * sample_rate))
    aligned_audio = audio[audio_first:audio_first + aligned_audio_count]
    if len(aligned_audio) != aligned_audio_count:
        raise ValueError("WAV ended before the aligned IMU sequence")

    participant = validate_component("participant", args.participant)
    context = validate_component("context", args.context)
    activity = validate_component("activity", args.activity)
    output_name = f"{participant}---{context}---{activity}---{args.trial}.pkl"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / output_name
    payload = {
        "IMU": imu,
        "Audio": aligned_audio.astype(np.int16),
    }
    with output_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)

    metadata = {
        "schema": "sensor-read-watchhar-export-v1",
        "session_id": session_id,
        "source": args.source,
        "events_file": str(args.events.resolve()),
        "audio_file": str(audio_path.resolve()),
        "aligned_start_unix_ns": int(round(overlap_start * 1_000_000_000)),
        "duration_s": sample_count / imu_rate,
        "imu_sample_rate_hz": int(imu_rate),
        "imu_columns": [*ACC_FIELDS, *GYRO_FIELDS],
        "imu_shape": list(imu.shape),
        "audio_sample_rate_hz": sample_rate,
        "audio_samples": int(len(aligned_audio)),
        "watchhar_note": "Raw format consumed by WatchHAR preprocess.py (IMU + Audio).",
    }
    metadata_path = output_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return output_path, metadata_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Sensor Read 的同步 WAV + IMU 导出为 WatchHAR 原始 pickle")
    parser.add_argument("--events", type=Path, required=True,
                        help="设备本地或电脑保存的 NDJSON")
    parser.add_argument("--audio", type=Path, default=None,
                        help="对应 WAV；省略时按 session ID 在 NDJSON 同目录查找")
    parser.add_argument("--source", choices=("apple_watch", "iphone"), default="apple_watch")
    parser.add_argument("--session-id", default=None,
                        help="NDJSON 含多个会话时指定完整 session ID")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--activity", required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        output_path, metadata_path = export(args)
    except (OSError, ValueError, wave.Error) as error:
        raise SystemExit(f"导出失败：{error}") from error
    print(f"WatchHAR pickle: {output_path}")
    print(f"同步元数据: {metadata_path}")


if __name__ == "__main__":
    main()
