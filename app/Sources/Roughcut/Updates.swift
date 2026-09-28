import Foundation

/// Whether a newer Roughcut is published: GitHub's list of releases, read once a day. Only releases that carry a
/// Roughcut disk image count (a release may carry only the engine runtime).
@MainActor
final class Updates: ObservableObject {
    struct Release: Equatable { let version: String; let page: URL }

    @Published var available: Release?
    static let list = URL(string: ProcessInfo.processInfo.environment["ROUGHCUT_RELEASES_URL"]
                          ?? "https://api.github.com/repos/Bouliw/fcpxml-roughcut/releases?per_page=30")!
    nonisolated static var current: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0" }

    /// Looks again when the last look is a day old (a newer version found earlier is shown at once).
    func check() {
        if let v = Prefs.store.string(forKey: "update.version"), let p = Prefs.store.string(forKey: "update.page"),
           let page = URL(string: p), Updates.newer(v, than: Updates.current) {
            available = Release(version: v, page: page)
        }
        let last = Prefs.store.object(forKey: "update.checked") as? Date ?? .distantPast
        guard Date().timeIntervalSince(last) > 86_400 else { return }
        var request = URLRequest(url: Updates.list)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15
        Task {
            guard let (data, response) = try? await URLSession.shared.data(for: request),
                  (response as? HTTPURLResponse)?.statusCode == 200 else { return }  // offline: next launch
            Prefs.store.set(Date(), forKey: "update.checked")
            if let r = Updates.newest(in: data, than: Updates.current) {
                Prefs.store.set(r.version, forKey: "update.version")
                Prefs.store.set(r.page.absoluteString, forKey: "update.page")
                available = r
            } else {
                available = nil
            }
        }
    }

    /// The newest published Roughcut (not a draft, not a pre-release, with a disk image) newer than `current`.
    nonisolated static func newest(in json: Data, than current: String) -> Release? {
        guard let list = try? JSONSerialization.jsonObject(with: json) as? [[String: Any]] else { return nil }
        var best: Release?
        for r in list {
            guard r["draft"] as? Bool != true, r["prerelease"] as? Bool != true,
                  let tag = r["tag_name"] as? String, let page = (r["html_url"] as? String).flatMap(URL.init(string:)),
                  let assets = r["assets"] as? [[String: Any]],
                  assets.contains(where: { ($0["name"] as? String).map { $0.hasPrefix("Roughcut-") && $0.hasSuffix(".dmg") } ?? false })
            else { continue }
            let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            if newer(version, than: best?.version ?? current) { best = Release(version: version, page: page) }
        }
        return best
    }

    nonisolated static func newer(_ a: String, than b: String) -> Bool {
        a.compare(b, options: .numeric) == .orderedDescending
    }
}
