"""``sidepulse serve``: the indicator as a machine-readable endpoint.

CodexBar's ``serve`` spawned its whole integration ecosystem -- Stream
Deck, Waybar, KDE widgets -- because a local JSON endpoint is the one
surface every other tool can consume. This is SidePulse's: a loopback
HTTP server over the app's own persisted state files, read fresh per
request so it never needs the app's process (or even the app running --
it serves the last persisted truth with its timestamps, and honesty
lives in those timestamps).

    GET /status.json   anonymous agent aggregates + redacted provider quota

Loopback only, read only, no query parameters, nothing written. The public
schema is rebuilt from an allowlist and never forwards persisted rows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .local_api_contract import (
    LocalAPIRequest,
    LocalAPIResponse,
    ReplayGuard,
    decode_request,
    redacted_response,
    validate_authenticated_request,
)
from .phone_glance import (
    PhoneGlance,
    PhoneGlanceEnvelope,
    PhoneGlancePolicy,
    build_phone_glance,
    encode_phone_glance,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_facts import NextActor, SourceFreshness, SourceHealth, WorkLifecycle
from .provider_usage_platform import ProviderSourceState, provider_descriptors
from .provider_usage_store import default_provider_usage_state_path
from .providers import default_state_dir

SERVE_DEFAULT_PORT = 8737
SERVE_SCHEMA_VERSION = 2
_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_WORKS = 1_000
_MAX_SNAPSHOTS = 32
_MAX_LANES = 64
_WORK_LIFECYCLES = frozenset(item.value for item in WorkLifecycle)
_NEXT_ACTORS = frozenset(item.value for item in NextActor)
_SOURCE_HEALTH = frozenset(item.value for item in SourceHealth)
_SOURCE_FRESHNESS = frozenset(item.value for item in SourceFreshness)
_PROVIDER_STATES = frozenset(item.value for item in ProviderSourceState)
_PROVIDER_IDS = frozenset(item.provider_id for item in provider_descriptors())
_DEFAULT_GLANCE_SOURCE_ID = "sidepulse"
_MAX_GLANCE_SEQUENCE = 1_000_000
_MAX_GLANCE_SECRET_BYTES = 4_096


def _read_json(path: Path) -> object | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > _MAX_STATE_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _timestamp(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        return None
    return float(value)


def _increment(counts: dict[str, int], value: str) -> None:
    counts[value] = counts.get(value, 0) + 1


def _public_agents(latest: object) -> dict[str, object] | None:
    if not isinstance(latest, dict) or latest.get("version") != 2:
        return None
    generation = latest.get("generation")
    works = latest.get("works")
    if (
        type(generation) is not int
        or generation < 0
        or not isinstance(works, list)
    ):
        return None
    lifecycle_counts: dict[str, int] = {}
    next_actor_counts: dict[str, int] = {}
    health_counts: dict[str, int] = {}
    freshness_counts: dict[str, int] = {}
    timing_uncertain_count = 0
    work_count = 0
    for raw in works[:_MAX_WORKS]:
        if not isinstance(raw, dict):
            continue
        lifecycle = raw.get("lifecycle")
        next_actor = raw.get("next_actor")
        source_health = raw.get("source_health")
        source_freshness = raw.get("source_freshness")
        timing_uncertain = raw.get("timing_uncertain")
        if not (
            type(lifecycle) is str
            and lifecycle in _WORK_LIFECYCLES
            and type(next_actor) is str
            and next_actor in _NEXT_ACTORS
            and type(source_health) is str
            and source_health in _SOURCE_HEALTH
            and type(source_freshness) is str
            and source_freshness in _SOURCE_FRESHNESS
            and type(timing_uncertain) is bool
        ):
            continue
        work_count += 1
        _increment(lifecycle_counts, lifecycle)
        _increment(next_actor_counts, next_actor)
        _increment(health_counts, source_health)
        _increment(freshness_counts, source_freshness)
        timing_uncertain_count += int(timing_uncertain)
    return {
        "generation": generation,
        "work_count": work_count,
        "lifecycle_counts": lifecycle_counts,
        "next_actor_counts": next_actor_counts,
        "source_health_counts": health_counts,
        "source_freshness_counts": freshness_counts,
        "timing_uncertain_count": timing_uncertain_count,
    }


def _quota_summary(lanes: object, *, provider_id: str) -> dict[str, object]:
    remaining: list[float] = []
    resets: list[float] = []
    window_count = 0
    if isinstance(lanes, list):
        for lane in lanes[:_MAX_LANES]:
            if not isinstance(lane, dict) or lane.get("provider_id") != provider_id:
                continue
            raw_remaining = lane.get("remaining_percent")
            raw_reset = lane.get("reset_at")
            remaining_value = None
            reset_value = None
            if raw_remaining is not None:
                remaining_value = _timestamp(raw_remaining)
                if remaining_value is None or remaining_value > 100.0:
                    continue
            if raw_reset is not None:
                reset_value = _timestamp(raw_reset)
                if reset_value is None:
                    continue
            if remaining_value is not None:
                remaining.append(remaining_value)
            if reset_value is not None:
                resets.append(reset_value)
            window_count += 1
    return {
        "window_count": window_count,
        "remaining_percent": min(remaining) if remaining else None,
        "next_reset_at": min(resets) if resets else None,
    }


def _public_usage(usage: object) -> dict[str, object] | None:
    if not isinstance(usage, dict) or usage.get("schema_version") != 1:
        return None
    snapshots = usage.get("snapshots")
    if not isinstance(snapshots, list):
        return None
    providers: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in snapshots[:_MAX_SNAPSHOTS]:
        if not isinstance(raw, dict):
            continue
        provider_id = raw.get("provider_id")
        observed_at = _timestamp(raw.get("observed_at"))
        state = raw.get("state")
        if not (
            type(provider_id) is str
            and provider_id in _PROVIDER_IDS
            and provider_id not in seen
            and observed_at is not None
            and type(state) is str
            and state in _PROVIDER_STATES
        ):
            continue
        seen.add(provider_id)
        providers.append(
            {
                "provider_id": provider_id,
                "observed_at": observed_at,
                "state": state,
                "quota": _quota_summary(raw.get("lanes"), provider_id=provider_id),
            }
        )
    providers.sort(key=lambda item: str(item["provider_id"]))
    return {
        "refreshed_at": _timestamp(usage.get("refreshed_at")),
        "next_refresh_at": _timestamp(usage.get("next_refresh_at")),
        "providers": providers,
    }


def build_serve_document(home: Path | None = None) -> dict:
    """Build the endpoint's explicit public projection of persisted truth."""
    latest = _read_json(default_state_dir(home) / "latest.json")
    usage = _read_json(default_provider_usage_state_path(home))
    return {
        "schema_version": SERVE_SCHEMA_VERSION,
        "privacy": "redacted",
        "agents": _public_agents(latest),
        "usage": _public_usage(usage),
    }


