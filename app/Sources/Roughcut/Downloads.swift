import CryptoKit
import Foundation

/// The two downloads of the first launch, with their progress: the engine's tools (an archive checked against its
/// SHA-256, unpacked into Application Support) and the Whisper model. Files an app downloads itself carry no
/// quarantine flag, so macOS never blocks the tools inside.
@MainActor
final class Downloads: NSObject, ObservableObject {
    enum State: Equatable { case waiting, present, running(Double), done, failed(String) }

    @Published var engine = State.waiting
    @Published var model = State.waiting
    private var fetches: [Fetch] = []

    var ready: Bool {
        [engine, model].allSatisfy { $0 == .present || $0 == .done }
    }

    /// Why the tools are not there, when a download failed.
    var failure: String? {
        for s in [engine, model] { if case .failed(let why) = s { return why } }
        return nil
    }

    override init() {
        super.init()
        for f in (try? Paths.fm.contentsOfDirectory(atPath: Paths.support.path)) ?? [] where f.hasPrefix("download-") {
            try? Paths.fm.removeItem(at: Paths.support.appendingPathComponent(f))  // left by a download cut short
        }
        engine = Runtime.installed ? .present : .waiting
        model = WhisperModel.chosen.location != nil ? .present : .waiting
    }

    /// Starts whatever is missing, one download after the other, so a speed limit holds for the whole.
    func start() {
        if engine == .waiting || isFailed(engine) {
            fetchEngine()
        } else if model == .waiting || isFailed(model) {
            fetchModel()
        }
    }

    func refresh() {
        if Runtime.installed { engine = .present }
        model = WhisperModel.chosen.location != nil ? .present : (model == .present ? .waiting : model)
    }

    private func isFailed(_ s: State) -> Bool { if case .failed = s { return true }; return false }

    /// The reason a download failed, in words anyone understands.
    nonisolated static func explain(_ error: Error?) -> String {
        let e = error as NSError?
        if e?.domain == NSURLErrorDomain, [NSURLErrorNotConnectedToInternet, NSURLErrorNetworkConnectionLost, NSURLErrorCannotFindHost,
                                            NSURLErrorCannotConnectToHost, NSURLErrorTimedOut, NSURLErrorDNSLookupFailed,
                                            NSURLErrorDataNotAllowed, NSURLErrorInternationalRoamingOff].contains(e!.code) {
            return L10n.t("download.offline")
        }
        if e?.domain == NSPOSIXErrorDomain, [1, 50, 51, 60, 61, 65].contains(e!.code) {  // blocked, network down or unreachable
            return L10n.t("download.offline")
        }
        if (e?.domain == NSCocoaErrorDomain && e?.code == NSFileWriteOutOfSpaceError) || (e?.domain == NSPOSIXErrorDomain && e?.code == 28) {
            return L10n.t("download.disk", ByteCountFormatter.string(fromByteCount: 2_000_000_000, countStyle: .file))
        }
        return L10n.t("download.other")
    }

    /// Enough room for `bytes` (and a margin), or the reason to stop before downloading.
    private func room(_ bytes: Int64) -> String? {
        let needed = bytes + 200_000_000
        return Paths.freeSpace(Paths.support) < needed ? L10n.t("download.disk", ByteCountFormatter.string(fromByteCount: needed, countStyle: .file)) : nil
    }

    private func fetchEngine() {
        let model = WhisperModel.chosen.location == nil ? WhisperModel.chosen.bytes : 0  // said once, for both downloads
        if let full = room(300_000_000 + model) { engine = .failed(full); return }
        engine = .running(0)
        let local = ProcessInfo.processInfo.environment["ROUGHCUT_ENGINE_ARCHIVE"]  // a build not published yet
        if let local {
            install(archive: URL(fileURLWithPath: local), keep: true)
            return
        }
        download(Runtime.url, progress: { [weak self] p in self?.engine = .running(p * 0.9) }) { [weak self] file, error in
            guard let self else { return }
            guard let file else { self.engine = .failed(Downloads.explain(error)); return }
            self.install(archive: file, keep: false)
        }
    }

    /// Checks and unpacks the engine archive next to its final place, then moves it there in one step.
    private func install(archive: URL, keep: Bool) {
        let expected = Runtime.sha256
        Task.detached {
            let result: String? = {
                if !expected.isEmpty, (try? Downloads.sha256(archive)) != expected {
                    if !keep { try? Paths.fm.removeItem(at: archive) }
                    return L10n.t("download.damaged")
                }
                let temp = Paths.support.appendingPathComponent("engine-\(Runtime.version).partial")
                try? Paths.fm.removeItem(at: temp)
                try? Paths.fm.createDirectory(at: temp, withIntermediateDirectories: true)
                let tar = Process()
                tar.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
                tar.arguments = ["-xzf", archive.path, "-C", temp.path]
                do { try tar.run() } catch { return L10n.t("download.other") }
                tar.waitUntilExit()
                guard tar.terminationStatus == 0 else { try? Paths.fm.removeItem(at: temp); return Downloads.explain(nil) }
                try? Paths.fm.removeItem(at: Paths.runtime)
                do { try Paths.fm.moveItem(at: temp, to: Paths.runtime) } catch { return Downloads.explain(error) }
                if !keep { try? Paths.fm.removeItem(at: archive) }
                return nil
            }()
            await MainActor.run {
                self.engine = result == nil ? .done : .failed(result!)
                if result == nil { self.start() }  // then the model
            }
        }
    }

