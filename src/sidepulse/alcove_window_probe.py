"""Bounded background discovery for Alcove WindowServer facts."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlcoveWindowSnapshot:
    """Plain WindowServer facts consumed by the presentation path."""

    values: tuple[int, float, float, float]
    level: int


class AlcoveWindowProbe:
    """Coalesce Alcove window discovery and keep it off the caller thread."""

    def __init__(
        self,
        *,
        probe: Callable[[float, float], AlcoveWindowSnapshot | None],
        ttl_seconds: float = 1.0,
        start_refresh: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._probe = probe
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._start_refresh = start_refresh or self._start_thread
        self._key: tuple[float, float] | None = None
        self._value: AlcoveWindowSnapshot | None = None
        self._has_sample = False
        self._sampled_at = 0.0
        self._refreshing = False
        self._generation = 0
        self._lock = threading.Lock()

    def read(
        self,
        screen_x: float,
        screen_width: float,
        *,
        now: float | None = None,
    ) -> AlcoveWindowSnapshot | None:
        moment = time.monotonic() if now is None else float(now)
        key = (float(screen_x), float(screen_width))
        task = None
        with self._lock:
            if key != self._key:
                self._key = key
                self._value = None
                self._has_sample = False
                self._refreshing = False
                self._generation += 1
            fresh = self._has_sample and moment - self._sampled_at < self._ttl_seconds
            if not fresh and not self._refreshing:
                self._refreshing = True
                generation = self._generation

                def refresh_task() -> None:
                    self._refresh(key, generation, moment)

                task = refresh_task
            value = self._value if self._has_sample else None
        if task is not None:
            try:
                self._start_refresh(task)
            except Exception:
                with self._lock:
                    if key == self._key and generation == self._generation:
                        self._refreshing = False
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._key = None
            self._value = None
            self._has_sample = False
            self._refreshing = False
            self._generation += 1

    @staticmethod
    def _start_thread(task: Callable[[], None]) -> None:
        threading.Thread(
            target=task,
            name="sidepulse-alcove-window-probe",
            daemon=True,
        ).start()

    def _refresh(
        self,
        key: tuple[float, float],
        generation: int,
        sampled_at: float,
    ) -> None:
        try:
            value = self._probe(*key)
        except Exception:
            value = None
        if value is not None and type(value) is not AlcoveWindowSnapshot:
            value = None
        with self._lock:
            if key != self._key or generation != self._generation:
                return
            self._value = value
            self._has_sample = True
            self._sampled_at = sampled_at
            self._refreshing = False


def select_alcove_window_values(
    info: object,
    screen_x: float,
    screen_width: float,
    *,
    owner_name: str,
) -> tuple[int, float, float, float] | None:
    """Select one visible capsule from an already-fetched window list."""
    candidates = []
    for entry in info or ():
        try:
            if str(entry.get("kCGWindowOwnerName", "")) != owner_name:
                continue
            window_number = int(entry.get("kCGWindowNumber", 0))
            window_layer = int(entry.get("kCGWindowLayer", 0))
            bounds = entry.get("kCGWindowBounds") or {}
            window_x = float(bounds.get("X", 0.0))
            window_y = float(bounds.get("Y", 0.0))
            window_width = float(bounds.get("Width", 0.0))
            window_height = float(bounds.get("Height", 0.0))
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if (
            window_number <= 0
            or not all(
                math.isfinite(value)
                for value in (window_x, window_y, window_width, window_height)
            )
            or window_width < 40.0
            or window_height < 1.0
        ):
            continue
        center_x = window_x + window_width / 2.0
        if not screen_x <= center_x <= screen_x + screen_width:
            continue
        candidates.append(
            (
                window_y,
                -window_width,
                -window_layer,
                -window_number,
                window_number,
                window_x,
                window_y,
                window_width,
            )
        )
    if not candidates:
        return None
    selected = min(candidates)
    return selected[4], selected[5], selected[6], selected[7]


__all__ = [
    "AlcoveWindowProbe",
    "AlcoveWindowSnapshot",
    "select_alcove_window_values",
]
