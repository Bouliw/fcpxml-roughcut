import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The one window: drop a folder, press Edit, follow the steps, open the result in Final Cut Pro.
struct MainView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.openWindow) private var openWindow
    @ObservedObject var updates = AppState.shared.updates
    @Environment(\.openSettings) private var openSettings
    @State private var targeted = false

    var body: some View {
        VStack(spacing: 0) {
            if let r = updates.available, app.setupDone {  // a newer version is out: said once, quietly, with the way to it
                HStack(spacing: 8) {
                    Image(systemName: "arrow.down.circle").accessibilityHidden(true)
                    Text(L10n.t("update.available", r.version))
                    Button(L10n.t("update.download")) { NSWorkspace.shared.open(r.page) }.buttonStyle(.link)
                }
                .font(.callout)
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity)
                .background(Color.accentColor.opacity(0.12))
            }
            content
        }
    }

    @ViewBuilder var content: some View {
        Group {
            if !app.setupDone {
                SetupView()
            } else if let job = app.job {
                JobView(job: job)
            } else if let footage = app.footage {
                ReadyView(footage: footage)
            } else {
                DropView(targeted: targeted)
            }
        }
        .frame(minWidth: 520, idealWidth: 580, maxWidth: .infinity, minHeight: 420, idealHeight: 440, maxHeight: .infinity)
        .onAppear {  // open -a Roughcut --args --settings
            if CommandLine.arguments.contains("--settings") { NSApp.activate(ignoringOtherApps: true); openSettings() }
            if CommandLine.arguments.contains("--text") { openWindow(id: "text") }
        }
        .onDrop(of: [.fileURL], isTargeted: $targeted) { providers in
            guard app.setupDone, app.job?.state != .running, let p = providers.first else { return false }
            _ = p.loadObject(ofClass: URL.self) { url, _ in
                if let url { Task { @MainActor in app.open(url) } }
            }
            return true
        }
    }
}

struct DropView: View {
    @EnvironmentObject var app: AppState
    let targeted: Bool

    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
                    .foregroundStyle(targeted ? Color.accentColor : Color.secondary.opacity(0.5))
                    .background(RoundedRectangle(cornerRadius: 18).fill(targeted ? Color.accentColor.opacity(0.08) : .clear))
                VStack(spacing: 12) {
                    Image(systemName: "film.stack").font(.system(size: 44, weight: .light)).foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                    Text(L10n.t("drop.title")).font(.title3.weight(.medium))
                    Text(L10n.t("drop.or")).foregroundStyle(.secondary)
                    Button(L10n.t("drop.choose")) { app.chooseFolder() }
                }
            }
            .padding(28)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(L10n.t("drop.label"))
            if app.finalCut == nil {
                Label(L10n.t("noFCP"), systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                    .padding(.bottom, 12)
            } else if let old = app.finalCutTooOld {
                Label(L10n.t("oldFCP", old), systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                    .padding(.bottom, 12)
            }
        }
    }
}

struct ReadyView: View {
    @EnvironmentObject var app: AppState
    @ObservedObject var downloads = AppState.shared.downloads
    let footage: Footage
    @State private var music = "auto"

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Image(systemName: "folder.fill").font(.system(size: 40)).foregroundStyle(Color.accentColor).accessibilityHidden(true)
            VStack(spacing: 4) {
                Text(footage.name).font(.title2.weight(.semibold))
                Text(footage.clips.isEmpty ? L10n.t("ready.noClips") : (footage.clips.count == 1 ? L10n.t("ready.clip", clock(footage.seconds)) : L10n.t("ready.clips", footage.clips.count, clock(footage.seconds))))
                    .foregroundStyle(.secondary)
            }
            musicPicker.frame(width: 340)
            VStack(spacing: 8) {
                Button {
                    app.edit(music: music)
                } label: {
                    Text(L10n.t("ready.edit")).font(.headline).frame(width: 180, height: 28)
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(footage.clips.isEmpty || !downloads.ready)
                if let failure = downloads.failure {
                    HStack {
                        Label(failure, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                        Button(L10n.t("setup.download.retry")) { downloads.start() }.controlSize(.small)
                    }
                    .frame(maxWidth: 440)
                } else if !downloads.ready {
                    Text(L10n.t("ready.notReady")).font(.caption).foregroundStyle(.secondary)
                } else if !app.scanning && !app.brainAvailable {
                    Text(L10n.t("ready.noBrain")).font(.caption).foregroundStyle(.orange).multilineTextAlignment(.center)
                        .frame(maxWidth: 380)
                }
                Button(L10n.t("ready.other")) { app.reset() }.buttonStyle(.link)
            }
            Spacer()
        }
        .padding()
        .onAppear { app.rescan() }
    }

