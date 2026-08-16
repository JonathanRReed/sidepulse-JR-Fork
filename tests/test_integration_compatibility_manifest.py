from __future__ import annotations

from sidepulse.codexbar_compat import (
    CODEXBAR_MAXIMUM_TESTED_VERSION,
    CODEXBAR_MINIMUM_VERSION,
    CODEXBAR_PROTOCOL_FINGERPRINT,
    CODEXBAR_SOURCE_COMMIT,
)
from sidepulse.integration_compatibility import (
    load_integration_compatibility_manifest,
)
from sidepulse.t3_compat import (
    T3_MAXIMUM_TESTED_VERSION,
    T3_MINIMUM_VERSION,
    T3_PROTOCOL_FINGERPRINT,
    T3_SOURCE_COMMIT,
)


def test_packaged_manifest_matches_both_runtime_protocols() -> None:
    manifest = load_integration_compatibility_manifest()
    codexbar = manifest.entry("codexbar")
    t3code = manifest.entry("t3code")

    assert codexbar is not None
    assert codexbar.minimum_version == CODEXBAR_MINIMUM_VERSION
    assert codexbar.maximum_tested_version == CODEXBAR_MAXIMUM_TESTED_VERSION
    assert codexbar.protocol_fingerprint == CODEXBAR_PROTOCOL_FINGERPRINT
    assert codexbar.source_commit == CODEXBAR_SOURCE_COMMIT
    assert codexbar.connection_mode == "dashboard-v1"

    assert t3code is not None
    assert t3code.minimum_version == T3_MINIMUM_VERSION
    assert t3code.maximum_tested_version == T3_MAXIMUM_TESTED_VERSION
    assert t3code.protocol_fingerprint == T3_PROTOCOL_FINGERPRINT
    assert t3code.source_commit == T3_SOURCE_COMMIT
    assert t3code.connection_mode == "sqlite-readonly-v1"
