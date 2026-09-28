import AppKit
import AVFoundation
import Foundation

/// The four steps the engine reports, in order.
enum Step: String, CaseIterable {
    case transcribe, analyze, choose, build
    var title: String { L10n.t("run.\(rawValue)") }
}

/// A folder of footage, as dropped: its clips and their total length.
struct Footage {
    let folder: URL
    let clips: [URL]
    let seconds: Double
    var name: String { folder.lastPathComponent }

    static let extensions: Set<String> = ["mp4", "mov", "m4v", "mts", "mxf"]

    static func read(_ folder: URL) async -> Footage {
        let files = ((try? Paths.fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [])
            .filter { extensions.contains($0.pathExtension.lowercased()) && !$0.lastPathComponent.hasPrefix(".") }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        var total = 0.0
        for f in files {
            if let d = try? await AVURLAsset(url: f).load(.duration), d.isNumeric { total += d.seconds }
        }
        return Footage(folder: folder, clips: files, seconds: total)
    }
}

/// What a finished edit gives.
struct EditResult {
    let fcpxml: URL
    let project: URL
    let length: Double
    let shorts: [Double]  // the length of each Short
    let library: String?
    let noMusic: String?
    let note: String?

    /// " · Short 0:45", " · 3 Shorts", or nothing.
    var shortsText: String {
        switch shorts.count {
        case 0: return ""
        case 1: return L10n.t("done.short", clock(shorts[0]))
        default: return L10n.t("done.shorts", shorts.count)
        }
    }
}

/// Expected length of each step, from the length of the footage; measured on each edit and remembered, so the time
/// left gets closer to what this Mac really does.
struct Estimator {
    static let defaults: [Step: (fixed: Double, perSecond: Double)] = [
        // measured on an M4 Max (large-v3-turbo, 4K footage; the analysis runs during the transcription, this is what is left of it)
        .transcribe: (5, 0.065), .analyze: (3, 0.08), .choose: (45, 0.01), .build: (6, 0.004),
    ]

    static func factor(_ step: Step) -> Double {
        let f = Prefs.store.double(forKey: "speed.\(step.rawValue)")
        return f > 0 ? f : 1
    }

    /// Seconds the step should take. `basis`: seconds of footage it works on (to transcribe, or all of it).
    static func expected(_ step: Step, basis: Double, model: WhisperModel) -> Double {
        let d = defaults[step]!
        var perSecond = d.perSecond
        if step == .transcribe { perSecond *= model == .large ? 2 : 1 }
        if step == .transcribe && basis == 0 { return 1 }
        return (d.fixed + perSecond * basis) * factor(step)
    }

    /// After a step: nudge its factor towards what it really took.
    static func learn(_ step: Step, took: Double, expected: Double) {
        guard expected > 2, took > 0 else { return }
        let ratio = min(max(took / (expected / factor(step)), 0.1), 10)
        Prefs.store.set(0.5 * factor(step) + 0.5 * ratio, forKey: "speed.\(step.rawValue)")
    }
}

/// A script of the engine, run the way the Quick Action and the scripts run it: our tools, the Mac's, nothing else.
enum EngineProcess {
    @MainActor static func make(_ script: String, _ args: [String], apiKey: String?, model: WhisperModel = WhisperModel.chosen) -> Process {
        let p = Process()
        if ProcessInfo.processInfo.environment["ROUGHCUT_PYTHON"] != nil {  // development: a checkout and a Python
            p.executableURL = Runtime.python
            p.arguments = [Paths.engine.appendingPathComponent("scripts/\(script)").path] + args
        } else {  // the launcher the Quick Action and the scripts use too: one engine, set up in one place
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [Paths.engine.appendingPathComponent("roughcut").path, script] + args
        }
        try? Paths.fm.createDirectory(at: Paths.projects, withIntermediateDirectories: true)
        p.currentDirectoryURL = Paths.projects
        var env = ProcessInfo.processInfo.environment
        let home = Paths.fm.homeDirectoryForCurrentUser.path
        // our tools, LM Studio's lms and the system's, nothing else: the edit never depends on what else is installed
        env["PATH"] = [Runtime.bin.path, Runtime.python.deletingLastPathComponent().path, "\(home)/.lmstudio/bin",
                       "/usr/bin", "/bin", "/usr/sbin", "/sbin"].joined(separator: ":")
        env["HOME"] = home
        env["ROUGHCUT_SUPPORT"] = Paths.support.path
        env["ROUGHCUT_PROJECTS"] = Paths.projects.path
        env["USER"] = env["USER"] ?? NSUserName()  // Claude Code finds its sign-in by the user's name
        env["LOGNAME"] = env["LOGNAME"] ?? NSUserName()
        env["ROUGHCUT_LANG"] = L10n.language  // the engine's messages in the app's language
        if let claude = BrainScan.claudePath { env["ROUGHCUT_CLAUDE"] = claude }  // wherever its installer put it
        env["WHISPER_MODEL_DIR"] = (model.location ?? Paths.models.appendingPathComponent(model.file)).deletingLastPathComponent().path
        env["WHISPER_MODEL"] = model.file
        env["ROUGHCUT_NO_NOTIFY"] = "1"
        env["PYTHONPYCACHEPREFIX"] = Paths.pycache.path
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env.removeValue(forKey: "PYTHONHOME")
        env.removeValue(forKey: "PYTHONPATH")
        if let apiKey { env["ROUGHCUT_API_KEY"] = apiKey }
        p.environment = env
        return p
    }

