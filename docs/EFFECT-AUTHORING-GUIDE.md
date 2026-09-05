# Effect authoring guide

JR-Bar effects are semantic, finite, and data-driven. An effect communicates a bounded meaning on one or more approved surfaces. It does not execute code, inspect provider content, bypass display policy, or own hardware writes. The authoring path is `effect_registry` -> semantic router -> finite policy -> runtime surface.

## Define an effect in the registry

`src/sidepulse/effect_registry.py` owns the immutable metadata model. `EffectDefinition` requires an identifier, label, description, meaning, at least one unique surface, and a positive version. `safety` is `safe`, `attention`, or `critical`; `energy` is `low`, `medium`, or `high`. Parameters are typed `EffectParameter` values, normalized in declaration order. Supported types are boolean, integer, number, choice, color, and palette.

Use the public APIs `get_effect`, `list_effects`, `normalize_effect_parameters`, `reduced_motion_effect`, and `provider_animation_effects`. Built-ins include `none`, `pulse`, `rainbow`, `alert`, and `notification`, plus the provider animation catalog. Built-in definitions declare surfaces such as `status_bar`, `screen_bar`, `sidepulse_pro`, `sidepulse_dot`, `glance_light`, and `settings_preview`; a router or renderer must not assume every effect works everywhere.

```python
from sidepulse.effect_registry import EffectDefinition, EffectParameter

steady = EffectDefinition(
    identifier="steady-example",
    label="Steady Example",
    description="A static example presentation.",
    meaning="example state",
    surfaces=("status_bar", "settings_preview"),
    reduce_motion_fallback="none",
    parameter_metadata=(
        EffectParameter("color", "color", "#FFFFFF", "Display color."),
    ),
)
```

The constructor validates parameter names, bounds, choices, palettes, fallback metadata, and surface adaptations. `EffectRegistry` rejects conflicting identifiers. For a local extension, build a new `EffectRegistry` containing the existing definitions and the new immutable definition. Do not mutate `EFFECT_REGISTRY` at runtime.

## Semantic routing is the authority

`src/sidepulse/semantic_effect_router.py` converts content-free `SemanticEffectCandidate` values into one `SemanticEffectSelection`. Candidate keys are opaque and bounded; sequence numbers break ties. The fixed priority is ASK, FAILURE, NOTIFICATION, HANDOFF, WORK, COMPLETION, RECOVERY, ENVIRONMENT, IDLE.

`route_semantic_effects()` applies display admission, courtesy suppression, the `SemanticEffectMap`, registry lookup, Reduce Motion fallback, and destination filtering. The result records the winner and typed suppression reasons. Missing effects and unsupported surfaces suppress that candidate and allow a later candidate to win. ASK and FAILURE are urgent and cannot be replaced by Scene-specific assignments. The router performs no rendering, settings access, timing, notifications, or device I/O.

```python
selection = route_semantic_effects(
    (SemanticEffectCandidate("agent-7", SemanticEventKind.ASK, sequence=12),),
    display_admission=DisplayAdmission.ALL,
    reduce_motion=False,
)
# selection.registry_effect_identifier == "alert"
```

A new semantic meaning requires a reviewed change to `SemanticEventKind`, priority, default map, and tests. Do not encode provider names or prompt text in a semantic candidate.

## Finite motion policy

`src/sidepulse/finite_effect_policy.py` is the execution contract for timed motion. Call `plan_one_shot_intro`, `plan_finite_loop`, or `plan_finite_effect`. An unbounded `RepeatStep(count=None)` is never preserved. Repetitions are clamped to `MAX_FINITE_REPETITIONS` and the duration budget, and a cycle that would exceed 2 Hz receives a dark rest so `effective_hz <= 2.0`.

Reduce Motion is applied before motion planning and returns a static fallback plan with `FiniteEffectDecision.STATIC_SUBSTITUTE` and `StaticSubstitutionReason.REDUCE_MOTION`. No timed intro or loop may leak through. A plan that cannot fit one complete cycle also becomes a static fallback. The policy is pure; the runtime still sends the resulting animation through the existing writer and device validation gates.

## Packs, history, and runtime ownership

`src/sidepulse/effect_packs.py` accepts only bounded JSON data. `validate_pack()` migrates version 1 to `CURRENT_PACK_VERSION` 2, requires `safety.data_only: true`, `safety.network: false`, and accessibility support for reduced motion and high contrast. `_reject_code()` rejects executable keys and code-like markers. `effect_definitions_from_pack()` namespaces IDs as `pack:<pack_id>:<effect_id>` and validates local fallbacks. `registry_with_pack()` returns a new registry and rejects collisions. `preview_pack()` is safe UI metadata; `export_pack()` emits canonical JSON.

