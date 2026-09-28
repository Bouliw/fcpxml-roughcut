import AppKit
import SwiftUI

/// Five simple settings; the rest under Advanced. All of them are written to roughcut.json, which the engine reads.
struct SettingsView: View {
    @EnvironmentObject var app: AppState
    @State private var tab = "simple"

    var body: some View {
        TabView(selection: $tab) {
            SimpleSettingsView().tabItem { Label(L10n.t("settings.tab.simple"), systemImage: "gearshape") }.tag("simple")
            AdvancedSettingsView().tabItem { Label(L10n.t("settings.tab.advanced"), systemImage: "slider.horizontal.3") }.tag("advanced")
        }
        .onAppear {
            app.reloadSettings()
            app.rescan()
            if let t = ProcessInfo.processInfo.environment["ROUGHCUT_SETTINGS_TAB"] { tab = t }  // for screenshots
        }
    }
}

/// The few settings everyone may want: the rest is in the advanced settings, and nobody needs to open them.
struct SimpleSettingsView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        Form {
            Section(L10n.t("settings.brain")) {
                BrainChoice(compact: true)
            }
            Section(L10n.t("settings.music")) {
                LabeledContent(L10n.t("settings.musicFolder")) {
                    HStack {
                        Text(app.musicFolder?.lastPathComponent ?? L10n.t("settings.musicNone")).foregroundStyle(.secondary)
                        Button(L10n.t("settings.change")) { app.chooseMusicFolder() }
                        if app.musicFolder != nil { Button(L10n.t("settings.remove")) { app.musicFolder = nil } }
                    }
                }
                LabeledContent(L10n.t("settings.duck")) {
                    HStack {
                        Slider(value: Binding(get: { app.duckDB }, set: { app.duckDB = $0 }), in: -36...(-10), step: 1)
                            .frame(width: 180)
                        Text("\(Int(app.duckDB)) dB").monospacedDigit().frame(width: 52, alignment: .trailing)
                    }
                }
                Toggle(L10n.t("settings.voice"), isOn: Binding(get: { app.voiceIsolation }, set: { app.voiceIsolation = $0 }))
                Text(L10n.t("settings.voice.help")).font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Picker(L10n.t("settings.style"), selection: Binding(get: { app.style }, set: { app.style = $0 })) {
                    Text(L10n.t("settings.style.vlog")).tag("vlog")
                    Text(L10n.t("settings.style.discussion")).tag("discussion")
                }
                Text(L10n.t("settings.style.help")).font(.caption).foregroundStyle(.secondary)
                Picker(L10n.t("settings.dressing"), selection: Binding(get: { app.dressing }, set: { app.dressing = $0 })) {
                    Text(L10n.t("settings.dressing.none")).tag("none")
                    Text(L10n.t("settings.dressing.light")).tag("light")
                    Text(L10n.t("settings.dressing.full")).tag("full")
                }
                Text(L10n.t("settings.dressing.help")).font(.caption).foregroundStyle(.secondary)
                Toggle(L10n.t("settings.zooms"), isOn: Binding(get: { app.zooms }, set: { app.zooms = $0 }))
                Picker(L10n.t("settings.short"), selection: Binding(get: { app.shorts }, set: { app.shorts = $0 })) {
                    Text(L10n.t("settings.short.none")).tag(0)
                    Text("1").tag(1)
                    ForEach(2...3, id: \.self) { n in Text(L10n.t("settings.short.upTo", n)).tag(n) }
                }
            }
            Section(L10n.t("settings.captions")) {
                CaptionStyleView()
            }
        }
        .formStyle(.grouped)
        .frame(width: 560)
        .frame(minHeight: 560)
    }
}

/// The look of the animated captions, for the main edit or the Shorts, with a preview.
struct CaptionStyleView: View {
    @EnvironmentObject var app: AppState
    @State private var vertical = ProcessInfo.processInfo.environment["ROUGHCUT_CAPTIONS_SHORTS"] != nil  // for screenshots
    static let families = NSFontManager.shared.availableFontFamilies.sorted()

