"""Compact AppKit renderer for an immutable usage heatmap."""

from __future__ import annotations

import math

import objc
from AppKit import NSColor, NSPopUpButton, NSTextField, NSView

from .usage_heatmap import UsageHeatmap

_WIDTH = 560.0
_HEIGHT = 126.0
_HEADER_HEIGHT = 26.0
_CELL_GAP = 2.0


def _color(value: str):
    cleaned = value.lstrip("#")
    try:
        red, green, blue = (int(cleaned[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    except (ValueError, IndexError):
        return NSColor.quaternaryLabelColor()
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


class UsageHeatmapView(NSView):
    """One provider at a time, with an aggregate option and accessible cells."""

    def initWithFrame_(self, frame):
        self = objc.super(UsageHeatmapView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._heatmap = None
        self._provider_id = "all"
        self._cell_views = []

        self._picker = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((0.0, _HEIGHT - _HEADER_HEIGHT), (180.0, 24.0)), False
        )
        self._picker.setTarget_(self)
        self._picker.setAction_("selectProvider:")
        self._picker.setAccessibilityLabel_("Daily activity source")
        self.addSubview_(self._picker)

        self._summary = NSTextField.labelWithString_("Scanning local activity…")
        self._summary.setFrame_(((190.0, _HEIGHT - 22.0), (360.0, 18.0)))
        self._summary.setTextColor_(NSColor.secondaryLabelColor())
        self._summary.setAccessibilityLabel_("Daily activity summary")
        self.addSubview_(self._summary)
        return self

    def setHeatmap_(self, heatmap):
        if type(heatmap) is not UsageHeatmap:
            return
        self._heatmap = heatmap
        available_ids = tuple(heatmap.providers)
        if self._provider_id not in {"all", *available_ids}:
            self._provider_id = "all"
        self._picker.removeAllItems()
        self._picker.addItemWithTitle_("All providers")
        self._picker.lastItem().setRepresentedObject_("all")
        for provider_id in available_ids:
            self._picker.addItemWithTitle_(provider_id.replace("_", " ").title())
            self._picker.lastItem().setRepresentedObject_(provider_id)
        for index in range(self._picker.numberOfItems()):
            item = self._picker.itemAtIndex_(index)
            if str(item.representedObject()) == self._provider_id:
                self._picker.selectItemAtIndex_(index)
                break
        self._render_cells()

    @objc.IBAction
    def selectProvider_(self, sender):
        item = sender.selectedItem()
        provider_id = str(item.representedObject() or "") if item is not None else ""
        if self._heatmap is None or provider_id not in {
            "all",
            *self._heatmap.providers,
        }:
            return
        self._provider_id = provider_id
        self._render_cells()

    def _render_cells(self):
        for view in self._cell_views:
            view.removeFromSuperview()
        self._cell_views = []
        heatmap = self._heatmap
        if heatmap is None:
            return
        provider = heatmap.aggregate if self._provider_id == "all" else heatmap.providers[self._provider_id]
        title = "All providers" if self._provider_id == "all" else self._provider_id.replace("_", " ").title()
        if provider.data_status == "unavailable":
            self._summary.setStringValue_(f"{title} · local activity unavailable")
        else:
            self._summary.setStringValue_(
                f"{title} · {provider.totals.tokens:,} tokens · {provider.totals.sessions} sessions"
            )

        offset = heatmap.days[0].weekday()
        columns = max(1, math.ceil((offset + len(heatmap.days)) / 7))
        cell_size = max(4.0, min(10.0, (_WIDTH - (columns - 1) * _CELL_GAP) / columns))
        for index, cell in enumerate(provider.cells):
            position = offset + index
            column, row = divmod(position, 7)
            x = column * (cell_size + _CELL_GAP)
            y = _HEIGHT - _HEADER_HEIGHT - (row + 1) * (cell_size + _CELL_GAP)
            cell_view = NSView.alloc().initWithFrame_(((x, y), (cell_size, cell_size)))
            cell_view.setWantsLayer_(True)
            layer = cell_view.layer()
            layer.setBackgroundColor_(_color(cell.color).CGColor())
            layer.setCornerRadius_(min(2.0, cell_size / 4.0))
            if provider.data_status == "unavailable":
                layer.setBackgroundColor_(NSColor.clearColor().CGColor())
                layer.setBorderColor_(NSColor.tertiaryLabelColor().CGColor())
                layer.setBorderWidth_(1.0)
            cell_view.setAccessibilityElement_(True)
            cell_view.setAccessibilityRole_("AXStaticText")
            cell_view.setAccessibilityLabel_(cell.accessibility_label)
            cell_view.setToolTip_(cell.accessibility_label)
            self.addSubview_(cell_view)
            self._cell_views.append(cell_view)


__all__ = ["UsageHeatmapView"]
