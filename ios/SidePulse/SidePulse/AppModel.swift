import Foundation
import UIKit

enum ProductIdentity {
    static let displayName = "JR-Bar"
}

enum PhoneGlanceLoadState: Equatable {
    case unconfigured
    case idle
    case loading
    case ready(VerifiedPhoneGlance)
    case stale(VerifiedPhoneGlance)
    case unavailable

    var statusMessage: String {
        switch self {
        case .unconfigured:
            return "Computer glance needs setup"
        case .idle:
            return "Computer glance is ready to check"
        case .loading:
            return "Checking computer glance"
        case .ready:
            return "Computer glance verified"
        case .stale:
            return "Last verified computer glance may be out of date"
        case .unavailable:
            return "Computer glance is unavailable"
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    enum PhoneGlanceConfigurationSaveResult {
        case saved
        case validationFailure
        case keychainStorageFailure
    }

    static let shared = AppModel()

    @Published private(set) var pushToken: String

    @Published var selectedFolderPath: String = "No USB folder selected"
    @Published var hasFolderAccess: Bool = false

    @Published var ledText: String {
        didSet { UserDefaults.standard.set(ledText, forKey: Defaults.ledText) }
    }

    @Published var serverBaseURL: String {
        didSet {
            guard let sanitized = Self.sanitizedBaseURL(serverBaseURL) else {
                return
            }
            UserDefaults.standard.set(sanitized, forKey: Defaults.serverBaseURL)
        }
    }

    @Published private(set) var sharedSecret: String

    @Published private(set) var phoneGlanceHost: String
    @Published private(set) var phoneGlancePort: String
    @Published private(set) var phoneGlanceLoadState: PhoneGlanceLoadState

    @Published var lastMessage: String = "Ready"
    @Published var eventLog: [String] = []
    @Published var receivedPushes: [ReceivedPush] {
        didSet { persistReceivedPushes() }
    }

    private enum Defaults {
        static let ledText = "ledText"
        static let serverBaseURL = "serverBaseURL"
        static let receivedPushes = "receivedPushes"
    }

    private let phoneGlanceStateStore: PhoneGlanceStateStore
    private let readPhoneGlanceCredentials: () -> PhoneGlanceCredentialsReadResult
    private let writePhoneGlanceCredentials: (ProtectedPhoneGlanceCredentials) -> Bool
    private let fetchPhoneGlance: (
        PhoneGlanceEndpoint,
        Data,
        String,
        Int64?
    ) async throws -> VerifiedPhoneGlance
    private var lastVerifiedPhoneGlance: VerifiedPhoneGlance?
    private var isRefreshingPhoneGlance = false

    init(
        phoneGlanceStateStore: PhoneGlanceStateStore = PhoneGlanceStateStore(),
        readPhoneGlanceCredentials: @escaping () -> PhoneGlanceCredentialsReadResult = {
            KeychainStore.shared.readPhoneGlanceCredentials()
        },
        writePhoneGlanceCredentials: @escaping (ProtectedPhoneGlanceCredentials) -> Bool = {
            KeychainStore.shared.setPhoneGlanceCredentials($0)
        },
        fetchPhoneGlance: @escaping (
            PhoneGlanceEndpoint,
            Data,
            String,
            Int64?
        ) async throws -> VerifiedPhoneGlance = { endpoint, secret, accessToken, lastSequence in
            try await PhoneGlanceClient.fetch(
                endpoint: endpoint,
                secret: secret,
                accessToken: accessToken,
                lastSequence: lastSequence
            )
        }
    ) {
        self.phoneGlanceStateStore = phoneGlanceStateStore
        self.readPhoneGlanceCredentials = readPhoneGlanceCredentials
        self.writePhoneGlanceCredentials = writePhoneGlanceCredentials
        self.fetchPhoneGlance = fetchPhoneGlance
        let keychain = KeychainStore.shared
        keychain.migrateLegacySecrets()
        self.pushToken = keychain.string(for: .pushToken) ?? ""
        self.ledText = UserDefaults.standard.string(forKey: Defaults.ledText) ?? """
        #404040 1.4s pulse
        off 400ms none
        repeat
        """
        let savedBaseURL = UserDefaults.standard.string(forKey: Defaults.serverBaseURL) ?? "http://127.0.0.1:8787"
        self.serverBaseURL = Self.sanitizedBaseURL(savedBaseURL) ?? "http://127.0.0.1:8787"
        self.sharedSecret = keychain.string(for: .sharedSecret) ?? ""
        if let endpoint = phoneGlanceStateStore.loadEndpoint() {
            self.phoneGlanceHost = endpoint.host
            self.phoneGlancePort = String(endpoint.port)
            switch readPhoneGlanceCredentials() {
            case .value:
                self.phoneGlanceLoadState = .idle
            case .unavailable:
                self.phoneGlanceLoadState = .unavailable
            case .missing:
                self.phoneGlanceLoadState = .unconfigured
            }
        } else {
            self.phoneGlanceHost = ""
            self.phoneGlancePort = ""
            self.phoneGlanceLoadState = .unconfigured
            phoneGlanceStateStore.clearConfiguration()
        }
        self.receivedPushes = Self.loadReceivedPushes()
        self.eventLog = EventLog.entries()
        UserDefaults.standard.set(self.serverBaseURL, forKey: Defaults.serverBaseURL)
        refreshFolderStatus()
    }

    @discardableResult
    func savePhoneGlanceConfiguration(
        host: String,
        port: String,
        secret: String,
        accessToken: String
    ) -> PhoneGlanceConfigurationSaveResult {
        let trimmedPort = port.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parsedPort = Int(trimmedPort),
              let endpoint = try? PhoneGlanceEndpoint(host: host, port: parsedPort),
              let credentials = try? ProtectedPhoneGlanceCredentials(
                secret: secret,
                accessToken: accessToken
              ) else {
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unconfigured
            return .validationFailure
        }

        guard writePhoneGlanceCredentials(credentials) else {
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unavailable
            return .keychainStorageFailure
        }

        lastVerifiedPhoneGlance = nil
        phoneGlanceHost = endpoint.host
        phoneGlancePort = String(endpoint.port)
        phoneGlanceStateStore.saveConfiguration(endpoint)
        phoneGlanceLoadState = .idle
        return .saved
    }

    func refreshPhoneGlance() async {
        guard !isRefreshingPhoneGlance else { return }
        guard let endpoint = phoneGlanceEndpoint() else {
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unconfigured
            return
        }
        let credentials: ProtectedPhoneGlanceCredentials
        switch readPhoneGlanceCredentials() {
        case .value(let storedCredentials):
            credentials = storedCredentials
        case .missing:
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unconfigured
            return
        case .unavailable:
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unavailable
            return
        }

        isRefreshingPhoneGlance = true
        phoneGlanceLoadState = .loading
        defer { isRefreshingPhoneGlance = false }

        do {
            let verified = try await fetchPhoneGlance(
                endpoint,
                Data(credentials.secret.utf8),
                credentials.accessToken,
                nil
            )
            guard verified.sequence > (phoneGlanceStateStore.lastAcceptedSequence(for: verified.sourceID) ?? 0) else {
                throw PhoneGlanceError.invalidResponse
            }
            phoneGlanceStateStore.saveAcceptedSequence(verified.sequence, for: verified.sourceID)
            lastVerifiedPhoneGlance = verified
            phoneGlanceLoadState = .ready(verified)
        } catch {
            phoneGlanceLoadState = lastVerifiedPhoneGlance.map(PhoneGlanceLoadState.stale) ?? .unavailable
        }
    }

    func setPushToken(from deviceToken: Data) {
        let candidate = deviceToken.map { String(format: "%02x", $0) }.joined()
        guard KeychainStore.shared.set(candidate, for: .pushToken) else {
            recordCredentialSaveFailure()
            return
        }
        pushToken = candidate
        EventLog.append("APNs token updated")
        lastMessage = "Push token updated"
        refreshEventLog()
    }

    @discardableResult
    func saveSharedSecret(_ candidate: String) -> Bool {
        guard KeychainStore.shared.set(candidate, for: .sharedSecret) else {
            recordCredentialSaveFailure()
            return false
        }
        sharedSecret = candidate
        let message = candidate.isEmpty ? "Shared secret cleared" : "Shared secret updated"
        EventLog.append(message)
        lastMessage = message
        refreshEventLog()
        return true
    }

    func refreshFolderStatus() {
        hasFolderAccess = DriveWriter.shared.hasSavedFolder
        selectedFolderPath = DriveWriter.shared.savedFolderDisplayName
    }

    func recordWriteSuccess(_ message: String) {
        EventLog.append(message)
        lastMessage = message
        refreshFolderStatus()
        refreshEventLog()
    }

    func recordError(_: Error) {
        let message = "Operation failed"
        EventLog.append(message)
        lastMessage = message
        refreshFolderStatus()
        refreshEventLog()
    }

    func recordReceivedPush(_ push: ReceivedPush) {
        var next = [push]
        next.append(contentsOf: receivedPushes)
        if next.count > 50 {
            next.removeLast(next.count - 50)
        }
        receivedPushes = next

        let status = push.writeStatus.displayName
        EventLog.append("\(push.source): \(push.title) (\(status))")
        lastMessage = "\(push.title) - \(status)"
        refreshFolderStatus()
        refreshEventLog()
    }

    func clearReceivedPushes() {
        receivedPushes = []
        lastMessage = "Cleared received pushes"
    }

    func refreshEventLog() {
        eventLog = EventLog.entries()
    }

    func clearEventLog() {
        EventLog.clear()
        refreshEventLog()
    }

    private func recordCredentialSaveFailure() {
        let message = "Protected credential save failed"
        EventLog.append(message)
        lastMessage = message
        refreshEventLog()
    }

    var pushEndpointURL: String? {
        guard let sanitizedBaseURL = Self.sanitizedBaseURL(serverBaseURL) else {
            return nil
        }
        return sanitizedBaseURL + "/v1/push"
    }

    var curlExample: String? {
        guard let pushEndpointURL else {
            return nil
        }

        let tokenLine = "\n  -d \"{\\\"device_token\\\":\\\"${SIDEPULSE_DEVICE_TOKEN}\\\",\\\"pattern\\\":\\\"green_pulse_2\\\"}\""
        let authHeader = " \\\n  -H \"Authorization: Bearer ${SIDEPULSE_SHARED_SECRET}\""
        return """
        curl -X POST \(pushEndpointURL)\(authHeader) \\
          -H "content-type: application/json" \(tokenLine)
        """
    }

    var shortcutWriteURL: String? {
        guard !ledText.isEmpty else {
            return nil
        }

        var components = URLComponents()
        components.scheme = "sidepulse"
        components.host = "write"
        components.queryItems = [
            URLQueryItem(name: "text", value: ledText)
        ]
        return components.url?.absoluteString
    }

    func shortcutPatternURL(for pattern: LEDPattern) -> String? {
        var components = URLComponents()
        components.scheme = "sidepulse"
        components.host = "write"
        components.queryItems = [
            URLQueryItem(name: "pattern", value: pattern.name)
        ]
        return components.url?.absoluteString
    }

    private static func sanitizedBaseURL(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              components.host != nil else {
            return nil
        }

        components.user = nil
        components.password = nil
        components.query = nil
        components.fragment = nil
        let trimmedPath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = trimmedPath.isEmpty ? "" : "/" + trimmedPath
        guard let result = components.url?.absoluteString else {
            return nil
        }
        return result.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private func phoneGlanceEndpoint() -> PhoneGlanceEndpoint? {
        guard let port = Int(phoneGlancePort) else {
            return nil
        }
        return try? PhoneGlanceEndpoint(host: phoneGlanceHost, port: port)
    }

    private func persistReceivedPushes() {
        if let data = try? JSONEncoder().encode(receivedPushes) {
            UserDefaults.standard.set(data, forKey: Defaults.receivedPushes)
        }
    }

    private static func loadReceivedPushes() -> [ReceivedPush] {
        guard let data = UserDefaults.standard.data(forKey: Defaults.receivedPushes),
              let pushes = try? JSONDecoder().decode([ReceivedPush].self, from: data) else {
            return []
        }
        return Array(pushes.prefix(50))
    }
}

extension ReceivedPush.WriteStatus {
    var displayName: String {
        switch self {
        case .received:
            return "Received"
        case .wrote:
            return "Wrote LEDS.LED"
        case .noFolder:
            return "Folder needed"
        case .failed:
            return "Failed"
        case .unsupportedPattern:
            return "Unknown pattern"
        }
    }
}
