import AppKit
import SwiftUI

/// The advanced settings, described by the engine itself (config/advanced.json): each with a few words in plain
/// language and its default value (config/defaults.json), and one button to put them all back.
struct AdvancedSchema: Decodable {
    struct Text: Decodable {
        let en: String
        let fr: String
        var local: String { L10n.french ? fr : en }
    }
    struct Option: Decodable {
        let value: String
        let label: Text
    }
    struct Item: Decodable, Identifiable {
        let key: String
        let type: String
        let label: Text
        let help: Text
        let min: Double?
        let max: Double?
        let step: Double?
        let unit: String?
        let options: [Option]?
        var id: String { key }
        var section: String { String(key.split(separator: ".", maxSplits: 1)[0]) }
        var name: String { String(key.split(separator: ".", maxSplits: 1)[1]) }
    }
    struct Group: Decodable, Identifiable {
        let title: Text
        let items: [Item]
        var id: String { title.en }
    }
    let groups: [Group]

    static func load() -> AdvancedSchema? {
        guard let d = try? Data(contentsOf: Paths.engine.appendingPathComponent("config/advanced.json")) else { return nil }
        return try? JSONDecoder().decode(AdvancedSchema.self, from: d)
    }

    /// The engine's default values, section by section.
    static func defaults() -> [String: [String: Any]] {
        guard let d = try? Data(contentsOf: Paths.engine.appendingPathComponent("config/defaults.json")),
              let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
        return obj.compactMapValues { $0 as? [String: Any] }
    }
}

struct AdvancedSettingsView: View {
    @EnvironmentObject var app: AppState
    @ObservedObject var downloads = AppState.shared.downloads
    @State private var confirmReset = false
    private let schema = AdvancedSchema.load()
    private let defaults = AdvancedSchema.defaults()

