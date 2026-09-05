#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import queue
import sys
import time
from pathlib import Path
from typing import Any

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if REPO_SRC.exists():
    sys.path.insert(0, str(REPO_SRC))

from sidepulse.device_writer import (  # noqa: E402
    DEFAULT_FILE_NAME,
    DeviceWriteError,
    validate_led_text,
    write_led_program,
)
from sidepulse.led_status import apply_brightness, led_count_for_target  # noqa: E402

DEFAULT_FPS = 25.0
DEFAULT_IDLE_BRIGHTNESS = 0.08
DEFAULT_MAX_BRIGHTNESS = 1.0
DEFAULT_NOISE_FLOOR_DB = -54.0
DEFAULT_PEAK_DB = -8.0
DEFAULT_CURVE = 0.72
DEFAULT_ATTACK_SECONDS = 0.045
DEFAULT_RELEASE_SECONDS = 0.32
DEFAULT_TRANSITION_MS = 90
MIN_RMS = 1e-9


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_led_count(led_count: int) -> int:
    return max(1, min(8, int(led_count)))


def green_to_red_rgb(index: int, led_count: int) -> tuple[int, int, int]:
    count = normalize_led_count(led_count)
    if count == 1:
        return 0, 255, 0

    position = clamp(index / (count - 1))
    if position <= 0.5:
        red = int(round(255 * (position / 0.5)))
        green = 255
    else:
        red = 255
        green = int(round(255 * (1.0 - ((position - 0.5) / 0.5))))
    return red, green, 0


