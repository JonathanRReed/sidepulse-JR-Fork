"""Pure planning for stable native menu publication.

This module owns no AppKit objects, clocks, provider work, or persistence.  It
decides whether a prepared menu snapshot can patch existing native items while
tracking, or whether the latest snapshot must wait for the menu to close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .navigation_policy import OperatorActionKind


class MenuPublicationKind(str, Enum):
    NO_CHANGE = "no-change"
    PATCH_IN_PLACE = "patch-in-place"
    DEFER_REBUILD = "defer-rebuild"


@dataclass(frozen=True, slots=True)
class MenuItemState:
    item_key: str
    parent_key: str | None
    order: int
    submenu_key: str | None
    action_kind: OperatorActionKind | None
    key_equivalent: str
    title: str
    enabled: bool
    state: int
    measured_width: int
    measured_height: int
    accessibility_label: str
    accessibility_value: str
    accessibility_help: str

    def __post_init__(self) -> None:
        text_values = (
            self.item_key,
            self.key_equivalent,
            self.title,
            self.accessibility_label,
            self.accessibility_value,
            self.accessibility_help,
        )
        optional_text_values = (self.parent_key, self.submenu_key)
        if not (
            all(type(value) is str for value in text_values)
            and self.item_key
            and all(value is None or type(value) is str for value in optional_text_values)
            and type(self.order) is int
            and self.order >= 0
            and (self.action_kind is None or type(self.action_kind) is OperatorActionKind)
            and len(self.key_equivalent) <= 1
            and type(self.enabled) is bool
            and type(self.state) is int
            and type(self.measured_width) is int
            and self.measured_width >= 0
            and type(self.measured_height) is int
            and self.measured_height >= 0
        ):
            raise ValueError("invalid menu item state")


@dataclass(frozen=True, slots=True)
class MenuPublication:
    kind: MenuPublicationKind
    patches: tuple[MenuItemState, ...]
    next_boundary_epoch: float | None


class VisitEvidenceKind(str, Enum):
    ROOT_OPEN = "root-open"
    SHELF_REVEALED = "shelf-revealed"
    ROW_FOCUSED = "row-focused"
    ROW_ACTIVATED = "row-activated"
    BROWSER_ROW_FOCUSED = "browser-row-focused"


_PATCHABLE_FIELDS = frozenset(
    {
        "title",
        "enabled",
        "state",
        "accessibility_value",
        "accessibility_help",
    }
)


def _structural_identity(item: MenuItemState) -> tuple[object, ...]:
    return (
        item.item_key,
        item.parent_key,
        item.order,
        item.submenu_key,
        item.action_kind,
        item.key_equivalent,
        item.measured_width,
        item.measured_height,
        item.accessibility_label,
    )


def plan_menu_publication(
    previous: tuple[MenuItemState, ...],
    current: tuple[MenuItemState, ...],
    *,
    tracking: bool,
) -> MenuPublication:
    """Return the smallest geometry-safe publication for two snapshots."""
    if not (
        type(previous) is tuple
        and type(current) is tuple
        and type(tracking) is bool
        and all(type(item) is MenuItemState for item in (*previous, *current))
    ):
        raise ValueError("invalid menu publication input")
    if previous == current:
        return MenuPublication(MenuPublicationKind.NO_CHANGE, (), None)

    previous_identities = tuple(_structural_identity(item) for item in previous)
    current_identities = tuple(_structural_identity(item) for item in current)
    if previous_identities != current_identities:
        return MenuPublication(MenuPublicationKind.DEFER_REBUILD, (), None)

    patches = tuple(
        after
        for before, after in zip(previous, current, strict=True)
        if any(getattr(before, field) != getattr(after, field) for field in _PATCHABLE_FIELDS)
    )
    if not patches:
        return MenuPublication(MenuPublicationKind.NO_CHANGE, (), None)
    return MenuPublication(MenuPublicationKind.PATCH_IN_PLACE, patches, None)


class StableNativeMenuRegistry:
    """Own one native item per stable key and apply only safe copy patches."""

    def __init__(self) -> None:
        self._states: tuple[MenuItemState, ...] = ()
        self._items: dict[str, object] = {}
        self._pending: tuple[MenuItemState, ...] | None = None

    def install(
        self,
        states: tuple[MenuItemState, ...],
        items: dict[str, object],
    ) -> None:
        keys = tuple(state.item_key for state in states)
        if len(keys) != len(set(keys)) or set(items) != set(keys):
            raise ValueError("native menu registry keys do not match")
        self._states = states
        self._items = dict(items)
        self._pending = None

    def item_for_key(self, item_key: str):
        return self._items.get(item_key)

    def publish(
        self,
        current: tuple[MenuItemState, ...],
        *,
        tracking: bool,
    ) -> MenuPublication:
        plan = plan_menu_publication(self._states, current, tracking=tracking)
        if plan.kind is MenuPublicationKind.PATCH_IN_PLACE and any(
            self._has_custom_view(state.item_key) for state in plan.patches
        ):
            plan = MenuPublication(MenuPublicationKind.DEFER_REBUILD, (), None)
        if plan.kind is MenuPublicationKind.DEFER_REBUILD:
            if tracking:
                self._pending = current
            return plan
        if plan.kind is MenuPublicationKind.PATCH_IN_PLACE:
            for state in plan.patches:
                self._patch_item(self._items[state.item_key], state)
            self._states = current
        return plan

    def take_deferred_after_close(self) -> tuple[MenuItemState, ...] | None:
        pending = self._pending
        self._pending = None
        return pending

    def _has_custom_view(self, item_key: str) -> bool:
        item = self._items[item_key]
        getter = getattr(item, "view", None)
        return callable(getter) and getter() is not None

    @staticmethod
    def _patch_item(item: object, state: MenuItemState) -> None:
        for selector, value in (
            ("setTitle_", state.title),
            ("setEnabled_", state.enabled),
            ("setState_", state.state),
            ("setAccessibilityValue_", state.accessibility_value),
            ("setAccessibilityHelp_", state.accessibility_help),
        ):
            setter = getattr(item, selector, None)
            if callable(setter):
                setter(value)


@dataclass(frozen=True, slots=True)
class BoundaryToken:
    generation: int
    deadline_epoch: float


class ExactBoundarySchedule:
    """Generation-fence one exact wall-clock copy boundary."""

    def __init__(self) -> None:
        self._generation = 0
        self._token: BoundaryToken | None = None

    @property
    def deadline_epoch(self) -> float | None:
        return None if self._token is None else self._token.deadline_epoch

    def replace(self, deadline_epoch: float) -> BoundaryToken:
        if not (
            isinstance(deadline_epoch, (int, float))
            and not isinstance(deadline_epoch, bool)
            and math.isfinite(deadline_epoch)
            and deadline_epoch >= 0.0
        ):
            raise ValueError("invalid menu boundary")
        self._generation += 1
        self._token = BoundaryToken(self._generation, float(deadline_epoch))
        return self._token

    def clear(self) -> None:
        self._generation += 1
        self._token = None

    def callback_due(self, token: BoundaryToken, *, now_epoch: float) -> bool:
        if self._token != token or not math.isfinite(now_epoch):
            return False
        if now_epoch < token.deadline_epoch:
            return False
        self._token = None
        return True
