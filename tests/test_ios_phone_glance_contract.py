import base64
import hashlib
import hmac
import ipaddress
import json
import platform
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "ios" / "SidePulse" / "SidePulse" / "PhoneGlanceContract.swift"
CLIENT = ROOT / "ios" / "SidePulse" / "SidePulse" / "PhoneGlanceClient.swift"
KEYCHAIN = ROOT / "ios" / "SidePulse" / "SidePulse" / "KeychainStore.swift"
SECRET = b"python-to-swift-phone-glance-secret"


def _network_envelope(
    *,
    source_id: str = "mac.local",
    sequence: int = 42,
    observed_at: float = 1_000.0,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    readable_payload = payload or {
        "status": "working",
        "outcome": "pending",
        "label": "Private Mac",
        "usage": {"input_tokens": 3, "estimated_cost_usd": 0.01},
        "capacity": {"remaining_percent": 80, "window": "weekly"},
    }
    signed = json.dumps(
        {
            "source_id": source_id,
            "sequence": sequence,
            "observed_at": observed_at,
            "payload": readable_payload,
        },
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()
    return {
        "source_id": source_id,
        "sequence": sequence,
        "observed_at": observed_at,
        "payload": readable_payload,
        "signed_body": base64.urlsafe_b64encode(signed).rstrip(b"=").decode(),
        "signature": hmac.new(SECRET, signed, hashlib.sha256).hexdigest(),
    }


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the Darwin Swift toolchain")
def test_python_signed_fixture_obeys_the_pure_swift_contract(tmp_path: Path):
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(json.dumps(_network_envelope(), separators=(",", ":")).encode())
    harness = tmp_path / "main.swift"
    harness.write_text(
        r'''
import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(1)
    }
}

func refused(_ label: String, _ operation: () throws -> Void) {
    do {
        try operation()
        require(false, "accepted " + label)
    } catch {
        // A refusal is the expected observable behavior.
    }
}

let fixtureURL = URL(fileURLWithPath: CommandLine.arguments[1])
let validData = try Data(contentsOf: fixtureURL)
let secret = Data("python-to-swift-phone-glance-secret".utf8)
let valid = try PhoneGlanceContract.verify(
    data: validData,
    secret: secret,
    lastSequence: 41,
    now: Date(timeIntervalSince1970: 1_000)
)
require(valid.sourceID == "mac.local", "wrong source")
require(valid.sequence == 42, "wrong sequence")
require(valid.payload.status == "working", "wrong status")
require(valid.payload.outcome == "pending", "wrong outcome")
require(valid.payload.label == "Private Mac", "wrong label")

for host in ["10.0.0.1", "172.16.0.1", "192.168.1.2", "169.254.1.2", "fc00::1", "fd12::1", "fe80::1"] {
    do {
        let endpoint = try PhoneGlanceEndpoint(host: host, port: 8765)
        require(endpoint.url.absoluteString.contains("/glance.json"), "missing fixed path")
    } catch {
        require(false, "rejected allowed endpoint " + host)
    }
}
do {
    let endpoint = try PhoneGlanceEndpoint(host: "fe80::1%en0", port: 8765)
    require(endpoint.host == "fe80::1%en0", "scoped host was not preserved")
    require(endpoint.url.absoluteString == "https://[fe80::1%25en0]:8765/glance.json", "scope was not RFC 6874 encoded")
} catch {
    require(false, "rejected scoped link-local endpoint")
}
for host in ["0.0.0.0", "127.0.0.1", "localhost", "192.0.2.1", "100.64.0.1", "224.0.0.1", "240.0.0.1", "8.8.8.8", "::", "::1", "2001:db8::1", "2606:4700:4700::1111"] {
    refused("endpoint " + host) { _ = try PhoneGlanceEndpoint(host: host, port: 8765) }
}
for host in ["fe80::1%", "fe80::1%en0%other", "fe80::1%bad scope", "fe80::1%en0/other", "fe80::1%25en0", "fd12::1%en0"] {
    refused("scoped endpoint " + host) { _ = try PhoneGlanceEndpoint(host: host, port: 8765) }
}
refused("port zero") { _ = try PhoneGlanceEndpoint(host: "192.168.1.2", port: 0) }
refused("port overflow") { _ = try PhoneGlanceEndpoint(host: "192.168.1.2", port: 65_536) }
require(PhoneGlanceCredential.isValid("A23456789012345678901234"), "valid access token was rejected")
require(PhoneGlanceCredential.isValid(String(repeating: "A", count: 4096)), "maximum access token was rejected")
for token in ["short", String(repeating: "A", count: 4097), "A23456789012345678901 34", "A23456789012345678901\n34", "A23456789012345678901é"] {
    require(!PhoneGlanceCredential.isValid(token), "invalid access token was accepted")
}

func changed(_ transform: (inout [String: Any]) -> Void) throws -> Data {
    var object = try JSONSerialization.jsonObject(with: validData) as! [String: Any]
    transform(&object)
    return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

let outerTamper = try changed { object in
    var payload = object["payload"] as! [String: Any]
    payload["status"] = "waiting"
    object["payload"] = payload
}
refused("readable-field tampering") {
    _ = try PhoneGlanceContract.verify(data: outerTamper, secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 1_000))
}
let signatureTamper = try changed { $0["signature"] = String(repeating: "0", count: 64) }
refused("signature tampering") {
    _ = try PhoneGlanceContract.verify(data: signatureTamper, secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 1_000))
}
let paddedBase64 = try changed { $0["signed_body"] = ($0["signed_body"] as! String) + "=" }
refused("padded base64url") {
    _ = try PhoneGlanceContract.verify(data: paddedBase64, secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 1_000))
}
refused("replay") {
    _ = try PhoneGlanceContract.verify(data: validData, secret: secret, lastSequence: 42, now: Date(timeIntervalSince1970: 1_000))
}
refused("stale observation") {
    _ = try PhoneGlanceContract.verify(data: validData, secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 1_300.001))
}
refused("future observation") {
    _ = try PhoneGlanceContract.verify(data: validData, secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 994.999))
}
refused("oversized complete response") {
    _ = try PhoneGlanceContract.verify(data: Data(repeating: 0x20, count: 8_193), secret: secret, lastSequence: nil, now: Date(timeIntervalSince1970: 1_000))
}
refused("empty secret") {
    _ = try PhoneGlanceContract.verify(data: validData, secret: Data(), lastSequence: nil, now: Date(timeIntervalSince1970: 1_000))
}
'''
    )
    executable = tmp_path / "contract-harness"
    compile_result = subprocess.run(
        ["xcrun", "swiftc", str(CONTRACT), str(harness), "-o", str(executable)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    run_result = subprocess.run(
        [str(executable), str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run_result.returncode == 0, run_result.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the Darwin Swift toolchain")
def test_swift_phone_glance_persistence_and_keychain_error_contract(tmp_path: Path):
    harness = tmp_path / "main.swift"
    harness.write_text(
        r'''
import Foundation
import Security

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(1)
    }
}

let suiteName = "PhoneGlanceHarness." + UUID().uuidString
let defaults = UserDefaults(suiteName: suiteName)!
defer { defaults.removePersistentDomain(forName: suiteName) }

let endpoint = try PhoneGlanceEndpoint(host: "192.168.1.20", port: 8738)
let firstRun = PhoneGlanceStateStore(defaults: defaults)
firstRun.saveConfiguration(endpoint)
firstRun.saveAcceptedSequence(17, for: "sidepulse:instance-a")
require(firstRun.lastAcceptedSequence(for: "sidepulse:instance-a") == 17, "first run did not save checkpoint")

let restartedApp = PhoneGlanceStateStore(defaults: defaults)
require(restartedApp.loadEndpoint() == endpoint, "endpoint did not survive app restart")
require(restartedApp.lastAcceptedSequence(for: "sidepulse:instance-a") == 17, "checkpoint did not survive app restart")
require(restartedApp.lastAcceptedSequence(for: "sidepulse:instance-b") == nil, "new listener inherited an incompatible checkpoint")

let replacement = try PhoneGlanceEndpoint(host: "192.168.1.21", port: 8738)
restartedApp.saveConfiguration(replacement)
require(restartedApp.loadEndpoint() == replacement, "reconfiguration did not persist")
require(restartedApp.lastAcceptedSequence(for: "sidepulse:instance-a") == nil, "reconfiguration did not clear old checkpoints")

require(KeychainStore.classifyRead(status: errSecItemNotFound, data: nil) == .missing, "missing Keychain entry was not classified as missing")
require(KeychainStore.classifyRead(status: errSecInteractionNotAllowed, data: nil) == .unavailable, "Keychain read failure was misclassified as missing")
require(KeychainStore.classifyRead(status: errSecSuccess, data: Data("secret".utf8)) == .value("secret"), "valid Keychain data was not returned")
require(KeychainStore.classifyRead(status: errSecSuccess, data: Data([0xff])) == .unavailable, "invalid Keychain data did not fail closed")

let oldCredentials = try ProtectedPhoneGlanceCredentials(
    secret: "previous-signing-secret",
    accessToken: "previous-access-token-123"
)
var storedData = try JSONEncoder().encode(oldCredentials)
let failingStore = PhoneGlanceCredentialStore(
    readData: { .value(storedData) },
    writeData: { _ in false }
)
let replacementCredentials = try ProtectedPhoneGlanceCredentials(
    secret: "replacement-signing-secret",
    accessToken: "replacement-access-token-123"
)
require(!failingStore.save(replacementCredentials), "failed credential commit reported success")
require(failingStore.read() == .value(oldCredentials), "failed credential commit replaced the previous pair")

let missingStore = PhoneGlanceCredentialStore(readData: { .missing }, writeData: { _ in true })
require(missingStore.read() == .missing, "missing credential pair was not preserved")
let unavailableStore = PhoneGlanceCredentialStore(readData: { .unavailable }, writeData: { _ in true })
require(unavailableStore.read() == .unavailable, "unavailable credential pair was misclassified")
'''
    )
    executable = tmp_path / "state-harness"
    compile_result = subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(CONTRACT),
            str(CLIENT),
            str(KEYCHAIN),
            str(harness),
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    run_result = subprocess.run(
        [str(executable)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run_result.returncode == 0, run_result.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the Darwin Swift toolchain")
def test_swift_client_sends_bearer_token_and_verifies_synthetic_https_response(tmp_path: Path):
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(json.dumps(_network_envelope(), separators=(",", ":")).encode())
    harness = tmp_path / "main.swift"
    harness.write_text(
        r'''
import Foundation

final class GlanceProtocol: URLProtocol {
    static var fixture = Data()
    static var authorization: String?
    static var mode = "valid"
    static var requestCount = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requestCount += 1
        Self.authorization = request.value(forHTTPHeaderField: "Authorization")
        if Self.mode == "redirect" {
            let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": "https://192.168.1.21:8738/glance.json"])!
            let redirected = URLRequest(url: URL(string: "https://192.168.1.21:8738/glance.json")!)
            client?.urlProtocol(self, wasRedirectedTo: redirected, redirectResponse: response)
            return
        }
        let body: Data
        let status: Int
        let declaredLength: Int
        switch Self.mode {
        case "empty":
            body = Data()
            status = 200
            declaredLength = 0
        case "non-200":
            body = Data()
            status = 503
            declaredLength = 0
        case "declared-overflow":
            body = Data()
            status = 200
            declaredLength = 8_193
        case "streamed-overflow":
            body = Data(repeating: 0x78, count: 8_193)
            status = 200
            declaredLength = -1
        default:
            body = Self.fixture
            status = 200
            declaredLength = body.count
        }
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: declaredLength >= 0 ? ["Content-Length": String(declaredLength)] : nil
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if !body.isEmpty { client?.urlProtocol(self, didLoad: body) }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@main
struct Harness {
    static func fetch(configuration: URLSessionConfiguration) async throws -> VerifiedPhoneGlance {
        try await PhoneGlanceClient.fetch(
            endpoint: try PhoneGlanceEndpoint(host: "192.168.1.20", port: 8738),
            secret: Data("python-to-swift-phone-glance-secret".utf8),
            accessToken: "independent-access-token-123",
            lastSequence: 41,
            now: { Date(timeIntervalSince1970: 1_000) },
            configuration: configuration
        )
    }

    static func main() async throws {
        GlanceProtocol.fixture = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GlanceProtocol.self]
        let token = "independent-access-token-123"
        let verified = try await fetch(configuration: configuration)
        guard verified.sequence == 42 else { exit(1) }
        guard GlanceProtocol.authorization == "Bearer " + token else { exit(2) }
        for mode in ["redirect", "empty", "non-200", "declared-overflow", "streamed-overflow"] {
            GlanceProtocol.mode = mode
            do {
                _ = try await fetch(configuration: configuration)
                exit(3)
            } catch {
                // Refusal is the required behavior.
            }
        }
        let countBeforeInvalidCredentials = GlanceProtocol.requestCount
        for invalidSecret in ["", String(repeating: "S", count: 4_097), token] {
            do {
                _ = try await PhoneGlanceClient.fetch(
                    endpoint: try PhoneGlanceEndpoint(host: "192.168.1.20", port: 8738),
                    secret: Data(invalidSecret.utf8),
                    accessToken: token,
                    lastSequence: nil,
                    configuration: configuration
                )
                exit(4)
            } catch {
                // Invalid or reused credentials must fail before a request starts.
            }
        }
        guard GlanceProtocol.requestCount == countBeforeInvalidCredentials else { exit(5) }
    }
}
'''
    )
    executable = tmp_path / "client-header-harness"
    compile_result = subprocess.run(
        ["xcrun", "swiftc", "-parse-as-library", str(CONTRACT), str(CLIENT), str(harness), "-o", str(executable)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run([str(executable), str(fixture)], capture_output=True, text=True, timeout=30)
    assert run_result.returncode == 0, run_result.stderr


def _private_ipv4() -> str | None:
    for interface in ("en0", "en1", "en2", "en3"):
        result = subprocess.run(
            ["/usr/sbin/ipconfig", "getifaddr", interface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        candidate = result.stdout.strip()
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address) and address.is_private:
            return candidate
    return None


class _PhoneGlanceScenarioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self.server.request_count += 1
        self.send_error(404)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the Darwin Swift toolchain")
def test_live_swift_client_refuses_plaintext_http_before_sending_credentials(
    tmp_path: Path,
):
    host = _private_ipv4()
    if host is None:
        pytest.skip("no private IPv4 interface is available for the strict endpoint allowlist")

    scenarios = ("slow-valid",)
    servers = []
    threads = []
    try:
        for scenario in scenarios:
            server = ThreadingHTTPServer((host, 0), _PhoneGlanceScenarioHandler)
            server.scenario = scenario
            server.request_count = 0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)

        harness = tmp_path / "main.swift"
        harness.write_text(
            r'''
import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(1)
    }
}

func mustRefuse(_ label: String, _ operation: () async throws -> Void) async {
    do {
        try await operation()
        require(false, "accepted " + label)
    } catch {
        // Refusal is the required observable behavior.
    }
}

@main
struct Harness {
    static func main() async throws {
        let host = CommandLine.arguments[1]
        let ports = CommandLine.arguments.dropFirst(2).map { Int($0)! }
        let secret = Data("python-to-swift-phone-glance-secret".utf8)
        let endpoint = try PhoneGlanceEndpoint(host: host, port: ports[0])
        require(endpoint.url.scheme == "https", "endpoint did not require HTTPS")
        await mustRefuse("plaintext listener") {
            _ = try await PhoneGlanceClient.fetch(
                endpoint: endpoint,
                secret: secret,
                accessToken: "A23456789012345678901234",
                lastSequence: nil,
                now: { Date() }
            )
        }
    }
}
'''
        )
        executable = tmp_path / "client-harness"
        compile_result = subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-parse-as-library",
                str(CONTRACT),
                str(CLIENT),
                str(harness),
                "-o",
                str(executable),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            [str(executable), host, *(str(server.server_port) for server in servers)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        counts = {
            server.scenario: server.request_count
            for server in servers
        }
        assert counts["slow-valid"] == 0, counts
        assert run_result.returncode == 0, f"{run_result.stderr}\nrequest counts: {counts}"
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
