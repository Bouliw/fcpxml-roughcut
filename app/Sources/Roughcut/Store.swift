import Foundation

/// The app's preferences (ROUGHCUT_DEFAULTS names another set, to try the app without touching one's own).
enum Prefs {
    static let store = ProcessInfo.processInfo.environment["ROUGHCUT_DEFAULTS"].flatMap { UserDefaults(suiteName: $0) } ?? .standard
}

/// Where things live. The engine's scripts ship inside the app; its tools (Python, ffmpeg, whisper-cli) and the
/// transcription model are downloaded once into Application Support; the projects and their roughcut.json go to the
/// projects folder (~/Movies/Roughcut by default).
enum Paths {
    static let fm = FileManager.default
    static let support: URL = {
        let url = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Roughcut")
        try? fm.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }()
    static var runtime: URL { support.appendingPathComponent("engine-\(Runtime.version)") }
    static var models: URL { support.appendingPathComponent("models") }
    static var pycache: URL { support.appendingPathComponent("pycache") }
    /// The engine's scripts: in the app, or a checkout of the repository for development (ROUGHCUT_ENGINE=repo).
    static var engine: URL {
        if let dev = ProcessInfo.processInfo.environment["ROUGHCUT_ENGINE"] { return URL(fileURLWithPath: dev) }
        return Bundle.main.resourceURL!.appendingPathComponent("engine")
    }
    /// Models the transcription can use without a download: ours, then whisper.cpp's usual cache.
    static var modelFolders: [URL] { [models, fm.homeDirectoryForCurrentUser.appendingPathComponent(".cache/whisper-cpp")] }

    /// Bytes free on the disk that holds `url`.
    static func freeSpace(_ url: URL) -> Int64 {
        (try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?.volumeAvailableCapacityForImportantUsage ?? .max
    }

    static var projects: URL {
        get {
            if let p = Prefs.store.string(forKey: "projectsFolder") { return URL(fileURLWithPath: p) }
            return fm.urls(for: .moviesDirectory, in: .userDomainMask)[0].appendingPathComponent("Roughcut")
        }
        set { Prefs.store.set(newValue.path, forKey: "projectsFolder") }
    }
}

/// What the app downloads once: the engine's tools, built by app/runtime/build_runtime.sh and published with the
/// release, and a Whisper model.
enum Runtime {
    /// Written into Info.plist by build_app.sh, from app/runtime/versions.sh.
    static var version: Int { Int(Bundle.main.object(forInfoDictionaryKey: "RoughcutEngineVersion") as? String ?? "") ?? 1 }
    /// Published once per runtime version, with a release of the app (Info.plist, from app/runtime/versions.sh).
    static var url: URL {
        URL(string: Bundle.main.object(forInfoDictionaryKey: "RoughcutEngineURL") as? String ?? "")
            ?? URL(string: "https://github.com/Bouliw/fcpxml-roughcut/releases/download/v0.1.0/Roughcut-engine-1-arm64.tar.gz")!
    }
    /// Its SHA-256, written into Info.plist by build_app.sh.
    static var sha256: String { Bundle.main.object(forInfoDictionaryKey: "RoughcutEngineSHA256") as? String ?? "" }
    static var python: URL {
        if let dev = ProcessInfo.processInfo.environment["ROUGHCUT_PYTHON"] { return URL(fileURLWithPath: dev) }
        return Paths.runtime.appendingPathComponent("python/bin/python3")
    }
    static var bin: URL { Paths.runtime.appendingPathComponent("bin") }
    static var installed: Bool {
        ProcessInfo.processInfo.environment["ROUGHCUT_PYTHON"] != nil
            || Paths.fm.isExecutableFile(atPath: Paths.runtime.appendingPathComponent("bin/whisper-cli").path)
    }
}

struct WhisperModel: Identifiable, Hashable {
    let file: String
    let label: String
    let bytes: Int64
    let sha256: String  // as published with the model on Hugging Face
    var id: String { file }
    var url: URL { URL(string: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/\(file)")! }

    static let turbo = WhisperModel(file: "ggml-large-v3-turbo.bin", label: "large-v3-turbo", bytes: 1_624_555_275,
                                    sha256: "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69")
    static let large = WhisperModel(file: "ggml-large-v3.bin", label: "large-v3", bytes: 3_095_033_483,
                                    sha256: "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2")
    static let all = [turbo, large]

    /// The model on disk, wherever it is.
    var location: URL? {
        for folder in Paths.modelFolders {
            let url = folder.appendingPathComponent(file)
            if let size = (try? Paths.fm.attributesOfItem(atPath: url.path))?[.size] as? NSNumber, size.int64Value > 100_000_000 {
                return url
            }
        }
        return nil
    }

    /// The model to use: the one chosen in the settings, else turbo: three times faster, as accurate in French, and it
    /// writes an English interview in English where large-v3 translated it (or dropped it in a clip mixing both).
    static var chosen: WhisperModel {
        get {
            if let f = Prefs.store.string(forKey: "whisperModel"), let m = all.first(where: { $0.file == f }) { return m }
            return turbo
        }
        set { Prefs.store.set(newValue.file, forKey: "whisperModel") }
    }
}

/// The engine's settings file, roughcut.json in the projects folder: the app reads and writes only the keys it
/// shows, and keeps everything else the user put there.
struct EngineSettings {
    var data: [String: Any] = [:]
    var url: URL { Paths.projects.appendingPathComponent("roughcut.json") }

    static func load() -> EngineSettings {
        var s = EngineSettings()
        if let d = try? Data(contentsOf: s.url), let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            s.data = obj
        }
        return s
    }

    func get<T>(_ section: String, _ key: String) -> T? { (data[section] as? [String: Any])?[key] as? T }

    mutating func set(_ section: String, _ key: String, _ value: Any?) {
        var sec = data[section] as? [String: Any] ?? [:]
        sec[key] = value
        data[section] = sec
    }

    func save() {
        try? Paths.fm.createDirectory(at: Paths.projects, withIntermediateDirectories: true)
        if let d = try? JSONSerialization.data(withJSONObject: data, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]) {
            try? d.write(to: url, options: .atomic)
        }
    }
}
