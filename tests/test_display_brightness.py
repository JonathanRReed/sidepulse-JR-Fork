from __future__ import annotations

from unittest.mock import call, patch

import pytest

from sidepulse import display_brightness


def test_display_services_read_is_authoritative_for_active_display() -> None:
    with patch.object(
        display_brightness,
        "_current_display_context",
        return_value=(41, True),
    ), patch.object(
        display_brightness,
        "display_services_brightness_fraction",
        return_value=0.37,
    ) as display_services, patch.object(
        display_brightness,
        "_ioreg_brightness_fraction",
    ) as ioreg:
        assert display_brightness.current_screen_brightness_fraction() == 0.37

    display_services.assert_called_once_with(41)
    ioreg.assert_not_called()


def test_builtin_display_can_use_ioreg_nits_fallback() -> None:
    with patch.object(
        display_brightness,
        "_current_display_context",
        return_value=(42, True),
    ), patch.object(
        display_brightness,
        "display_services_brightness_fraction",
        return_value=None,
    ), patch.object(
        display_brightness,
        "_ioreg_brightness_fraction",
        return_value=0.25,
    ) as ioreg:
        assert display_brightness.current_screen_brightness_fraction() == 0.25

    ioreg.assert_called_once_with()


def test_unsupported_external_display_does_not_borrow_builtin_fallback() -> None:
    with patch.object(
        display_brightness,
        "_current_display_context",
        return_value=(43, False),
    ), patch.object(
        display_brightness,
        "display_services_brightness_fraction",
        return_value=None,
    ), patch.object(
        display_brightness,
        "_ioreg_brightness_fraction",
    ) as ioreg, pytest.raises(
        display_brightness.DisplayBrightnessUnavailableError,
        match="external display",
    ):
        display_brightness.current_screen_brightness_fraction()

    ioreg.assert_not_called()


@pytest.mark.parametrize(
    ("active", "asleep"),
    ((False, False), (True, True)),
)
def test_inactive_or_sleeping_display_skips_brightness_readers(
    monkeypatch,
    active: bool,
    asleep: bool,
) -> None:
    monkeypatch.setattr(display_brightness, "_main_display_id", lambda: 44)
    monkeypatch.setattr(
        display_brightness,
        "_display_state",
        lambda _display_id: (active, asleep, True),
    )
    with patch.object(
        display_brightness,
        "display_services_brightness_fraction",
    ) as display_services, patch.object(
        display_brightness,
        "_ioreg_brightness_fraction",
    ) as ioreg, pytest.raises(
        display_brightness.DisplayBrightnessUnavailableError,
        match="inactive or asleep",
    ):
        display_brightness.current_screen_brightness_fraction()

    display_services.assert_not_called()
    ioreg.assert_not_called()


def test_main_display_identifier_is_resolved_for_every_read() -> None:
    with patch.object(
        display_brightness,
        "_current_display_context",
        side_effect=((101, True), (202, False)),
    ), patch.object(
        display_brightness,
        "display_services_brightness_fraction",
        side_effect=(0.2, 0.8),
    ) as display_services:
        assert display_brightness.current_screen_brightness_fraction() == 0.2
        assert display_brightness.current_screen_brightness_fraction() == 0.8

    assert display_services.call_args_list == [call(101), call(202)]


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        (
            '"BrightnessMilliNits"={"min"=3979,"value"=381794,'
            '"uncalMilliNits"=140000,"max"=1599999}',
            381794 / 1599999,
        ),
        ('"BrightnessMilliNits"={"value"=150,"max"=100}', 1.0),
        ('"BrightnessMilliNits"={"value"=25,"max"=0}', None),
        ('"brightness"={"value"=32768,"max"=65536}', None),
        ("", None),
    ),
)
def test_ioreg_nits_parser_uses_only_truthful_value_over_max(
    text: str,
    expected: float | None,
) -> None:
    actual = display_brightness.ioreg_nits_fraction(text)
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected)


def test_ioreg_query_is_bounded_and_uses_apple_arm_backlight() -> None:
    output = '"BrightnessMilliNits"={"value"=25,"max"=100}'
    with patch.object(display_brightness.subprocess, "run") as run:
        run.return_value.stdout = output
        assert display_brightness._ioreg_brightness_fraction() == 0.25

    run.assert_called_once_with(
        ["/usr/sbin/ioreg", "-rc", "AppleARMBacklight"],
        capture_output=True,
        text=True,
        timeout=display_brightness.IOREG_TIMEOUT_SECONDS,
        check=False,
    )


@pytest.mark.parametrize(
    ("status", "value"),
    ((1, 0.5), (0, float("nan")), (0, float("inf")), (0, -0.1), (0, 1.1)),
)
def test_display_services_rejects_failed_or_invalid_readings(
    status: int,
    value: float,
) -> None:
    class _DisplayServices:
        @staticmethod
        def DisplayServicesGetBrightness(_display_id, out_value) -> int:
            out_value._obj.value = value
            return status

    with patch.object(
        display_brightness,
        "_ensure_display_services_lib",
        return_value=_DisplayServices(),
    ):
        assert display_brightness.display_services_brightness_fraction(45) is None


def test_display_services_returns_valid_fraction() -> None:
    class _DisplayServices:
        @staticmethod
        def DisplayServicesGetBrightness(display_id, out_value) -> int:
            assert display_id.value == 46
            out_value._obj.value = 0.61
            return 0

    with patch.object(
        display_brightness,
        "_ensure_display_services_lib",
        return_value=_DisplayServices(),
    ):
        assert display_brightness.display_services_brightness_fraction(46) == pytest.approx(0.61)


def test_no_truthful_source_raises_instead_of_assuming_full_brightness() -> None:
    with patch.object(
        display_brightness,
        "_current_display_context",
        return_value=(47, True),
    ), patch.object(
        display_brightness,
        "display_services_brightness_fraction",
        return_value=None,
    ), patch.object(
        display_brightness,
        "_ioreg_brightness_fraction",
        return_value=None,
    ), pytest.raises(
        display_brightness.DisplayBrightnessUnavailableError,
        match="No trustworthy brightness reading",
    ):
        display_brightness.current_screen_brightness_fraction()
