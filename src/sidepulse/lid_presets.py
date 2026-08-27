"""Lid animation preset programs -- pure data, extracted from
settings_window for its size ratchet (2026-08-26). Four looks per
lid transition kind; every program is firmware-parsed by tests."""

from __future__ import annotations

from .settings import (
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_CLOSED_ACTIVE,
    LID_ANIMATION_OPEN,
    LID_ANIMATION_OPEN_ACTIVE,
)

LID_ANIMATION_PRESETS: dict[str, tuple[tuple[str, float, str], ...]] = {
    LID_ANIMATION_CLOSED: (
        ("Fade Out", 1.0, "#8A7CFF 300ms pulse\noff 700ms cosine"),
        ("Blink Out", 0.9, "#FF4F79 150ms pulse\noff 150ms linear\n#FF4F79 150ms pulse\noff 450ms cosine"),
        ("Ember", 1.6, "#FF9F0A 500ms pulse\n#5A3A00 400ms cosine\noff 700ms cosine"),
        ("Cool Down", 1.4, "#00E5FF 350ms pulse\n#0044AA 450ms cosine\noff 600ms cosine"),
    ),
    LID_ANIMATION_OPEN: (
        ("Rise", 1.0, "off 100ms linear\n#00E5FF 400ms cosine\n#00E5FF 500ms pulse"),
        ("Hello", 1.4, "#12E3B0 300ms pulse\n#0FA07C 300ms cosine\n#12E3B0 800ms pulse"),
        ("Sunrise", 1.6, "#331A00 300ms cosine\n#FF9F0A 600ms cosine\n#FFD60A 700ms pulse"),
        ("Quick Blink", 0.8, "#FFFFFF 150ms pulse\noff 150ms linear\n#FFFFFF 500ms pulse"),
    ),
    # Agents-running variants: unmistakably different rhythms so the
    # lid itself tells you work is still cooking.
    LID_ANIMATION_CLOSED_ACTIVE: (
        ("Still Cooking", 1.5, "#FF9F0A 300ms pulse\n#FF9F0A 250ms cosine\n#5A3A00 350ms cosine\n#1A1200 600ms cosine"),
        ("Baton Pass", 1.2, "#00E5FF 250ms pulse\n#8A7CFF 250ms cosine\n#12E3B0 250ms pulse\noff 450ms cosine"),
        ("Ember Watch", 1.8, "#FF6A3D 400ms pulse\n#802000 500ms cosine\n#331000 900ms cosine"),
        # Named for the shape it actually has. Two equal hard-ish thumps
        # and a long rest is a KNOCK; a heartbeat's second thump is dimmer
        # than its first, which is the whole difference between the two in
        # the signal vocabulary. Calling this one "Heartbeat" left the
        # window using one word for two motions.
        ("Knock Out", 1.3, "#FF2D55 150ms pulse\noff 120ms linear\n#FF2D55 150ms pulse\noff 880ms cosine"),
    ),
    LID_ANIMATION_OPEN_ACTIVE: (
        ("Back On It", 1.2, "#12E3B0 200ms pulse\n#00E5FF 300ms cosine\n#00E5FF 700ms pulse"),
        ("Status Sweep", 1.4, "#8A7CFF 250ms pulse\n#00E5FF 250ms cosine\n#12E3B0 250ms pulse\n#12E3B0 650ms pulse"),
        ("Rekindle", 1.6, "#331000 300ms cosine\n#FF6A3D 500ms cosine\n#FFD60A 800ms pulse"),
        ("Double Take", 1.0, "#FFFFFF 120ms pulse\noff 100ms linear\n#00E5FF 180ms pulse\n#00E5FF 600ms pulse"),
    ),
}
