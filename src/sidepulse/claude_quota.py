"""Pure normalization boundary for explicitly supplied Claude quota evidence.

Credential discovery and remote quota acquisition are deliberately outside the
trusted SidePulse core. Until an independently supported provider capability
delivers evidence to this module, ``fetch_windows`` fails closed with a
product-owned reason code.
"""

from __future__ import annotations

import math

from .capacity_types import CapacitySourceHealth, SourceHealthKind, SourceKey

MAX_CLAUDE_WINDOWS = 32
CLAUDE_REMOTE_QUOTA_UNSUPPORTED = "claude_remote_quota_unsupported"
CLAUDE_QUOTA_SOURCE = SourceKey(
    provider_id="claude",
    adapter_id="quota",
    source_instance_id="unsupported",
    capability_id="remote_quota_windows",
)
_PRODUCT_MODEL_LABELS = {
    "claude-opus": "Opus",
    "opus": "Opus",
    "claude-sonnet": "Sonnet",
    "sonnet": "Sonnet",
    "fable": "Fable",
}


class ClaudeQuotaUnavailableError(RuntimeError):
    pass


def unsupported_source_health(*, observed_at: float) -> CapacitySourceHealth:
    """Return bounded health for the unavailable trusted Claude source."""
    return CapacitySourceHealth(
        source=CLAUDE_QUOTA_SOURCE,
        kind=SourceHealthKind.UNSUPPORTED,
        observed_at=observed_at,
        last_attempt_at=observed_at,
        retry_at=None,
        reason_code=CLAUDE_REMOTE_QUOTA_UNSUPPORTED,
        has_last_known_good=False,
    )


def fetch_windows() -> list[dict]:
    """Fail closed until a supported provider capability supplies evidence."""
    raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_UNSUPPORTED)


def _product_model_label(model: object) -> str | None:
    if not isinstance(model, dict):
        return None
    candidates = (model.get("id"), model.get("display_name"))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = "-".join(candidate.strip().lower().split())
        label = _PRODUCT_MODEL_LABELS.get(normalized)
        if label is not None:
            return label
    return None


def windows_from_payload(payload: object) -> list[dict]:
    """Pure and fixture-testable, tolerant of the schema's growth: known
    top-level windows plus the newer ``limits[]`` array."""
    if not isinstance(payload, dict):
        return []
    windows: list[dict] = []

    def add(
        label: str,
        entry: object,
        semantic_minutes: int | None = None,
        *,
        percent_key: str = "utilization",
    ) -> bool:
        if not isinstance(entry, dict) or len(windows) >= MAX_CLAUDE_WINDOWS:
            return False
        utilization = entry.get(percent_key)
        if (
            isinstance(utilization, bool)
            or not isinstance(utilization, (int, float))
            or not math.isfinite(float(utilization))
        ):
            return False
        minutes = entry.get("window_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            seconds = entry.get("limit_window_seconds", entry.get("window_seconds"))
            minutes = (
                seconds / 60.0
                if not isinstance(seconds, bool)
                and isinstance(seconds, (int, float))
                and math.isfinite(float(seconds))
                else None
            )
        if isinstance(minutes, (int, float)) and not math.isfinite(float(minutes)):
            minutes = None
        if not isinstance(minutes, (int, float)):
            minutes = semantic_minutes
        windows.append(
            {
                "label": label,
                "utilization": max(0.0, min(100.0, float(utilization))),
                "window_minutes": (
                    max(1, int(round(float(minutes))))
                    if isinstance(minutes, (int, float)) and float(minutes) > 0.0
                    else None
                ),
                "resets_at": (
                    entry.get("resets_at", entry.get("reset_at"))
                    if not isinstance(
                        entry.get("resets_at", entry.get("reset_at")), bool
                    )
                    and isinstance(
                        entry.get("resets_at", entry.get("reset_at")),
                        (str, int, float),
                    )
                    else None
                ),
            }
        )
        return True

    add("5-hour", payload.get("five_hour"), 5 * 60)
    add("weekly", payload.get("seven_day"), 7 * 24 * 60)
    add("Sonnet only", payload.get("seven_day_sonnet"), 7 * 24 * 60)
    add("Opus only", payload.get("seven_day_opus"), 7 * 24 * 60)
    limits = payload.get("limits")
    if isinstance(limits, list):
        seen_scopes: set[str] = set()
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            group = entry.get("group")
            if kind is None and group is None:
                scope = entry.get("scope")
                label = None
                if isinstance(scope, dict):
                    model = scope.get("model")
                    label = _product_model_label(model)
                if label is None:
                    raw_name = entry.get("name")
                    label = _PRODUCT_MODEL_LABELS.get(
                        "-".join(raw_name.strip().lower().split())
                        if isinstance(raw_name, str)
                        else ""
                    )
                add(label or "limit", entry)
                continue
            if kind != "weekly_scoped" or group != "weekly":
                continue
            scope = entry.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            if not isinstance(model, dict):
                continue
            name = _product_model_label(model)
            model_id = model.get("id")
            normalized_id = (
                "-".join(model_id.strip().lower().split())
                if isinstance(model_id, str)
                else ""
            )
            normalized = normalized_id or (name or "").lower()
            if (
                not name
                or normalized in {"all-models", "all_models"}
                or normalized.endswith("-all-models")
                or name.lower() == "all models"
                or normalized in seen_scopes
            ):
                continue
            seen_scopes.add(normalized)
            add(
                f"{name} only",
                entry,
                7 * 24 * 60,
                percent_key="percent",
            )

    for key in (
        "seven_day_routines",
        "seven_day_claude_routines",
        "claude_routines",
        "routines",
        "routine",
        "seven_day_cowork",
        "cowork",
    ):
        if add("Daily Routines", payload.get(key), 7 * 24 * 60):
            break
    return windows


def summary_line(windows: list[dict]) -> str | None:
    if not windows:
        return None
    parts = [
        f"{window['label']} {window['utilization']:.0f}%"
        for window in windows[:3]
    ]
    return "Claude plan: " + " · ".join(parts)