    var body: some View {
        let font: String = app.caption(vertical, "font")
        let size: Double = app.caption(vertical, "font_size")
        let height: Double = app.caption(vertical, AppState.heightKey(vertical))
        Picker(L10n.t("settings.captions.for"), selection: $vertical) {
            Text(L10n.t("settings.captions.main")).tag(false)
            Text(L10n.t("settings.captions.shorts")).tag(true)
        }
        .pickerStyle(.segmented)
        HStack {
            Picker(L10n.t("settings.captions.style"), selection: Binding(get: { CaptionStyles.current(app, vertical) }, set: {
                CaptionStyles.apply($0, app, vertical)
            })) {
                ForEach(CaptionStyles.all(vertical), id: \.id) { style in Text(style.name).tag(style.id) }
                if CaptionStyles.current(app, vertical) == "" { Text(L10n.t("settings.captions.custom")).tag("") }
            }
            Button(L10n.t("settings.captions.save")) { CaptionStyles.saveCurrent(app, vertical) }.controlSize(.small)
            if CaptionStyles.current(app, vertical).hasPrefix("saved:") {
                Button(L10n.t("settings.captions.delete")) { CaptionStyles.delete(CaptionStyles.current(app, vertical)); app.objectWillChange.send() }
                    .controlSize(.small)
            }
        }
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                Picker(L10n.t("settings.captions.font"), selection: Binding(get: { font }, set: { app.setCaption(vertical, "font", $0) })) {
                    ForEach(Self.families.contains(font) ? Self.families : [font] + Self.families, id: \.self) { Text($0).tag($0) }
                }
                LabeledContent(L10n.t("settings.captions.size")) {
                    Slider(value: Binding(get: { size }, set: { app.setCaption(vertical, "font_size", ($0 * 1000).rounded() / 1000) }),
                           in: 0.03...0.1)
                }
                LabeledContent(L10n.t("settings.captions.position")) {
                    Slider(value: Binding(get: { height }, set: { app.setCaption(vertical, AppState.heightKey(vertical), ($0 * 100).rounded() / 100) }),
                           in: 0.02...0.8)
                }
                ColorPicker(L10n.t("settings.captions.colour"), selection: colour("colour"), supportsOpacity: false)
                ColorPicker(L10n.t("settings.captions.highlight"), selection: colour("highlight_colour"), supportsOpacity: false)
                Button(L10n.t("settings.captions.reset")) { app.resetCaptions(vertical) }.controlSize(.small)
            }
            CaptionPreview(vertical: vertical, font: font, size: size, height: height,
                           colour: Color(hex: app.caption(vertical, "colour")), highlight: Color(hex: app.caption(vertical, "highlight_colour")),
                           uppercase: app.caption(vertical, "uppercase"), karaoke: app.caption(vertical, "karaoke"),
                           pop: app.caption(vertical, "pop"))
        }
        Text(L10n.t("regen.help")).font(.caption).foregroundStyle(.secondary)
    }

    func colour(_ key: String) -> Binding<Color> {
        Binding(get: { Color(hex: app.caption(vertical, key)) }, set: { app.setCaption(vertical, key, $0.hex) })
    }
}

/// The captions as they will show, over a stand-in picture of the shape of the video.
struct CaptionPreview: View {
    let vertical: Bool, font: String, size: Double, height: Double, colour: Color, highlight: Color
    var uppercase = false, karaoke = false, pop = 1.12

    var body: some View {
        let h: CGFloat = vertical ? 230 : 135
        let w: CGFloat = vertical ? h * 9 / 16 : h * 16 / 9
        let words = L10n.french ? ["on", "y", "va"] : ["let's", "go", "now"]
        ZStack(alignment: .bottom) {
            LinearGradient(colors: [Color(white: 0.55), Color(white: 0.25)], startPoint: .top, endPoint: .bottom)
            HStack(spacing: h * size * 0.3) {
                ForEach(Array(words.enumerated()), id: \.offset) { i, word in  // the second word being said
                    Text(uppercase ? word.uppercased() : word).foregroundStyle(i == 1 || (karaoke && i < 1) ? highlight : colour)
                        .scaleEffect(i == 1 ? pop : 1)
                }
            }
            .font(.custom(font, size: h * size))
            .shadow(color: .black, radius: 0.8)
            .padding(.bottom, h * height)
        }
        .frame(width: w, height: h)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .accessibilityHidden(true)
    }
}

