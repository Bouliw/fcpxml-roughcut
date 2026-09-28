import SwiftUI

/// First launch, three screens: who makes the choices, the music folder (optional), the downloads.
struct SetupView: View {
    @EnvironmentObject var app: AppState
    @State private var page = Int(ProcessInfo.processInfo.environment["ROUGHCUT_SETUP_PAGE"] ?? "") ?? 0  // for screenshots

    var body: some View {
        VStack(spacing: 0) {
            Group {
                switch page {
                case 0: BrainChoice(compact: false)
                case 1: MusicPage()
                default: DownloadsPage()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(.horizontal, 32).padding(.top, 28)
            Divider()
            HStack {
                HStack(spacing: 6) {
                    ForEach(0..<3) { i in Circle().fill(i == page ? Color.accentColor : Color.secondary.opacity(0.3)).frame(width: 7, height: 7) }
                }
                Spacer()
                if page > 0 { Button(L10n.t("setup.back")) { page -= 1 } }
                if page < 2 {
                    Button(L10n.t("setup.next")) { page += 1 }.keyboardShortcut(.defaultAction)
                } else {
                    Button(L10n.t("setup.done")) { app.finishSetup() }.keyboardShortcut(.defaultAction)
                }
            }
            .padding(.horizontal, 24).padding(.vertical, 14)
        }
        .onAppear { app.rescan() }
    }
}

/// The three brains, what was found for each, and the settings each needs (also in Settings).
struct BrainChoice: View {
    @EnvironmentObject var app: AppState
    let compact: Bool
    @State private var provider = "anthropic"
    @State private var key = ""
    @State private var saved = false
    @State private var localModel = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !compact {
                Text(L10n.t("setup.brain.title")).font(.title2.weight(.semibold))
                Text(L10n.t("setup.brain.text")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(BrainKind.allCases) { kind in
                Button { app.brain = kind } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: app.brain == kind ? "largecircle.fill.circle" : "circle")
                            .foregroundStyle(app.brain == kind ? Color.accentColor : .secondary).font(.title3)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(kind.title).font(.headline)
                            Text(kind.line).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            if app.brain == kind { details(kind).padding(.top, 4) }
                        }
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(app.brain == kind ? .isSelected : [])
            }
        }
        .onAppear {
            provider = app.settings.get("brain", "api_provider") ?? "anthropic"
            saved = Keychain.get(provider) != nil
            localModel = app.settings.get("brain", "local_model") ?? ""
        }
    }

    @ViewBuilder func details(_ kind: BrainKind) -> some View {
        switch kind {
        case .claude:
            HStack {
                switch app.scan.claude {
                case .signedIn(let plan): Label(L10n.t("brain.claude.ok", plan), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                case .signedOut: Label(L10n.t("brain.claude.out"), systemImage: "exclamationmark.circle").foregroundStyle(.orange)
                case .missing: Label(L10n.t("brain.claude.missing"), systemImage: "xmark.circle").foregroundStyle(.orange)
                }
                recheck
            }
            .font(.callout)
        case .local:
            VStack(alignment: .leading, spacing: 6) {
                if let s = app.scan.local {
                    Label(L10n.t(s.name == "LM Studio" ? "brain.local.lmstudio" : "brain.local.ollama",
                                 s.models.isEmpty ? L10n.t("brain.local.noModel") : s.models.joined(separator: ", ")),
                          systemImage: "checkmark.circle.fill").foregroundStyle(.green).lineLimit(2)
                    if !s.models.isEmpty {
                        Picker(L10n.t("brain.local.model"), selection: $localModel) {
                            ForEach(s.models, id: \.self) { Text($0).tag($0) }
                        }
                        .frame(maxWidth: 360)
                        .onChange(of: localModel) { _, m in app.useLocal(s, model: m) }
                        .onAppear { if !s.models.contains(localModel) { localModel = s.models[0]; app.useLocal(s, model: localModel) } }
                    }
                } else {
                    Text(L10n.t("brain.local.missing")).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
                recheck
            }
            .font(.callout)
        case .api:
            VStack(alignment: .leading, spacing: 6) {
                Picker(L10n.t("brain.api.provider"), selection: $provider) {
                    Text("Claude (Anthropic)").tag("anthropic")
                    Text("OpenAI").tag("openai")
                }
                .pickerStyle(.segmented).frame(maxWidth: 300)
                .onChange(of: provider) { _, p in
                    app.update("brain", "api_provider", p)
                    saved = Keychain.get(p) != nil
                }
                HStack {
                    SecureField(L10n.t("brain.api.key"), text: $key).frame(maxWidth: 260)
                    Button(L10n.t("brain.api.save")) {
                        saved = Keychain.set(provider, key.trimmingCharacters(in: .whitespacesAndNewlines))
                        key = ""
                    }
                    .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if saved { Label(L10n.t("brain.api.saved"), systemImage: "lock.fill").foregroundStyle(.green) }
            }
            .font(.callout)
        }
    }

    var recheck: some View {
        Button(L10n.t("brain.recheck")) { app.rescan() }.buttonStyle(.link).disabled(app.scanning)
    }
}

struct MusicPage: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L10n.t("setup.videos.title")).font(.title2.weight(.semibold))
            // the main spoken language: given to Whisper, it writes the other languages as spoken too
            Text(L10n.t("setup.language.text")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker(L10n.t("settings.language"), selection: Binding(get: { app.spokenLanguage }, set: { app.spokenLanguage = $0 })) {
                ForEach(AppState.spokenLanguages, id: \.code) { l in
                    Text(l.code == "auto" ? L10n.t("setup.language.several") : l.name).tag(l.code)
                }
            }
            .frame(maxWidth: 360)
            .onAppear {  // the Mac's language to start with, written at once: it is what the edits will use
                if app.settings.get("auto", "transcription_language") as String? == nil { app.spokenLanguage = L10n.language }
            }
            Divider().padding(.vertical, 4)
            Text(L10n.t("setup.music.title")).font(.headline)
            Text(L10n.t("setup.music.text")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Image(systemName: "music.note.list").font(.title2).foregroundStyle(Color.accentColor)
                Text(app.musicFolder?.lastPathComponent ?? L10n.t("setup.music.none"))
                    .foregroundStyle(app.musicFolder == nil ? .secondary : .primary)
                Spacer()
                Button(L10n.t("setup.music.choose")) { app.chooseMusicFolder() }
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.08)))
        }
    }
}

struct DownloadsPage: View {
    @ObservedObject var downloads = AppState.shared.downloads

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(L10n.t("setup.download.title")).font(.title2.weight(.semibold))
            Text(L10n.t("setup.download.text")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            row(L10n.t("setup.download.engine"), downloads.engine)
            row(L10n.t("setup.download.model", ByteCountFormatter.string(fromByteCount: WhisperModel.chosen.bytes, countStyle: .file)), downloads.model)
        }
    }

    func row(_ title: String, _ state: Downloads.State) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            switch state {
            case .waiting: Text(L10n.t("setup.download.waiting")).font(.caption).foregroundStyle(.secondary)
            case .running(let p): ProgressView(value: p); Text("\(Int(p * 100)) %").font(.caption).monospacedDigit().foregroundStyle(.secondary)
            case .present: Label(L10n.t("setup.download.present"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            case .done: Label(L10n.t("setup.download.done"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            case .failed(let why):
                HStack {
                    Label(why, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                    Button(L10n.t("setup.download.retry")) { downloads.start() }
                }
            }
        }
    }
}