def build_authenticated_local_api_response(
    request: LocalAPIRequest | bytes | str,
    *,
    secret: bytes,
    replay_guard: ReplayGuard,
    home: Path | None = None,
    now: float | None = None,
) -> LocalAPIResponse:
    """Serve one authenticated read request without adding a new transport."""
    if not isinstance(replay_guard, ReplayGuard):
        raise ValueError("local API replay guard required")
    parsed = request if type(request) is LocalAPIRequest else decode_request(request)
    generated_at = time.time() if now is None else now
    validate_authenticated_request(
        parsed,
        secret,
        now=generated_at,
        replay_guard=replay_guard,
    )
    document = build_serve_document(home)
    projections: dict[str, dict[str, object]] = {
        "status.read": {"status": document},
        "agents.read": {"agents": document["agents"]},
        "usage.read": {"usage": document["usage"]},
    }
    return redacted_response(
        parsed.capability,
        projections[parsed.capability],
        generated_at=generated_at,
    )


def _phone_glance_state(document: Mapping[str, object]) -> tuple[str, str]:
    agents = document.get("agents")
    if not isinstance(agents, Mapping):
        return "unknown", "unavailable"
    lifecycle = agents.get("lifecycle_counts")
    next_actor = agents.get("next_actor_counts")
    health = agents.get("source_health_counts")
    if not all(isinstance(value, Mapping) for value in (lifecycle, next_actor, health)):
        return "unknown", "unavailable"

    active = lifecycle.get("active", 0)
    waiting = lifecycle.get("waiting", 0)
    needs_user = next_actor.get("user", 0)
    degraded = sum(
        count
        for name, count in health.items()
        if name != "healthy" and type(count) is int and count > 0
    )
    if type(active) is not int or type(waiting) is not int or type(needs_user) is not int:
        return "unknown", "unavailable"
    status = "working" if active > 0 else "waiting" if waiting > 0 else "idle"
    if needs_user > 0:
        outcome = "attention"
    elif degraded > 0:
        outcome = "degraded"
    elif active > 0:
        outcome = "in_progress"
    else:
        outcome = "steady"
    return status, outcome


