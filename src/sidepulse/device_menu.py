"""The dropdown's per-device item, extracted whole from the monolith.

Extracted 2026-08-26 to honor the legacy-can-only-shrink ratchet while
the snooze-scope and runway wiring added their (small, unavoidable)
claims there. The body is verbatim; every monolith global it used
arrives through the function-level import below -- the same
cycle-dodging pattern setup_window.py and mailbox_menu.py bless.
"""

from __future__ import annotations


def build_device_menu_item(device, target):
    from .status_bar_legacy import (
        LED_DISPLAY_AGENT,
        LED_DISPLAY_BATTERY,
        VIRTUAL_DEVICE_ID,
        NSMenu,
        NSMenuItem,
        brightness_percent,
        build_brightness_slider_item,
        build_channel_gain_slider_item,
        disabled_menu_item,
    )

    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(device.name, None, "")
    item.setState_(1 if device.connected else 0)
    submenu = NSMenu.alloc().init()

    agent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Agent Status",
        "setDeviceDisplayAgent:",
        "",
    )
    agent.setTarget_(target)
    agent.setRepresentedObject_(device.device_id)
    agent.setState_(1 if device.display == LED_DISPLAY_AGENT else 0)
    submenu.addItem_(agent)

    battery = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Battery Level",
        "setDeviceDisplayBattery:",
        "",
    )
    battery.setTarget_(target)
    battery.setRepresentedObject_(device.device_id)
    battery.setState_(1 if device.display == LED_DISPLAY_BATTERY else 0)
    submenu.addItem_(battery)

    submenu.addItem_(NSMenuItem.separatorItem())
    submenu.addItem_(disabled_menu_item(f"Brightness {brightness_percent(device.brightness)}%"))
    submenu.addItem_(build_brightness_slider_item(device, target))
    auto_brightness = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Auto-Brightness (matches screen)",
        "toggleDeviceAutoBrightness:",
        "",
    )
    auto_brightness.setTarget_(target)
    auto_brightness.setRepresentedObject_(device.device_id)
    auto_brightness.setState_(1 if device.auto_brightness_enabled else 0)
    submenu.addItem_(auto_brightness)

    submenu.addItem_(NSMenuItem.separatorItem())
    red_gain, green_gain, blue_gain = device.channel_gains
    submenu.addItem_(
        disabled_menu_item(
            f"Color Calibration -- R{round(red_gain * 100)}% "
            f"G{round(green_gain * 100)}% B{round(blue_gain * 100)}%"
        )
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Red", red_gain, "setDeviceRedGain:")
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Green", green_gain, "setDeviceGreenGain:")
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Blue", blue_gain, "setDeviceBlueGain:")
    )
    reset_calibration = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Reset Calibration",
        "resetDeviceColorCalibration:",
        "",
    )
    reset_calibration.setTarget_(target)
    reset_calibration.setRepresentedObject_(device.device_id)
    submenu.addItem_(reset_calibration)

    if not device.connected:
        submenu.addItem_(NSMenuItem.separatorItem())
        submenu.addItem_(disabled_menu_item("Not connected"))
        remove = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Remove",
            "removeRememberedDevice:",
            "",
        )
        remove.setTarget_(target)
        remove.setRepresentedObject_(device.device_id)
        submenu.addItem_(remove)

    if device.device_id == VIRTUAL_DEVICE_ID:
        submenu.addItem_(NSMenuItem.separatorItem())
        remove_bar = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Remove Screen Bar",
            "toggleVirtualStatusDevice:",
            "",
        )
        remove_bar.setTarget_(target)
        submenu.addItem_(remove_bar)
    elif device.connected and device.target is not None:
        # The software replug, one click: the 2026-08-20 firmware wedge
        # needed the guard daemon paused, the volume ejected, and a
        # physical reseat -- a ritual the owner should never have to
        # know. See resetStripDevice_.
        submenu.addItem_(NSMenuItem.separatorItem())
        reset_strip = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Reset Strip (eject, then replug)…",
            "resetStripDevice:",
            "",
        )
        reset_strip.setTarget_(target)
        reset_strip.setRepresentedObject_(str(device.target))
        submenu.addItem_(reset_strip)

    item.setSubmenu_(submenu)
    return item