    private func fetchModel() {
        let m = WhisperModel.chosen
        if let full = room(m.bytes) { model = .failed(full); return }
        model = .running(0)
        download(m.url, progress: { [weak self] p in self?.model = .running(p * 0.98) }) { [weak self] file, error in
            guard let self else { return }
            guard let file else { self.model = .failed(Downloads.explain(error)); return }
            Task.detached {  // checked against the published checksum before it is used
                let result: String? = {
                    guard (try? Downloads.sha256(file)) == m.sha256 else {
                        try? Paths.fm.removeItem(at: file)
                        return L10n.t("download.damaged")
                    }
                    do {
                        try Paths.fm.createDirectory(at: Paths.models, withIntermediateDirectories: true)
                        let dest = Paths.models.appendingPathComponent(m.file)
                        try? Paths.fm.removeItem(at: dest)
                        try Paths.fm.moveItem(at: file, to: dest)
                        return nil
                    } catch {
                        return Downloads.explain(error)
                    }
                }()
                await MainActor.run { self.model = result == nil ? .done : .failed(result!) }
            }
        }
    }

    private func download(_ url: URL, progress: @escaping (Double) -> Void, done: @escaping (URL?, Error?) -> Void) {
        let file = Paths.support.appendingPathComponent("download-\(UUID().uuidString)")
        let fetch = Fetch(url: url, to: file, limit: Downloads.limit, progress: { p in Task { @MainActor in progress(p) } },
                          done: { url, error in Task { @MainActor in done(url, error) } })
        fetches.append(fetch)
        fetch.start()
    }

    /// Bytes per second at most, 0 for no limit: ROUGHCUT_DOWNLOAD_LIMIT, else the setting (Advanced).
    nonisolated static var limit: Double {
        if let env = ProcessInfo.processInfo.environment["ROUGHCUT_DOWNLOAD_LIMIT"], let v = Double(env) { return v }
        return Prefs.store.double(forKey: "downloadLimit")
    }

    nonisolated static func sha256(_ url: URL) throws -> String {
        let h = try FileHandle(forReadingFrom: url)
        defer { try? h.close() }
        var hasher = SHA256()
        while let chunk = try h.read(upToCount: 8 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

/// One download to a file, at most `limit` bytes a second: whenever it runs ahead, it stops reading for a moment and
/// the transfer slows down with it, so the rest of the connection stays usable (a video keeps playing).
final class Fetch: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let url: URL, file: URL, limit: Double
    let progress: (Double) -> Void, done: (URL?, Error?) -> Void
    private let queue = DispatchQueue(label: "roughcut.fetch")
    private var session: URLSession!
    private var task: URLSessionDataTask?
    private var handle: FileHandle?
    private var received: Int64 = 0, expected: Int64 = 0, status = 0
    private var began = Date(), lastReport = Date.distantPast

    init(url: URL, to file: URL, limit: Double, progress: @escaping (Double) -> Void, done: @escaping (URL?, Error?) -> Void) {
        self.url = url; self.file = file; self.limit = limit; self.progress = progress; self.done = done
        super.init()
        let ops = OperationQueue()
        ops.underlyingQueue = queue
        ops.maxConcurrentOperationCount = 1
        session = URLSession(configuration: .default, delegate: self, delegateQueue: ops)
    }

    func start() {
        began = Date()
        task = session.dataTask(with: url)
        task?.resume()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        status = (response as? HTTPURLResponse)?.statusCode ?? 0
        expected = response.expectedContentLength
        guard status == 200, FileManager.default.createFile(atPath: file.path, contents: nil) else { completionHandler(.cancel); return }
        handle = try? FileHandle(forWritingTo: file)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        handle?.write(data)
        received += Int64(data.count)
        if expected > 0, Date().timeIntervalSince(lastReport) > 0.25 {
            lastReport = Date()
            progress(Double(received) / Double(expected))
        }
        guard limit > 0 else { return }
        // ahead of the limit: wait here, on this download's own queue; nothing more is read meanwhile, so the
        // connection itself slows down (pausing the task instead let data pile up and arrive faster than allowed)
        let ahead = Double(received) / limit - Date().timeIntervalSince(began)
        if ahead > 0.01 { Thread.sleep(forTimeInterval: ahead) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        try? handle?.close()
        session.finishTasksAndInvalidate()
        if error == nil, status == 200, expected <= 0 || received == expected {
            done(file, nil)
        } else {
            try? FileManager.default.removeItem(at: file)
            done(nil, error ?? NSError(domain: "HTTP", code: status, userInfo: [NSLocalizedDescriptionKey: "HTTP \(status)"]))
        }
    }
}
