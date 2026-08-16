"""Bounded battery API facade.

The historical implementation remains in :mod:`sidepulse._battery_legacy`.
This facade preserves its public model while making every external battery and
hardware-model probe terminate within a strict deadline.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from functools import lru_cache

from . import _battery_legacy as _legacy

BATTERY_READ_TIMEOUT_SECONDS = 2.0
BATTERY_MODEL_TIMEOUT_SECONDS = 2.0
_ORIGINAL_READ_BATTERY_SNAPSHOT = _legacy.read_battery_snapshot


def _bounded_runner(
    runner: Callable[..., subprocess.CompletedProcess],
    timeout: float,
):
    def run(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return runner(*args, **kwargs)

    return run


def read_product_tree_text(
    *,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> str:
    run = runner or subprocess.run
    try:
        result = _bounded_runner(run, BATTERY_MODEL_TIMEOUT_SECONDS)(
            [
                str(_legacy.trusted_system_tool("ioreg")),
                "-p",
                "IODeviceTree",
                "-r",
                "-d",
                "1",
                "-n",
                "product",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return ""
    return result.stdout if isinstance(result.stdout, str) else ""


def _watts_for_product_text(product_text: str) -> float | None:
    product_lower = product_text.lower()
    chip_match = re.search(r'"product-soc-name"\s*=\s*<"([^"]+)">', product_text)
    chip = chip_match.group(1).lower() if chip_match else ""
    if "macbook pro" not in product_lower:
        return None
    if "16-inch" in product_lower:
        return 140.0
    if "14-inch" in product_lower:
        return 96.0 if "pro" in chip or "max" in chip else 70.0
    return None


@lru_cache(maxsize=1)
def default_full_charge_watts(
    *,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> float:
    run = runner or subprocess.run
    from_tree = _watts_for_product_text(read_product_tree_text(runner=run))
    if from_tree is not None:
        return from_tree
    try:
        result = _bounded_runner(run, BATTERY_MODEL_TIMEOUT_SECONDS)(
            [
                str(_legacy.trusted_system_tool("system_profiler")),
                "SPHardwareDataType",
                "-json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        items = data.get("SPHardwareDataType") or []
        hardware = items[0] if items else {}
    except (
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
        ValueError,
        TypeError,
    ):
        hardware = {}
    chip = str(hardware.get("chip_type", "")).lower()
    model = " ".join(
        str(hardware.get(key, ""))
        for key in ("machine_name", "machine_model", "model_number")
    ).lower()
    if "macbook pro" in model:
        if "16-inch" in model:
            return 140.0
        if "14-inch" in model:
            return 96.0 if "pro" in chip or "max" in chip else 70.0
    return 100.0


def read_battery_snapshot(
    *,
    full_charge_watts: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
):
    return _ORIGINAL_READ_BATTERY_SNAPSHOT(
        full_charge_watts=full_charge_watts,
        runner=_bounded_runner(runner, BATTERY_READ_TIMEOUT_SECONDS),
    )


_legacy.BATTERY_READ_TIMEOUT_SECONDS = BATTERY_READ_TIMEOUT_SECONDS
_legacy.BATTERY_MODEL_TIMEOUT_SECONDS = BATTERY_MODEL_TIMEOUT_SECONDS
_legacy.read_product_tree_text = read_product_tree_text
_legacy.default_full_charge_watts = default_full_charge_watts
_legacy.read_battery_snapshot = read_battery_snapshot

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
