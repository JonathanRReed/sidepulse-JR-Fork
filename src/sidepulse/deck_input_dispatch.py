"""Bounded handoff from the device reader to user-approved main-thread actions."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .deck_actions import DeckAction
from .deck_control_settings import DeckControlSettings
from .deck_input import DeckInputRouter


@dataclass(frozen=True, slots=True)
class DeckInputBatch:
    owner: DeckInputDispatch
    created_at: float
    actions: tuple[DeckAction, ...]


class DeckInputDispatch:
    def __init__(self, target, settings: DeckControlSettings, *, clock=time.monotonic):
        self._target = target
        self._settings = settings
        self._clock = clock
        self._router = DeckInputRouter()
        self._lock = threading.RLock()
        self._pending: DeckInputBatch | None = None
        self._closed = False

    def receive(self, messages: list[dict]) -> None:
        with self._lock:
            if self._closed or not self._settings.enabled:
                return
            actions = []
            for message in messages[:128]:
                key = self._router.accept(message)
                action = self._settings.action_for(key) if key is not None else None
                if action is not None and len(actions) < 8:
                    actions.append(action)
            if not actions or self._pending is not None:
                return
            batch = DeckInputBatch(self, self._clock(), tuple(actions))
            self._pending = batch
            try:
                self._target.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyDeckInput:", batch, False,
                )
            except Exception:
                self._pending = None

    def deliver(self, batch: DeckInputBatch, executor) -> tuple:
        with self._lock:
            if batch is not self._pending or self._closed:
                return ()
            self._pending = None
            if not 0 <= self._clock() - batch.created_at <= 0.5:
                return ()
            receipts = []
            for action in batch.actions:
                if self._closed or not 0 <= self._clock() - batch.created_at <= 0.5:
                    break
                receipts.append(executor.execute(action))
            return tuple(receipts)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
