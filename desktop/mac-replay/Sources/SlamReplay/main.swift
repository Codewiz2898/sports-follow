import AppKit

// SlamReplay: a throwaway spike. Modes:
//   (default)          menu-bar recorder for --target (Dota 2), ⌃⇧S or `kill -USR1 <pid>` saves
//   --probe-only       no recorder, only the FPS probe and stats (the "without" run)
//   --selftest [s]     synthetic frames + tone through encoder, ring and writer (no permission needed)
//   --probe <file>     read a saved clip back

func runAsync(_ body: @escaping () async throws -> Void) -> Never {
    Task.detached {
        do { try await body(); Log.flush(); exit(0) } catch {
            print("error: \(error)")
            Log.flush()
            exit(1)
        }
    }
    dispatchMain()
}

var args = Array(CommandLine.arguments.dropFirst())
func value(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

if args.contains("--selftest") {
    let s = value("--selftest").flatMap(Int.init) ?? 40
    runAsync { try await SelfTest.run(seconds: s, hevc: args.contains("--hevc")) }
}
if let file = value("--probe") {
    runAsync { try await Probe.run(URL(fileURLWithPath: file)) }
}

var opts = Options()
if let t = value("--target") { opts.target = t; opts.label = value("--label") ?? t.split(separator: ".").last.map(String.init) ?? "clip" }
if let l = value("--label") { opts.label = l }
if let s = value("--seconds").flatMap(Double.init) { opts.seconds = s }
if let f = value("--fps").flatMap(Int.init) { opts.fps = f }
if let b = value("--bitrate").flatMap(Int.init) { opts.bitrate = b }
if let w = value("--max-width").flatMap(Int.init) { opts.maxWidth = w }
if let h = value("--max-height").flatMap(Int.init) { opts.maxHeight = h }
opts.hevc = args.contains("--hevc")
opts.audio = !args.contains("--no-audio")
opts.picker = args.contains("--picker")
opts.fpsProbe = args.contains("--fps-probe")

let app = NSApplication.shared
let controller = AppController(opts: opts, probeOnly: args.contains("--probe-only"))
app.delegate = controller
app.setActivationPolicy(.accessory)
app.run()
