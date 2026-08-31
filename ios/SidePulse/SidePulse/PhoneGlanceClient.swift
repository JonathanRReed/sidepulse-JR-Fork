import Foundation

enum PhoneGlanceClient {
    static func fetch(
        endpoint: PhoneGlanceEndpoint,
        secret: Data,
        lastSequence: Int64?,
        now: () -> Date = { Date() }
    ) async throws -> VerifiedPhoneGlance {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 5
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false

        let delegate = BoundedPhoneGlanceDelegate()
        let delegateQueue = OperationQueue()
        delegateQueue.maxConcurrentOperationCount = 1
        let session = URLSession(
            configuration: configuration,
            delegate: delegate,
            delegateQueue: delegateQueue
        )
        defer { session.invalidateAndCancel() }

        var request = URLRequest(
            url: endpoint.url,
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: 5
        )
        request.httpMethod = "GET"
        request.httpBody = nil
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let data = try await delegate.load(request: request, using: session)
        let verificationTime = now()
        return try PhoneGlanceContract.verify(
            data: data,
            secret: secret,
            lastSequence: lastSequence,
            now: verificationTime
        )
    }
}

enum PhoneGlanceSecretReadResult: Equatable {
    case value(String)
    case missing
    case unavailable
}

struct PhoneGlanceStateStore {
    private enum Keys {
        static let host = "phoneGlanceHost"
        static let port = "phoneGlancePort"
        static let sequences = "phoneGlanceSequences"
    }

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func loadEndpoint() -> PhoneGlanceEndpoint? {
        let host = defaults.string(forKey: Keys.host) ?? ""
        let port = defaults.integer(forKey: Keys.port)
        return try? PhoneGlanceEndpoint(host: host, port: port)
    }

    func saveConfiguration(_ endpoint: PhoneGlanceEndpoint) {
        defaults.set(endpoint.host, forKey: Keys.host)
        defaults.set(endpoint.port, forKey: Keys.port)
        defaults.removeObject(forKey: Keys.sequences)
    }

    func clearConfiguration() {
        defaults.removeObject(forKey: Keys.host)
        defaults.removeObject(forKey: Keys.port)
        defaults.removeObject(forKey: Keys.sequences)
    }

    func lastAcceptedSequence(for sourceID: String) -> Int64? {
        guard let sequences = defaults.dictionary(forKey: Keys.sequences),
              let value = sequences[sourceID] as? NSNumber,
              value.int64Value > 0,
              value.doubleValue == Double(value.int64Value) else {
            return nil
        }
        return value.int64Value
    }

    func saveAcceptedSequence(_ sequence: Int64, for sourceID: String) {
        guard sequence > 0 else { return }
        var sequences = defaults.dictionary(forKey: Keys.sequences) ?? [:]
        sequences[sourceID] = NSNumber(value: sequence)
        defaults.set(sequences, forKey: Keys.sequences)
    }
}

private final class BoundedPhoneGlanceDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private var continuation: CheckedContinuation<Data, Error>?
    private var received = Data()
    private var refused = false

    func load(request: URLRequest, using session: URLSession) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            session.dataTask(with: request).resume()
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        refused = true
        completionHandler(nil)
        task.cancel()
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        guard let response = response as? HTTPURLResponse,
              response.statusCode == 200,
              response.expectedContentLength <= Int64(PhoneGlanceContract.maximumResponseBytes) else {
            refused = true
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive data: Data
    ) {
        guard !refused,
              received.count <= PhoneGlanceContract.maximumResponseBytes - data.count else {
            refused = true
            dataTask.cancel()
            return
        }
        received.append(data)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        guard let continuation else { return }
        self.continuation = nil
        if refused || error != nil || received.isEmpty {
            continuation.resume(throwing: PhoneGlanceError.invalidResponse)
        } else {
            continuation.resume(returning: received)
        }
    }
}
