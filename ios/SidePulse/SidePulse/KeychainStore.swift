import Foundation
import Security

struct ProtectedPhoneGlanceCredentials: Codable, Equatable {
    let secret: String
    let accessToken: String

    init(secret: String, accessToken: String) throws {
        guard PhoneGlanceCredential.pairIsValid(secret: secret, accessToken: accessToken) else {
            throw PhoneGlanceError.invalidResponse
        }
        self.secret = secret
        self.accessToken = accessToken
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard Set(container.allKeys) == Set(CodingKeys.allCases) else {
            throw PhoneGlanceError.invalidResponse
        }
        try self.init(
            secret: container.decode(String.self, forKey: .secret),
            accessToken: container.decode(String.self, forKey: .accessToken)
        )
    }

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case secret
        case accessToken
    }
}

enum PhoneGlanceCredentialDataReadResult: Equatable {
    case value(Data)
    case missing
    case unavailable
}

enum PhoneGlanceCredentialsReadResult: Equatable {
    case value(ProtectedPhoneGlanceCredentials)
    case missing
    case unavailable
}

struct PhoneGlanceCredentialStore {
    let readData: () -> PhoneGlanceCredentialDataReadResult
    let writeData: (Data) -> Bool

    func read() -> PhoneGlanceCredentialsReadResult {
        switch readData() {
        case .value(let data):
            guard let credentials = try? JSONDecoder().decode(
                ProtectedPhoneGlanceCredentials.self,
                from: data
            ) else {
                return .unavailable
            }
            return .value(credentials)
        case .missing:
            return .missing
        case .unavailable:
            return .unavailable
        }
    }

    func save(_ credentials: ProtectedPhoneGlanceCredentials) -> Bool {
        guard let data = try? JSONEncoder().encode(credentials) else { return false }
        return writeData(data)
    }
}

struct KeychainStore {
    enum Key: String {
        case pushToken = "apns-device-token"
        case sharedSecret = "proxy-shared-secret"
        case phoneGlanceSecret = "phone-glance-secret"
        case phoneGlanceCredentials = "phone-glance-credentials"
    }

    static let shared = KeychainStore()

    private let service = "com.inteliwear.SidePulse.credentials"

    func string(for key: Key) -> String? {
        guard case .value(let value) = readString(for: key) else {
            return nil
        }
        return value
    }

    func readString(for key: Key) -> PhoneGlanceSecretReadResult {
        var query = baseQuery(for: key)
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnData as String] = true

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        return Self.classifyRead(status: status, data: result as? Data)
    }

    static func classifyRead(status: OSStatus, data: Data?) -> PhoneGlanceSecretReadResult {
        if status == errSecItemNotFound {
            return .missing
        }
        guard status == errSecSuccess,
              let data,
              let value = String(data: data, encoding: .utf8) else {
            return .unavailable
        }
        return .value(value)
    }

    @discardableResult
    func set(_ value: String, for key: Key) -> Bool {
        guard !value.isEmpty else {
            return remove(key)
        }

        let data = Data(value.utf8)
        let query = baseQuery(for: key)
        let updates: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, updates as CFDictionary)
        if updateStatus == errSecSuccess {
            return true
        }
        guard updateStatus == errSecItemNotFound else {
            return false
        }

        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess
    }

    func migrateLegacySecrets(from defaults: UserDefaults = .standard) {
        migrateLegacySecret(defaultsKey: "pushToken", to: .pushToken, from: defaults)
        migrateLegacySecret(defaultsKey: "sharedSecret", to: .sharedSecret, from: defaults)
    }

    func readPhoneGlanceCredentials() -> PhoneGlanceCredentialsReadResult {
        credentialStore().read()
    }

    func setPhoneGlanceCredentials(_ credentials: ProtectedPhoneGlanceCredentials) -> Bool {
        credentialStore().save(credentials)
    }

    private func migrateLegacySecret(defaultsKey: String, to key: Key, from defaults: UserDefaults) {
        guard let value = defaults.string(forKey: defaultsKey), !value.isEmpty else {
            return
        }
        guard set(value, for: key) else {
            return
        }
        defaults.removeObject(forKey: defaultsKey)
    }

    private func remove(_ key: Key) -> Bool {
        let status = SecItemDelete(baseQuery(for: key) as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    private func credentialStore() -> PhoneGlanceCredentialStore {
        PhoneGlanceCredentialStore(
            readData: { readData(for: .phoneGlanceCredentials) },
            writeData: { setData($0, for: .phoneGlanceCredentials) }
        )
    }

    private func readData(for key: Key) -> PhoneGlanceCredentialDataReadResult {
        var query = baseQuery(for: key)
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnData as String] = true
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return .missing }
        guard status == errSecSuccess, let data = result as? Data else { return .unavailable }
        return .value(data)
    }

    private func setData(_ data: Data, for key: Key) -> Bool {
        let query = baseQuery(for: key)
        let updates: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, updates as CFDictionary)
        if updateStatus == errSecSuccess { return true }
        guard updateStatus == errSecItemNotFound else { return false }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess
    }

    private func baseQuery(for key: Key) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue
        ]
    }
}