/// Caption styles ready to use, and the ones saved from the settings; for the main edit or the Shorts.
enum CaptionStyles {
    struct Style {
        let id: String
        let name: String
        let values: [String: Any]
    }
    static let keys = ["font", "font_size", "colour", "highlight_colour", "outline_colour", "outline", "pop", "uppercase", "words", "karaoke"]

    static func builtIn(_ vertical: Bool) -> [Style] {
        let k = vertical ? 1.1 : 1.0  // a vertical Short: a little bigger
        func style(_ id: String, _ en: String, _ fr: String, _ v: [String: Any]) -> Style {
            var values = v
            values["font_size"] = ((v["font_size"] as! Double) * k * 1000).rounded() / 1000
            return Style(id: id, name: L10n.french ? fr : en, values: values)
        }
        return [
            style("pop", "Yellow pop", "Pop jaune", ["font": "Arial Black", "font_size": 0.05, "colour": "#FFFFFF", "highlight_colour": "#FFD400",
                  "outline_colour": "#000000", "outline": 0.08, "pop": 1.12, "uppercase": false, "words": 3, "karaoke": false]),
            style("classic", "Classic", "Classique", ["font": "Helvetica Neue", "font_size": 0.042, "colour": "#FFFFFF", "highlight_colour": "#FFFFFF",
                  "outline_colour": "#000000", "outline": 0.06, "pop": 1.0, "uppercase": false, "words": 6, "karaoke": false]),
            style("minimal", "Minimal", "Minimaliste", ["font": "Avenir", "font_size": 0.038, "colour": "#FFFFFF", "highlight_colour": "#FFFFFF",
                  "outline_colour": "#000000", "outline": 0.03, "pop": 1.0, "uppercase": false, "words": 4, "karaoke": false]),
            style("impact", "Big impact", "Gros impact", ["font": "Impact", "font_size": 0.075, "colour": "#FFFFFF", "highlight_colour": "#FFD400",
                  "outline_colour": "#000000", "outline": 0.12, "pop": 1.18, "uppercase": true, "words": 2, "karaoke": false]),
            style("karaoke", "Karaoke", "Karaoké", ["font": "Arial Rounded MT Bold", "font_size": 0.05, "colour": "#FFFFFF", "highlight_colour": "#4FD1FF",
                  "outline_colour": "#000000", "outline": 0.08, "pop": 1.05, "uppercase": false, "words": 5, "karaoke": true]),
        ]
    }

    static var saved: [[String: Any]] {
        get { Prefs.store.array(forKey: "captionStyles") as? [[String: Any]] ?? [] }
        set { Prefs.store.set(newValue, forKey: "captionStyles") }
    }

    static func all(_ vertical: Bool) -> [Style] {
        builtIn(vertical) + saved.filter { ($0["vertical"] as? Bool) == vertical }.compactMap { s in
            guard let name = s["name"] as? String, let values = s["values"] as? [String: Any] else { return nil }
            return Style(id: "saved:\(vertical):\(name)", name: name, values: values)
        }
    }

    /// The style the settings match now, or "" when they match none.
    @MainActor static func current(_ app: AppState, _ vertical: Bool) -> String {
        all(vertical).first { style in
            style.values.allSatisfy { key, v in same(app.captionValue(vertical, key), v) }
        }?.id ?? ""
    }

    static func same(_ a: Any?, _ b: Any?) -> Bool {
        if let x = a as? NSNumber, let y = b as? NSNumber { return abs(x.doubleValue - y.doubleValue) < 0.0005 }
        if let x = a as? String, let y = b as? String { return x.caseInsensitiveCompare(y) == .orderedSame }
        return false
    }

    @MainActor static func apply(_ id: String, _ app: AppState, _ vertical: Bool) {
        guard let style = all(vertical).first(where: { $0.id == id }) else { return }
        for (key, v) in style.values { app.settings.set(AppState.captionSection(vertical), key, v) }
        app.settings.save()
        app.objectWillChange.send()
    }

