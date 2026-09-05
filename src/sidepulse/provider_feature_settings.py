"""Small, immutable settings views for native provider features.

The durable provider documents contain settings for several consumers.  These
views make each consumer's input explicit: collection code receives no menu or
sync choices, presentation code receives no peer credentials, and sync code
receives no presentation state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .provider_instances import (
    OPEN_SESSION_ACTION_CHOICES,
    REMOTE_SHARING_CHOICES,
)
from .provider_usage_settings import ProviderUsageSettings
from .provider_usage_sync_settings import ProviderSyncPeer, ProviderSyncSettings

MAX_FEATURE_IDS = 128
MAX_FEATURE_ID_LENGTH = 128


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ProviderCollectionFeature:
    """Settings that a provider collector is allowed to observe."""

    provider_id: str
    enabled: bool
    browser_sources: bool
    options: tuple[tuple[str, str], ...]
    source_instance_id: str = "default"

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        if (
            type(self.enabled) is not bool
            or type(self.browser_sources) is not bool
            or type(self.options) is not tuple
            or len({key for key, _value in self.options}) != len(self.options)
            or not all(
                isinstance(key, str)
                and bool(key)
                and isinstance(value, str)
                for key, value in self.options
            )
            or not isinstance(self.source_instance_id, str)
            or not self.source_instance_id
        ):
            raise ValueError("invalid provider collection feature")

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id

    def option(self, key: str) -> str | None:
        """Return one collector option without exposing the durable document."""

        if not isinstance(key, str) or not key:
            raise ValueError("invalid provider collection option key")
        return next((value for name, value in self.options if name == key), None)


@dataclass(frozen=True, slots=True)
class ProviderCollectionSettings:
    """Immutable provider settings boundary for usage collection."""

    providers: tuple[ProviderCollectionFeature, ...]

    def __post_init__(self) -> None:
        if (
            type(self.providers) is not tuple
            or not all(type(item) is ProviderCollectionFeature for item in self.providers)
            or len({item.identity for item in self.providers}) != len(self.providers)
        ):
            raise ValueError("invalid provider collection settings")

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderCollectionFeature:
        return next(
            item
            for item in self.providers
            if item.identity == (provider_id, source_instance_id)
        )

    preference = provider


@dataclass(frozen=True, slots=True)
class ProviderMenuPresentation:
    """Menu-wide presentation switches, kept separate from collection."""

    show_meters: bool = True
    show_totals: bool = True
    show_cost: bool = True
    show_detail_lanes: bool = True
    show_menu_bar_percent: bool = True
    privacy_mode: bool = False

    def __post_init__(self) -> None:
        if not all(
            type(getattr(self, name)) is bool
            for name in (
                "show_meters",
                "show_totals",
                "show_cost",
                "show_detail_lanes",
                "show_menu_bar_percent",
                "privacy_mode",
            )
        ):
            raise ValueError("invalid provider menu presentation")


@dataclass(frozen=True, slots=True)
class ProviderPresentationFeature:
    """Per-provider curation and alert choices for presentation surfaces."""

    provider_id: str
    menu_visible: bool
    reset_celebrations: bool
    reset_overlay: bool
    reset_hardware: bool
    reset_notification: bool
    reset_sound: bool
    threshold_remaining: float
    source_instance_id: str = "default"

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        if (
            type(self.menu_visible) is not bool
            or type(self.reset_celebrations) is not bool
            or not all(
                type(value) is bool
                for value in (
                    self.reset_overlay,
                    self.reset_hardware,
                    self.reset_notification,
                    self.reset_sound,
                )
            )
            or isinstance(self.threshold_remaining, bool)
            or not isinstance(self.threshold_remaining, (int, float))
            or not 0.0 <= float(self.threshold_remaining) <= 100.0
            or not isinstance(self.source_instance_id, str)
            or not self.source_instance_id
        ):
            raise ValueError("invalid provider presentation feature")
        object.__setattr__(self, "threshold_remaining", float(self.threshold_remaining))

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id


@dataclass(frozen=True, slots=True)
class ProviderPresentationSettings:
    """Immutable provider settings boundary for menus and other UI."""

    providers: tuple[ProviderPresentationFeature, ...]
    menu: ProviderMenuPresentation = ProviderMenuPresentation()

    def __post_init__(self) -> None:
        if (
            type(self.providers) is not tuple
            or not all(type(item) is ProviderPresentationFeature for item in self.providers)
            or len({item.identity for item in self.providers}) != len(self.providers)
            or type(self.menu) is not ProviderMenuPresentation
        ):
            raise ValueError("invalid provider presentation settings")

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderPresentationFeature:
        return next(
            item
            for item in self.providers
            if item.identity == (provider_id, source_instance_id)
        )

    preference = provider

    @property
    def menu_display(self) -> ProviderMenuPresentation:
        return self.menu

    @property
    def show_meters(self) -> bool:
        return self.menu.show_meters

    @property
    def show_totals(self) -> bool:
        return self.menu.show_totals

    @property
    def show_cost(self) -> bool:
        return self.menu.show_cost

    @property
    def show_detail_lanes(self) -> bool:
        return self.menu.show_detail_lanes

    @property
    def show_menu_bar_percent(self) -> bool:
        return self.menu.show_menu_bar_percent

    @property
    def privacy_mode(self) -> bool:
        return self.menu.privacy_mode

    def hidden_menu_providers(self) -> frozenset[str]:
        provider_ids = {preference.provider_id for preference in self.providers}
        return frozenset(
            provider_id
            for provider_id in provider_ids
            if all(
                not preference.menu_visible
                for preference in self.providers
                if preference.provider_id == provider_id
            )
        )

    def hidden_menu_instances(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            preference.identity
            for preference in self.providers
            if not preference.menu_visible
        )


@dataclass(frozen=True, slots=True)
class ProviderInstanceVisualPolicy:
    """Non-secret identity choices available to presentation surfaces."""

    provider_id: str
    source_instance_id: str
    label: str
    color_override: str | None

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        _require_string(self.source_instance_id, "source_instance_id")
        _require_string(self.label, "label")
        if self.color_override is not None and not isinstance(self.color_override, str):
            raise ValueError("invalid provider instance visual policy")

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id


@dataclass(frozen=True, slots=True)
class ProviderInstanceRetentionPolicy:
    """History-retention choice for one exact provider instance."""

    provider_id: str
    source_instance_id: str
    retention_days: int

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        _require_string(self.source_instance_id, "source_instance_id")
        if type(self.retention_days) is not int or self.retention_days not in {0, 7, 30, 90}:
            raise ValueError("invalid provider instance retention policy")

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id


@dataclass(frozen=True, slots=True)
class ProviderInstanceSharingPolicy:
    """Outbound-sharing choice for one exact provider instance."""

    provider_id: str
    source_instance_id: str
    remote_sharing_choice: str

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        _require_string(self.source_instance_id, "source_instance_id")
        if self.remote_sharing_choice not in REMOTE_SHARING_CHOICES:
            raise ValueError("invalid provider instance sharing policy")

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id


@dataclass(frozen=True, slots=True)
class ProviderInstanceSessionActionPolicy:
    """Open-session choice for one exact provider instance."""

    provider_id: str
    source_instance_id: str
    open_session_action: str

    def __post_init__(self) -> None:
        _require_string(self.provider_id, "provider_id")
        _require_string(self.source_instance_id, "source_instance_id")
        if self.open_session_action not in OPEN_SESSION_ACTION_CHOICES:
            raise ValueError("invalid provider instance session action policy")

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id


def _validate_instance_policy_items(
    providers: object,
    item_type: type[object],
) -> None:
    if (
        type(providers) is not tuple
        or not all(type(item) is item_type for item in providers)
        or len({item.identity for item in providers}) != len(providers)
    ):
        raise ValueError("invalid provider instance policy projection")


def _instance_policy_provider(
    providers: tuple[object, ...],
    provider_id: str,
    source_instance_id: str,
) -> object:
    return next(
        item
        for item in providers
        if item.identity == (provider_id, source_instance_id)
    )


@dataclass(frozen=True, slots=True)
class ProviderInstanceVisualProjection:
    providers: tuple[ProviderInstanceVisualPolicy, ...]

    def __post_init__(self) -> None:
        _validate_instance_policy_items(self.providers, ProviderInstanceVisualPolicy)

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderInstanceVisualPolicy:
        return _instance_policy_provider(  # type: ignore[return-value]
            self.providers,
            provider_id,
            source_instance_id,
        )


@dataclass(frozen=True, slots=True)
class ProviderInstanceRetentionProjection:
    providers: tuple[ProviderInstanceRetentionPolicy, ...]

    def __post_init__(self) -> None:
        _validate_instance_policy_items(self.providers, ProviderInstanceRetentionPolicy)

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderInstanceRetentionPolicy:
        return _instance_policy_provider(  # type: ignore[return-value]
            self.providers,
            provider_id,
            source_instance_id,
        )


@dataclass(frozen=True, slots=True)
class ProviderInstanceSharingProjection:
    providers: tuple[ProviderInstanceSharingPolicy, ...]

    def __post_init__(self) -> None:
        _validate_instance_policy_items(self.providers, ProviderInstanceSharingPolicy)

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderInstanceSharingPolicy:
        return _instance_policy_provider(  # type: ignore[return-value]
            self.providers,
            provider_id,
            source_instance_id,
        )


@dataclass(frozen=True, slots=True)
class ProviderInstanceSessionActionProjection:
    providers: tuple[ProviderInstanceSessionActionPolicy, ...]

    def __post_init__(self) -> None:
        _validate_instance_policy_items(self.providers, ProviderInstanceSessionActionPolicy)

    def provider(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderInstanceSessionActionPolicy:
        return _instance_policy_provider(  # type: ignore[return-value]
            self.providers,
            provider_id,
            source_instance_id,
        )


@dataclass(frozen=True, slots=True)
class ProviderInstancePolicyProjection:
    """Privacy-safe projections for all provider-instance profile consumers."""

    visual: ProviderInstanceVisualProjection
    retention: ProviderInstanceRetentionProjection
    sharing: ProviderInstanceSharingProjection
    session_action: ProviderInstanceSessionActionProjection

    def __post_init__(self) -> None:
        if (
            type(self.visual) is not ProviderInstanceVisualProjection
            or type(self.retention) is not ProviderInstanceRetentionProjection
            or type(self.sharing) is not ProviderInstanceSharingProjection
            or type(self.session_action) is not ProviderInstanceSessionActionProjection
        ):
            raise ValueError("invalid provider instance policy projection")


@dataclass(frozen=True, slots=True)
class ProviderSyncSettingsProjection:
    """Immutable provider settings boundary for remote synchronization."""

    enabled: bool
    device_id: str | None
    categories: tuple[str, ...]
    peers: tuple[ProviderSyncPeer, ...]

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or (self.device_id is not None and not isinstance(self.device_id, str))
            or type(self.categories) is not tuple
            or type(self.peers) is not tuple
            or not all(isinstance(category, str) for category in self.categories)
            or not all(type(peer) is ProviderSyncPeer for peer in self.peers)
        ):
            raise ValueError("invalid provider sync settings projection")


@dataclass(frozen=True, slots=True)
class ProviderSettingsChangeReceipt:
    """Bounded, immutable record of one provider-feature settings revision."""

    revision: int
    changed_feature_ids: frozenset[str]
    MAX_FEATURE_IDS: ClassVar[int] = MAX_FEATURE_IDS

    def __post_init__(self) -> None:
        if (
            type(self.revision) is not int
            or self.revision < 0
            or type(self.changed_feature_ids) is not frozenset
            or len(self.changed_feature_ids) > self.MAX_FEATURE_IDS
            or not all(
                isinstance(feature_id, str)
                and bool(feature_id)
                and len(feature_id) <= MAX_FEATURE_ID_LENGTH
                for feature_id in self.changed_feature_ids
            )
        ):
            raise ValueError("invalid provider settings change receipt")

    @property
    def changed_features(self) -> tuple[str, ...]:
        """Stable ordering for loggers and consumers that avoid set semantics."""

        return tuple(sorted(self.changed_feature_ids))


@dataclass(frozen=True, slots=True)
class ProviderFeatureSettingsProjection:
    """All three typed views plus the revision receipt that produced them."""

    collection: ProviderCollectionSettings
    presentation: ProviderPresentationSettings
    sync: ProviderSyncSettingsProjection
    receipt: ProviderSettingsChangeReceipt

    def __post_init__(self) -> None:
        if (
            type(self.collection) is not ProviderCollectionSettings
            or type(self.presentation) is not ProviderPresentationSettings
            or type(self.sync) is not ProviderSyncSettingsProjection
            or type(self.receipt) is not ProviderSettingsChangeReceipt
        ):
            raise ValueError("invalid provider feature settings projection")


# Descriptive aliases make the projections convenient to import without
# duplicating data classes or introducing a second vocabulary.
CollectionSettingsProjection = ProviderCollectionSettings
PresentationSettingsProjection = ProviderPresentationSettings
SyncSettingsProjection = ProviderSyncSettingsProjection
ProviderCollectionSettingsProjection = ProviderCollectionSettings
ProviderPresentationSettingsProjection = ProviderPresentationSettings
ProviderSyncFeatureSettingsProjection = ProviderSyncSettingsProjection
ProviderFeatureSettings = ProviderFeatureSettingsProjection


def project_collection_settings(settings: ProviderUsageSettings) -> ProviderCollectionSettings:
    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    return ProviderCollectionSettings(
        tuple(
            ProviderCollectionFeature(
                provider_id=preference.provider_id,
                enabled=preference.enabled,
                browser_sources=preference.browser_sources,
                options=preference.options,
                source_instance_id=preference.source_instance_id,
            )
            for preference in settings.providers
        )
    )


def project_presentation_settings(settings: ProviderUsageSettings) -> ProviderPresentationSettings:
    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    return ProviderPresentationSettings(
        providers=tuple(
            ProviderPresentationFeature(
                provider_id=preference.provider_id,
                menu_visible=preference.menu_visible,
                reset_celebrations=preference.reset_celebrations,
                reset_overlay=preference.reset_overlay,
                reset_hardware=preference.reset_hardware,
                reset_notification=preference.reset_notification,
                reset_sound=preference.reset_sound,
                threshold_remaining=preference.threshold_remaining,
                source_instance_id=preference.source_instance_id,
            )
            for preference in settings.providers
        ),
        menu=ProviderMenuPresentation(
            **{
                name: getattr(settings.menu_display, name)
                for name in (
                    "show_meters",
                    "show_totals",
                    "show_cost",
                    "show_detail_lanes",
                    "show_menu_bar_percent",
                    "privacy_mode",
                )
            }
        ),
    )


def project_instance_policies(settings: ProviderUsageSettings) -> ProviderInstancePolicyProjection:
    """Project durable profiles without consent or credential references."""

    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    return ProviderInstancePolicyProjection(
        visual=ProviderInstanceVisualProjection(
            tuple(
                ProviderInstanceVisualPolicy(
                    provider_id=preference.provider_id,
                    source_instance_id=preference.source_instance_id,
                    label=preference.label,
                    color_override=preference.color_override,
                )
                for preference in settings.providers
            )
        ),
        retention=ProviderInstanceRetentionProjection(
            tuple(
                ProviderInstanceRetentionPolicy(
                    provider_id=preference.provider_id,
                    source_instance_id=preference.source_instance_id,
                    retention_days=preference.retention_days,
                )
                for preference in settings.providers
            )
        ),
        sharing=ProviderInstanceSharingProjection(
            tuple(
                ProviderInstanceSharingPolicy(
                    provider_id=preference.provider_id,
                    source_instance_id=preference.source_instance_id,
                    remote_sharing_choice=preference.remote_sharing_choice,
                )
                for preference in settings.providers
            )
        ),
        session_action=ProviderInstanceSessionActionProjection(
            tuple(
                ProviderInstanceSessionActionPolicy(
                    provider_id=preference.provider_id,
                    source_instance_id=preference.source_instance_id,
                    open_session_action=preference.open_session_action,
                )
                for preference in settings.providers
            )
        ),
    )


def project_sync_settings(settings: ProviderSyncSettings) -> ProviderSyncSettingsProjection:
    if type(settings) is not ProviderSyncSettings:
        raise TypeError("expected ProviderSyncSettings")
    return ProviderSyncSettingsProjection(
        enabled=settings.enabled,
        device_id=settings.device_id,
        categories=settings.categories,
        peers=settings.peers,
    )


project_provider_collection_settings = project_collection_settings
project_provider_presentation_settings = project_presentation_settings
project_provider_sync_settings = project_sync_settings


def _feature_ids(
    collection: ProviderCollectionSettings,
    presentation: ProviderPresentationSettings,
    sync: ProviderSyncSettingsProjection,
) -> frozenset[str]:
    def prefix(domain: str, provider_id: str, source_instance_id: str) -> str:
        instance = "" if source_instance_id == "default" else f".{source_instance_id}"
        return f"{domain}.{provider_id}{instance}"

    identifiers = {
        f"{prefix('collection', item.provider_id, item.source_instance_id)}.enabled"
        for item in collection.providers
    }
    identifiers.update(
        f"{prefix('collection', item.provider_id, item.source_instance_id)}.browser_sources"
        for item in collection.providers
    )
    identifiers.update(
        f"{prefix('collection', item.provider_id, item.source_instance_id)}.options"
        for item in collection.providers
    )
    identifiers.update(
        f"{prefix('presentation', item.provider_id, item.source_instance_id)}.{field}"
        for item in presentation.providers
        for field in (
            "menu_visible", "reset_celebrations", "reset_overlay",
            "reset_hardware", "reset_notification", "reset_sound",
            "threshold_remaining",
        )
    )
    identifiers.update(
        f"presentation.menu.{field}"
        for field in (
            "show_meters",
            "show_totals",
            "show_cost",
            "show_detail_lanes",
            "show_menu_bar_percent",
            "privacy_mode",
        )
    )
    identifiers.update({"sync.enabled", "sync.device_id", "sync.categories", "sync.peers"})
    if len(identifiers) > MAX_FEATURE_IDS:
        raise ValueError("provider feature id vocabulary exceeds its bound")
    return frozenset(identifiers)


def _changed_feature_ids(
    current: ProviderFeatureSettingsProjection,
    previous: ProviderFeatureSettingsProjection | None,
) -> frozenset[str]:
    def prefix(domain: str, provider_id: str, source_instance_id: str) -> str:
        instance = "" if source_instance_id == "default" else f".{source_instance_id}"
        return f"{domain}.{provider_id}{instance}"

    if previous is None:
        return _feature_ids(current.collection, current.presentation, current.sync)
    changed: set[str] = set()
    if current.collection != previous.collection:
        before_by_provider = {item.identity: item for item in previous.collection.providers}
        for after in current.collection.providers:
            before = before_by_provider.get(after.identity)
            feature_prefix = prefix(
                "collection",
                after.provider_id,
                after.source_instance_id,
            )
            if before is None:
                changed.update(
                    {
                        f"{feature_prefix}.enabled",
                        f"{feature_prefix}.browser_sources",
                        f"{feature_prefix}.options",
                    }
                )
                continue
            if before.enabled != after.enabled:
                changed.add(f"{feature_prefix}.enabled")
            if before.browser_sources != after.browser_sources:
                changed.add(f"{feature_prefix}.browser_sources")
            if before.options != after.options:
                changed.add(f"{feature_prefix}.options")
    if current.presentation != previous.presentation:
        before_by_provider = {item.identity: item for item in previous.presentation.providers}
        for after in current.presentation.providers:
            before = before_by_provider.get(after.identity)
            feature_prefix = prefix(
                "presentation",
                after.provider_id,
                after.source_instance_id,
            )
            if before is None:
                changed.update(
                    f"{feature_prefix}.{field}"
                    for field in (
                        "menu_visible", "reset_celebrations", "reset_overlay",
                        "reset_hardware", "reset_notification", "reset_sound",
                        "threshold_remaining",
                    )
                )
                continue
            for field in (
                "menu_visible", "reset_celebrations", "reset_overlay",
                "reset_hardware", "reset_notification", "reset_sound",
                "threshold_remaining",
            ):
                if getattr(before, field) != getattr(after, field):
                    changed.add(f"{feature_prefix}.{field}")
        for field in (
            "show_meters",
            "show_totals",
            "show_cost",
            "show_detail_lanes",
            "show_menu_bar_percent",
            "privacy_mode",
        ):
            if getattr(current.presentation.menu, field) != getattr(previous.presentation.menu, field):
                changed.add(f"presentation.menu.{field}")
    if current.sync != previous.sync:
        for field in ("enabled", "device_id", "categories", "peers"):
            if getattr(current.sync, field) != getattr(previous.sync, field):
                changed.add(f"sync.{field}")
    if len(changed) > MAX_FEATURE_IDS:
        raise ValueError("provider settings change exceeds its bound")
    return frozenset(changed)


def project_provider_feature_settings(
    usage_settings: ProviderUsageSettings,
    sync_settings: ProviderSyncSettings,
    *,
    previous: ProviderFeatureSettingsProjection | None = None,
    revision: int | None = None,
) -> ProviderFeatureSettingsProjection:
    """Project durable documents and issue a monotonic change receipt."""

    collection = project_collection_settings(usage_settings)
    presentation = project_presentation_settings(usage_settings)
    sync = project_sync_settings(sync_settings)
    if previous is not None and type(previous) is not ProviderFeatureSettingsProjection:
        raise TypeError("expected ProviderFeatureSettingsProjection as previous")
    expected_revision = 0 if previous is None else previous.receipt.revision + 1
    if revision is None:
        revision = expected_revision
    if type(revision) is not int or revision < 0:
        raise ValueError("revision must be a non-negative integer")
    if previous is not None and revision <= previous.receipt.revision:
        raise ValueError("revision must be monotonic")
    provisional = ProviderFeatureSettingsProjection(
        collection,
        presentation,
        sync,
        ProviderSettingsChangeReceipt(revision, frozenset()),
    )
    receipt = ProviderSettingsChangeReceipt(
        revision,
        _changed_feature_ids(provisional, previous),
    )
    return ProviderFeatureSettingsProjection(collection, presentation, sync, receipt)


class ProviderSettingsChangeTracker:
    """Stateful convenience wrapper that advances revisions on each update."""

    __slots__ = ("_projection",)

    def __init__(self) -> None:
        self._projection: ProviderFeatureSettingsProjection | None = None

    @property
    def projection(self) -> ProviderFeatureSettingsProjection | None:
        return self._projection

    def update(
        self,
        usage_settings: ProviderUsageSettings,
        sync_settings: ProviderSyncSettings,
    ) -> ProviderFeatureSettingsProjection:
        self._projection = project_provider_feature_settings(
            usage_settings,
            sync_settings,
            previous=self._projection,
        )
        return self._projection

    observe = update


__all__ = [
    "MAX_FEATURE_IDS",
    "MAX_FEATURE_ID_LENGTH",
    "CollectionSettingsProjection",
    "PresentationSettingsProjection",
    "ProviderCollectionFeature",
    "ProviderCollectionSettings",
    "ProviderCollectionSettingsProjection",
    "ProviderFeatureSettings",
    "ProviderFeatureSettingsProjection",
    "ProviderInstancePolicyProjection",
    "ProviderInstanceRetentionPolicy",
    "ProviderInstanceRetentionProjection",
    "ProviderInstanceSessionActionPolicy",
    "ProviderInstanceSessionActionProjection",
    "ProviderInstanceSharingPolicy",
    "ProviderInstanceSharingProjection",
    "ProviderInstanceVisualPolicy",
    "ProviderInstanceVisualProjection",
    "ProviderMenuPresentation",
    "ProviderPresentationFeature",
    "ProviderPresentationSettings",
    "ProviderPresentationSettingsProjection",
    "ProviderSettingsChangeReceipt",
    "ProviderSettingsChangeTracker",
    "ProviderSyncFeatureSettingsProjection",
    "ProviderSyncSettingsProjection",
    "SyncSettingsProjection",
    "project_collection_settings",
    "project_instance_policies",
    "project_presentation_settings",
    "project_provider_collection_settings",
    "project_provider_feature_settings",
    "project_provider_presentation_settings",
    "project_provider_sync_settings",
    "project_sync_settings",
]