def _phone_glance_capacity(document: Mapping[str, object]) -> dict[str, object] | None:
    usage = document.get("usage")
    if not isinstance(usage, Mapping):
        return None
    providers = usage.get("providers")
    if not isinstance(providers, list):
        return None
    remaining: list[float] = []
    resets: list[float] = []
    for provider in providers[:_MAX_SNAPSHOTS]:
        if not isinstance(provider, Mapping):
            continue
        quota = provider.get("quota")
        if not isinstance(quota, Mapping):
            continue
        remaining_value = _timestamp(quota.get("remaining_percent"))
        reset_value = _timestamp(quota.get("next_reset_at"))
        if remaining_value is not None and remaining_value <= 100.0:
            remaining.append(remaining_value)
        if reset_value is not None:
            resets.append(reset_value)
    capacity: dict[str, object] = {}
    if remaining:
        capacity["remaining_percent"] = min(remaining)
    if resets:
        capacity["reset_at"] = min(resets)
    return capacity or None


def build_phone_glance_projection(
    policy: PhoneGlancePolicy,
    *,
    signer: Callable[[bytes], str],
    sequence: int,
    home: Path | None = None,
    observed_at: float | None = None,
) -> PhoneGlanceEnvelope:
    """Build a signed, content-minimized glance from the public projection."""
    document = build_serve_document(home)
    status, outcome = _phone_glance_state(document)
    glance = PhoneGlance(
        source_id=policy.source_id,
        sequence=sequence,
        observed_at=time.time() if observed_at is None else observed_at,
        status=status,
        outcome=outcome,
        capacity=_phone_glance_capacity(document),
    )
    return build_phone_glance(glance, policy, signer=signer)


@dataclass(frozen=True, slots=True, repr=False)
class ServeConfiguration:
    """Explicit, in-memory configuration for the loopback server.

    The glance route is enabled only when ``glance_secret`` is supplied. The
    secret is retained only in this process and is deliberately omitted from
    the configuration representation.
    """

    home: Path | None = None
    glance_secret: bytes | None = field(default=None, repr=False)
    glance_source_id: str = _DEFAULT_GLANCE_SOURCE_ID
    glance_sequence_limit: int = _MAX_GLANCE_SEQUENCE
    glance_sequence_start: int = 0

    def __post_init__(self) -> None:
        if self.home is not None and not isinstance(self.home, Path):
            raise ValueError("invalid serve home")
        if self.glance_secret is not None and (
            type(self.glance_secret) is not bytes
            or not self.glance_secret
            or len(self.glance_secret) > _MAX_GLANCE_SECRET_BYTES
        ):
            raise ValueError("invalid glance secret")
        # PhoneGlancePolicy owns the bounded source identity contract.
        PhoneGlancePolicy(self.glance_source_id)
        if (
            type(self.glance_sequence_limit) is not int
            or not 1 <= self.glance_sequence_limit <= _MAX_GLANCE_SEQUENCE
        ):
            raise ValueError("invalid glance sequence limit")
        if (
            type(self.glance_sequence_start) is not int
            or not 0 <= self.glance_sequence_start < self.glance_sequence_limit
        ):
            raise ValueError("invalid glance sequence start")

    @property
    def glance_enabled(self) -> bool:
        return self.glance_secret is not None


