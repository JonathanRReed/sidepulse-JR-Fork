import SwiftUI
import UIKit
import UserNotifications

@MainActor
struct ContentView: View {
    @StateObject private var model: AppModel
    @State private var isShowingFolderPicker = false
    @State private var activeSheet: ActiveSheet?
    @Environment(\.scenePhase) private var scenePhase

    init() {
        _model = StateObject(wrappedValue: AppModel.shared)
    }

    init(model: AppModel) {
        _model = StateObject(wrappedValue: model)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HeaderPanel(model: model) {
                        activeSheet = .token
                        requestPushToken()
                    }

                    ComputerGlancePanel(model: model) {
                        refreshComputerGlance()
                    }

                    RecentPushesPanel(pushes: Array(model.receivedPushes.prefix(5)))

                    SidePulseDotSetupPanel(model: model) {
                        activeSheet = .folderSetup
                    }

                    QuickPatternsPanel { pattern in
                        write(pattern)
                    }
                }
                .padding(16)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle(ProductIdentity.displayName)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        SettingsView(
                            model: model,
                            requestPushToken: requestPushToken,
                            showFolderPicker: showFolderPicker
                        )
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Settings")
                }
            }
        }
        .sheet(item: $activeSheet) { sheet in
            switch sheet {
            case .token:
                TokenSheet(model: model, requestPushToken: requestPushToken)
            case .folderSetup:
                FolderSetupSheet {
                    showFolderPicker()
                }
            }
        }
        .sheet(isPresented: $isShowingFolderPicker) {
            FolderPicker { url in
                isShowingFolderPicker = false
                do {
                    try DriveWriter.shared.saveFolder(url)
                    model.refreshFolderStatus()
                    model.lastMessage = "Selected \(url.lastPathComponent)"
                } catch {
                    model.recordError(error)
                }
            } onCancel: {
                isShowingFolderPicker = false
            }
        }
        .onAppear {
            model.refreshFolderStatus()
        }
        .onChange(of: scenePhase) { newPhase in
            guard newPhase == .active else { return }
            refreshComputerGlance()
        }
    }

    private func requestPushToken() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { _, error in
            Task { @MainActor in
                if let error {
                    model.recordError(error)
                    return
                }

                UIApplication.shared.registerForRemoteNotifications()
                model.lastMessage = "Registering with APNs"
            }
        }
    }

    private func showFolderPicker() {
        activeSheet = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            isShowingFolderPicker = true
        }
    }

    private func refreshComputerGlance() {
        Task {
            await model.refreshPhoneGlance()
        }
    }

    private func write(_ pattern: LEDPattern) {
        let pushBase = ReceivedPush(
            source: "Quick Pattern",
            title: pattern.displayName,
            body: pattern.detail,
            patternName: pattern.name,
            ledText: pattern.ledText,
            payloadSummary: "{\"pattern\":\"\(pattern.name)\"}",
            writeStatus: .received
        )

        guard model.hasFolderAccess else {
            var push = pushBase
            push.writeStatus = .noFolder
            model.recordReceivedPush(push)
            activeSheet = .folderSetup
            return
        }

        do {
            let targetURL = try DriveWriter.shared.write(pattern.ledText)
            var push = pushBase
            push.body = "Wrote \(targetURL.lastPathComponent)"
            push.writeStatus = .wrote
            model.recordReceivedPush(push)
        } catch {
            var push = pushBase
            push.writeStatus = .failed
            push.errorMessage = "Write failed"
            model.recordReceivedPush(push)
        }
    }
}

private enum ActiveSheet: Identifiable {
    case token
    case folderSetup

    var id: String {
        switch self {
        case .token:
            return "token"
        case .folderSetup:
            return "folderSetup"
        }
    }
}

private struct HeaderPanel: View {
    @ObservedObject var model: AppModel
    let getToken: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Label(ProductIdentity.displayName, systemImage: "dot.radiowaves.left.and.right")
                            .font(.title2.weight(.semibold))
                        Text("Push inbox and SidePulse Dot writer")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    StatusPill(isConnected: model.hasFolderAccess)
                }

                Button {
                    getToken()
                } label: {
                    Label(model.pushToken.isEmpty ? "Get Push Token" : "Push Notifications Ready", systemImage: "key.horizontal")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }
}

private struct StatusPill: View {
    let isConnected: Bool

