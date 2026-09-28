import AppKit
import Foundation

/// "Report a problem": the versions, what the edit said, and the end of its log, without the user's name or home
/// folder; copied to the clipboard and opened as a new GitHub issue, which the user reads before sending.
enum Report {
    static let issues = "https://github.com/Bouliw/fcpxml-roughcut/issues/new"

    /// The report as text. `message`: what the user was told; `detail`: the technical reason; `log`: montage.log.
    static func text(message: String?, detail: String?, log: URL?) -> String {
        let info = ProcessInfo.processInfo
        let os = info.operatingSystemVersion
        var lines = ["Roughcut \(Updates.current), engine \(Runtime.version), macOS \(os.majorVersion).\(os.minorVersion).\(os.patchVersion), \(L10n.language)"]
        if let message { lines += ["", message] }
        if let detail, !detail.isEmpty { lines += ["", "Detail: \(detail)"] }
        if let log, let content = try? String(contentsOf: log, encoding: .utf8) {
            lines += ["", "End of \(log.lastPathComponent):", "```"] + content.split(separator: "\n", omittingEmptySubsequences: false).suffix(40).map(String.init) + ["```"]
        }
        return anonymized(lines.joined(separator: "\n"))
    }

    /// Without the home folder's path and the user's names.
    static func anonymized(_ text: String) -> String {
        var t = text.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        for name in [NSUserName()] + NSFullUserName().split(separator: " ").map(String.init) where name.count > 2 {
            t = t.replacingOccurrences(of: name, with: "<user>", options: .caseInsensitive)
        }
        return t
    }

    /// The new-issue page, filled in (the text cut to what a link can carry: the whole of it is on the clipboard).
    static func url(title: String, body: String) -> URL {
        let note = L10n.t("report.note")
        var shown = note + "\n\n" + body
        if shown.count > 5_000 { shown = String(shown.prefix(5_000)) + "\n…" }
        var c = URLComponents(string: issues)!
        c.queryItems = [URLQueryItem(name: "title", value: title), URLQueryItem(name: "body", value: shown)]
        return c.url!
    }

    @MainActor static func open(message: String?, detail: String?, log: URL?) {
        let body = text(message: message, detail: detail, log: log)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(body, forType: .string)
        NSWorkspace.shared.open(url(title: message.map { L10n.t("report.title.failed", String($0.prefix(80))) } ?? L10n.t("report.title"), body: body))
    }
}
