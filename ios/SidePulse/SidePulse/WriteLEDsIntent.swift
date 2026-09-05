import AppIntents
import Foundation

@available(iOS 16.0, *)
struct WriteLEDsIntent: AppIntent {
    // App Intents requires a build-time literal for extracted title metadata.
    static var title: LocalizedStringResource = "Write JR-Bar LEDS.LED"
    static var description = IntentDescription("Writes the supplied LED program to LEDS.LED on the selected USB drive.")
    static var openAppWhenRun = false

    @Parameter(title: "LEDS.LED Text")
    var ledsText: String

    init() {}

    init(ledsText: String) {
        self.ledsText = ledsText
    }

    func perform() async throws -> some IntentResult {
        _ = try DriveWriter.shared.write(ledsText)
        await MainActor.run {
            AppModel.shared.recordWriteSuccess("Shortcut wrote LEDS.LED")
        }
        return .result()
    }
}

@available(iOS 16.0, *)
struct SidePulseShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: WriteLEDsIntent(),
            phrases: [
                "Write \(.applicationName) LEDs",
                "Send \(.applicationName) LEDs"
            ],
            shortTitle: "Write LEDs",
            systemImageName: "externaldrive"
        )
    }
}