    var body: some View {
        Form {
            Section {
                Text(L10n.t("adv.intro")).font(.callout).foregroundStyle(.secondary)
            }
            Section(L10n.t("adv.app")) {
                LabeledContent(L10n.t("settings.projects")) {
                    HStack {
                        Text(Paths.projects.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                            .foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        Button(L10n.t("settings.change")) { chooseProjects() }
                    }
                }
                Picker(L10n.t("settings.model"), selection: Binding(get: { WhisperModel.chosen }, set: {
                    WhisperModel.chosen = $0
                    downloads.refresh()
                    downloads.start()
                    app.objectWillChange.send()
                })) {
                    Text(L10n.t("settings.model.turbo")).tag(WhisperModel.turbo)
                    Text(L10n.t("settings.model.large")).tag(WhisperModel.large)
                }
                Picker(L10n.t("settings.language"), selection: Binding(get: { app.spokenLanguage }, set: { app.spokenLanguage = $0 })) {
                    ForEach(AppState.spokenLanguages, id: \.code) { l in
                        Text(l.code == "auto" ? L10n.t("settings.language.auto") : l.name).tag(l.code)
                    }
                }
                TextField(L10n.t("settings.library"), text: Binding(get: { app.library }, set: { app.library = $0 }))
                Picker(L10n.t("settings.speed"), selection: Binding(get: { Prefs.store.double(forKey: "downloadLimit") },
                                                                    set: { Prefs.store.set($0, forKey: "downloadLimit"); app.objectWillChange.send() })) {
                    Text(L10n.t("settings.speed.none")).tag(0.0)
                    Text(L10n.t("settings.speed.mb", 10)).tag(10_000_000.0)
                    Text(L10n.t("settings.speed.mb", 3)).tag(3_000_000.0)
                    Text(L10n.t("settings.speed.mb", 1)).tag(1_000_000.0)
                }
                Picker(L10n.t("settings.appLanguage"), selection: Binding(get: { L10n.choice ?? "" }, set: { app.setLanguage($0.isEmpty ? nil : $0) })) {
                    Text(L10n.t("settings.appLanguage.system")).tag("")
                    Text("Français").tag("fr")
                    Text("English").tag("en")
                }
            }
            if let schema {
                ForEach(schema.groups) { group in
                    Section(group.title.local) {
                        ForEach(group.items) { item in row(item) }
                    }
                }
            }
            Section {
                HStack {
                    Button(L10n.t("adv.reset")) { confirmReset = true }
                    Spacer()
                    Button(L10n.t("settings.openJSON")) {
                        app.settings.save()
                        NSWorkspace.shared.open(app.settings.url)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 560)
        .frame(minHeight: 560)
        .confirmationDialog(L10n.t("adv.reset.confirm"), isPresented: $confirmReset) {
            Button(L10n.t("adv.reset"), role: .destructive) { reset() }
        }
    }

    @ViewBuilder func row(_ item: AdvancedSchema.Item) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(item.label.local)
                Spacer(minLength: 12)
                control(item)
            }
            let help = item.help.local
            Text((help.isEmpty ? "" : help + " ") + L10n.t("adv.default", display(defaultValue(item), item)))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder func control(_ item: AdvancedSchema.Item) -> some View {
        switch item.type {
        case "bool":
            Toggle("", isOn: Binding(get: { (value(item) as? Bool) ?? false }, set: { app.update(item.section, item.name, $0) }))
                .labelsHidden()
        case "choice":
            Picker("", selection: Binding(get: { (value(item) as? String) ?? "" }, set: { app.update(item.section, item.name, $0) })) {
                ForEach(item.options ?? [], id: \.value) { o in Text(o.label.local).tag(o.value) }
            }
            .labelsHidden().fixedSize()
        case "text":
            TextField("", text: Binding(get: { (value(item) as? String) ?? "" }, set: { app.update(item.section, item.name, $0) }))
                .frame(width: 180)
        case "list":
            TextField("", text: Binding(get: { ((value(item) as? [String]) ?? []).joined(separator: ", ") }, set: {
                app.update(item.section, item.name, $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
            }))
            .frame(width: 140)
        default:  // number, int
            let binding = Binding<Double>(get: { number(value(item)) }, set: { v in
                let clamped = Swift.min(item.max ?? .infinity, Swift.max(item.min ?? -.infinity, v))
                app.update(item.section, item.name, item.type == "int" ? Int(clamped.rounded()) as Any : (clamped * 1000).rounded() / 1000)
            })
            HStack(spacing: 4) {
                TextField("", value: binding, format: .number).frame(width: 64).multilineTextAlignment(.trailing)
                if let unit = item.unit { Text(unit).foregroundStyle(.secondary) }
                Stepper("", value: binding, in: (item.min ?? -1e9)...(item.max ?? 1e9), step: item.step ?? 1).labelsHidden()
            }
        }
    }

    func defaultValue(_ item: AdvancedSchema.Item) -> Any? { defaults[item.section]?[item.name] }

    func value(_ item: AdvancedSchema.Item) -> Any? {
        (app.settings.data[item.section] as? [String: Any])?[item.name] ?? defaultValue(item)
    }

    func number(_ v: Any?) -> Double { (v as? NSNumber)?.doubleValue ?? 0 }

    func display(_ v: Any?, _ item: AdvancedSchema.Item) -> String {
        switch v {
        case let b as Bool where item.type == "bool": return L10n.t(b ? "adv.on" : "adv.off")
        case let s as String where item.type == "choice":
            return item.options?.first { $0.value == s }?.label.local ?? s
        case let s as String: return s.isEmpty ? L10n.t("adv.empty") : s
        case let a as [Any]: return a.isEmpty ? L10n.t("adv.empty") : a.map { "\($0)" }.joined(separator: ", ")
        case let n as NSNumber: return n.stringValue + (item.unit.map { " " + $0 } ?? "")
        default: return "—"
        }
    }

    /// Every advanced setting back to the engine's default: its key taken out of roughcut.json.
    func reset() {
        for item in schema?.groups.flatMap(\.items) ?? [] { app.settings.set(item.section, item.name, nil) }
        app.settings.save()
        Prefs.store.set(0.0, forKey: "downloadLimit")
        app.objectWillChange.send()
    }

    func chooseProjects() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            Paths.projects = url
            app.reloadSettings()
        }
    }
}