    /// A script run to its end: its exit code and all it printed.
    @MainActor static func output(_ script: String, _ args: [String], apiKey: String? = nil) async -> (Int32, String) {
        let p = make(script, args, apiKey: apiKey)
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        let collected = Collected()
        out.fileHandleForReading.readabilityHandler = { h in
            let data = h.availableData
            if data.isEmpty { h.readabilityHandler = nil } else { collected.append(data) }
        }
        return await withCheckedContinuation { done in
            p.terminationHandler = { proc in
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { done.resume(returning: (proc.terminationStatus, collected.text)) }
            }
            do { try p.run() } catch { done.resume(returning: (-1, error.localizedDescription)) }
        }
    }
}

/// Bytes read from a pipe, from its own thread.
final class Collected: @unchecked Sendable {
    private var data = Data()
    private let lock = NSLock()
    func append(_ d: Data) { lock.lock(); data.append(d); lock.unlock() }
    var text: String { lock.lock(); defer { lock.unlock() }; return String(decoding: data, as: UTF8.self) }
}

/// The captions of an edit made again after a word was fixed in its captions.txt: the animated captions, in place
/// (Final Cut Pro shows them where they are), and the SRT files.
@MainActor
final class CaptionsJob: ObservableObject {
    enum State: Equatable { case idle, running, done(String), failed(String) }
    @Published var state = State.idle
    var onEnd: ((String) -> Void)?

    func run(project: URL, apiKey: String?) {
        guard state != .running else { return }
        state = .running
        let p = EngineProcess.make("subtitles.py", [project.path, "--regenerate"], apiKey: apiKey)
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        let collected = Collected()
        out.fileHandleForReading.readabilityHandler = { h in  // read as it comes: a full pipe would stop the engine
            let data = h.availableData
            if data.isEmpty { h.readabilityHandler = nil } else { collected.append(data) }
        }
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(200))  // the last lines
                let text = collected.text
                guard let self else { return }
                if code == 0 {
                    let kept = text.components(separatedBy: "\n").first { $0.hasPrefix("corrections kept: ") }
                        .flatMap { Int($0.dropFirst("corrections kept: ".count)) } ?? 0
                    self.state = .done(L10n.t("regen.done", kept))
                } else {
                    let last = text.split(separator: "\n").last.map(String.init) ?? "?"
                    self.state = .failed(L10n.t("regen.failed", last))
                }
                switch self.state {
                case .done(let m), .failed(let m): self.onEnd?(m)
                default: break
                }
            }
        }
        do { try p.run() } catch { state = .failed(L10n.t("regen.failed", error.localizedDescription)) }
    }
}

/// One edit: the engine run as a child process, its progress lines read as they come.
@MainActor
final class EditJob: ObservableObject {
    enum State: Equatable { case running, done, failed(String), cancelled }

    let footage: Footage
    @Published var state = State.running
    @Published var step: Step?
    @Published var finished: Set<Step> = []
    @Published var progress = 0.0
    @Published var remaining: Double?
    @Published var result: EditResult?
    @Published var cancelling = false
    var log: URL?
    var detail: String?  // the technical reason of a failure, for a report

    private var process: Process?
    private var buffer = Data()
    private var toTranscribe = 0.0
    private var transcribed = 0.0
    private var stepStart = Date()
    private var expected: [Step: Double] = [:]
    private var timer: Timer?
    private let model = WhisperModel.chosen
    var onEnd: ((EditJob) -> Void)?

    init(footage: Footage) {
        self.footage = footage
        toTranscribe = footage.seconds
        for s in Step.allCases { expected[s] = Estimator.expected(s, basis: footage.seconds, model: model) }
    }

