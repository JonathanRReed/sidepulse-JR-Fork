from __future__ import annotations

from sidepulse.integration_compatibility import (
    load_integration_compatibility_manifest,
)
from sidepulse.t3_compat import (
    T3_MAXIMUM_TESTED_VERSION,
    T3_MINIMUM_VERSION,
    T3_PROTOCOL_FINGERPRINT,
    T3_SOURCE_COMMIT,
)


def test_packaged_manifest_matches_the_t3_runtime_protocol() -> None:
    manifest = load_integration_compatibility_manifest()
    t3code = manifest.entry("t3code")

    assert manifest.entry("codexbar") is None
    assert t3code is not None
    assert t3code.minimum_version == T3_MINIMUM_VERSION
    assert t3code.maximum_tested_version == T3_MAXIMUM_TESTED_VERSION
    assert t3code.protocol_fingerprint == T3_PROTOCOL_FINGERPRINT
    assert t3code.source_commit == T3_SOURCE_COMMIT
    assert t3code.connection_mode == "sqlite-readonly-v1"
