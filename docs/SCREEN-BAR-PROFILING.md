# Screen Bar profiling

JR Bar separates runtime measurements from Instruments measurements. A runtime
capture proves what the app observed. An Instruments profile adds wakeups,
energy impact, memory, and CPU evidence and binds those values to a raw trace.
Neither file is a release claim by itself.

## Required matrix

Capture each scenario for at least five minutes:

1. `static`
2. `working`
3. `asking`
4. `multi-agent`
5. `dnd`
6. `low-power`
7. `hidden`

The DND run is accepted only when JR Bar can observe an active macOS Focus.
The low-power run is accepted only while Low Power Mode is active. The hidden
run is accepted only when the Screen Bar is not visible and presents no
frames. Unreadable Focus state remains `unknown`; it is never converted to a
successful DND observation.

## Capture one runtime profile

Choose a private output path and launch JR Bar in the foreground with the
scenario named explicitly:

```bash
SIDEPULSE_SCREEN_BAR_PROFILE_SCENARIO=static \
SIDEPULSE_SCREEN_BAR_PROFILE_OUTPUT="$PWD/performance-evidence/static.runtime.json" \
.venv/bin/python -m sidepulse status-bar start --foreground
```

Put the app into the named state, keep it there for at least 300 seconds, and
quit it normally. JR Bar writes the profile only during termination. Without
both environment variables, the ordinary runtime performs no profile export.
During an explicit profiling run, a content-free state sampler observes the
scenario every five seconds and whenever Screen Bar visibility or display
sleep changes. The five-minute window starts only after the declared scenario
first matches. Any later mismatch invalidates the run, and fewer than 30 state
samples are rejected. This prevents a last-second Focus, Low Power Mode, or
visibility toggle from relabeling an unrelated capture.

Validate the capture:

```bash
.venv/bin/python scripts/screen_bar_profile_evidence.py \
  validate-runtime performance-evidence/static.runtime.json
```

The runtime profile contains no prompt text, session labels, account labels,
Focus names, serial numbers, or cloud telemetry. It records bounded callback
timings, JavaScriptCore single and batch calls, batch successes and fallbacks,
cached frames, invalidated prefetch, finite-horizon truncations, processed and
suppressed callbacks, presented frames, display refresh, visibility, display
sleep, Low Power Mode, thermal state, and a content-free Focus state. A batch
invalidation means prefetched work was discarded because command identity or
timing changed. It is not counted as a JavaScriptCore failure. A truncation
means JR Bar deliberately requested fewer than the 24-frame ceiling because a
finite cue had fewer deliverable samples remaining.

## Add Instruments evidence

Record the same foreground process in Apple Instruments for the full runtime
capture. Preserve the raw `.trace` file under the evidence root. Review the
trace and export these non-negative numeric values to a JSON object:

```json
{
  "measurement_duration_seconds": 300.0,
  "wakeups_per_second": 0.0,
  "energy_impact": 0.0,
  "peak_resident_memory_mb": 0.0,
  "average_cpu_percent": 0.0,
  "cpu_time_seconds": 0.0
}
```

The zeroes above illustrate the schema only. Replace them with observed values;
do not use the example as evidence. Finalize one scenario:

```bash
.venv/bin/python scripts/screen_bar_profile_evidence.py finalize \
  --root performance-evidence \
  --runtime performance-evidence/static.runtime.json \
  --instruments performance-evidence/static.instruments.json \
  --trace performance-evidence/static.trace \
  --output performance-evidence/static.profile.json
```

The finalizer revalidates the runtime capture, rejects non-finite or negative
measurements, requires the Instruments duration to cover the runtime capture,
and records the trace path, size, and SHA-256. Changing the trace invalidates
the profile.

## Assemble the matrix

Pass exactly one finalized profile for each required scenario:

```bash
.venv/bin/python scripts/screen_bar_profile_evidence.py matrix \
  --root performance-evidence \
  --profile performance-evidence/static.profile.json \
  --profile performance-evidence/working.profile.json \
  --profile performance-evidence/asking.profile.json \
  --profile performance-evidence/multi-agent.profile.json \
  --profile performance-evidence/dnd.profile.json \
  --profile performance-evidence/low-power.profile.json \
  --profile performance-evidence/hidden.profile.json \
  --output performance-evidence/screen-bar-profile-matrix.json
```

The command rejects missing, duplicate, unknown, tampered, secret-shaped, or
cross-trace evidence. Validate a saved matrix with `validate-matrix` before
using it in performance work.

## Current external gate

On the 2026-08-29 development checkout, `xcrun xctrace version` fails because
`xctrace` is not installed in the selected developer toolchain. No Instruments
trace, physical-device energy result, or completed seven-scenario matrix is
claimed from that environment.
