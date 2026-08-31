import CryptoKit
import Darwin
import Foundation

enum PhoneGlanceError: Error, Equatable {
    case invalidEndpoint
    case invalidResponse
}

struct PhoneGlanceEndpoint: Equatable, Sendable {
    let host: String
    let port: Int
    let url: URL

    init(host: String, port: Int) throws {
        guard (1...65_535).contains(port),
              let authorityHost = Self.allowedAuthorityHost(host) else {
            throw PhoneGlanceError.invalidEndpoint
        }

        let authority = authorityHost.contains(":") ? "[\(authorityHost)]" : authorityHost
        guard let url = URL(string: "http://\(authority):\(port)/glance.json"),
              url.scheme == "http",
              url.host == host,
              url.port == port,
              url.path == "/glance.json",
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil else {
            throw PhoneGlanceError.invalidEndpoint
        }

        self.host = host
        self.port = port
        self.url = url
    }

    private static func allowedAuthorityHost(_ host: String) -> String? {
        guard !host.isEmpty, host == host.trimmingCharacters(in: .whitespacesAndNewlines) else {
            return nil
        }

        var ipv4 = in_addr()
        if host.withCString({ inet_pton(AF_INET, $0, &ipv4) }) == 1 {
            let address = UInt32(bigEndian: ipv4.s_addr)
            let first = UInt8((address >> 24) & 0xff)
            let second = UInt8((address >> 16) & 0xff)
            let allowed = first == 10
                || (first == 172 && (16...31).contains(second))
                || (first == 192 && second == 168)
                || (first == 169 && second == 254)
            return allowed ? host : nil
        }

        let addressAndScope = host.split(separator: "%", omittingEmptySubsequences: false)
        guard addressAndScope.count <= 2 else { return nil }
        let address = String(addressAndScope[0])
        let scope: String?
        if addressAndScope.count == 2 {
            let candidate = String(addressAndScope[1])
            guard !host.contains("%25"), validScopeID(candidate) else { return nil }
            scope = candidate
        } else {
            scope = nil
        }

        var ipv6 = in6_addr()
        if address.withCString({ inet_pton(AF_INET6, $0, &ipv6) }) == 1 {
            let kinds = withUnsafeBytes(of: &ipv6) { bytes -> (privateAddress: Bool, linkLocal: Bool) in
                guard bytes.count == 16 else { return (false, false) }
                let first = bytes[0]
                let second = bytes[1]
                let linkLocal = first == 0xfe && (second & 0xc0) == 0x80
                return ((first & 0xfe) == 0xfc || linkLocal, linkLocal)
            }
            guard kinds.privateAddress, scope == nil || kinds.linkLocal else { return nil }
            return scope.map { "\(address)%25\($0)" } ?? address
        }

        return nil
    }

    private static func validScopeID(_ value: String) -> Bool {
        let bytes = Array(value.utf8)
        guard (1...32).contains(bytes.count), let first = bytes.first else { return false }
        func isAlphanumeric(_ byte: UInt8) -> Bool {
            (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte)
        }
        guard isAlphanumeric(first) else { return false }
        return bytes.allSatisfy { byte in
            isAlphanumeric(byte) || byte == 45 || byte == 46 || byte == 95
        }
    }
}

enum PhoneGlanceScalar: Equatable, Sendable {
    case boolean(Bool)
    case integer(Int64)
    case number(Double)
    case text(String)
}

struct PhoneGlancePayload: Equatable, Sendable {
    let status: String
    let outcome: String
    let label: String?
    let message: String?
    let usage: [String: PhoneGlanceScalar]?
    let capacity: [String: PhoneGlanceScalar]?
}

struct VerifiedPhoneGlance: Equatable, Sendable {
    let sourceID: String
    let sequence: Int64
    let observedAt: Date
    let payload: PhoneGlancePayload
}

enum PhoneGlanceContract {
    static let maximumResponseBytes = 8 * 1_024
    private static let maximumAge: TimeInterval = 300
    private static let maximumFutureSkew: TimeInterval = 5
    private static let sourcePattern = try! NSRegularExpression(
        pattern: #"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z"#
    )
    private static let mapKeyPattern = try! NSRegularExpression(
        pattern: #"\A[a-z][a-z0-9_]{0,63}\z"#
    )
    private static let payloadFields: Set<String> = [
        "status", "outcome", "label", "message", "usage", "capacity",
    ]
    private static let usageFields: Set<String> = [
        "input_tokens", "cached_input_tokens", "output_tokens", "model_count",
        "estimated_cost_usd",
    ]
    private static let capacityFields: Set<String> = [
        "remaining_percent", "reset_at", "window", "label",
    ]