    func start(music: String, apiKey: String?, decisions: URL? = nil) {
        let edit = [footage.folder.path, "--work-dir", Paths.projects.path, "--no-dialogs", "--no-open", "--progress", "--music", music]
            + (decisions.map { ["--decisions", $0.path] } ?? [])
        let p = EngineProcess.make("auto_edit.py", edit, apiKey: apiKey, model: model)
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            if data.isEmpty { h.readabilityHandler = nil }  // the end: without this the handler fires again and again
            Task { @MainActor in self?.read(data) }
        }
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in self?.ended(code) }
        }
        do {
            try p.run()
        } catch {
            state = .failed(L10n.t("failed.unknown"))
            return
        }
        process = p
        stepStart = Date()
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)  // it keeps ticking while the menu bar menu is open
        self.timer = timer
    }

    /// Stops the engine and everything it started (it leads its own process group).
    func cancel() {
        guard let p = process, p.isRunning else { return }
        cancelling = true
        if kill(-p.processIdentifier, SIGTERM) != 0 { p.terminate() }  // not yet leading its group: the process alone
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
            if p.isRunning { kill(-p.processIdentifier, SIGKILL) }
        }
    }

    private func read(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = String(decoding: buffer[buffer.startIndex..<nl], as: UTF8.self)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard line.hasPrefix("@"), let space = line.firstIndex(of: " ") else { continue }
            let kind = String(line[line.index(after: line.startIndex)..<space])
            let json = (try? JSONSerialization.jsonObject(with: Data(line[line.index(after: space)...].utf8))) as? [String: Any] ?? [:]
            event(kind, json)
        }
    }

    private func event(_ kind: String, _ e: [String: Any]) {
        switch kind {
        case "info":
            toTranscribe = e["to_transcribe"] as? Double ?? footage.seconds
            let total = e["duration"] as? Double ?? footage.seconds
            if let project = e["project"] as? String { log = URL(fileURLWithPath: project).appendingPathComponent("montage.log") }
            expected[.transcribe] = Estimator.expected(.transcribe, basis: toTranscribe, model: model)
            for s in [Step.analyze, .choose, .build] { expected[s] = Estimator.expected(s, basis: total, model: model) }
        case "step":
            guard let name = e["name"] as? String, let s = Step(rawValue: name) else { return }
            if e["state"] as? String == "start" {
                step = s
                stepStart = Date()
            } else {
                Estimator.learn(s, took: Date().timeIntervalSince(stepStart), expected: expected[s] ?? 0)
                finished.insert(s)
            }
        case "transcribed":
            transcribed = e["seconds"] as? Double ?? transcribed
        case "done":
            result = EditResult(fcpxml: URL(fileURLWithPath: e["fcpxml"] as? String ?? ""),
                                project: URL(fileURLWithPath: e["project"] as? String ?? ""),
                                length: e["length"] as? Double ?? 0, shorts: e["shorts"] as? [Double] ?? [],
                                library: e["library"] as? String, noMusic: e["no_music"] as? String, note: e["note"] as? String)
        case "failed":
            state = .failed(e["why"] as? String ?? "?")
            detail = e["detail"] as? String
            if let l = e["log"] as? String { log = URL(fileURLWithPath: l) }
        default:
            break
        }
        tick()
    }

    /// Progress and time left: finished steps count in full, the current one by its expected length (or, for the
    /// transcription, by the footage already transcribed).
    private func tick() {
        guard state == .running else { return }
        let total = Step.allCases.reduce(0) { $0 + (expected[$1] ?? 0) }
        var done = 0.0, left = 0.0
        for s in Step.allCases {
            let exp = expected[s] ?? 0
            if finished.contains(s) {
                done += exp
            } else if s == step {
                let elapsed = Date().timeIntervalSince(stepStart)
                let exp = max(exp, elapsed * 1.15 + 5)  // running late: it still takes a while, never "almost done" too soon
                var share = min(elapsed / max(exp, 1), 0.95)
                if s == .transcribe && toTranscribe > 0 { share = max(share * 0.5, min(transcribed / toTranscribe, 0.95)) }
                done += exp * share
                left += max(exp - elapsed, exp * (1 - share), 3)
            } else {
                left += exp
            }
        }
        progress = total > 0 ? min(done / total, 0.99) : 0
        remaining = left
    }

    private func ended(_ code: Int32) {
        timer?.invalidate()
        buffer.append(0x0A)
        read(Data())
        if case .failed = state {
        } else if code == 130 || cancelling {
            state = .cancelled
        } else if code == 0, result != nil {
            progress = 1
            state = .done
        } else {
            state = .failed(L10n.t("failed.unknown"))  // no word from the engine: the log says why
        }
        onEnd?(self)
    }
}