def scale_rgb(rgb: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    brightness = clamp(brightness)
    return tuple(int(round(channel * brightness)) for channel in rgb)


def rgb_hex(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    return f"#{red:02X}{green:02X}{blue:02X}"


def build_led_program(
    level: float,
    *,
    led_count: int = 8,
    idle_brightness: float = DEFAULT_IDLE_BRIGHTNESS,
    max_brightness: float = DEFAULT_MAX_BRIGHTNESS,
    transition_ms: int = DEFAULT_TRANSITION_MS,
) -> str:
    count = normalize_led_count(led_count)
    level = clamp(level)
    idle = clamp(idle_brightness)
    peak = max(idle, clamp(max_brightness))
    fill = level * count
    duration = max(0, int(transition_ms))
    global_brightness = int(round(peak * 255))

    segments: list[str] = []
    for index in range(count):
        segment_level = clamp(fill - index)
        absolute_brightness = idle + ((peak - idle) * segment_level)
        brightness = absolute_brightness / peak if peak > 0 else 0.0
        color = rgb_hex(scale_rgb(green_to_red_rgb(index, count), brightness))
        token = f"{index}:{color}"
        if duration:
            token += f" {duration}ms cosine"
        segments.append(token)

    program = apply_brightness("; ".join(segments), global_brightness)
    validate_led_text(program)
    return program


def rms_to_dbfs(rms: float, *, gain_db: float = 0.0) -> float:
    return 20.0 * math.log10(max(float(rms), MIN_RMS)) + gain_db


def rms_to_level(
    rms: float,
    *,
    noise_floor_db: float = DEFAULT_NOISE_FLOOR_DB,
    peak_db: float = DEFAULT_PEAK_DB,
    gain_db: float = 0.0,
    curve: float = DEFAULT_CURVE,
) -> float:
    if peak_db <= noise_floor_db:
        raise ValueError("--peak-db must be greater than --noise-floor-db")

    db = rms_to_dbfs(rms, gain_db=gain_db)
    level = clamp((db - noise_floor_db) / (peak_db - noise_floor_db))
    return level ** max(0.05, float(curve))


def smooth_level(
    previous: float,
    target: float,
    *,
    elapsed: float,
    attack_seconds: float = DEFAULT_ATTACK_SECONDS,
    release_seconds: float = DEFAULT_RELEASE_SECONDS,
) -> float:
    previous = clamp(previous)
    target = clamp(target)
    tau = attack_seconds if target > previous else release_seconds
    if tau <= 0:
        return target
    alpha = 1.0 - math.exp(-max(0.0, elapsed) / tau)
    return previous + ((target - previous) * alpha)


def push_latest(samples: queue.Queue[float], value: float) -> None:
    try:
        samples.put_nowait(value)
        return
    except queue.Full:
        pass

    try:
        samples.get_nowait()
    except queue.Empty:
        pass
    samples.put_nowait(value)


def drain_peak(samples: queue.Queue[float]) -> float:
    peak = 0.0
    while True:
        try:
            peak = max(peak, samples.get_nowait())
        except queue.Empty:
            return peak


def terminal_meter(level: float, *, led_count: int) -> str:
    count = normalize_led_count(led_count)
    fill = clamp(level) * count
    cells: list[str] = []
    for index in range(count):
        segment_level = clamp(fill - index)
        if segment_level >= 0.66:
            char = "#"
        elif segment_level >= 0.33:
            char = "="
        elif segment_level > 0:
            char = "-"
        else:
            char = "."

        red, green, blue = green_to_red_rgb(index, count)
        cells.append(f"\033[38;2;{red};{green};{blue}m{char}\033[0m")
    return "".join(cells)


def parse_input_device(value: str | None) -> int | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def load_audio_modules() -> tuple[Any, Any]:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit(
            "audio_monitor.py needs sounddevice and numpy for live audio.\n"
            "Install them with: python3 -m pip install sounddevice numpy"
        ) from exc
    return sd, np


def list_input_devices() -> int:
    sd, _ = load_audio_modules()
    devices = sd.query_devices()
    default_input = None
    try:
        default_input = sd.default.device[0]
    except (AttributeError, IndexError, TypeError):
        pass

    for index, device in enumerate(devices):
        channels = int(device.get("max_input_channels", 0))
        if channels <= 0:
            continue
        marker = "*" if index == default_input else " "
        name = device.get("name", "Unnamed input")
        sample_rate = int(float(device.get("default_samplerate", 0)))
        print(f"{marker} {index:2d}: {name} ({channels} ch, {sample_rate} Hz)")
    return 0


def resolve_target(args: argparse.Namespace) -> Path | None:
    if args.dry_run and args.device is None:
        return None
    return write_led_program(
        "off",
        device_path=args.device,
        file_name=args.file_name,
        dry_run=True,
    )


def infer_led_count(args: argparse.Namespace, target: Path | None) -> int:
    if args.led_count is not None:
        return normalize_led_count(args.led_count)
    if target is not None:
        return led_count_for_target(target)
    return 8


def print_preview(
    *,
    level: float,
    rms: float,
    led_count: int,
    target: Path | None,
    dry_run: bool,
) -> None:
    db = rms_to_dbfs(rms)
    destination = "dry-run" if dry_run else str(target)
    print(
        f"\r{terminal_meter(level, led_count=led_count)} "
        f"{level:5.1%} {db:6.1f} dBFS -> {destination}",
        end="",
        flush=True,
    )


def run_monitor(args: argparse.Namespace) -> int:
    if args.list_inputs:
        return list_input_devices()

    sd, np = load_audio_modules()
    target = resolve_target(args)
    led_count = infer_led_count(args, target)
    audio_samples: queue.Queue[float] = queue.Queue(maxsize=8)
    stream_errors: queue.Queue[str] = queue.Queue(maxsize=4)
    preview = args.terminal or args.dry_run
    frame_seconds = 1.0 / max(1.0, float(args.fps))
    input_device = parse_input_device(args.input_device)
    sample_rate = int(args.sample_rate)
    block_size = max(0, int(args.block_size))

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            try:
                stream_errors.put_nowait(str(status))
            except queue.Full:
                pass
        rms = math.sqrt(float(np.mean(np.square(indata))))
        push_latest(audio_samples, rms)

    if args.dry_run:
        print(f"audio monitor: dry-run preview with {led_count} LEDs")
    else:
        print(f"audio monitor: writing {led_count} LEDs to {target}")
    print("press Ctrl-C to stop")

    smoothed = 0.0
    latest_rms = 0.0
    last_program: str | None = None
    last_time = time.monotonic()
    printed_preview = False

    try:
        with sd.InputStream(
            channels=1,
            samplerate=sample_rate,
            blocksize=block_size,
            callback=callback,
            device=input_device,
            dtype="float32",
        ):
            while True:
                frame_start = time.monotonic()
                rms = drain_peak(audio_samples)
                if rms > 0.0:
                    latest_rms = rms

                target_level = rms_to_level(
                    rms,
                    noise_floor_db=args.noise_floor_db,
                    peak_db=args.peak_db,
                    gain_db=args.gain_db,
                    curve=args.curve,
                )
                now = time.monotonic()
                smoothed = smooth_level(
                    smoothed,
                    target_level,
                    elapsed=now - last_time,
                    attack_seconds=args.attack,
                    release_seconds=args.release,
                )
                last_time = now

                program = build_led_program(
                    smoothed,
                    led_count=led_count,
                    idle_brightness=args.idle_brightness,
                    max_brightness=args.max_brightness,
                    transition_ms=args.transition_ms,
                )
                if program != last_program:
                    if not args.dry_run:
                        write_led_program(program, device_path=target, file_name=args.file_name)
                    last_program = program

                if preview:
                    printed_preview = True
                    print_preview(
                        level=smoothed,
                        rms=latest_rms,
                        led_count=led_count,
                        target=target,
                        dry_run=args.dry_run,
                    )

                try:
                    error = stream_errors.get_nowait()
                except queue.Empty:
                    error = None
                if error and not preview:
                    print(f"audio stream: {error}", file=sys.stderr)

                sleep_for = frame_seconds - (time.monotonic() - frame_start)
                if sleep_for > 0:
                    time.sleep(sleep_for)
    except KeyboardInterrupt:
        return 0
    finally:
        if printed_preview:
            print()
        if args.off_on_exit and target is not None and not args.dry_run:
            try:
                write_led_program("off", device_path=target, file_name=args.file_name)
            except (DeviceWriteError, OSError) as exc:
                print(f"audio monitor: could not turn LEDs off: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Show microphone volume as a smooth dim green-to-red SidePulse LED bar.",
    )
    parser.add_argument(
        "--device",
        type=Path,
        help="Mounted device folder or LED program file. Defaults to auto-detecting /Volumes.",
    )
    parser.add_argument(
        "--file-name",
        default=DEFAULT_FILE_NAME,
        help=f"Target file name when --device is a folder. Default: {DEFAULT_FILE_NAME}.",
    )
    parser.add_argument("--led-count", type=int, help="LED count. Defaults to device name, then 8.")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="LED update rate.")
    parser.add_argument(
        "--transition-ms",
        type=int,
        default=DEFAULT_TRANSITION_MS,
        help="Controller-side cosine fade per frame; set 0 for immediate updates.",
    )
    parser.add_argument(
        "--idle-brightness",
        type=float,
        default=DEFAULT_IDLE_BRIGHTNESS,
        help="Dim baseline brightness from 0.0 to 1.0.",
    )
    parser.add_argument(
        "--max-brightness",
        type=float,
        default=DEFAULT_MAX_BRIGHTNESS,
        help="Peak brightness from 0.0 to 1.0.",
    )
    parser.add_argument(
        "--noise-floor-db",
        type=float,
        default=DEFAULT_NOISE_FLOOR_DB,
        help="Input level that maps to an empty bar.",
    )
    parser.add_argument(
        "--peak-db",
        type=float,
        default=DEFAULT_PEAK_DB,
        help="Input level that maps to a full bar.",
    )
    parser.add_argument("--gain-db", type=float, default=0.0, help="Gain applied before level mapping.")
    parser.add_argument(
        "--curve",
        type=float,
        default=DEFAULT_CURVE,
        help="Response curve; lower values make quiet sounds more visible.",
    )
    parser.add_argument(
        "--attack",
        type=float,
        default=DEFAULT_ATTACK_SECONDS,
        help="Seconds for the meter to rise toward louder audio.",
    )
    parser.add_argument(
        "--release",
        type=float,
        default=DEFAULT_RELEASE_SECONDS,
        help="Seconds for the meter to fall after audio gets quieter.",
    )
    parser.add_argument("--sample-rate", type=float, default=44100.0, help="Audio sample rate.")
    parser.add_argument("--block-size", type=int, default=0, help="Audio callback block size; 0 lets PortAudio choose.")
    parser.add_argument("--input-device", help="sounddevice input index or name.")
    parser.add_argument("--list-inputs", action="store_true", help="List audio input devices and exit.")
    parser.add_argument("--terminal", action="store_true", help="Show a terminal meter while running.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing to a SidePulse Pro or SidePulse Dot device.",
    )
    parser.add_argument("--off-on-exit", action="store_true", help="Write 'off' when the monitor exits.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_monitor(args)
    except ValueError as exc:
        print(f"audio monitor: {exc}", file=sys.stderr)
        return 2
    except DeviceWriteError as exc:
        print(f"audio monitor: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"audio monitor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
