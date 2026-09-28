// Checks of the app's logic, compiled with its sources by AppLogicTest (tests/test_scripts.py): the Command Line
// Tools have no Swift test framework. Prints one line per check; exits 1 when one fails.
// Usage: checks <url of a 3 MB file> <limit in bytes per second>
import Foundation

var failed = false
func check(_ ok: Bool, _ what: String) {
    print((ok ? "ok   " : "FAIL ") + what)
    if !ok { failed = true }
}

// download errors in plain words
check(Downloads.explain(NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)) == L10n.t("download.offline"), "offline")
check(Downloads.explain(NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotFindHost)) == L10n.t("download.offline"), "no host")
check(Downloads.explain(NSError(domain: NSPOSIXErrorDomain, code: 1)) == L10n.t("download.offline"), "blocked")
check(Downloads.explain(NSError(domain: NSCocoaErrorDomain, code: NSFileWriteOutOfSpaceError)).hasPrefix(L10n.t("download.disk", "").components(separatedBy: ":")[0]), "disk full")
check(Downloads.explain(NSError(domain: "HTTP", code: 404)) == L10n.t("download.other"), "anything else")

// time estimates grow with the footage and learn from what happened
let short = Estimator.expected(.transcribe, basis: 60, model: .turbo), long = Estimator.expected(.transcribe, basis: 3600, model: .turbo)
check(short > 0 && long > short, "estimates grow with the footage")
check(Estimator.expected(.transcribe, basis: 60, model: .large) > short, "large-v3 is slower")
Estimator.learn(.build, took: 40, expected: Estimator.expected(.build, basis: 60, model: .turbo))
check(Estimator.factor(.build) > 1, "a slow step raises its estimate")

// a newer version is found among the releases that carry a Roughcut disk image, and only those
let releases = #"""
[{"tag_name": "v1.0.0", "html_url": "https://example.org/v1", "draft": false, "prerelease": false, "assets": [{"name": "scripts.zip"}]},
 {"tag_name": "v0.3.0", "html_url": "https://example.org/v3", "draft": true, "prerelease": false, "assets": [{"name": "Roughcut-0.3.0.dmg"}]},
 {"tag_name": "v0.2.2", "html_url": "https://example.org/beta", "draft": false, "prerelease": true, "assets": [{"name": "Roughcut-0.2.2.dmg"}]},
 {"tag_name": "v0.2.1", "html_url": "https://example.org/v021", "draft": false, "prerelease": false, "assets": [{"name": "Roughcut-0.2.1.dmg"}]},
 {"tag_name": "v0.1.2", "html_url": "https://example.org/v012", "draft": false, "prerelease": false, "assets": [{"name": "Roughcut-0.1.2.dmg"}]}]
"""#.data(using: .utf8)!
check(Updates.newest(in: releases, than: "0.2.0")?.version == "0.2.1", "a newer version is found")
check(Updates.newest(in: releases, than: "0.2.1") == nil, "none when up to date")
check(Updates.newer("0.10.0", than: "0.9.9"), "versions compare as numbers")

// a report says what happened without the user's name or home folder, and fits in a link
let logFile = FileManager.default.temporaryDirectory.appendingPathComponent("report-\(UUID().uuidString).log")
try! (NSHomeDirectory() + "/Movies/Roughcut/Trip/C0001.MP4 by " + NSUserName() + "\nline 2\n").write(to: logFile, atomically: true, encoding: .utf8)
let report = Report.text(message: "The edit stopped", detail: "analyze.py: stopped by signal 9", log: logFile)
check(report.contains("The edit stopped") && report.contains("stopped by signal 9") && report.contains("~/Movies/Roughcut/Trip"), "a report says what happened")
check(!report.contains(NSHomeDirectory()) && !report.contains(NSUserName()), "no home folder, no user name")
let link = Report.url(title: "Edit stopped", body: String(repeating: "x", count: 20_000)).absoluteString
check(link.hasPrefix("https://github.com/Bouliw/fcpxml-roughcut/issues/new?title=Edit%20stopped&body=") && link.count < 16_000, "a new issue, filled in, short enough")
try? FileManager.default.removeItem(at: logFile)

// the number of Shorts, from the settings as written now and before several could be made
let settingsJSON = try! JSONSerialization.jsonObject(with: #"{"a": true, "b": false, "c": 2, "d": 9, "e": 1}"#.data(using: .utf8)!) as! [String: Any]
check(["a", "b", "c", "d", "e", "none"].map { AppState.shortsCount(settingsJSON[$0]) } == [3, 0, 2, 3, 1, 3], "Shorts: true is 3, false none, a number as is")
let three = EditResult(fcpxml: URL(fileURLWithPath: "/x"), project: URL(fileURLWithPath: "/x"), length: 60, shorts: [40, 50, 55],
                       library: nil, noMusic: nil, note: nil)
check(three.shortsText.contains("3 Shorts"), "three Shorts are counted")

// the speed limit holds
let file = FileManager.default.temporaryDirectory.appendingPathComponent("fetch-\(UUID().uuidString)")
let limit = Double(CommandLine.arguments[2])!
let began = Date()
var result: (URL?, Error?) = (nil, nil)
let fetch = Fetch(url: URL(string: CommandLine.arguments[1])!, to: file, limit: limit, progress: { _ in }, done: { result = ($0, $1) })
fetch.start()
while result.0 == nil && result.1 == nil && Date().timeIntervalSince(began) < 60 { RunLoop.main.run(until: Date().addingTimeInterval(0.1)) }
let took = Date().timeIntervalSince(began)
let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
check(result.0 != nil && size == 3_000_000, "the file arrives whole")
check(took >= 3_000_000 / limit * 0.85, "no faster than the limit (\(String(format: "%.1f", took)) s)")
try? FileManager.default.removeItem(at: file)
exit(failed ? 1 : 0)
