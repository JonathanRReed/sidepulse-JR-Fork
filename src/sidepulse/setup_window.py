"""The first-run Welcome window, extracted whole from the monolith.

Extracted 2026-08-26 to honor the legacy-can-only-shrink ratchet while
the ambient/reconnect work added its (small, unavoidable) glue there.
The body is verbatim; every monolith-global it used arrives through the
function-level import below -- the same cycle-dodging pattern the
architecture doc blesses for colors/led_status.
"""

from __future__ import annotations

import time


def build_setup_window(target):
    """The welcome window: what SidePulse is (shown live, not described),
    which agents to connect, and the Mac-level installs -- a first-run
    moment that should feel like the product, not a permissions form."""
    from AppKit import (
        NSBackingStoreBuffered,
        NSLayoutConstraint,
        NSView,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskTitled,
    )

    from .status_bar_legacy import (
        HOOK_PROVIDERS,
        SD_EJECT_GUARD_DISPLAY_NAME,
        SETUP_DEMO_HEIGHT,
        SETUP_DEMO_WIDTH,
        ColorSettings,
        VirtualLedView,
        _setup_toggle_row,
        colors_module,
        native_ui,
        program_for_snapshot,
        provider_spec,
        running_inside_bundle,
        set_checkbox_state,
    )

    width, height = 680, 800
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Welcome to JR-BAR")
    window.setReleasedWhenClosed_(False)
    window.center()

    root = NSView.alloc().init()
    window.setContentView_(root)
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    root.heightAnchor().constraintEqualToConstant_(height).setActive_(True)

    stack = native_ui.make_fill_stack(spacing=14.0)
    scroll = native_ui.wrap_in_scroll_pane(stack, padding=0.0)
    root.addSubview_(scroll)

    # Hero: the product introduces itself by DOING the thing -- a live
    # LED strip playing the full-team demo, not a paragraph about LEDs.
    title = native_ui.make_label("JR-BAR", size=27.0, bold=True)
    hero_title_holder = native_ui.make_stack(orientation="vertical", spacing=0.0)
    hero_title_holder.addArrangedSubview_(title)
    stack.addArrangedSubview_(hero_title_holder)
    subtitle = native_ui.make_label("Your agents, at a glance — as light.", secondary=True, size=14.0)
    subtitle_holder = native_ui.make_stack(orientation="vertical", spacing=0.0)
    subtitle_holder.addArrangedSubview_(subtitle)
    stack.addArrangedSubview_(subtitle_holder)

    demo_container = native_ui.make_fixed_area(SETUP_DEMO_WIDTH, SETUP_DEMO_HEIGHT)
    demo_view = VirtualLedView.alloc().initWithFrame_(((0.0, 0.0), (SETUP_DEMO_WIDTH, SETUP_DEMO_HEIGHT)))
    demo_view.setHasNotch_(False)
    demo_colors = getattr(getattr(target, "settings", None), "colors", None) or ColorSettings.defaults()
    _, demo_program = program_for_snapshot(
        colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_FULL_TEAM),
        led_count=8,
        colors=demo_colors,
        brightness=255,
    )
    demo_view.setProgram_startedAt_(demo_program, time.monotonic())
    demo_container.addSubview_(demo_view)
    stack.addArrangedSubview_(demo_container)

    # Connect Your Agents: the same contextual one-action rows the
    # Settings Agents pane uses, so first-run and settings agree.
    agents_outer, agents_inner = native_ui.make_card("Connect Your Agents")
    setup_fields: dict[str, object] = {"demo_view": demo_view}
    setup_buttons: dict[str, object] = {}
    for index, provider in enumerate(HOOK_PROVIDERS):
        status_label = native_ui.make_label("", secondary=True, size=12.0)
        install_button = native_ui.make_button("Install", target, f"install{provider.title()}Hooks:")
        cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        cluster.addArrangedSubview_(status_label)
        cluster.addArrangedSubview_(install_button)
        agents_inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, cluster))
        if index < len(HOOK_PROVIDERS) - 1:
            native_ui.add_separator(agents_inner)
        setup_fields[f"setup_{provider}_status"] = status_label
        setup_buttons[f"setup_{provider}_install"] = install_button
    stack.addArrangedSubview_(agents_outer)

    # Set Up This Mac: the three system-level installs as switch rows.
    mac_outer, mac_inner = native_ui.make_card("Set Up This Mac")
    launch_row, launch, launch_status = _setup_toggle_row(
        "Run at Login", "Start the menu-bar app automatically."
    )
    mac_inner.addArrangedSubview_(launch_row)
    eject_row, eject_guard, eject_status = _setup_toggle_row(
        SD_EJECT_GUARD_DISPLAY_NAME,
        "Keep SidePulse Pro/SidePulse Dot available after sleep.",
    )
    mac_inner.addArrangedSubview_(eject_row)
    sleep_row, sleep_helper, sleep_status = _setup_toggle_row(
        "Closed-Lid Sleep Prevention",
        "Opens a one-time administrator setup in Terminal.",
    )
    mac_inner.addArrangedSubview_(sleep_row)
    # Full Disk Access unlocks Focus features (dimming per Focus mode).
    # It can't be granted programmatically -- the row states the status
    # and hands the user the Privacy pane.
    fda_status = native_ui.make_label("", secondary=True, size=12.0)
    fda_button = native_ui.make_button("Grant…", target, "openFullDiskAccessSettings:")
    fda_reveal = native_ui.make_button(
        "Reveal SidePulse" if running_inside_bundle() else "Reveal Program",
        target,
        "revealFocusBinaryInFinder:",
    )
    fda_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    fda_cluster.addArrangedSubview_(fda_status)
    fda_cluster.addArrangedSubview_(fda_reveal)
    fda_cluster.addArrangedSubview_(fda_button)
    mac_inner.addArrangedSubview_(
        native_ui.make_row(
            "Focus Detection (Full Disk Access)",
            fda_cluster,
            help_text=(
                "Lets SidePulse see which macOS Focus is active, so LEDs "
                "can dim or turn off per Focus. Grant\u2026 opens the "
                "Privacy pane; click +, then pick the app Reveal shows "
                "you (macOS won't list it by itself). The full "
                "walkthrough lives in Settings → Notifications & Focus → Focus."
            ),
        )
    )
    eject_uninstall = native_ui.make_button("Uninstall", target, "uninstallSdEjectGuard:")
    eject_uninstall.setHidden_(True)
    uninstall_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    uninstall_cluster.addArrangedSubview_(eject_uninstall)
    uninstall_cluster.addArrangedSubview_(native_ui.make_hspacer())
    mac_inner.addArrangedSubview_(uninstall_cluster)
    stack.addArrangedSubview_(mac_outer)

    # Footer: transient message + the two actions, pinned below the
    # scrollable body so a long error cannot paint over the Mac card.
    message = native_ui.make_wrapping_label("", secondary=True, size=12.0, max_width=420.0)
    skip_button = native_ui.make_button("Skip for Now", target, "skipFirstLaunchSetup:")
    setup_button = native_ui.make_button("Set Up", target, "runFirstLaunchSetup:")
    setup_button.setKeyEquivalent_("\r")
    actions = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    actions.addArrangedSubview_(native_ui.make_hspacer())
    actions.addArrangedSubview_(skip_button)
    actions.addArrangedSubview_(setup_button)
    footer = native_ui.make_stack(orientation="vertical", spacing=8.0)
    footer.addArrangedSubview_(message)
    footer.addArrangedSubview_(actions)
    root.addSubview_(footer)
    NSLayoutConstraint.activateConstraints_(
        [
            scroll.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 28.0),
            scroll.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 28.0),
            scroll.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -28.0),
            scroll.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -12.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 28.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -28.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -20.0),
        ]
    )

    setup_fields.update(
        {
            "launch_status": launch_status,
            "eject_status": eject_status,
            "sleep_status": sleep_status,
            "fda_status": fda_status,
            "message": message,
        }
    )
    setup_buttons["fda_grant"] = fda_button
    setup_buttons.update(
        {
            "launch": launch,
            "eject_guard": eject_guard,
            "eject_guard_uninstall": eject_uninstall,
            "sleep_helper": sleep_helper,
        }
    )
    # Recommended defaults, set ONCE here -- refresh_setup_window only
    # touches enablement, never the checked state, so a user's opt-out
    # survives every refresh (each provider Install click triggers one).
    for key in ("launch", "eject_guard", "sleep_helper"):
        set_checkbox_state(setup_buttons[key], True)
    target.setup_fields = setup_fields
    target.setup_buttons = setup_buttons
    return window
