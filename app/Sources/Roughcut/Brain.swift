import Foundation
import Security

/// The three ways to make the editing choices (brain.py in the engine), and what this Mac offers for each.
enum BrainKind: String, CaseIterable, Identifiable {
    case claude, local, api
    var id: String { rawValue }
    var title: String { L10n.t("brain.\(rawValue)") }
    var line: String { L10n.t("brain.\(rawValue).line") }
}

struct LocalServer: Equatable {
    let name: String      // "LM Studio" or "Ollama"
    let url: String       // OpenAI-compatible base URL
    let models: [String]
}

/// What was found: Claude Code signed in or not, a local model server, a saved API key.
struct BrainScan: Equatable {
    var claude: ClaudeState = .missing
    var local: LocalServer?
    enum ClaudeState: Equatable { case missing, signedOut, signedIn(String) }

    static func run() async -> BrainScan {
        async let c = claudeState()
        async let l = localServer()
        return BrainScan(claude: await c, local: await l)
    }

    /// The claude command, where its installers put it.
    static var claudePath: String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["\(home)/.local/bin/claude", "\(home)/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static func claudeState() async -> ClaudeState {
        guard let path = claudePath else { return .missing }
        guard let out = await run(path, ["auth", "status", "--json"], timeout: 20),
              let obj = try? JSONSerialization.jsonObject(with: Data(out.utf8)) as? [String: Any] else { return .signedOut }
        guard obj["loggedIn"] as? Bool == true else { return .signedOut }
        let plan = (obj["subscriptionType"] as? String).map { "Claude \($0.capitalized)" } ?? "Claude"
        return .signedIn(plan)
    }

    static func localServer() async -> LocalServer? {
        if let ids = await modelIDs("http://localhost:1234/v1/models", key: "data", id: "id") {
            return LocalServer(name: "LM Studio", url: "http://localhost:1234/v1", models: ids)
        }
        if let ids = await modelIDs("http://localhost:11434/api/tags", key: "models", id: "name") {
            return LocalServer(name: "Ollama", url: "http://localhost:11434/v1", models: ids)
        }
        return nil
    }

    private static func modelIDs(_ url: String, key: String, id: String) async -> [String]? {
        var req = URLRequest(url: URL(string: url)!)
        req.timeoutInterval = 2
        guard let (data, resp) = try? await URLSession.shared.data(for: req), (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = obj[key] as? [[String: Any]] else { return nil }
        return list.compactMap { $0[id] as? String }.filter { !$0.contains("embed") }
    }

    /// Output of a command, or nil (not found, failed or too slow).
    static func run(_ path: String, _ args: [String], timeout: Double) async -> String? {
        await withCheckedContinuation { cont in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = args
            let out = Pipe()
            p.standardOutput = out
            p.standardError = FileHandle.nullDevice
            p.terminationHandler = { proc in
                let data = out.fileHandleForReading.readDataToEndOfFile()
                cont.resume(returning: proc.terminationStatus == 0 ? String(decoding: data, as: UTF8.self) : nil)
            }
            do { try p.run() } catch { cont.resume(returning: nil); return }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { if p.isRunning { p.terminate() } }
        }
    }
}

/// API keys in the macOS keychain, under the name the engine looks for (brain.py: service "fcpxml-roughcut").
enum Keychain {
    static let service = "fcpxml-roughcut"

    static func get(_ account: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func set(_ account: String, _ value: String) -> Bool {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        let data = Data(value.utf8)
        if SecItemUpdate(q as CFDictionary, [kSecValueData as String: data] as CFDictionary) == errSecSuccess { return true }
        var add = q
        add[kSecValueData as String] = data
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }
}
