"""Deterministic, side-effect-free fixtures for the JR Bar preview surfaces.

The demo sandbox is deliberately not a second runtime.  It produces small,
content-minimized event timelines and typed snapshots that a future UI can
adapt to the existing attention, device, and renderer projections.  It never
installs hooks, reads credentials or user state, opens a network connection, or
writes to a device.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Final

MAX_EVENTS: Final = 128
MAX_AGENTS: Final = 16
MAX_DEVICES: Final = 16
MAX_MACHINES: Final = 16
MAX_QUOTAS: Final = 16
DEFAULT_START_TIME: Final = datetime(2026, 1, 1, tzinfo=timezone.utc)


class DemoScenario(str, Enum):
    OVERVIEW = "overview"
    ASK = "ask"
    ERROR = "error"
    COMPLETION = "completion"
    QUOTA = "quota"
    FLEET = "fleet"
    WEATHER = "weather"
    DND = "dnd"
    LOW_POWER = "low_power"
    NOTIFICATION_LIGHT = "notification_light"


DEMO_SCENARIOS: tuple[str, ...] = tuple(item.value for item in DemoScenario)
_SCENARIO_ALIASES: Final = {
    "all": DemoScenario.OVERVIEW,
    "showcase": DemoScenario.OVERVIEW,
}


@dataclass(frozen=True, slots=True)
class DemoScenarioSpec:
    name: str
    label: str
    description: str


SCENARIO_SPECS: tuple[DemoScenarioSpec, ...] = (
    DemoScenarioSpec("overview", "Overview", "A bounded fleet timeline covering every demo domain."),
    DemoScenarioSpec("ask", "Ask", "One agent waits for a user decision."),
    DemoScenarioSpec("error", "Error", "One agent reports a recoverable failure."),
    DemoScenarioSpec("completion", "Completion", "One agent finishes an unseen task."),
    DemoScenarioSpec("quota", "Quota", "A provider approaches its synthetic quota."),
    DemoScenarioSpec("fleet", "Remote fleet", "Local and remote machines share a read-only view."),
    DemoScenarioSpec("weather", "Weather", "A severe-weather courtesy signal is available."),
    DemoScenarioSpec("dnd", "Do Not Disturb", "A courtesy completion is suppressed by DND."),
    DemoScenarioSpec("low_power", "Low power", "Working output is reduced to a calm low-energy state."),
    DemoScenarioSpec(
        "notification_light",
        "Notification light",
        "The finite light language visits its priority states.",
    ),
)


class DemoAgentState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    ASKING = "asking"
    ERROR = "error"
    COMPLETED = "completed"


class DemoLightMode(str, Enum):
    OFF = "off"
    WORKING = "working"
    ASKING = "asking"
    ERROR = "error"
    COMPLETION = "completion"
    QUOTA = "quota"
    WEATHER = "weather"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class DemoSafetyMetadata:
    """The explicit boundary a preview consumer can show to an operator."""

    network_access: bool = False
    credential_access: bool = False
    filesystem_reads: bool = False
    filesystem_writes: bool = False
    hook_installation: bool = False
    hardware_writes: bool = False
    user_state_mutation: bool = False
    deterministic: bool = True
    seed: int = 0
    max_events: int = MAX_EVENTS

    @property
    def safe_for_preview(self) -> bool:
        return not any(
            (
                self.network_access,
                self.credential_access,
                self.filesystem_reads,
                self.filesystem_writes,
                self.hook_installation,
                self.hardware_writes,
                self.user_state_mutation,
            )
        )

    @property
    def no_external_io(self) -> bool:
        """Convenient positive assertion for preview and harness callers."""
        return self.safe_for_preview

    def as_dict(self) -> dict[str, object]:
        return {
            "network_access": self.network_access,
            "credential_access": self.credential_access,
            "filesystem_reads": self.filesystem_reads,
            "filesystem_writes": self.filesystem_writes,
            "hook_installation": self.hook_installation,
            "hardware_writes": self.hardware_writes,
            "user_state_mutation": self.user_state_mutation,
            "deterministic": self.deterministic,
            "seed": self.seed,
            "max_events": self.max_events,
            "safe_for_preview": self.safe_for_preview,
        }


def _immutable_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or len(payload) > 32:
        raise ValueError("demo event payload must be a bounded mapping")
    forbidden = (
        "credential",
        "password",
        "secret",
        "token",
        "api_key",
        "transcript",
        "prompt",
    )
    for key, value in payload.items():
        if type(key) is not str or not key or len(key) > 64:
            raise ValueError("invalid demo event payload key")
        if any(part in key.casefold() for part in forbidden):
            raise ValueError("demo payload cannot carry private content")
        if isinstance(value, str) and len(value) > 256:
            raise ValueError("demo event payload text is too long")
    return MappingProxyType(dict(payload))


@dataclass(frozen=True, slots=True)
class DemoEvent:
    sequence: int
    at: datetime
    kind: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("invalid demo event sequence")
        if not isinstance(self.at, datetime) or self.at.tzinfo is None:
            raise ValueError("demo event times must be timezone-aware")
        if type(self.kind) is not str or not self.kind:
            raise ValueError("invalid demo event kind")
        object.__setattr__(self, "payload", _immutable_payload(self.payload))

    @property
    def timestamp(self) -> datetime:
        """Alias used by timeline consumers that call the time field timestamp."""
        return self.at


@dataclass(frozen=True, slots=True)
class DemoAgent:
    agent_id: str
    provider: str
    display_name: str
    machine_id: str
    state: DemoAgentState
    updated_at: datetime
    remote: bool = False
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DemoQuota:
    provider: str
    used: int
    limit: int
    reset_at: datetime

    @property
    def remaining_ratio(self) -> float:
        return max(0.0, min(1.0, (self.limit - self.used) / self.limit)) if self.limit else 0.0


@dataclass(frozen=True, slots=True)
class DemoDevice:
    device_id: str
    kind: str
    machine_id: str
    led_count: int
    connected: bool = True


@dataclass(frozen=True, slots=True)
class DemoRemoteMachine:
    machine_id: str
    label: str
    online: bool = True
    remote: bool = True


@dataclass(frozen=True, slots=True)
class DemoWeather:
    condition: str
    severity: str
    temperature_c: float


@dataclass(frozen=True, slots=True)
class DemoLight:
    mode: DemoLightMode
    pattern: str
    color: str
    brightness: int
    persistent: bool
    suppressed: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DemoRenderInput:
    """Surface-neutral input for an existing renderer adapter."""

    surface: str
    at: datetime
    light_mode: str
    pattern: str
    color: str
    brightness: int
    agent_count: int
    dnd: bool
    low_power: bool
    device_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DemoSnapshot:
    at: datetime
    agents: tuple[DemoAgent, ...] = ()
    quotas: tuple[DemoQuota, ...] = ()
    devices: tuple[DemoDevice, ...] = ()
    machines: tuple[DemoRemoteMachine, ...] = ()
    weather: DemoWeather | None = None
    dnd: bool = False
    low_power: bool = False
    light: DemoLight = DemoLight(DemoLightMode.OFF, "off", "#000000", 0, False)

    def to_projection_rows(self) -> tuple[dict[str, object], ...]:
        """Return content-minimized rows shaped for the attention seam."""
        mode_map = {
            DemoAgentState.IDLE: "idle_ready",
            DemoAgentState.WORKING: "working",
            DemoAgentState.ASKING: "waiting_for_input",
            DemoAgentState.ERROR: "blocked_error",
            DemoAgentState.COMPLETED: "completed",
        }
        return tuple(
            {
                "provider": agent.provider,
                "agent_id": agent.agent_id,
                "display_name": agent.display_name,
                "mode": mode_map[agent.state],
                "updated_at": agent.updated_at,
                "event_name": agent.state.value,
                "machine_id": agent.machine_id,
                "remote": agent.remote,
                "stale": False,
            }
            for agent in self.agents
        )

    def to_agent_rows(self) -> tuple[dict[str, object], ...]:
        """Alias for consumers that call the attention seam an agent adapter."""
        return self.to_projection_rows()

    def to_render_input(self, *, surface: str = "screen_bar") -> DemoRenderInput:
        if type(surface) is not str or not surface or len(surface) > 32:
            raise ValueError("invalid demo render surface")
        return DemoRenderInput(
            surface=surface,
            at=self.at,
            light_mode=self.light.mode.value,
            pattern=self.light.pattern,
            color=self.light.color,
            brightness=self.light.brightness,
            agent_count=len(self.agents),
            dnd=self.dnd,
            low_power=self.low_power,
            device_ids=tuple(device.device_id for device in self.devices if device.connected),
        )


@dataclass(frozen=True, slots=True)
class DemoRun:
    scenario: DemoScenario
    start_time: datetime
    seed: int
    events: tuple[DemoEvent, ...]
    snapshots: tuple[DemoSnapshot, ...]
    safety: DemoSafetyMetadata

    @property
    def final_snapshot(self) -> DemoSnapshot:
        return self.snapshots[-1] if self.snapshots else DemoSnapshot(self.start_time)

    @property
    def safety_metadata(self) -> DemoSafetyMetadata:
        return self.safety

    def projection_inputs(self, *, surface: str = "screen_bar") -> tuple[DemoRenderInput, ...]:
        return tuple(snapshot.to_render_input(surface=surface) for snapshot in self.snapshots)


class DemoProjectionAdapter:
    """Small pure adapter layer kept separate from host-specific renderers."""

    @staticmethod
    def agent_rows(snapshot: DemoSnapshot) -> tuple[dict[str, object], ...]:
        if type(snapshot) is not DemoSnapshot:
            raise TypeError("snapshot must be DemoSnapshot")
        return snapshot.to_projection_rows()

    @staticmethod
    def render_input(snapshot: DemoSnapshot, *, surface: str = "screen_bar") -> DemoRenderInput:
        if type(snapshot) is not DemoSnapshot:
            raise TypeError("snapshot must be DemoSnapshot")
        return snapshot.to_render_input(surface=surface)


def available_scenarios() -> tuple[str, ...]:
    return DEMO_SCENARIOS


def build_demo_run(
    scenario: str | DemoScenario = DemoScenario.OVERVIEW,
    *,
    start_time: datetime = DEFAULT_START_TIME,
    seed: int = 0,
    max_events: int = MAX_EVENTS,
) -> DemoRun:
    """Convenience factory for preview and source-AppKit harness callers."""
    return DemoSandbox(start_time=start_time, seed=seed, max_events=max_events).run(scenario)


def _canonical_scenario(value: str | DemoScenario) -> DemoScenario:
    if isinstance(value, DemoScenario):
        return value
    if type(value) is str:
        try:
            return DemoScenario(value)
        except ValueError:
            alias = _SCENARIO_ALIASES.get(value.casefold())
            if alias is not None:
                return alias
    raise ValueError(f"unknown demo scenario: {value}")


class DemoSandbox:
    """Build synthetic timelines without crossing a host or device boundary."""

    def __init__(
        self,
        *,
        start_time: datetime = DEFAULT_START_TIME,
        seed: int = 0,
        max_events: int = MAX_EVENTS,
    ) -> None:
        if not isinstance(start_time, datetime):
            raise TypeError("start_time must be a datetime")
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if type(seed) is not int:
            raise TypeError("seed must be an integer")
        if type(max_events) is not int or not 0 < max_events <= MAX_EVENTS:
            raise ValueError(f"max_events must be between 1 and {MAX_EVENTS}")
        self.start_time = start_time
        self.seed = seed
        self.max_events = max_events

    @classmethod
    def available_scenarios(cls) -> tuple[str, ...]:
        return available_scenarios()

    @classmethod
    def scenario_specs(cls) -> tuple[DemoScenarioSpec, ...]:
        return SCENARIO_SPECS

    @property
    def safety(self) -> DemoSafetyMetadata:
        return DemoSafetyMetadata(seed=self.seed, max_events=self.max_events)

    @property
    def safety_metadata(self) -> DemoSafetyMetadata:
        return self.safety

    def run(self, scenario: str | DemoScenario) -> DemoRun:
        canonical = _canonical_scenario(scenario)
        events = self._events(canonical)[: self.max_events]
        snapshots = self._replay(events)
        return DemoRun(canonical, self.start_time, self.seed, events, snapshots, self.safety)

    build = run

    def _events(self, scenario: DemoScenario) -> tuple[DemoEvent, ...]:
        rng = random.Random(self.seed)
        weather_temperature = round(rng.uniform(-12.0, 38.0), 1)
        weather_code = f"WX-{rng.randrange(1000, 10000)}"
        rows: list[tuple[int, str, dict[str, object]]] = []

        def add(offset: int, kind: str, payload: dict[str, object]) -> None:
            rows.append((offset, kind, payload))

        def local_setup(*, remote: bool = False) -> None:
            add(0, "device", {"device_id": "screen-bar", "kind": "screen_bar", "machine_id": "local", "led_count": 24})
            add(0, "device", {"device_id": "dot-local", "kind": "dot", "machine_id": "local", "led_count": 2})
            if remote:
                add(0, "remote_machine", {"machine_id": "remote-build", "label": "Remote Build Mac", "online": True})
                add(
                    0,
                    "device",
                    {
                        "device_id": "dot-remote",
                        "kind": "dot",
                        "machine_id": "remote-build",
                        "led_count": 2,
                    },
                )

        def agent(
            offset: int,
            agent_id: str,
            state: DemoAgentState,
            *,
            remote: bool = False,
            error_code: str | None = None,
        ) -> None:
            kind = {
                DemoAgentState.ASKING: "ask",
                DemoAgentState.ERROR: "error",
                DemoAgentState.COMPLETED: "completion",
            }.get(state, "agent")
            add(
                offset,
                kind,
                {
                    "agent_id": agent_id,
                    "provider": "codex" if "codex" in agent_id else "claude",
                    "display_name": "Codex demo" if "codex" in agent_id else "Claude demo",
                    "machine_id": "remote-build" if remote else "local",
                    "state": state.value,
                    "error_code": error_code,
                },
            )

        if scenario is DemoScenario.OVERVIEW:
            local_setup(remote=True)
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(2, "claude-demo", DemoAgentState.ASKING)
            agent(4, "claude-demo", DemoAgentState.ERROR, error_code="provider_unavailable")
            agent(6, "claude-demo", DemoAgentState.COMPLETED)
            agent(8, "remote-codex-demo", DemoAgentState.WORKING, remote=True)
            add(9, "quota", {"provider": "codex", "used": 82, "limit": 100, "reset_after_seconds": 3600})
            add(
                10,
                "weather",
                {
                    "condition": "severe_weather",
                    "severity": "warning",
                    "temperature_c": weather_temperature,
                    "alert_code": weather_code,
                },
            )
            add(12, "policy", {"dnd": True, "low_power": False})
            add(14, "policy", {"dnd": False, "low_power": True})
            add(16, "policy", {"dnd": False, "low_power": False})
        elif scenario is DemoScenario.ASK:
            local_setup()
            agent(1, "claude-demo", DemoAgentState.WORKING)
            agent(2, "claude-demo", DemoAgentState.ASKING)
        elif scenario is DemoScenario.ERROR:
            local_setup()
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(3, "codex-demo", DemoAgentState.ERROR, error_code="provider_unavailable")
        elif scenario is DemoScenario.COMPLETION:
            local_setup()
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(4, "codex-demo", DemoAgentState.COMPLETED)
        elif scenario is DemoScenario.QUOTA:
            local_setup()
            add(1, "quota", {"provider": "codex", "used": 92, "limit": 100, "reset_after_seconds": 3600})
        elif scenario is DemoScenario.FLEET:
            local_setup(remote=True)
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(2, "remote-codex-demo", DemoAgentState.WORKING, remote=True)
        elif scenario is DemoScenario.WEATHER:
            local_setup()
            add(
                1,
                "weather",
                {
                    "condition": "severe_weather",
                    "severity": "warning",
                    "temperature_c": weather_temperature,
                    "alert_code": weather_code,
                },
            )
        elif scenario is DemoScenario.DND:
            local_setup()
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(3, "codex-demo", DemoAgentState.COMPLETED)
            add(4, "policy", {"dnd": True, "low_power": False})
        elif scenario is DemoScenario.LOW_POWER:
            local_setup()
            agent(1, "codex-demo", DemoAgentState.WORKING)
            add(2, "policy", {"dnd": False, "low_power": True})
        else:
            local_setup()
            agent(1, "codex-demo", DemoAgentState.WORKING)
            agent(2, "codex-demo", DemoAgentState.ASKING)
            agent(4, "codex-demo", DemoAgentState.ERROR, error_code="provider_unavailable")
            agent(6, "codex-demo", DemoAgentState.COMPLETED)

        ordered = sorted(
            rows,
            key=lambda row: (row[0], row[1], repr(sorted(row[2].items()))),
        )
        return tuple(
            DemoEvent(index, self.start_time + timedelta(seconds=offset), kind, payload)
            for index, (offset, kind, payload) in enumerate(ordered)
        )

    def _replay(self, events: tuple[DemoEvent, ...]) -> tuple[DemoSnapshot, ...]:
        agents: dict[str, DemoAgent] = {}
        quotas: dict[str, DemoQuota] = {}
        devices: dict[str, DemoDevice] = {}
        machines: dict[str, DemoRemoteMachine] = {}
        weather: DemoWeather | None = None
        dnd = False
        low_power = False
        snapshots: list[DemoSnapshot] = []

        for event in events:
            payload = event.payload
            if event.kind in {"agent", "ask", "error", "completion"}:
                state = DemoAgentState(str(payload["state"]))
                machine_id = str(payload["machine_id"])
                agents[str(payload["agent_id"])] = DemoAgent(
                    agent_id=str(payload["agent_id"]),
                    provider=str(payload["provider"]),
                    display_name=str(payload["display_name"]),
                    machine_id=machine_id,
                    state=state,
                    updated_at=event.at,
                    remote=machine_id != "local",
                    error_code=payload.get("error_code") or None,
                )
            elif event.kind == "quota":
                provider = str(payload["provider"])
                quotas[provider] = DemoQuota(
                    provider,
                    int(payload["used"]),
                    int(payload["limit"]),
                    event.at
                    + timedelta(seconds=int(payload["reset_after_seconds"])),
                )
            elif event.kind == "device":
                device_id = str(payload["device_id"])
                devices[device_id] = DemoDevice(
                    device_id,
                    str(payload["kind"]),
                    str(payload["machine_id"]),
                    int(payload["led_count"]),
                )
            elif event.kind == "remote_machine":
                machine_id = str(payload["machine_id"])
                machines[machine_id] = DemoRemoteMachine(machine_id, str(payload["label"]), bool(payload["online"]))
            elif event.kind == "weather":
                weather = DemoWeather(
                    str(payload["condition"]),
                    str(payload["severity"]),
                    float(payload["temperature_c"]),
                )
            elif event.kind == "policy":
                dnd = bool(payload["dnd"])
                low_power = bool(payload["low_power"])
            snapshots.append(
                DemoSnapshot(
                    at=event.at,
                    agents=tuple(sorted(agents.values(), key=lambda row: row.agent_id)),
                    quotas=tuple(sorted(quotas.values(), key=lambda row: row.provider)),
                    devices=tuple(sorted(devices.values(), key=lambda row: row.device_id)),
                    machines=tuple(sorted(machines.values(), key=lambda row: row.machine_id)),
                    weather=weather,
                    dnd=dnd,
                    low_power=low_power,
                    light=_light_for(agents, quotas, weather, dnd, low_power),
                )
            )
        return tuple(snapshots)


def _light_for(
    agents: Mapping[str, DemoAgent],
    quotas: Mapping[str, DemoQuota],
    weather: DemoWeather | None,
    dnd: bool,
    low_power: bool,
) -> DemoLight:
    states = {agent.state for agent in agents.values()}
    if DemoAgentState.ASKING in states:
        light = DemoLight(DemoLightMode.ASKING, "heartbeat", "#FFB000", 100, True)
    elif DemoAgentState.ERROR in states:
        light = DemoLight(DemoLightMode.ERROR, "blink", "#FF3030", 100, True)
    elif DemoAgentState.COMPLETED in states:
        light = DemoLight(DemoLightMode.COMPLETION, "pulse", "#42D77D", 80, False)
    elif DemoAgentState.WORKING in states:
        light = DemoLight(DemoLightMode.WORKING, "breathe", "#4FA3FF", 55, True)
    elif weather is not None and weather.severity in {"warning", "severe"}:
        light = DemoLight(DemoLightMode.WEATHER, "pulse", "#5AC8FA", 45, False)
    elif any(quota.remaining_ratio <= 0.2 for quota in quotas.values()):
        light = DemoLight(DemoLightMode.QUOTA, "steady", "#FFB000", 35, True)
    else:
        light = DemoLight(DemoLightMode.OFF, "off", "#000000", 0, False)

    if dnd and light.mode in {
        DemoLightMode.COMPLETION,
        DemoLightMode.WEATHER,
        DemoLightMode.QUOTA,
        DemoLightMode.WORKING,
    }:
        return DemoLight(
            DemoLightMode.SUPPRESSED,
            "off",
            "#000000",
            0,
            False,
            suppressed=True,
            reason="dnd",
        )
    if low_power and not light.suppressed:
        return DemoLight(
            light.mode,
            "steady",
            light.color,
            min(light.brightness, 20),
            light.persistent,
            reason="low_power",
        )
    return light


__all__ = [
    "DEFAULT_START_TIME",
    "DEMO_SCENARIOS",
    "MAX_EVENTS",
    "SCENARIO_SPECS",
    "DemoAgent",
    "DemoAgentState",
    "DemoDevice",
    "DemoEvent",
    "DemoLight",
    "DemoLightMode",
    "DemoProjectionAdapter",
    "DemoQuota",
    "DemoRemoteMachine",
    "DemoRenderInput",
    "DemoRun",
    "DemoSafetyMetadata",
    "DemoSandbox",
    "DemoScenario",
    "DemoScenarioSpec",
    "DemoSnapshot",
    "DemoWeather",
    "available_scenarios",
    "build_demo_run",
]
