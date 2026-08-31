# JR Bar support policy

JR Bar is a macOS menu-bar application and command-line tool. It reads local
agent/provider state and can drive SidePulse Pro, SidePulse Dot, and the
on-screen Screen Bar. Support is evidence-based and applies to a specific
release artifact, host, provider, and hardware setup.

## What is supported

The supported product is the newest signed and notarized GitHub Release that
has passed `scripts/verify_macos_release.sh` on the reviewed release Mac. The
release page is the source of truth for the version, checksums, SBOM, release
environment, and candidate-bound verification evidence. A source checkout,
editable install, unsigned package, development wrapper, or arbitrary commit
is not a production support target.

If no GitHub Release meets those conditions, there is no production-supported
artifact to claim. The release gate and publication process remain separate
from source-level implementation or beta testing.

The application and CLI declare Python 3.10 through 3.13. macOS-specific
behavior requires macOS and PyObjC. Physical-device behavior is specific to
the SidePulse Pro and SidePulse Dot hardware covered by the release's hardware
matrix. A release may narrow these claims when its evidence says so.

Provider accounting is native and provider-specific. A provider being listed
in the documentation does not mean that its service, credentials, plan,
quota endpoint, browser session, or local data is available on every machine.
T3 Code is opt-in and read-only. Its reviewed compatibility window is recorded
in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Getting help

Use the repository's [GitHub issue tracker](https://github.com/JonathanRReed/sidepulse-JR-Fork/issues)
for reproducible product bugs, documentation corrections, and feature
discussion. Include the JR Bar version, installation artifact, macOS version,
architecture, relevant provider or hardware, exact command or action, and the
observed result. Attach only sanitized logs and synthetic fixtures.

Issues are a public collaboration mechanism, not a guaranteed support desk.
There is no response-time, compatibility, feature, or repair guarantee.

## Boundaries

The project does not provide support for upstream provider outages or policy
changes, macOS defects, third-party applications, Tailscale/SSH/SFTP setup,
modified binaries, disabled macOS protections, unsupported hardware, or data
recovery after a user bypasses the documented installer and migration paths.
Those systems may still be relevant to diagnosis, but their behavior is not a
JR Bar support commitment.

Do not put passwords, access tokens, refresh tokens, cookies, private prompts,
full transcripts, account identifiers, or personal paths in an issue. For a
suspected vulnerability or privacy leak, do not open a public issue. Send the
private report required by [SECURITY.md](SECURITY.md) to
`Contact@JonathanRReed.com`.

Before reporting an installed-release problem, `sidepulse doctor` and
`sidepulse integrations status --json` can provide bounded local facts. They
do not replace the release evidence or prove a third-party service is
compatible.
