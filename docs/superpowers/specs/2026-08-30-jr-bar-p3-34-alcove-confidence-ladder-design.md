# JR Bar P3.34 Alcove confidence ladder design

## Goal

Give Alcove following one honest, typed, seven-state product contract. Settings,
Doctor, Screen Bar geometry, Screen Bar motion, and accessibility must distinguish
fresh, stale, permission denied, disconnected, unsupported, not following, and
recovering without deriving their own meanings from raw capture failures.

This is a source and native-UI tranche. It does not authorize changing Screen
Recording permission, replacing the installed application, changing physical LED
output, publishing, or claiming installed-app acceptance.

## Boundary

The existing capture layer remains factual:

- `CAPTURED` means one capture returned a validated observation.
- `SCREEN_RECORDING_DENIED` means promptless preflight observed denial.
- `WINDOW_UNAVAILABLE` means no capturable Alcove window was available.
- `IMAGE_UNUSABLE` means image bytes existed but did not produce safe geometry.
- `CAPTURE_FAILED` means capture raised or declined to provide a more specific
  outcome.
- `NOT_FOLLOWING` means the owner disabled following or chose manual geometry.

These raw values are not expanded with semantic states. A new AppKit-free
projection interprets them together with status age and last-good geometry age.
This keeps capture evidence separate from product meaning and prevents three
consumers from drifting into different answers.

## Pure confidence contract

`sidepulse.alcove_observation` owns these types:

```python
class AlcoveConfidenceState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    PERMISSION_DENIED = "permission_denied"
    DISCONNECTED = "disconnected"
    UNSUPPORTED = "unsupported"
    NOT_FOLLOWING = "not_following"
    RECOVERING = "recovering"


class AlcoveGeometryIntent(str, Enum):
    FOLLOW_LIVE = "follow_live"
    HOLD_LAST_GOOD = "hold_last_good"
    USE_SCREEN_BAR_GEOMETRY = "use_screen_bar_geometry"


class AlcoveMotionIntent(str, Enum):
    TRACK = "track"
    HOLD = "hold"
    SETTLE_ON_RECOVERY = "settle_on_recovery"
    STATIC = "static"


@dataclass(frozen=True, slots=True)
class AlcoveConfidenceProjection:
    state: AlcoveConfidenceState
    message: str
    accessibility_value: str
    accessibility_help: str
    geometry_intent: AlcoveGeometryIntent
    motion_intent: AlcoveMotionIntent
    needs_permission_action: bool
```

`AlcoveStatusSnapshot` gains optional, validated geometry evidence:

- `geometry_age_seconds`: age of the last validated observation when the
  snapshot was recorded, or `None` when no last-good geometry exists;
- `geometry_available`: whether the reducer still owns that last-good
  observation.

`note_alcove_status` accepts those values. Existing callers that record
`CAPTURED` without explicit geometry keep source compatibility by recording a
fresh zero-age observation. Non-captured outcomes default to no geometry.

`project_alcove_confidence`, with only plain values and an injectable `now`, is
the sole state resolver. It accepts `following`, the latest snapshot, and a
promptless current blocker. It validates non-finite or negative ages and fails
closed to recovering with Screen Bar geometry.

## State resolution

Resolution order is significant:

1. Following disabled always produces `NOT_FOLLOWING`.
2. A current `SCREEN_RECORDING_DENIED` blocker produces `PERMISSION_DENIED`,
   even when an older successful snapshot exists.
3. A current `WINDOW_UNAVAILABLE` blocker produces `DISCONNECTED`.
4. A snapshot older than `ALCOVE_STATUS_MAX_AGE_SECONDS` produces `STALE`.
5. `IMAGE_UNUSABLE` produces `UNSUPPORTED`.
6. `CAPTURE_FAILED` or no first result produces `RECOVERING`.
7. A `CAPTURED` snapshot with available geometry no older than
   `ALCOVE_MAX_AGE_SECONDS` produces `FRESH`.
8. A `CAPTURED` snapshot whose last-good geometry is older than
   `ALCOVE_MAX_AGE_SECONDS` produces `STALE`.

Last-good geometry may only be held through the existing
`ALCOVE_HOLD_SECONDS` budget. Older or absent geometry uses Screen Bar's own
measured or configured shape.

## Text and accessibility

Every state has a fixed state label plus plain-language cause and behavior.
Color and motion are never the only distinctions.

| State | Visible text | Accessibility behavior |
| --- | --- | --- |
| Fresh | `Live. Matching Alcove's width.` | Value `Fresh`; help says the Screen Bar is following a current measurement. |
| Stale, held | `Stale. Holding the last trusted width while JR Bar checks again.` | Value `Stale`; help says the held shape will expire and is not current. |
| Stale, expired | `Stale. Using the Screen Bar's own size while JR Bar checks again.` | Value `Stale`; help says no current trusted shape is available. |
| Permission denied | `Screen Recording is off, so JR Bar cannot see Alcove's capsule. The bar keeps its own size until you grant it.` | Value `Permission denied`; help names the explicit Screen Recording action. |
| Disconnected | `Disconnected. Alcove is not showing a capsule, so the bar is using its own size.` | Value `Disconnected`; help says following resumes when a capsule is available. |
| Unsupported | `Unsupported shape. The captured capsule could not be measured safely, so the bar is using its own size.` | Value `Unsupported`; help says no unsafe geometry is used. |
| Not following | `Not following Alcove. The bar uses its own size.` | Value `Not following`; help says the setting or manual geometry controls the shape. |
| Recovering, held | `Recovering. Holding the last trusted width while measurement resumes.` | Value `Recovering`; help says the held shape is temporary. |
| Recovering, empty | `Recovering. Using the Screen Bar's own size until a fresh measurement arrives.` | Value `Recovering`; help says the app is waiting for fresh evidence. |

