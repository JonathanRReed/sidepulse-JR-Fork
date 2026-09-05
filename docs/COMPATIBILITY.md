# JR-Bar compatibility policy

This document defines what JR-Bar calls compatible. Compatibility is a claim
about an identified release and an identified boundary, not a promise that an
undocumented provider, operating-system build, or third-party application will
continue to work.

## Evidence levels

- **Production-supported:** the newest signed and notarized GitHub Release has
  passed the authoritative macOS gate, including the relevant package,
  signing, notarization, installation, upgrade, and hardware evidence.
- **Reviewed:** source and contract or fixture checks cover the boundary, but
  the exact release still lacks the required installed or physical evidence.
- **Best effort:** the code may recognize the input, but no compatibility
  window or release claim is made.
- **Unsupported:** the project deliberately does not claim the boundary.

Portable tests and a successful build are not production compatibility proof.
Developer ID signing without notarization is not notarized compatibility.
Likewise, a provider name in a menu or parser is not proof that its current
service, credentials, plan, or endpoint is compatible.

## Current boundaries

| Boundary | Current evidence-based claim |
| --- | --- |
| Python | The package declares Python 3.10, 3.11, 3.12, and 3.13. macOS behavior additionally requires the declared PyObjC dependencies. Release support follows the exact signed artifact and gate evidence. |
| Operating system | JR-Bar is a macOS product. This source set does not publish a minimum macOS version, so no narrower OS claim is made here. A release's verification evidence controls what was actually qualified. |
| CPU architecture | Release artifacts are architecture-specific (`arm64` or `x86_64`). An architecture is supported only when the release publishes that exact artifact and its corresponding verification evidence. |
| SidePulse hardware | SidePulse Pro and SidePulse Dot are the first-party hardware targets. The release gate's hardware matrix controls which device claims are production-supported. Screen Bar output is a separate macOS surface and does not prove physical-device compatibility. |
| Providers | Claude, ChatGPT/Codex, Cursor, Devin, Grok, Antigravity, and optional OpenAI API accounting have provider-specific sources and setup. Availability, permissions, credentials, and service responses can reduce a source to partial, stale, denied, or unsupported without making a false zero. |
| T3 Code | The current reviewed window is version `0.0.33` through maximum tested version `0.0.33`, using `sqlite-readonly-v1`. The packaged manifest records the reviewed source commit, protocol fingerprint, fixture version, and review date. |
| Alcove | JR-Bar has an observation boundary for Alcove coexistence, but this source set does not establish a released Alcove version range. Treat it as reviewed or best effort according to the exact release evidence, not as a version guarantee. |

## Integration policy

An integration remains compatible only while its required schema, protocol
fingerprint, permissions, connection mode, and safety contract remain within
the packaged compatibility manifest. Additive T3 Code columns may be accepted;
missing required columns fail closed as unsupported. A failed or busy refresh
retains the last known snapshot and marks it stale rather than fabricating
state. JR-Bar's T3 adapter does not write the database, invoke T3 commands,
read T3 credentials, or provide mutation actions.

When an upstream change falls outside the reviewed window, JR-Bar may continue
to display an explicit unsupported or stale state. That is not a compatibility
claim. A new claim requires updated fixtures or protocol evidence and the
relevant release gate; do not infer compatibility from a matching version
string alone.

## Reporting compatibility problems

Report reproducible, non-sensitive compatibility problems through the
[GitHub issue tracker](https://github.com/JonathanRReed/sidepulse-JR-Fork/issues)
with the exact JR-Bar artifact, host and architecture, macOS version, provider
or integration version, hardware, and sanitized evidence. Security and privacy
reports remain private under [SECURITY.md](../SECURITY.md).
