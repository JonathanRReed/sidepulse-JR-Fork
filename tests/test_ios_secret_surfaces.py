from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IOS_SOURCE = REPO_ROOT / "ios" / "SidePulse" / "SidePulse"


def _read(name: str) -> str:
    return (IOS_SOURCE / name).read_text(encoding="utf-8")


def _declaration_body(source: str, declaration: str) -> str:
    match = re.search(declaration + r"[^\{]*\{", source)
    assert match is not None, f"missing declaration matching {declaration}"
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise AssertionError(f"unterminated declaration matching {declaration}")


def _property_body(source: str, name: str) -> str:
    return _declaration_body(
        source,
        rf"var\s+{re.escape(name)}\s*:\s*String\??\s*",
    )


def test_credentials_persist_only_through_native_keychain_storage() -> None:
    """Catches either credential reverting to unprotected defaults storage."""
    app_model = _read("AppModel.swift")
    keychain = _read("KeychainStore.swift")

    assert "UserDefaults.standard.set(pushToken" not in app_model
    assert "UserDefaults.standard.set(sharedSecret" not in app_model
    assert "UserDefaults.standard.string(forKey: Defaults.pushToken)" not in app_model
    assert "UserDefaults.standard.string(forKey: Defaults.sharedSecret)" not in app_model
    assert "import Security" in keychain
    assert "SecItemCopyMatching" in keychain
    assert "SecItemAdd" in keychain
    assert "SecItemUpdate" in keychain
    assert "kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly" in keychain


def test_legacy_defaults_are_removed_only_after_keychain_accepts_each_value() -> None:
    """Catches migration deleting the only credential copy after a storage failure."""
    keychain = _read("KeychainStore.swift")
    body = _declaration_body(
        keychain,
        r"func\s+migrateLegacySecret\(",
    )
    write = body.find("set(")
    remove = body.find("removeObject(")
    assert write >= 0
    assert remove > write
    assert re.search(r"guard\s+set\(.*?\)\s+else\s*\{", body, flags=re.DOTALL)


def test_no_preauthenticated_or_query_credential_url_exists() -> None:
    """Catches credentials moving into URLs, histories, analytics, or proxy logs."""
    app_model = _read("AppModel.swift")

    assert "preauthenticatedPostURL" not in app_model
    assert 'URLQueryItem(name: "device_token"' not in app_model
    assert 'URLQueryItem(name: "key"' not in app_model


def test_curl_example_contains_only_literal_environment_placeholders() -> None:
    """Catches copied commands interpolating an in-memory token or secret."""
    app_model = _read("AppModel.swift")
    curl_body = _property_body(app_model, "curlExample")

    assert "${SIDEPULSE_DEVICE_TOKEN}" in curl_body
    assert "${SIDEPULSE_SHARED_SECRET}" in curl_body
    assert "\\(pushToken)" not in curl_body
    assert "\\(sharedSecret)" not in curl_body
    assert "'${SIDEPULSE_DEVICE_TOKEN}'" not in curl_body


def test_proxy_url_strips_credentials_and_replaces_legacy_persisted_value() -> None:
    """Catches URL user-info or query secrets surviving into defaults or copied curl."""
    app_model = _read("AppModel.swift")

    for removal in (
        "components.user = nil",
        "components.password = nil",
        "components.query = nil",
        "components.fragment = nil",
    ):
        assert removal in app_model
    initialization = _declaration_body(app_model, r"private\s+init\(\)")
    assignment = initialization.find("self.serverBaseURL =")
    replacement = initialization.find(
        "UserDefaults.standard.set(self.serverBaseURL, forKey: Defaults.serverBaseURL)"
    )
    assert assignment >= 0
    assert replacement > assignment


def test_content_view_never_renders_or_copies_credential_values() -> None:
    """Catches token or secret exposure through selectable text or pasteboard writes."""
    content = _read("ContentView.swift")

    forbidden_surfaces = (
        "Text(model.pushToken)",
        "Text(model.sharedSecret)",
        "UIPasteboard.general.string = model.pushToken",
        "UIPasteboard.general.string = model.sharedSecret",
        'Label("Copy Token"',
        'Label("Show Push Token"',
    )
    for surface in forbidden_surfaces:
        assert surface not in content


def test_errors_and_logs_do_not_receive_raw_credentials_or_error_text() -> None:
    """Catches sensitive server or system errors reaching persistent diagnostics."""
    app_model = _read("AppModel.swift")

    assert "error.localizedDescription" not in app_model
    assert not re.search(r"EventLog\.append\([^\n]*(?:pushToken|sharedSecret)", app_model)
    assert not re.search(r"lastMessage\s*=\s*[^\n]*(?:pushToken|sharedSecret)", app_model)


def test_app_delegate_scrubs_errors_before_event_log_and_push_history() -> None:
    """Catches raw framework or filesystem errors reaching either persistent sink."""
    app_delegate = _read("AppDelegate.swift")
    event_log = _read("EventLog.swift")

    assert "localizedDescription" not in app_delegate
    assert "localizedDescription" not in event_log
    assert "logger.info" in event_log
    assert "defaults.set(entries, forKey: defaultsKey)" in event_log
    assert not re.search(r"errorMessage\s*=\s*error\.", app_delegate)
    assert 'errorMessage = "Write failed"' in app_delegate
    assert 'EventLog.append("APNs registration failed")' in app_delegate
    assert 'EventLog.append("\\(source) write failed")' in app_delegate


def test_secret_mutations_publish_only_after_keychain_confirmation() -> None:
    """Catches failed saves or deletes accepting state that can later resurrect."""
    app_model = _read("AppModel.swift")
    content = _read("ContentView.swift")

    assert "@Published private(set) var pushToken: String" in app_model
    assert "@Published private(set) var sharedSecret: String" in app_model
    assert "didSet { KeychainStore.shared.set(pushToken" not in app_model
    assert "didSet { KeychainStore.shared.set(sharedSecret" not in app_model

    token_mutation = _declaration_body(app_model, r"func\s+setPushToken\(")
    token_write = token_mutation.find("KeychainStore.shared.set(candidate, for: .pushToken)")
    token_publish = token_mutation.find("pushToken = candidate")
    assert token_write >= 0
    assert token_publish > token_write
    assert re.search(r"guard\s+KeychainStore\.shared\.set\(.*?\)\s+else\s*\{", token_mutation)

    secret_mutation = _declaration_body(app_model, r"func\s+saveSharedSecret\(")
    secret_write = secret_mutation.find("KeychainStore.shared.set(candidate, for: .sharedSecret)")
    secret_publish = secret_mutation.find("sharedSecret = candidate")
    assert secret_write >= 0
    assert secret_publish > secret_write
    assert re.search(r"guard\s+KeychainStore\.shared\.set\(.*?\)\s+else\s*\{", secret_mutation)

    assert 'let message = "Protected credential save failed"' in app_model
    assert "$model.sharedSecret" not in content
    assert "model.saveSharedSecret(sharedSecretDraft)" in content


def test_keychain_store_is_in_the_ios_target_sources() -> None:
    """Catches a secure implementation that is omitted from the shipped target."""
    project = (
        REPO_ROOT / "ios" / "SidePulse" / "SidePulse.xcodeproj" / "project.pbxproj"
    ).read_text(encoding="utf-8")

    assert "KeychainStore.swift" in project
    assert "KeychainStore.swift in Sources" in project