    static func verify(
        data: Data,
        secret: Data,
        lastSequence: Int64?,
        now: Date
    ) throws -> VerifiedPhoneGlance {
        guard !data.isEmpty,
              data.count <= maximumResponseBytes,
              !secret.isEmpty,
              now.timeIntervalSince1970.isFinite,
              now.timeIntervalSince1970 >= 0 else {
            throw PhoneGlanceError.invalidResponse
        }

        let outerObject = try parseJSONObject(data)
        let outer = try parseEnvelope(outerObject, requiresSignedTransport: true)
        let signedBytes = try decodeBase64URL(outer.signedBody)
        let signature = try decodeSignature(outer.signature)
        let key = SymmetricKey(data: secret)
        guard HMAC<SHA256>.isValidAuthenticationCode(
            signature,
            authenticating: signedBytes,
            using: key
        ) else {
            throw PhoneGlanceError.invalidResponse
        }

        let signedObject = try parseJSONObject(signedBytes)
        let signed = try parseSignedEnvelope(signedObject)
        guard outer.contents == signed else {
            throw PhoneGlanceError.invalidResponse
        }

        if let lastSequence, outer.contents.sequence <= lastSequence {
            throw PhoneGlanceError.invalidResponse
        }
        let age = now.timeIntervalSince1970 - outer.contents.observedAt
        guard age <= maximumAge, age >= -maximumFutureSkew else {
            throw PhoneGlanceError.invalidResponse
        }

        return VerifiedPhoneGlance(
            sourceID: outer.contents.sourceID,
            sequence: outer.contents.sequence,
            observedAt: Date(timeIntervalSince1970: outer.contents.observedAt),
            payload: outer.contents.payload
        )
    }

    private struct EnvelopeContents: Equatable {
        let sourceID: String
        let sequence: Int64
        let observedAt: Double
        let observedAtKind: NumberKind
        let payload: PhoneGlancePayload
    }

    private struct TransportEnvelope {
        let contents: EnvelopeContents
        let signedBody: String
        let signature: String
    }

    private enum NumberKind: Equatable {
        case integer
        case floatingPoint
    }

    private static func parseJSONObject(_ data: Data) throws -> [String: Any] {
        let value: Any
        do {
            value = try JSONSerialization.jsonObject(with: data, options: [])
        } catch {
            throw PhoneGlanceError.invalidResponse
        }
        guard let object = value as? [String: Any] else {
            throw PhoneGlanceError.invalidResponse
        }
        return object
    }

    private static func parseEnvelope(
        _ object: [String: Any],
        requiresSignedTransport: Bool
    ) throws -> TransportEnvelope {
        let required: Set<String> = [
            "source_id", "sequence", "observed_at", "payload", "signed_body", "signature",
        ]
        guard Set(object.keys) == required,
              let signedBody = object["signed_body"] as? String,
              let signature = object["signature"] as? String else {
            throw PhoneGlanceError.invalidResponse
        }
        let contents = try parseContents(object, expectedFields: required)
        return TransportEnvelope(contents: contents, signedBody: signedBody, signature: signature)
    }

    private static func parseSignedEnvelope(_ object: [String: Any]) throws -> EnvelopeContents {
        let required: Set<String> = ["source_id", "sequence", "observed_at", "payload"]
        guard Set(object.keys) == required else {
            throw PhoneGlanceError.invalidResponse
        }
        return try parseContents(object, expectedFields: required)
    }

    private static func parseContents(
        _ object: [String: Any],
        expectedFields: Set<String>
    ) throws -> EnvelopeContents {
        guard Set(object.keys) == expectedFields,
              let sourceID = object["source_id"] as? String,
              validSourceID(sourceID),
              let sequenceNumber = object["sequence"] as? NSNumber,
              !isBoolean(sequenceNumber),
              numberKind(sequenceNumber) == .integer,
              sequenceNumber.int64Value > 0,
              sequenceNumber.doubleValue == Double(sequenceNumber.int64Value),
              let observedNumber = object["observed_at"] as? NSNumber,
              !isBoolean(observedNumber),
              observedNumber.doubleValue.isFinite,
              observedNumber.doubleValue >= 0,
              let payloadObject = object["payload"] as? [String: Any] else {
            throw PhoneGlanceError.invalidResponse
        }
        return EnvelopeContents(
            sourceID: sourceID,
            sequence: sequenceNumber.int64Value,
            observedAt: observedNumber.doubleValue,
            observedAtKind: numberKind(observedNumber),
            payload: try parsePayload(payloadObject)
        )
    }

    private static func parsePayload(_ object: [String: Any]) throws -> PhoneGlancePayload {
        guard Set(object.keys).isSubset(of: payloadFields),
              let status = object["status"] as? String,
              validText(status, maximumScalars: 64),
              let outcome = object["outcome"] as? String,
              validText(outcome, maximumScalars: 64) else {
            throw PhoneGlanceError.invalidResponse
        }
        let label = try optionalText(object, key: "label", maximumScalars: 160)
        let message = try optionalText(object, key: "message", maximumScalars: 512)
        let usage = try optionalMap(object, key: "usage", allowedKeys: usageFields)
        let capacity = try optionalMap(object, key: "capacity", allowedKeys: capacityFields)
        return PhoneGlancePayload(
            status: status,
            outcome: outcome,
            label: label,
            message: message,
            usage: usage,
            capacity: capacity
        )
    }

