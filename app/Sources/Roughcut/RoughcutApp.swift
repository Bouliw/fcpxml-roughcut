import AppKit
import SwiftUI
import UserNotifications

@main
struct RoughcutApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var app = AppState.shared

    var body: some Scene {
        Window("Roughcut", id: "main") {
            MainView().environmentObject(app)
        }
        .windowResizability(.contentMinSize)
        .defaultPosition(.center)
        .commands {
            CommandGroup(replacing: .newItem) {  // ⌘O: the folder of footage, from the keyboard
                Button(L10n.t("drop.choose")) { AppState.shared.chooseFolder() }.keyboardShortcut("o")
            }
            CommandGroup(replacing: .help) {
                Button(L10n.t("report.menu")) { Report.open(message: nil, detail: nil, log: nil) }
            }
        }

        Window(L10n.t("text.title"), id: "text") {
            TextEditView().environmentObject(app)
        }
        .defaultSize(width: 760, height: 720)

        Settings {
            SettingsView().environmentObject(app)
        }

        MenuBarExtra {
            MenuBarView().environmentObject(app)
        } label: {
            MenuBarLabel(app: app)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        // open -a Roughcut --args --edit folder [--music auto|none|track]: straight to the edit (scripts, Finder actions);
        // --open folder: the folder, ready to edit; --text project: an edit by its text
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--open"), i + 1 < args.count {
            Task { @MainActor in AppState.shared.open(URL(fileURLWithPath: args[i + 1])) }
        }
        if let i = args.firstIndex(of: "--edit"), i + 1 < args.count {
            let music = args.firstIndex(of: "--music").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil } ?? "auto"
            Task { @MainActor in AppState.shared.editWhenReady(URL(fileURLWithPath: args[i + 1]), music: music) }
        }
        if let i = args.firstIndex(of: "--text"), i + 1 < args.count {  // an edit shown by its text
            Task { @MainActor in AppState.shared.textProject = URL(fileURLWithPath: args[i + 1]) }
        }

    }

    /// A folder dropped on the Dock icon, or opened with Roughcut from the Finder.
    func application(_ application: NSApplication, open urls: [URL]) {
        if let url = urls.first { Task { @MainActor in AppState.shared.open(url) } }
    }

    /// Closing the window does not stop an edit: the menu bar icon keeps following it.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// Quitting during an edit asks first: quitting stops it.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        MainActor.assumeIsolated {
            guard let job = AppState.shared.job, job.state == .running else { return .terminateNow }
            let alert = NSAlert()
            alert.messageText = L10n.t("quit.title")
            alert.informativeText = L10n.t("quit.text", job.footage.name)
            alert.addButton(withTitle: L10n.t("quit.continue"))
            alert.addButton(withTitle: L10n.t("quit.stop"))
            guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
            job.cancel()
            return .terminateNow
        }
    }

    // notifications show even when Roughcut is in front, and a click brings the window back
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.identifier?.rawValue.contains("main") ?? false }?.makeKeyAndOrderFront(nil)
        completionHandler()
    }
}