    var body: some View {
        Label(isConnected ? "Folder connected" : "No folder", systemImage: isConnected ? "checkmark.circle.fill" : "exclamationmark.circle")
            .font(.caption.weight(.semibold))
            .foregroundStyle(isConnected ? Color.green : Color.orange)
            .lineLimit(1)
    }
}

private struct ComputerGlancePanel: View {
    @ObservedObject var model: AppModel
    let refresh: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: statusSymbolName)
                        .font(.title2)
                        .foregroundStyle(statusTint)
                        .frame(width: 34, height: 34)

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Computer Glance")
                            .font(.headline)
                        Text(statusTitle)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(statusTint)
                        statusDetail
                    }

                    Spacer(minLength: 0)
                }

                if case .loading = model.phoneGlanceLoadState {
                    ProgressView("Checking Computer Glance")
                        .font(.footnote)
                } else {
                    Button {
                        refresh()
                    } label: {
                        Label("Refresh Computer Glance", systemImage: "arrow.clockwise")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityHint("Requests one current signed status from the configured Mac.")
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Computer Glance, \(statusTitle)")
    }

    @ViewBuilder
    private var statusDetail: some View {
        switch model.phoneGlanceLoadState {
        case .unconfigured:
            Text("Add your Mac private IP, port, and shared secret in Settings to view its signed, read-only local-network feed.")
        case .idle:
            Text("Saved settings are ready. Refresh to check the signed, read-only local-network feed.")
        case .loading:
            Text("Verifying the signed, read-only local-network feed.")
        case .ready(let glance):
            ComputerGlanceSnapshot(glance: glance)
        case .stale(let glance):
            VStack(alignment: .leading, spacing: 3) {
                ComputerGlanceSnapshot(glance: glance)
                Text("The last verified status may be out of date. Check that the Mac listener is running, then refresh.")
            }
        case .unavailable:
            Text("Check that the Mac listener is running and both devices are on the same local network, then refresh.")
        }
    }

    private var statusTitle: String {
        switch model.phoneGlanceLoadState {
        case .unconfigured:
            return "Not configured"
        case .idle:
            return "Ready to check"
        case .loading:
            return "Checking"
        case .ready:
            return "Verified"
        case .stale:
            return "Last verified status is stale"
        case .unavailable:
            return "Unavailable"
        }
    }

    private var statusSymbolName: String {
        switch model.phoneGlanceLoadState {
        case .unconfigured:
            return "desktopcomputer.and.iphone"
        case .idle:
            return "desktopcomputer"
        case .loading:
            return "arrow.triangle.2.circlepath"
        case .ready:
            return "checkmark.shield"
        case .stale:
            return "exclamationmark.arrow.triangle.2.circlepath"
        case .unavailable:
            return "exclamationmark.triangle"
        }
    }

    private var statusTint: Color {
        switch model.phoneGlanceLoadState {
        case .ready:
            return .green
        case .stale, .unconfigured:
            return .orange
        case .unavailable:
            return .red
        case .idle, .loading:
            return .accentColor
        }
    }
}

private struct ComputerGlanceSnapshot: View {
    let glance: VerifiedPhoneGlance

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(snapshotTitle)
                .font(.footnote.weight(.semibold))
            Text("\(glance.payload.outcome.capitalized) · Updated \(glance.observedAt, style: .relative)")
                .font(.footnote)
        }
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(snapshotTitle), \(glance.payload.outcome), updated \(glance.observedAt.formatted(date: .abbreviated, time: .shortened))")
    }

    private var snapshotTitle: String {
        let label = glance.payload.label?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let label, !label.isEmpty {
            return "\(label): \(glance.payload.status)"
        }
        return glance.payload.status
    }
}

private struct RecentPushesPanel: View {
    let pushes: [ReceivedPush]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Latest Pushes")
                    .font(.headline)
                Spacer()
                Text("\(pushes.count)")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            if pushes.isEmpty {
                EmptyInboxView()
            } else {
                VStack(spacing: 8) {
                    ForEach(pushes) { push in
                        ReceivedPushRow(push: push)
                    }
                }
            }
        }
    }
}