    @ViewBuilder var musicPicker: some View {
        if app.musicFolder == nil {
            VStack(spacing: 6) {
                Label(L10n.t("ready.noMusicFolder"), systemImage: "music.note").foregroundStyle(.secondary)
                Button(L10n.t("ready.chooseMusicFolder")) { app.chooseMusicFolder() }.buttonStyle(.link)
            }
        } else {
            Picker(L10n.t("ready.music"), selection: $music) {
                Text(L10n.t("ready.musicAuto")).tag("auto")
                Divider()
                ForEach(app.tracks, id: \.self) { Text(($0 as NSString).deletingPathExtension).tag($0) }
                Divider()
                Text(L10n.t("ready.musicNone")).tag("none")
            }
        }
    }
}

struct JobView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.openWindow) private var openWindow
    @ObservedObject var job: EditJob
    @ObservedObject var captions = AppState.shared.captions

    var body: some View {
        switch job.state {
        case .running: running
        case .done: done
        case .failed(let why): failed(why)
        case .cancelled:
            VStack(spacing: 16) {
                Text(L10n.t("cancelled.title")).font(.title2)
                Button(L10n.t("done.new")) { app.reset() }
            }
        }
    }

    var running: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(job.footage.name).font(.title2.weight(.semibold))
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Step.allCases, id: \.self) { s in
                    HStack(spacing: 10) {
                        Group {
                            if job.finished.contains(s) {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                            } else if job.step == s {
                                ProgressView().controlSize(.small)
                            } else {
                                Image(systemName: "circle").foregroundStyle(.tertiary)
                            }
                        }
                        .frame(width: 18)
                        Text(s.title).foregroundStyle(job.step == s || job.finished.contains(s) ? .primary : .secondary)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(s.title), " + L10n.t(job.finished.contains(s) ? "a11y.done" : job.step == s ? "a11y.running" : "a11y.waiting"))
                }
            }
            ProgressView(value: job.progress).accessibilityLabel(L10n.t("a11y.progress"))
            HStack {
                Text(remainingText).foregroundStyle(.secondary).monospacedDigit()
                Spacer()
                Button(job.cancelling ? L10n.t("run.cancelling") : L10n.t("run.cancel")) { job.cancel() }
                    .disabled(job.cancelling)
                    .keyboardShortcut(.cancelAction)  // Esc
            }
        }
        .padding(40)
    }

    var remainingText: String {
        guard let r = job.remaining else { return "" }
        return r < 20 ? L10n.t("run.almost") : L10n.t("run.remaining", shortDuration(r))
    }

    @ViewBuilder var done: some View {
        if let r = job.result {
            VStack(spacing: 18) {
                Image(systemName: "checkmark.seal.fill").font(.system(size: 46)).foregroundStyle(.green).accessibilityHidden(true)
                Text(L10n.t("done.title", job.footage.name)).font(.title2.weight(.semibold))
                Text(L10n.t("done.detail", clock(r.length)) + r.shortsText
                     + (r.noMusic != nil ? L10n.t("done.noMusic") : ""))
                    .foregroundStyle(.secondary)
                if let note = r.note { Text(note).font(.callout).foregroundStyle(.orange).multilineTextAlignment(.center) }
                if app.finalCut != nil {
                    Button {
                        app.openInFinalCut(r.fcpxml)
                    } label: {
                        Label(L10n.t("done.open"), systemImage: "play.rectangle").font(.headline).frame(width: 240, height: 28)
                    }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    Text(r.library.map { L10n.t("done.library", $0) } ?? L10n.t("done.libraryAny"))
                        .font(.callout).multilineTextAlignment(.center)
                } else {
                    Label(L10n.t("done.noFCP"), systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                        .multilineTextAlignment(.center).frame(maxWidth: 420)
                }
                HStack(spacing: 16) {
                    Button(L10n.t("done.finder")) { NSWorkspace.shared.activateFileViewerSelecting([r.project.appendingPathComponent("publication.txt")]) }
                    Button(L10n.t("done.text")) { app.textProject = r.project; openWindow(id: "text") }
                    Button(L10n.t("done.check")) { NSWorkspace.shared.open(r.project.appendingPathComponent("to-check.txt")) }
                    Button(L10n.t("regen.button")) { app.regenerateCaptions(r.project) }.disabled(captions.state == .running)
                    Button(L10n.t("done.new")) { app.reset() }
                }
                .buttonStyle(.link)
                switch captions.state {
                case .running: ProgressView(L10n.t("regen.running")).controlSize(.small)
                case .done(let m): Text(m).font(.callout).foregroundStyle(.green)
                case .failed(let m): Text(m).font(.callout).foregroundStyle(.orange).multilineTextAlignment(.center).frame(maxWidth: 420)
                case .idle: EmptyView()
                }
                Text(L10n.t("done.publication")).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding(30)
        }
    }

    func failed(_ why: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 40)).foregroundStyle(.orange).accessibilityHidden(true)
            Text(L10n.t("failed.title")).font(.title2.weight(.semibold))
            Text(why).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).lineLimit(6).textSelection(.enabled)
            HStack(spacing: 16) {
                if let log = job.log { Button(L10n.t("failed.log")) { NSWorkspace.shared.open(log) } }
                Button(L10n.t("report.button")) { Report.open(message: why, detail: job.detail, log: job.log) }
                Button(L10n.t("failed.retry")) { app.job = nil }
            }
        }
        .padding(40)
    }
}
