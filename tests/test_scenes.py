import json

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.dnd_policy import DisplayAdmission
from sidepulse.scenes import (
    DEFAULT_SCENE,
    SCENE_POLICIES,
    DeviceSelection,
    MotionLevel,
    NotificationMode,
    Scene,
    SurfaceRole,
    effective_policy_for_scene,
    policy_for_scene,
    scene_from_value,
    scene_options,
)
from sidepulse.settings import AgentMonitorSettings, load_settings, save_settings


def test_all_scenes_have_bounded_policies():
    assert set(SCENE_POLICIES) == set(Scene)
    for policy in SCENE_POLICIES.values():
        assert 0.0 <= policy.brightness <= 1.0
        assert isinstance(policy.display_admission, DisplayAdmission)


def test_scene_parsing_fails_closed():
    assert scene_from_value("focus") is Scene.FOCUS
    assert scene_from_value("unknown") is None
    assert scene_from_value(None) is None
    assert policy_for_scene("unknown") is None
    assert policy_for_scene("focus", reduce_motion="yes") is None


def test_reduce_motion_changes_effective_motion_without_mutating_policy():
    policy = policy_for_scene(Scene.DEMO, reduce_motion=True)
    assert policy is not None
    assert policy.motion is MotionLevel.FULL
    assert policy.effective_motion is MotionLevel.STATIC
    assert policy_for_scene(Scene.DEMO).effective_motion is MotionLevel.FULL


def test_effective_policy_consumes_existing_accessibility_snapshot():
    preferences = AccessibilityDisplayPreferences(reduce_motion=True)

    policy = effective_policy_for_scene(
        Scene.DEMO,
        accessibility_preferences=preferences,
    )

    assert policy is not None
    assert policy.motion is MotionLevel.FULL
    assert policy.effective_motion is MotionLevel.STATIC


def test_scene_semantics():
    assert policy_for_scene(Scene.NIGHT).notifications is NotificationMode.NONE
    assert policy_for_scene(Scene.DND).device_selection is DeviceSelection.NONE
    assert policy_for_scene(Scene.CALM).surface_role is SurfaceRole.AMBIENT


def test_options_are_stable_and_complete():
    assert scene_options() == tuple(Scene)


def test_settings_persist_active_scene_and_apply_accessibility_snapshot(tmp_path):
    path = tmp_path / "settings.json"
    settings = AgentMonitorSettings().with_active_scene(Scene.DEMO)

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.active_scene == Scene.DEMO.value
    assert loaded.to_dict()["active_scene"] == Scene.DEMO.value
    policy = loaded.effective_scene_policy(
        AccessibilityDisplayPreferences(reduce_motion=True)
    )
    assert policy is not None
    assert policy.scene is Scene.DEMO
    assert policy.effective_motion is MotionLevel.STATIC


def test_settings_default_scene_is_backward_compatible_for_missing_or_bad_values(
    tmp_path,
):
    missing_path = tmp_path / "missing-scene.json"
    missing_path.write_text('{"settings_schema_version": 2}\n')
    invalid_path = tmp_path / "invalid-scene.json"
    invalid_path.write_text(
        json.dumps({"settings_schema_version": 2, "active_scene": "unknown"})
    )

    assert load_settings(missing_path).active_scene == DEFAULT_SCENE.value
    assert load_settings(invalid_path).active_scene == DEFAULT_SCENE.value