class _GlanceSequence:
    def __init__(self, *, start: int, limit: int) -> None:
        self._value = start
        self._limit = limit
        self._lock = threading.Lock()

    def next(self) -> int | None:
        with self._lock:
            if self._value >= self._limit:
                return None
            self._value += 1
            return self._value


class _ServeServer(ThreadingHTTPServer):
    def __init__(self, address, handler, configuration: ServeConfiguration) -> None:
        self.serve_configuration = configuration
        self.glance_sequence = _GlanceSequence(
            start=configuration.glance_sequence_start,
            limit=configuration.glance_sequence_limit,
        )
        super().__init__(address, handler)


class _ServeHandler(BaseHTTPRequestHandler):
    server_version = PRODUCT_DISPLAY_NAME

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/glance.json":
            self._serve_glance()
            return
        if route not in ("/", "/status.json"):
            self.send_error(404)
            return
        payload = json.dumps(
            build_serve_document(self._configuration().home),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _configuration(self) -> ServeConfiguration:
        configuration = getattr(self.server, "serve_configuration", None)
        if not isinstance(configuration, ServeConfiguration):
            return ServeConfiguration()
        return configuration

    def _serve_glance(self) -> None:
        configuration = self._configuration()
        if not configuration.glance_enabled:
            self._send_glance_error(404)
            return
        sequence = getattr(self.server, "glance_sequence", None)
        if not isinstance(sequence, _GlanceSequence):
            self._send_glance_error(404)
            return
        next_sequence = sequence.next()
        if next_sequence is None:
            self._send_glance_error(503)
            return
        secret = configuration.glance_secret
        if secret is None:
            self._send_glance_error(404)
            return
        policy = PhoneGlancePolicy(configuration.glance_source_id)

        def signer(payload: bytes) -> str:
            return hmac.new(secret, payload, hashlib.sha256).hexdigest()

        envelope = build_phone_glance_projection(
            policy,
            signer=signer,
            sequence=next_sequence,
            home=configuration.home,
        )
        payload = encode_phone_glance(envelope, max_bytes=policy.max_bytes)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_glance_error(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args) -> None:
        """Quiet by design; integrators poll this."""


def serve(
    *,
    port: int = SERVE_DEFAULT_PORT,
    glance_secret: bytes | None = None,
    glance_source_id: str = _DEFAULT_GLANCE_SOURCE_ID,
) -> None:
    """Blocking loopback server; Ctrl-C stops it."""
    server = create_serve_server(
        port=port,
        glance_secret=glance_secret,
        glance_source_id=glance_source_id,
    )
    print(f"sidepulse serve: http://127.0.0.1:{int(port)}/status.json")
    if glance_secret is not None:
        print(f"sidepulse serve: http://127.0.0.1:{int(port)}/glance.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def create_serve_server(
    *,
    port: int = SERVE_DEFAULT_PORT,
    home: Path | None = None,
    glance_secret: bytes | None = None,
    glance_source_id: str = _DEFAULT_GLANCE_SOURCE_ID,
    glance_sequence_limit: int = _MAX_GLANCE_SEQUENCE,
    glance_sequence_start: int = 0,
) -> ThreadingHTTPServer:
    """Create the loopback server with explicit, testable configuration."""
    configuration = ServeConfiguration(
        home=home,
        glance_secret=glance_secret,
        glance_source_id=glance_source_id,
        glance_sequence_limit=glance_sequence_limit,
        glance_sequence_start=glance_sequence_start,
    )
    return _ServeServer(("127.0.0.1", int(port)), _ServeHandler, configuration)


__all__ = [
    "SERVE_DEFAULT_PORT",
    "SERVE_SCHEMA_VERSION",
    "ServeConfiguration",
    "build_authenticated_local_api_response",
    "build_phone_glance_projection",
    "build_serve_document",
    "create_serve_server",
    "serve",
]