    private static func optionalText(
        _ object: [String: Any],
        key: String,
        maximumScalars: Int
    ) throws -> String? {
        guard let value = object[key] else { return nil }
        guard let text = value as? String, validText(text, maximumScalars: maximumScalars) else {
            throw PhoneGlanceError.invalidResponse
        }
        return text
    }

    private static func optionalMap(
        _ object: [String: Any],
        key: String,
        allowedKeys: Set<String>
    ) throws -> [String: PhoneGlanceScalar]? {
        guard let value = object[key] else { return nil }
        guard let map = value as? [String: Any],
              map.count <= 16,
              Set(map.keys).isSubset(of: allowedKeys) else {
            throw PhoneGlanceError.invalidResponse
        }

        var parsed: [String: PhoneGlanceScalar] = [:]
        for (mapKey, rawValue) in map {
            guard matches(mapKey, expression: mapKeyPattern) else {
                throw PhoneGlanceError.invalidResponse
            }
            if let text = rawValue as? String {
                guard validText(text, maximumScalars: 512) else {
                    throw PhoneGlanceError.invalidResponse
                }
                parsed[mapKey] = .text(text)
            } else if let number = rawValue as? NSNumber {
                if isBoolean(number) {
                    parsed[mapKey] = .boolean(number.boolValue)
                } else if numberKind(number) == .integer {
                    guard number.int64Value >= 0,
                          number.doubleValue <= 1_000_000_000_000_000,
                          number.doubleValue == Double(number.int64Value) else {
                        throw PhoneGlanceError.invalidResponse
                    }
                    parsed[mapKey] = .integer(number.int64Value)
                } else {
                    let double = number.doubleValue
                    guard double.isFinite, double >= 0, double <= 1_000_000_000_000_000 else {
                        throw PhoneGlanceError.invalidResponse
                    }
                    parsed[mapKey] = .number(double)
                }
            } else {
                throw PhoneGlanceError.invalidResponse
            }
        }
        return parsed
    }

    private static func decodeBase64URL(_ value: String) throws -> Data {
        let maximumEncodedCharacters = (maximumResponseBytes * 4 + 2) / 3
        guard !value.isEmpty,
              value.utf8.count <= maximumEncodedCharacters,
              !value.contains("="),
              value.unicodeScalars.allSatisfy({ scalar in
                  ("A"..."Z").contains(Character(String(scalar)))
                      || ("a"..."z").contains(Character(String(scalar)))
                      || ("0"..."9").contains(Character(String(scalar)))
                      || scalar == "-"
                      || scalar == "_"
              }) else {
            throw PhoneGlanceError.invalidResponse
        }
        var padded = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        padded += String(repeating: "=", count: (4 - padded.count % 4) % 4)
        guard let decoded = Data(base64Encoded: padded),
              !decoded.isEmpty,
              decoded.count <= maximumResponseBytes,
              encodeBase64URL(decoded) == value else {
            throw PhoneGlanceError.invalidResponse
        }
        return decoded
    }

    private static func encodeBase64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private static func decodeSignature(_ value: String) throws -> Data {
        guard value.utf8.count == 64,
              value.unicodeScalars.allSatisfy({
                  ("0"..."9").contains(Character(String($0)))
                      || ("a"..."f").contains(Character(String($0)))
              }) else {
            throw PhoneGlanceError.invalidResponse
        }
        var result = Data(capacity: 32)
        var index = value.startIndex
        for _ in 0..<32 {
            let next = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index..<next], radix: 16) else {
                throw PhoneGlanceError.invalidResponse
            }
            result.append(byte)
            index = next
        }
        return result
    }

    private static func validSourceID(_ value: String) -> Bool {
        value.unicodeScalars.count <= 128 && matches(value, expression: sourcePattern)
    }

    private static func validText(_ value: String, maximumScalars: Int) -> Bool {
        guard !value.isEmpty, value.unicodeScalars.count <= maximumScalars else {
            return false
        }
        return value.unicodeScalars.allSatisfy { scalar in
            !(scalar.value < 32 || (0x7f...0x9f).contains(scalar.value))
        }
    }

    private static func matches(_ value: String, expression: NSRegularExpression) -> Bool {
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        return expression.firstMatch(in: value, range: range)?.range == range
    }

    private static func isBoolean(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    private static func numberKind(_ number: NSNumber) -> NumberKind {
        switch String(cString: number.objCType) {
        case "f", "d":
            return .floatingPoint
        default:
            return .integer
        }
    }
}
