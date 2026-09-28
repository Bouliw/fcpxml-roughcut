import AppKit
import SwiftUI

/// Editing by the text: the transcript of all the footage, the words of the edit in black and the passages cut in
/// grey. A click on a word takes it out (struck through) or puts it back; a click on the time of a line does the whole
/// line. "Update in Final Cut Pro" makes a new version of the edit from the words chosen (text_edit.py), with its
/// captions and subtitles timed again.
@MainActor
final class TextEditModel: ObservableObject {
    struct Word: Decodable, Identifiable {
        let w: String
        let start: Double
        let end: Double
        let state: String  // "kept", "hook" or "cut": where it is in the edit
        var id: Double { start }
    }
    struct Line: Decodable, Identifiable {
        let start: Double
        let end: Double
        let words: [Word]
        var id: Double { start }
    }
    struct Clip: Decodable, Identifiable {
        let rush: String
        let file: String
        let name: String
        let lines: [Line]
        var id: String { rush }
    }
    struct Text: Decodable { let clips: [Clip] }

    enum State: Equatable { case loading, ready, failed(String), updating }
    @Published var state = State.loading
    @Published var clips: [Clip] = []
    @Published var edits: [String: Bool] = [:]  // "file|start": kept or not, for the words changed
    var project: URL?

    static func key(_ file: String, _ w: Word) -> String { "\(file)|\(String(format: "%.2f", w.start))" }

    func kept(_ file: String, _ w: Word) -> Bool { edits[Self.key(file, w)] ?? (w.state != "cut") }

    func toggle(_ file: String, _ w: Word) {
        let k = Self.key(file, w), now = !kept(file, w)
        edits[k] = now == (w.state != "cut") ? nil : now
    }

    /// A whole line: out if any of its words is in, else all in.
    func toggle(_ file: String, _ line: Line) {
        let anyIn = line.words.contains { kept(file, $0) }
        for w in line.words {
            let k = Self.key(file, w)
            edits[k] = !anyIn == (w.state != "cut") ? nil : !anyIn
        }
    }

    var removed: Int { edits.values.filter { !$0 }.count }
    var restored: Int { edits.values.filter { $0 }.count }

    func load(_ project: URL) {
        self.project = project
        state = .loading
        edits = [:]
        Task {
            let (code, out) = await EngineProcess.output("text_edit.py", ["export", project.path])
            guard code == 0, let d = try? Data(contentsOf: project.appendingPathComponent("text.json")),
                  let text = try? JSONDecoder().decode(Text.self, from: d) else {
                state = .failed(out.split(separator: "\n").last.map(String.init) ?? "?")
                return
            }
            clips = text.clips
            state = .ready
        }
    }

    /// The words changed written for the engine, then a new version of the edit from them.
    func update(app: AppState, done: @escaping () -> Void) {
        guard let project else { return }
        state = .updating
        let words = edits.map { k, v -> [String: Any] in
            let parts = k.split(separator: "|", maxSplits: 1)
            return ["file": String(parts[0]), "start": Double(parts[1]) ?? 0, "kept": v]
        }
        let path = project.appendingPathComponent("text-edits.json")
        guard let data = try? JSONSerialization.data(withJSONObject: ["words": words]), (try? data.write(to: path)) != nil else {
            state = .failed(L10n.t("text.failed"))
            return
        }
        Task {
            let (code, out) = await EngineProcess.output("text_edit.py", ["apply", project.path, path.path])
            let decisions = project.appendingPathComponent("text-decisions.json")
            guard code == 0, Paths.fm.fileExists(atPath: decisions.path), let first = clips.first else {
                state = .failed(out.split(separator: "\n").last.map(String.init) ?? L10n.t("text.failed"))
                return
            }
            app.newVersion(project: project, footage: URL(fileURLWithPath: first.file).deletingLastPathComponent(), decisions: decisions)
            state = .ready
            done()
        }
    }
}

struct TextEditView: View {
    @EnvironmentObject var app: AppState
    @StateObject private var model = TextEditModel()
    @Environment(\.dismissWindow) private var dismissWindow
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            Label(L10n.t("text.warning"), systemImage: "exclamationmark.triangle.fill")
                .font(.callout).foregroundStyle(.orange).padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.1))
            Text(L10n.t("text.help")).font(.caption).foregroundStyle(.secondary)
                .padding(.horizontal, 12).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
            Divider()
            switch model.state {
            case .loading:
                ProgressView(L10n.t("text.loading")).frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let why):
                Text(why).foregroundStyle(.orange).frame(maxWidth: .infinity, maxHeight: .infinity)
            case .ready, .updating:
                transcript
            }
            Divider()
            HStack {
                Text(L10n.t("text.counts", model.removed, model.restored)).foregroundStyle(.secondary).monospacedDigit()
                Spacer()
                Button(L10n.t("text.undo")) { model.edits = [:] }.disabled(model.edits.isEmpty)
                Button {
                    model.update(app: app) {
                        openWindow(id: "main")
                        dismissWindow(id: "text")
                    }
                } label: {
                    if model.state == .updating { ProgressView().controlSize(.small) } else { Text(L10n.t("text.update")) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.edits.isEmpty || model.state != .ready || app.job?.state == .running)
            }
            .padding(12)
        }
        .frame(minWidth: 640, minHeight: 520)
        .onAppear { if let p = app.textProject { model.load(p) } }
        .onChange(of: app.textProject) { _, p in if let p { model.load(p) } }
    }

    var transcript: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 6) {
                ForEach(model.clips) { clip in
                    Text(clip.name).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 10)
                    ForEach(clip.lines) { line in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Button(clock(line.start)) { model.toggle(clip.file, line) }
                                .buttonStyle(.plain).font(.caption.monospacedDigit()).foregroundStyle(.tertiary)
                                .frame(width: 44, alignment: .trailing)
                                .help(L10n.t("text.line"))
                            FlowLayout(spacing: 4) {
                                ForEach(line.words) { w in word(clip.file, w) }
                            }
                        }
                    }
                }
            }
            .padding(12)
        }
    }

    func word(_ file: String, _ w: TextEditModel.Word) -> some View {
        let inEdit = w.state != "cut", kept = model.kept(file, w)
        let colour: Color = inEdit ? (kept ? (w.state == "hook" ? .blue : .primary) : .red) : (kept ? .green : .secondary.opacity(0.6))
        return SwiftUI.Text(w.w)
            .foregroundStyle(colour)
            .strikethrough(inEdit && !kept)
            .underline(!inEdit && kept)
            .contentShape(Rectangle())
            .onTapGesture { model.toggle(file, w) }
    }
}

/// Words laid out like text: as many on a row as fit, then the next row.
struct FlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 520
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0 && x + s.width > width {
                x = 0
                y += row + 2
                row = 0
            }
            x += s.width + spacing
            row = max(row, s.height)
        }
        return CGSize(width: width, height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > bounds.minX && x + s.width > bounds.maxX {
                x = bounds.minX
                y += row + 2
                row = 0
            }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            row = max(row, s.height)
        }
    }
}