    /// Asks a name, and keeps the current look of the captions under it.
    @MainActor static func saveCurrent(_ app: AppState, _ vertical: Bool) {
        let alert = NSAlert()
        alert.messageText = L10n.t("settings.captions.save.title")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
        alert.accessoryView = field
        alert.addButton(withTitle: L10n.t("settings.captions.save.ok"))
        alert.addButton(withTitle: L10n.t("settings.captions.save.cancel"))
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let name = field.stringValue.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        var values: [String: Any] = [:]
        for key in keys { values[key] = app.captionValue(vertical, key) }
        values[AppState.heightKey(vertical)] = app.captionValue(vertical, AppState.heightKey(vertical))
        saved = saved.filter { !(($0["name"] as? String) == name && ($0["vertical"] as? Bool) == vertical) }
            + [["name": name, "vertical": vertical, "values": values]]
        app.objectWillChange.send()
    }

    static func delete(_ id: String) {
        saved = saved.filter { "saved:\(($0["vertical"] as? Bool) ?? false):\(($0["name"] as? String) ?? "")" != id }
    }
}

extension Color {
    /// "#FFD400" -> the colour, in sRGB.
    init(hex: String) {
        let v = UInt32(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0xFFFFFF
        self.init(.sRGB, red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }

    var hex: String {
        let c = NSColor(self).usingColorSpace(.sRGB) ?? .white
        return String(format: "#%02X%02X%02X", Int((c.redComponent * 255).rounded()), Int((c.greenComponent * 255).rounded()),
                      Int((c.blueComponent * 255).rounded()))
    }
}

/// The menu bar icon: the edit running, its step and time left, and a way back to the window.
struct MenuBarView: View {
    @EnvironmentObject var app: AppState
    @ObservedObject var updates = AppState.shared.updates
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let job = app.job {
            JobMenu(job: job)
        } else {
            Text(L10n.t("menu.idle"))
        }
        if let r = updates.available {
            Button(L10n.t("update.available", r.version) + "…") { NSWorkspace.shared.open(r.page) }
        }
        Divider()
        Button(L10n.t("menu.show")) { show() }
        SettingsLink { Text(L10n.t("menu.settings")) }
        Button(L10n.t("text.menu")) { if app.chooseTextProject() { openWindow(id: "text"); NSApp.activate(ignoringOtherApps: true) } }
        Button(L10n.t("regen.menu")) { app.chooseProjectForCaptions() }
        Button(L10n.t("report.menu")) { Report.open(message: nil, detail: nil, log: nil) }
        Divider()
        Button(L10n.t("menu.quit")) { NSApp.terminate(nil) }
    }

    func show() {
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}

struct JobMenu: View {
    @EnvironmentObject var app: AppState
    @ObservedObject var job: EditJob

    var body: some View {
        switch job.state {
        case .running:
            Text("\(job.footage.name) · \(Int(job.progress * 100)) %")
            if let s = job.step { Text(s.title) }
            if let r = job.remaining { Text(r < 20 ? L10n.t("run.almost") : L10n.t("run.remaining", shortDuration(r))) }
            Button(L10n.t("run.cancel")) { job.cancel() }
        case .done:
            Text(L10n.t("done.title", job.footage.name))
            if let r = job.result { Button(L10n.t("done.open")) { app.openInFinalCut(r.fcpxml) } }
        case .failed:
            Text(L10n.t("failed.title"))
        case .cancelled:
            Text(L10n.t("cancelled.title"))
        }
    }
}

/// The icon in the menu bar: a film strip, with the progress next to it while an edit runs.
struct MenuBarLabel: View {
    @ObservedObject var app: AppState

    var body: some View {
        if let job = app.job { JobLabel(job: job) } else { Image(systemName: "film") }
    }
}

struct JobLabel: View {
    @ObservedObject var job: EditJob
    var body: some View {
        switch job.state {
        case .running: HStack(spacing: 3) { Image(systemName: "film"); Text("\(Int(job.progress * 100)) %").monospacedDigit() }
            .accessibilityLabel("Roughcut, \(Int(job.progress * 100)) %")
        case .done: Image(systemName: "checkmark.circle")
        default: Image(systemName: "film")
        }
    }
}
