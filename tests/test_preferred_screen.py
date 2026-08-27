"""The Screen Bar belongs on the notched display, not the focused one.

NSScreen.mainScreen() is the screen holding the KEY WINDOW, so focusing
any window on an external monitor made the bar frame itself against a
display with no notch -- one of the two causes of the 2026-08-27
overhang report (the other was never re-deriving geometry at all).
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("AppKit")


def _screen(name, top):
    return SimpleNamespace(
        localizedName=lambda _n=name: _n,
        safeAreaInsets=lambda _t=top: SimpleNamespace(top=_t),
    )


def _with_screens(monkeypatch, screens, main=None):
    """Replace the module-level NSScreen name -- ObjC classes refuse
    attribute patching, and preferred_screen resolves it there."""
    from sidepulse import virtual_device

    monkeypatch.setattr(
        virtual_device,
        "NSScreen",
        SimpleNamespace(
            screens=lambda: list(screens),
            mainScreen=lambda: main,
        ),
    )
    return virtual_device


def test_the_notched_screen_wins_over_the_focused_one(monkeypatch):
    external = _screen("Studio Display", 0.0)
    builtin = _screen("Built-in Retina Display", 32.0)
    vd = _with_screens(monkeypatch, [external, builtin], main=external)

    assert vd.preferred_screen() is builtin


def test_a_named_screen_wins_even_without_a_safe_area(monkeypatch):
    external = _screen("Studio Display", 0.0)
    builtin = _screen("Built-in Retina Display", 32.0)
    vd = _with_screens(monkeypatch, [external, builtin], main=builtin)

    assert vd.preferred_screen("Studio Display") is external


def test_an_unattached_name_falls_back_to_the_notched_screen(monkeypatch):
    external = _screen("Studio Display", 0.0)
    builtin = _screen("Built-in Retina Display", 32.0)
    vd = _with_screens(monkeypatch, [external, builtin], main=external)

    assert vd.preferred_screen("Unplugged Monitor") is builtin


def test_a_single_notchless_screen_still_works(monkeypatch):
    only = _screen("Some Display", 0.0)
    vd = _with_screens(monkeypatch, [only], main=only)

    assert vd.preferred_screen() is only


def test_the_display_change_handler_is_actually_registered():
    """screenDidChange_ existed fully written and NOTHING registered it,
    so the bar never re-derived geometry after a display change."""
    from pathlib import Path as _Path

    from sidepulse import virtual_device

    source = _Path(virtual_device.__file__).read_text(encoding="utf-8")
    assert "NSApplicationDidChangeScreenParametersNotification" in source
    assert "addObserver_selector_name_object_" in source
    assert b"screenParametersDidChange:".decode() in source
    assert "_install_screen_geometry_observer" in source
    # the burst macOS posts per transition is coalesced, not repositioned
    # once per notification
    assert "cancelPreviousPerformRequestsWithTarget_selector_object_" in source
    assert "applyScreenParameterChange_" in source
    # and the observer is torn down everywhere the power observers are
    assert source.count("_remove_screen_geometry_observer()") >= 3