private struct EmptyInboxView: View {
    var body: some View {
        Panel {
            HStack(spacing: 12) {
                Image(systemName: "tray")
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .frame(width: 32, height: 32)

                VStack(alignment: .leading, spacing: 3) {
                    Text("No pushes received")
                        .font(.subheadline.weight(.semibold))
                    Text("\(ProductIdentity.displayName) will store general pushes here even without a SidePulse Dot device.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct ReceivedPushRow: View {
    let push: ReceivedPush

    var body: some View {
        Panel {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: push.writeStatus.symbolName)
                    .font(.headline)
                    .foregroundStyle(push.writeStatus.tint)
                    .frame(width: 28, height: 28)

                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(push.title)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text(push.receivedAt, style: .relative)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Text(push.body)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)

                    HStack(spacing: 8) {
                        Text(push.writeStatus.displayName)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(push.writeStatus.tint)

                        if let patternName = push.patternName {
                            Text(patternName)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        } else if push.ledByteCount > 0 {
                            Text("\(push.ledByteCount) bytes")
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }
}

private struct SidePulseDotSetupPanel: View {
    @ObservedObject var model: AppModel
    let openSetup: () -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "externaldrive")
                        .font(.title2)
                        .foregroundStyle(model.hasFolderAccess ? Color.green : Color.accentColor)
                        .frame(width: 34, height: 34)

                    VStack(alignment: .leading, spacing: 5) {
                        Text("SidePulse Dot Folder")
                            .font(.headline)
                        Text(model.selectedFolderPath)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }

                Button {
                    openSetup()
                } label: {
                    Label(model.hasFolderAccess ? "Change LED Folder" : "Set Up SidePulse Dot Folder", systemImage: "folder.badge.plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
    }
}

private struct QuickPatternsPanel: View {
    let writePattern: (LEDPattern) -> Void
    private let columns = [
        GridItem(.adaptive(minimum: 150), spacing: 10)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Quick Patterns")
                .font(.headline)

            LazyVGrid(columns: columns, spacing: 10) {
                ForEach(LEDPatternCatalog.patterns) { pattern in
                    Button {
                        writePattern(pattern)
                    } label: {
                        PatternButtonLabel(pattern: pattern)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct PatternButtonLabel: View {
    let pattern: LEDPattern

    var body: some View {
        Panel {
            HStack(spacing: 10) {
                Circle()
                    .fill(Color(hex: pattern.tintHex))
                    .frame(width: 12, height: 12)

                VStack(alignment: .leading, spacing: 3) {
                    Text(pattern.displayName)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                    Text(pattern.name)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        }
    }
}

private struct TokenSheet: View {
    @ObservedObject var model: AppModel
    let requestPushToken: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Push Token") {
                    if model.pushToken.isEmpty {
                        Text("No token yet")
                            .foregroundStyle(.secondary)
                    } else {
                        Label("Token stored securely", systemImage: "checkmark.shield")
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        requestPushToken()
                    } label: {
                        Label("Request Token", systemImage: "bell.badge")
                    }
                }
            }
            .navigationTitle("Push Token")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}

private struct FolderSetupSheet: View {
    let openPicker: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                Label("SidePulse Dot Device in Files", systemImage: "externaldrive")
                    .font(.title3.weight(.semibold))

                VStack(alignment: .leading, spacing: 10) {
                    Text("1. Attach the SidePulse Dot USB drive to this iPhone or iPad.")
                    Text("2. Open Files and select the SidePulse Dot USB drive folder containing LEDS.LED.")
                    Text("3. \(ProductIdentity.displayName) will remember that folder for later pushes and Shortcuts.")
                }
                .font(.body)
                .foregroundStyle(.secondary)

                Button {
                    dismiss()
                    openPicker()
                } label: {
                    Label("Open Files", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)

                Spacer()
            }
            .padding(20)
            .navigationTitle("Set Up Folder")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}

private struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var sharedSecretDraft = ""
    @State private var phoneGlanceHostDraft = ""
    @State private var phoneGlancePortDraft = ""
    @State private var phoneGlanceSecretDraft = ""
    @State private var phoneGlanceAccessTokenDraft = ""
    @State private var phoneGlanceConfigurationMessage: String?
    let requestPushToken: () -> Void
    let showFolderPicker: () -> Void

    var body: some View {
        Form {
            Section("Push Token") {
                Button {
                    requestPushToken()
                } label: {
                    Label("Get Push Token", systemImage: "key.horizontal")
                }

                if model.pushToken.isEmpty {
                    Text("No token yet")
                        .foregroundStyle(.secondary)
                } else {
                    Label("Token stored securely", systemImage: "checkmark.shield")
                        .foregroundStyle(.secondary)
                }
            }

            Section("SidePulse Dot") {
                LabeledContent("Folder", value: model.selectedFolderPath)

                Button {
                    showFolderPicker()
                } label: {
                    Label("Set Up LED Folder", systemImage: "folder.badge.plus")
                }
            }

            Section("Computer Glance") {
                Text("View a signed, minimized, read-only status feed from your Mac over HTTPS on the same local network.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                TextField("Mac private IP address", text: $phoneGlanceHostDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.numbersAndPunctuation)

                TextField("Computer Glance port", text: $phoneGlancePortDraft)
                    .keyboardType(.numberPad)

                SecureField("Computer Glance shared secret", text: $phoneGlanceSecretDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.password)
                    .onSubmit {
                        saveComputerGlanceConfiguration()
                    }

                SecureField("Computer Glance access token", text: $phoneGlanceAccessTokenDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.password)

                Text("Enter two different credentials. The access token must be 24 to 4096 printable ASCII characters with no spaces. Both are stored only in the iOS Keychain.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Button {
                    saveComputerGlanceConfiguration()
                } label: {
                    Label("Save Computer Glance Settings", systemImage: "square.and.arrow.down")
                }
                .disabled(model.phoneGlanceLoadState == .loading)

                Button {
                    testComputerGlance()
                } label: {
                    Label("Test Computer Glance", systemImage: "checkmark.arrow.trianglehead.2.clockwise")
                }
                .disabled(model.phoneGlanceLoadState == .loading)
                .accessibilityHint("Saves newly entered settings first, then requests one signed status from the Mac.")

                if let phoneGlanceConfigurationMessage {
                    Text(phoneGlanceConfigurationMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                PhoneGlanceSettingsStatus(loadState: model.phoneGlanceLoadState)
            }

            Section("Advanced Server") {
                TextField("Proxy base URL", text: $model.serverBaseURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)

                SecureField("Shared secret", text: $sharedSecretDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit {
                        saveSharedSecret()
                    }

                Button {
                    saveSharedSecret()
                } label: {
                    Label(
                        model.sharedSecret.isEmpty ? "Save Shared Secret" : "Update Shared Secret",
                        systemImage: "lock.shield"
                    )
                }

                if let endpoint = model.pushEndpointURL {
                    Text(endpoint)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                }

                if let curlExample = model.curlExample {
                    Text(curlExample)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)

                    Button {
                        UIPasteboard.general.string = curlExample
                        model.lastMessage = "Copied curl example"
                    } label: {
                        Label("Copy Curl", systemImage: "terminal")
                    }
                }
            }

            Section("Raw LED Editor") {
                TextEditor(text: $model.ledText)
                    .font(.system(.body, design: .monospaced))
                    .frame(minHeight: 140)

                Button {
                    writeLocalTest()
                } label: {
                    Label("Write to USB", systemImage: "square.and.arrow.down")
                }

                if let shortcutURL = model.shortcutWriteURL {
                    Button {
                        UIPasteboard.general.string = shortcutURL
                        model.lastMessage = "Copied Shortcut URL"
                    } label: {
                        Label("Copy Shortcut URL", systemImage: "link.badge.plus")
                    }
                }
            }

            Section("Diagnostics") {
                Button {
                    model.refreshEventLog()
                } label: {
                    Label("Refresh Log", systemImage: "arrow.clockwise")
                }

                Button(role: .destructive) {
                    model.clearEventLog()
                } label: {
                    Label("Clear Log", systemImage: "trash")
                }

                if model.eventLog.isEmpty {
                    Text("No events")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.eventLog.reversed(), id: \.self) { line in
                        Text(line)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
            }

            Section("Inbox") {
                Button(role: .destructive) {
                    model.clearReceivedPushes()
                } label: {
                    Label("Clear Received Pushes", systemImage: "tray.and.arrow.down")
                }
            }
        }
        .navigationTitle("Settings")
        .onAppear {
            sharedSecretDraft = model.sharedSecret
            phoneGlanceHostDraft = model.phoneGlanceHost
            phoneGlancePortDraft = model.phoneGlancePort
        }
    }

    private func saveSharedSecret() {
        model.saveSharedSecret(sharedSecretDraft)
        sharedSecretDraft = model.sharedSecret
    }

    private func writeLocalTest() {
        do {
            let targetURL = try DriveWriter.shared.write(model.ledText)
            model.recordWriteSuccess("Wrote \(targetURL.lastPathComponent)")
        } catch {
            model.recordError(error)
        }
    }

    @discardableResult
    private func saveComputerGlanceConfiguration() -> Bool {
        guard model.phoneGlanceLoadState != .loading else {
            return false
        }

        guard !phoneGlanceSecretDraft.isEmpty, !phoneGlanceAccessTokenDraft.isEmpty else {
            phoneGlanceConfigurationMessage = "Enter the Computer Glance signing secret and access token before saving."
            return false
        }

        switch model.savePhoneGlanceConfiguration(
            host: phoneGlanceHostDraft,
            port: phoneGlancePortDraft,
            secret: phoneGlanceSecretDraft,
            accessToken: phoneGlanceAccessTokenDraft
        ) {
        case .validationFailure:
            phoneGlanceConfigurationMessage = "Use a private IP address, a port from 1 to 65535, and two different valid credentials. The access token must be 24 to 4096 printable ASCII characters without spaces."
            return false
        case .keychainStorageFailure:
            phoneGlanceConfigurationMessage = "The protected Computer Glance credentials could not be saved. Unlock the device if needed, then save again."
            return false
        case .saved:
            break
        }

        phoneGlanceHostDraft = model.phoneGlanceHost
        phoneGlancePortDraft = model.phoneGlancePort
        phoneGlanceSecretDraft = ""
        phoneGlanceAccessTokenDraft = ""
        phoneGlanceConfigurationMessage = nil
        return true
    }

    private func testComputerGlance() {
        if !phoneGlanceSecretDraft.isEmpty || !phoneGlanceAccessTokenDraft.isEmpty {
            guard saveComputerGlanceConfiguration() else { return }
        } else if phoneGlanceHostDraft != model.phoneGlanceHost || phoneGlancePortDraft != model.phoneGlancePort {
            phoneGlanceConfigurationMessage = "Enter both Computer Glance credentials before testing changed settings."
            return
        }

        phoneGlanceConfigurationMessage = nil
        Task {
            await model.refreshPhoneGlance()
        }
    }
}

private struct PhoneGlanceSettingsStatus: View {
    let loadState: PhoneGlanceLoadState

    var body: some View {
        switch loadState {
        case .unconfigured:
            Label("Computer Glance needs setup", systemImage: "exclamationmark.circle")
                .foregroundStyle(.orange)
        case .idle:
            Label("Computer Glance settings are saved", systemImage: "checkmark.circle")
                .foregroundStyle(.secondary)
        case .loading:
            Label("Checking Computer Glance", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(.secondary)
        case .ready(let glance):
            Label("Verified \(glance.observedAt, style: .relative)", systemImage: "checkmark.shield")
                .foregroundStyle(.green)
        case .stale(let glance):
            Label("Last verified \(glance.observedAt, style: .relative), refresh after checking the Mac listener", systemImage: "exclamationmark.arrow.triangle.2.circlepath")
                .foregroundStyle(.orange)
        case .unavailable:
            Label("Unavailable, check the Mac listener and same local network, then test again", systemImage: "exclamationmark.triangle")
                .foregroundStyle(.red)
        }
    }
}

private struct Panel<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        content()
            .padding(14)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private extension ReceivedPush.WriteStatus {
    var symbolName: String {
        switch self {
        case .received:
            return "tray.fill"
        case .wrote:
            return "checkmark.circle.fill"
        case .noFolder:
            return "folder.badge.questionmark"
        case .failed:
            return "xmark.octagon.fill"
        case .unsupportedPattern:
            return "questionmark.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .received:
            return .blue
        case .wrote:
            return .green
        case .noFolder:
            return .orange
        case .failed:
            return .red
        case .unsupportedPattern:
            return .purple
        }
    }
}

private extension Color {
    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&value)

        let red: UInt64
        let green: UInt64
        let blue: UInt64

        switch cleaned.count {
        case 6:
            red = (value >> 16) & 0xff
            green = (value >> 8) & 0xff
            blue = value & 0xff
        default:
            red = 0x3b
            green = 0x82
            blue = 0xf6
        }

        self.init(
            .sRGB,
            red: Double(red) / 255,
            green: Double(green) / 255,
            blue: Double(blue) / 255,
            opacity: 1
        )
    }
}
