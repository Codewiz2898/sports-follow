import AppKit
import AVFoundation

// Slam for Mac. Modes:
//   (default)          the menu-bar app: pick the game's window in macOS's picker; ⌃⇧S saves the last 30 s,
//                      cut with its replay and end card, and opens the Clip ready panel (Publish / Save / Share)
//   --dota             find the Dota 2 window itself instead (needs the Screen Recording permission)
// Developer tools (no window, no permission):
//   --selftest [s]     synthetic frames + tone through encoders, ring, writer and the cut
//   --from-frames <dir> [--sample]   a Dota `startmovie` export (jpg + wav) through it all
//   --montage [--vertical] [clips…]  --strip <link|live>  --probe <file>  --still <file> <s> <png>  --ocr <file> <s>
//   --panel <cut> [--browser] [--shot png]  --share-size <cut>

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

Fonts.register()
var args = Array(CommandLine.arguments.dropFirst())
func value(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

if let e = value("--end"), let style = Overlays.EndStyle(rawValue: e) { ClipEditor.endStyle = style }
if args.contains("--no-sting") { Sting.enabled = false }

if args.contains("--selftest") {
    let s = value("--selftest").flatMap(Int.init) ?? 40
    runAsync { try await SelfTest.run(seconds: s, hevc: args.contains("--hevc")) }
}
if let dir = value("--from-frames") {
    // --from-frames <dir> [--prefix clip1_] [--fps 30] [--sample]: a Dota startmovie export through the pipeline
    runAsync {
        try await FromFrames.run(dir: URL(fileURLWithPath: dir), prefix: value("--prefix") ?? "clip1_",
                                 fps: value("--fps").flatMap(Int.init) ?? 30, sample: args.contains("--sample"))
    }
}
if args.contains("--montage") {
    // --montage [--vertical] [clip.mp4 …]: today's clips (or these) into one video
    runAsync {
        let files = args.filter { $0.hasSuffix(".mp4") }.map { URL(fileURLWithPath: $0) }
        let clips = files.isEmpty ? Montage.todaysClips() : files
        var o = Montage.Options()
        o.vertical = args.contains("--vertical")
        let url = ClipWriter.newURL(label: o.vertical ? "montage-9x16" : "montage")
        let r = try await Montage.make(clips: clips, to: url, opts: o)
        print(String(format: "montage %@: %d clips, %.1f s, %.1f MB, made in %.0f ms", url.lastPathComponent, r.clips,
                     r.seconds, Double(r.bytes) / 1_048_576, r.ms))
        try await Probe.run(url)
    }
}
if let file = value("--probe") {
    runAsync { try await Probe.run(URL(fileURLWithPath: file)) }
}

if let i = args.firstIndex(of: "--still"), i + 3 < args.count {
    // --still <clip> <seconds> <out.png>: one frame, to look at the overlay
    runAsync {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: URL(fileURLWithPath: args[i + 1])))
        gen.requestedTimeToleranceBefore = .zero
        gen.requestedTimeToleranceAfter = .zero
        let (image, _) = try await gen.image(at: CMTime(seconds: Double(args[i + 2]) ?? 0, preferredTimescale: 600))
        let rep = NSBitmapImageRep(cgImage: image)
        try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[i + 3]))
    }
}

if let m = value("--strip") {
    // --strip <match link | live>: print the stat strip's line for a match, as a save would
    runAsync {
        let feed = MatchFeed(pinned: m == "live" ? nil : m)
        let line = await feed.line()
        print(line.map { "\($0.text)   (API clock \($0.clock ?? "-"), \(Int($0.ms)) ms, match \($0.match)\($0.auto ? ", found live" : ""))" }
              ?? "no line (match \(feed.pinned ?? "none live"))")
    }
}

if let file = value("--share-size") {
    // --share-size <cut.mp4>: the copy publishing uploads, and how big it is
    runAsync {
        let t0 = Date()
        let small = try await Publisher.shareSize(URL(fileURLWithPath: file))
        print(String(format: "%@: %dx%d, %.1f s, %.1f MB, %.1f Mbps, made in %.1f s", small.url.lastPathComponent, Int(small.size.width),
                     Int(small.size.height), small.seconds, Double(small.bytes) / 1_048_576, Double(small.bytes) * 8 / small.seconds / 1e6,
                     Date().timeIntervalSince(t0)))
    }
}

if let i = args.firstIndex(of: "--ocr"), i + 2 < args.count {
    // --ocr <clip> <seconds>: the clocks the save would read off that frame, and all the text it saw; first,
    // how long the launch's warm-up takes (long the first time a build at this path reads: TopBar.warmUp)
    runAsync {
        let w0 = Date()
        let warm = TopBar.warmUp()
        print("warm-up:", Int(Date().timeIntervalSince(w0) * 1000), "ms", warm)
        let r0 = Date()
        defer { print("frame read:", Int(Date().timeIntervalSince(r0) * 1000), "ms") }
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: URL(fileURLWithPath: args[i + 1])))
        gen.requestedTimeToleranceBefore = .zero
        gen.requestedTimeToleranceAfter = .zero
        let (image, _) = try await gen.image(at: CMTime(seconds: Double(args[i + 2]) ?? 0, preferredTimescale: 600))
        var pb: CVPixelBuffer?
        let attrs = [kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary] as CFDictionary
        CVPixelBufferCreate(nil, image.width, image.height, kCVPixelFormatType_32BGRA, attrs, &pb)
        CVPixelBufferLockBaseAddress(pb!, [])
        let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb!), width: image.width, height: image.height, bitsPerComponent: 8,
                            bytesPerRow: CVPixelBufferGetBytesPerRow(pb!), space: CGColorSpace(name: CGColorSpace.sRGB)!,
                            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        ctx?.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        CVPixelBufferUnlockBaseAddress(pb!, [])
        print("text:", TopBar.texts(in: pb!))
        print("moment:", TopBar.moment(in: pb!)?.line ?? "-")
    }
}

if let i = args.firstIndex(of: "--panel"), i + 1 < args.count {
    // --panel <cut.mp4> [--shot out.png]: the Clip ready panel for a file (with a sample moment); --shot draws
    // the window to a picture and quits, to check its layout without a screen.
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    Fonts.register()
    let source: ClipSource = args.contains("--browser") ? .browser : .gameClient
    let panel = ClipPanel(clip: .init(cut: URL(fileURLWithPath: args[i + 1]), title: "Clip 17:34", moment: .sample,
                                      match: "pandascore_esports/1753729", source: source, posterAt: 20)) { _ in exit(0) }
    if let shot = value("--shot") {
        panel.window?.appearance = NSAppearance(named: .aqua)  // the picture has no window behind it: draw it light
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            guard let view = panel.window?.contentView, let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { exit(1) }
            view.cacheDisplay(in: view.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: shot))
            exit(0)
        }
    }
    panel.show()
    app.run()
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
opts.picker = !args.contains("--dota")
opts.edit = !args.contains("--no-edit")
opts.match = value("--match")

let app = NSApplication.shared
let controller = AppController(opts: opts)
app.delegate = controller
app.setActivationPolicy(.accessory)
app.run()
