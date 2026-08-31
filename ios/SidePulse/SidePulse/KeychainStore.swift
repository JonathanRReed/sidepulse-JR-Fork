import Foundation
import Security

struct KeychainStore {
    enum Key: String {
        case pushToken = "apns-device-token"
        case sharedSecret = "proxy-shared-secret"
        case phoneGlanceSecret = "phone-glance-secret"
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

    private func baseQuery(for key: Key) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue
        ]
    }
}