Community packs may include one optional pack-level `license` object with a
bounded SPDX identifier, human-readable label, and optional absolute HTTP or
HTTPS source and attribution URLs. Licensing facts survive canonical export
and import planning. `project_gallery_pack()` and `build_gallery_index()`
derive a deterministic, immutable gallery index from validated manifests;
they do not create a network store, execute code, or mutate the registry.

Never treat a pack as an importable plugin. Pack parameters are metadata until a reviewed renderer understands them. A pack fallback must refer to another effect in the same pack. Preserve the original source and attribution URLs when redistributing a licensed pack, and do not infer permission from a missing license object.

`src/sidepulse/effect_history.py` records what JR-Bar attempted to present, where, and the bounded policy outcome. `EffectEvent` has a product-owned effect ID, semantic category, surface, outcome, optional closed suppression reason, and optional acknowledgement source. `record_effect_event`, `record_effect_events`, `mark_effect_history_seen`, and `project_effect_history` preserve bounded, content-free history. It stores no prompt, transcript, session identity, path, URL, or navigation target. Use the effect history store/runtime for persistence and scheduling, not the pure router or registry.

The runtime owner composes routing, finite planning, admission and power policy, and the surface writer. Keep hardware writes, timers, notifications, and persistence outside registry, router, and policy modules. A settings preview is not proof that a physical device accepted a frame.

## Settings preview and accessibility

`src/sidepulse/effect_studio.py` provides pure UI projections and explicit preview plans. It bounds search text, synthetic scenarios, timeline duration, assignment targets, source age, effect expiration, and physical preview duration. `plan_preview()` requires a registered effect and a typed preview session. Physical preview is at most 30 seconds and returns `CONSENT_REQUIRED` unless exact consent is supplied, with release triggers for close, sleep, app termination, and error.

`src/sidepulse/settings_preview_policy.py` keeps settings previews honest. `reduce_motion_active(target)` reads the native accessibility preference. `signal_preview_program()` returns a static color under Reduce Motion, while `mode_animation_thumb_program()` uses a static mode color. Settings accessibility tests require native roles, labels, help text, keyboard semantics, and static preview programs when Reduce Motion is enabled. High contrast and color-vision modes must preserve meaning without relying on color alone.

## Safe extension workflow

1. State the semantic meaning, safety level, energy cost, supported surfaces, and static fallback before writing animation code.
2. Add a typed `EffectDefinition` with bounded parameters and a valid `reduce_motion_fallback`. Confirm the fallback is registered and has the needed surface.
3. Add the effect to the semantic map only if the meaning is appropriate. Preserve urgent ASK and FAILURE overrides.
4. Route a candidate through `route_semantic_effects()`, then pass the selected effect to finite planning. Never let a renderer consume an unbounded animation directly.
5. If distributing a pack, validate data-only safety, accessibility metadata, namespaced IDs, local fallbacks, collision behavior, and pack-level licensing. Include a safe preview descriptor and retain attribution.
6. Add history assertions for shown, suppressed, acknowledged, and expired outcomes where the runtime uses the new effect.
7. Add settings preview coverage, including unsupported surfaces, missing effects, invalid parameters, Reduce Motion, high contrast, and physical-preview consent.
8. Run the targeted effect registry, semantic router, finite policy, pack, history, studio, runtime, and accessibility tests, then perform a rendered UI check. This documentation-only change did not run tests.

## Verification map

The authoritative tests are `tests/test_effect_registry.py`, `tests/test_semantic_effect_router.py`, `tests/test_finite_effect_policy.py`, `tests/test_effect_packs.py`, `tests/test_effect_history.py`, `tests/test_effect_history_store.py`, `tests/test_effect_studio.py`, `tests/test_ambient_effect_runtime.py`, `tests/test_semantic_effect_router.py`, and `tests/test_settings_accessibility.py`. Add runtime and surface tests for every destination declared by an effect.

Acceptance requires source and rendered evidence: the definition validates, semantic selection is deterministic, policy is finite and accessible, packs remain inert JSON, history remains content-free, settings previews expose usable controls, and the installed surface honors suppression, Reduce Motion, power, and device-write policy.