The Settings permission button remains visible only for permission denied.
Unsupported, disconnected, stale, and recovering states do not offer an action
that cannot fix their cause.

The visual Screen Bar exposes a fixed accessibility label, the projection's
accessibility value, and its state-specific help. The existing click action and
keyboard behavior remain unchanged.

## Geometry and motion

Geometry behavior is semantic and bounded:

- Fresh uses current validated geometry and follows at the existing cadence.
- Stale holds the reducer's last-good geometry only while it is inside the
  existing eight-second hold budget, then falls back.
- Recovering may hold the same last-good geometry inside that budget. It never
  extends the hold window.
- Permission denied, disconnected, unsupported, and not following immediately
  use Screen Bar geometry.

Motion is intentionally restrained:

- Fresh continuously tracks validated geometry without decorative motion.
- Stale freezes the held geometry.
- Recovering freezes any held geometry. The transition from recovering or stale
  back to fresh receives one 180 millisecond ease-out geometry settle.
- Permission denied, disconnected, unsupported, and not following are static.
- With Reduce Motion enabled, recovery snaps to the fresh frame and never starts
  the settle animation.

The settle applies only to the on-screen Screen Bar window. It never writes a
physical device, alters an LED program, changes alert motion, or starts a
continuous animation. The implementation must use the existing AppKit animation
boundary after verifying its version-specific API through Context7 or official
Apple documentation.

## Typed view handoff

The raw `(center_x, width, height, contour)` silhouette tuple becomes an
`AlcoveSilhouette` frozen dataclass in `alcove_observation.py`. Its constructor
rejects non-finite values, unsafe dimensions, malformed contours, and mutable
containers. `VirtualLedView.setAlcoveSilhouette_` accepts only that type or
`None` and stores a plain immutable value.

`VirtualLedView.setAlcoveConfidence_` receives an
`AlcoveConfidenceProjection`, updates accessibility metadata, and change-gates
repainting. Confidence presentation never changes the semantic LED colors or
agent signal program.

## Consumer flow

1. The capture worker publishes a raw capture outcome.
2. The reducer validates geometry and reports the age of its last-good
   observation.
3. `VirtualStatusDevice.reposition` combines following, blocker, capture status,
   status age, and geometry age through `project_alcove_confidence`.
4. Geometry and motion intents determine live, held, settled, or fallback Screen
   Bar placement.
5. The same status snapshot lets Settings and Doctor call the same projection.
6. Settings renders the projection's text and permission-action flag.
7. Doctor maps the seven semantic states one-to-one to seven content-free codes.

Doctor document version 4 adds `stale`, `recovering`, and `unsupported` to its
allowed fixed vocabulary. `fresh` continues to emit `healthy`; permission
denied, disconnected, and not following retain `not_permitted`, `not_running`,
and `not_configured`.

## Failure behavior

- Unknown status objects produce recovering with Screen Bar geometry.
- Non-finite clocks or geometry ages are rejected and never treated as fresh.
- An expired status cannot report fresh merely because the last enum was
  `CAPTURED`.
- A current permission or window blocker outranks cached success.
- Unsupported images never reuse their own rejected geometry.
- Failed recovery animation setup falls back to an immediate frame update.
- Accessibility setter failure cannot break drawing or Screen Bar placement.
- No permission prompt occurs outside the existing explicit Settings action.

## Tests and evidence

Source completion requires:

- table-driven pure tests for all states, precedence, boundary ages, invalid
  inputs, exact copy, geometry intent, motion intent, and permission action;
- reducer tests for fresh, held, and expired last-good ages;
- Doctor version, manifest, and seven-code tests;
- Settings tests for every visible state and permission-button visibility;
- Screen Bar tests for typed silhouette handoff, live follow, bounded hold,
  fallback, one-time recovery settle, failed animation fallback, and Reduce
  Motion substitution;
- accessibility tests for label, value, help, and no color-only meaning;
- isolated native AppKit renders of all seven states in light and dark
  appearances, with Reduce Motion checked for the recovery transition;
- Ruff, focused tests, `git diff --check`, `make fast`, one stable-fingerprint
  complete suite, and independent findings-first review.

Installed-app, real permission, live Alcove, physical display, signing,
notarization, and release evidence remain separate gates.

## Non-goals

- No new production dependency.
- No Alcove pixel-capture rewrite.
- No new continuous glow, alert, or physical LED effect.
- No Screen Recording request outside explicit owner action.
- No prompt, window-title, or captured-image persistence.
- No Effect Studio, Glance Light, multi-alert stack, Scene, or provider work;
  those remain later roadmap tranches.
