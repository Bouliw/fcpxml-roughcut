import AppKit
import Foundation
import UserNotifications

/// Everything the windows and the menu bar show: the folder dropped, the edit running, the first-launch setup and
/// the few settings a user sees.
@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    @Published var footage: Footage?
    @Published var job: EditJob?
    @Published var setupDone = Prefs.store.bool(forKey: "setupDone")
    @Published var scan = BrainScan()
    @Published var scanning = false
    @Published var settings = EngineSettings.load()
    @Published var textProject: URL?  // the edit shown by its text
    let downloads = Downloads()
    let updates = Updates()
    let captions = CaptionsJob()

    private init() {
        captions.onEnd = { [weak self] message in self?.notify(message) }
        downloads.start()
        updates.check()
        if settings.get("ui", "language") as String? == nil {  // the engine speaks the Mac's language
            settings.set("ui", "language", L10n.language)
            settings.save()
        }
    }

    // MARK: the few settings shown

    var brain: BrainKind {
        get { BrainKind(rawValue: settings.get("brain", "engine") ?? "") ?? .claude }
        set { update("brain", "engine", newValue.rawValue) }
    }
    var musicFolder: URL? {
        get { (settings.get("auto", "music_folder") as String?).flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) } }
        set { update("auto", "music_folder", newValue?.path ?? "") }
    }
    var duckDB: Double {
        get { (settings.get("music", "duck_db") as Double?) ?? -28 }
        set { update("music", "duck_db", newValue.rounded()) }
    }
    var zooms: Bool {
        get { settings.get("auto", "zooms") ?? true }
        set { update("auto", "zooms", newValue) }
    }
    /// The kind of video: "vlog" (a lighter cut, and IRL moments between what is said) or "discussion".
    var style: String {
        get { settings.get("auto", "style") ?? "vlog" }
        set { update("auto", "style", newValue) }
    }
    /// How hard the brain cuts: "" (from the style), "light", "normal" or "tight".
    var cut: String {
        get { settings.get("auto", "cut") ?? "" }
        set { update("auto", "cut", newValue) }
    }
    /// Final Cut Pro's own Voice Isolation on the speech (audio.voice_isolation): "auto" (the default: only where the
    /// background is loud), "off", "always".
    var voiceIsolation: Bool {
        get {
            let v = (settings.data["audio"] as? [String: Any])?["voice_isolation"]
            if let s = v as? String { return s != "off" }
            if let n = v as? NSNumber { return n.doubleValue > 0 }
            return true
        }
        set { update("audio", "voice_isolation", newValue ? "auto" : "off") }
    }
    /// How the main edit is dressed: "none", "light" (a title and a dissolve where each chapter starts) or "full"
    /// (also the time of day, the lower thirds and animated captions). The subtitles for YouTube are always made.
    var dressing: String {
        get { settings.get("auto", "dressing") ?? "light" }
        set { update("auto", "dressing", newValue) }
    }

    // MARK: the style of the animated captions, for the Shorts (captions) and for the main edit (captions_main)

    static let captionDefaults: [Bool: [String: Any]] = [
        true: ["font": "Arial Black", "font_size": 0.055, "colour": "#FFFFFF", "highlight_colour": "#FFD400", "outline_colour": "#000000",
               "outline": 0.08, "pop": 1.12, "uppercase": false, "words": 3, "karaoke": false, "bottom_margin_vertical": 0.3],
        false: ["font": "Arial Black", "font_size": 0.05, "colour": "#FFFFFF", "highlight_colour": "#FFD400", "outline_colour": "#000000",
                "outline": 0.08, "pop": 1.12, "uppercase": false, "words": 3, "karaoke": false, "bottom_margin": 0.08]]
    static func captionSection(_ vertical: Bool) -> String { vertical ? "captions" : "captions_main" }
    static func heightKey(_ vertical: Bool) -> String { vertical ? "bottom_margin_vertical" : "bottom_margin" }

    func caption<T>(_ vertical: Bool, _ key: String) -> T {
        settings.get(Self.captionSection(vertical), key) ?? Self.captionDefaults[vertical]![key] as! T
    }
    func setCaption(_ vertical: Bool, _ key: String, _ value: Any?) { update(Self.captionSection(vertical), key, value) }
    /// Any value of the captions' style, the engine's default when not set.
    func captionValue(_ vertical: Bool, _ key: String) -> Any? {
        (settings.data[Self.captionSection(vertical)] as? [String: Any])?[key] ?? Self.captionDefaults[vertical]![key]
    }
    func resetCaptions(_ vertical: Bool) {
        for key in Self.captionDefaults[vertical]!.keys { settings.set(Self.captionSection(vertical), key, nil) }
        settings.save()
        objectWillChange.send()
    }

    /// The captions of an edit made again after a fix in its captions.txt.
    func regenerateCaptions(_ project: URL) {
        guard Paths.fm.fileExists(atPath: project.appendingPathComponent("captions.txt").path) else {
            captions.state = .failed(L10n.t("regen.notProject"))
            return
        }
        let key = brain == .api ? Keychain.get(settings.get("brain", "api_provider") ?? "anthropic") : nil
        captions.run(project: project, apiKey: key)
    }

    func chooseProjectForCaptions() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = Paths.projects
        panel.message = L10n.t("regen.choose")
        if panel.runModal() == .OK, let url = panel.url { regenerateCaptions(url) }
    }

    var shorts: Int {
        get { AppState.shortsCount((settings.data["auto"] as? [String: Any])?["short"]) }
        set { update("auto", "short", newValue) }
    }
    /// How many Shorts at most, 0 to 3: auto.short is a number, or true / false as written before several could be made.
    nonisolated static func shortsCount(_ value: Any?) -> Int {
        guard let n = value as? NSNumber else { return 3 }
        if CFGetTypeID(n) == CFBooleanGetTypeID() { return n.boolValue ? 3 : 0 }
        return max(0, min(3, n.intValue))
    }
    var library: String {
        get { settings.get("auto", "library") ?? "" }
        set { update("auto", "library", newValue) }
    }
    /// The languages one can give for the transcription ("auto": detected), as offered in the setup and Settings.
    static let spokenLanguages: [(code: String, name: String)] = [
        ("fr", "Français"), ("en", "English"), ("es", "Español"), ("de", "Deutsch"), ("it", "Italiano"),
        ("pt", "Português"), ("zh", "中文"), ("auto", "")]

    var spokenLanguage: String {
        get { settings.get("auto", "transcription_language") ?? "auto" }
        set { update("auto", "transcription_language", newValue) }
    }

    /// The app's language, and the engine's messages with it.
    func setLanguage(_ code: String?) {
        L10n.choice = code
        update("ui", "language", L10n.language)
    }

    func update(_ section: String, _ key: String, _ value: Any?) {
        settings.set(section, key, value)
        settings.save()
        objectWillChange.send()
    }

    func reloadSettings() { settings = EngineSettings.load() }

    /// Local model server settings for the engine (LM Studio loads the model itself; Ollama needs its name).
    func useLocal(_ server: LocalServer, model: String) {
        update("brain", "local_url", server.url)
        update("brain", "local_model", model)
    }

    func rescan() {
        scanning = true
        Task {
            let s = await BrainScan.run()
            scan = s
            scanning = false
            if !setupDone && settings.get("brain", "engine") as String? == nil {  // first launch: what this Mac has
                if case .signedIn = s.claude {
                } else if let local = s.local, let model = local.models.first {
                    brain = .local
                    useLocal(local, model: model)
                }
            }
        }
    }

    /// Whether the brain chosen can answer on this Mac (an API key is taken on trust).
    var brainAvailable: Bool {
        switch brain {
        case .claude: if case .signedIn = scan.claude { return true }; return false
        case .local: return scan.local.map { !$0.models.isEmpty } ?? false
        case .api: return Keychain.get(settings.get("brain", "api_provider") ?? "anthropic") != nil
        }
    }

    // MARK: music

    var tracks: [String] {
        guard let folder = musicFolder else { return [] }
        let audio: Set<String> = ["mp3", "m4a", "wav", "aif", "aiff", "flac", "aac"]
        return ((try? Paths.fm.contentsOfDirectory(atPath: folder.path)) ?? [])
            .filter { audio.contains(($0 as NSString).pathExtension.lowercased()) }.sorted()
    }

    func chooseMusicFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.prompt = L10n.t("setup.music.choose").replacingOccurrences(of: "…", with: "")
        if panel.runModal() == .OK, let url = panel.url { musicFolder = url }
    }

    // MARK: an edit

    func open(_ url: URL) {
        guard job == nil || job?.state != .running else { return }
        var isDir: ObjCBool = false
        guard Paths.fm.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue else { return }
        job = nil
        Task { footage = await Footage.read(url) }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url { open(url) }
    }

    /// music: "auto" (without a music folder, the engine says there is no music, and why), "none", a track's file
    /// name in the music folder, or a full path.
    func edit(music: String) {
        guard let footage, !footage.clips.isEmpty else { return }
        let arg: String
        switch music {
        case "auto", "none": arg = music
        default: arg = music.hasPrefix("/") ? music : (musicFolder?.appendingPathComponent(music).path ?? "auto")
        }
        let job = EditJob(footage: footage)
        job.onEnd = { [weak self] j in self?.ended(j) }
        self.job = job
        let key = brain == .api ? Keychain.get(settings.get("brain", "api_provider") ?? "anthropic") : nil
        job.start(music: arg, apiKey: key)
    }

    /// Opens the folder and starts the edit as soon as the setup and the downloads are done.
    func editWhenReady(_ url: URL, music: String) {
        open(url)
        Task {
            while !(setupDone && downloads.ready && footage != nil) { try? await Task.sleep(for: .milliseconds(300)) }
            edit(music: music)
        }
    }

    func reset() {
        job = nil
        footage = nil
    }

    /// A new version of an edit from the words chosen in its text (text_edit.py), with the music it had.
    func newVersion(project: URL, footage folder: URL, decisions: URL) {
        guard job == nil || job?.state != .running else { return }
        var music = "none"
        if let d = try? Data(contentsOf: project.appendingPathComponent("edl.json")),
           let edl = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
           let file = (edl["music"] as? [String: Any])?["file"] as? String { music = file }
        Task {
            let footage = await Footage.read(folder)
            self.footage = footage
            let job = EditJob(footage: footage)
            job.onEnd = { [weak self] j in self?.ended(j) }
            self.job = job
            let key = brain == .api ? Keychain.get(settings.get("brain", "api_provider") ?? "anthropic") : nil
            job.start(music: music, apiKey: key, decisions: decisions)
        }
    }

    /// Chooses the project folder of an edit to show by its text; true when one was chosen.
    func chooseTextProject() -> Bool {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = Paths.projects
        panel.message = L10n.t("regen.choose")
        guard panel.runModal() == .OK, let url = panel.url,
              Paths.fm.fileExists(atPath: url.appendingPathComponent("edl.json").path) else { return false }
        textProject = url
        return true
    }

    private func ended(_ job: EditJob) {
        switch job.state {
        case .done:
            guard let r = job.result else { return }
            let detail = clock(r.length) + r.shortsText
            notify(L10n.t("notify.done", job.footage.name, detail))
        case .failed(let why):
            notify(L10n.t("notify.failed", job.footage.name, why))
        default:
            break
        }
    }

    // MARK: Final Cut Pro

    static let fcpIDs = ["com.apple.FinalCut", "com.apple.FinalCutTrial"]
    var finalCut: URL? { Self.fcpIDs.lazy.compactMap { NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) }.first }

    /// The installed Final Cut Pro's version when it is too old for the projects (before 10.6, FCPXML 1.10).
    var finalCutTooOld: String? {
        guard let app = finalCut, let v = Bundle(url: app)?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        else { return nil }
        return v.compare("10.6", options: .numeric) == .orderedAscending ? v : nil
    }

    func openInFinalCut(_ file: URL) {
        guard let app = finalCut else { NSWorkspace.shared.open(file); return }
        NSWorkspace.shared.open([file], withApplicationAt: app, configuration: NSWorkspace.OpenConfiguration())
    }

    // MARK: notifications

    func askNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func notify(_ text: String) {
        let c = UNMutableNotificationContent()
        c.title = "Roughcut"
        c.body = text
        c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }

    func finishSetup() {
        update("brain", "engine", brain.rawValue)  // the choice shown, even when left as it came
        setupDone = true
        Prefs.store.set(true, forKey: "setupDone")
        askNotifications()
    }
}
