import AppKit
import ScreenCaptureKit
import Vision
import CoreImage

/// Reads the game's own frame-rate counter (Dota's "Display network information", or the Metal HUD)
/// by taking a one-off screenshot of the top strip of its window every few seconds and reading the
/// text with Vision. Runs the same way with or without the recorder, so the two runs compare fairly.
final class FPSProbe {
    let target: String
    let every: Double
    private var timer: Timer?
    private var saved = 0
    private var n = 0

    init(target: String, every: Double = 5) {
        self.target = target
        self.every = every
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: every, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { await self.probe() }
        }
    }

    private func probe() async {
        n += 1
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let app = content.applications.first(where: { $0.bundleIdentifier == target }),
                  let win = Recorder.mainWindow(of: target, in: content) else { return }
            let display = content.displays.max {
                $0.frame.intersection(win.frame).area < $1.frame.intersection(win.frame).area
            } ?? content.displays[0]
            let filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
            let scale = CGFloat(filter.pointPixelScale)
            let strip = CGRect(x: win.frame.minX - display.frame.minX, y: win.frame.minY - display.frame.minY,
                               width: win.frame.width, height: min(win.frame.height, 90))
            let cfg = SCStreamConfiguration()
            cfg.sourceRect = strip
            cfg.width = Int(strip.width * scale)
            cfg.height = Int(strip.height * scale)
            cfg.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
            let texts = try recognize(image)
            let fps = FPSProbe.parseFPS(texts)
            var fields: [String: Any] = ["text": texts]
            if let fps { fields["fps"] = fps }
            Log.json("probe", fields)
            if saved < 4 || n % 12 == 0 { save(image) }
        } catch {
            Log.line("probe failed: \(error.localizedDescription)")
        }
    }

    private func recognize(_ image: CGImage) throws -> [String] {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image).perform([req])
        return (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    }

    static func parseFPS(_ texts: [String]) -> Int? {
        let joined = texts.joined(separator: " ")
        let patterns = [#"(?i)fps[^0-9]{0,3}(\d{1,3})"#, #"(?i)(\d{1,3})\s*fps"#]
        for p in patterns {
            if let r = try? NSRegularExpression(pattern: p),
               let m = r.firstMatch(in: joined, range: NSRange(joined.startIndex..., in: joined)),
               let range = Range(m.range(at: 1), in: joined) {
                return Int(joined[range])
            }
        }
        return nil
    }

    private func save(_ image: CGImage) {
        saved += 1
        let url = Log.dir.appendingPathComponent("probe-\(n).png")
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}

extension FPSProbe {
    /// Game clocks ("26:00") in the top fifth of a frame: Dota's clock when spectating in the client,
    /// or a stream's overlay. To compare with the API's clock at the same moment.
    static let ci = CIContext()

    /// Text in the middle of the top bar, where Dota keeps its clock: that strip cut out and scaled 3×
    /// first, as the clock is only ~14 px high in a 900-high frame and Vision misses it at that size.
    static func texts(in pb: CVPixelBuffer) -> [String] {
        let img = CIImage(cvPixelBuffer: pb)
        let e = img.extent
        let crop = CGRect(x: e.minX + e.width * 0.4, y: e.maxY - e.height * 0.06, width: e.width * 0.2, height: e.height * 0.06)
        let big = img.cropped(to: crop).transformed(by: CGAffineTransform(translationX: -crop.minX, y: -crop.minY))
            .transformed(by: CGAffineTransform(scaleX: 3, y: 3))
        guard let cg = ci.createCGImage(big, from: big.extent) else { return [] }
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try? VNImageRequestHandler(cgImage: cg).perform([req])
        return (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    }

    static func clocks(in pb: CVPixelBuffer) -> [String] {
        let texts = texts(in: pb)
        let r = try! NSRegularExpression(pattern: #"\b\d{1,2}:\d{2}\b"#)
        return texts.flatMap { t in
            r.matches(in: t, range: NSRange(t.startIndex..., in: t)).compactMap { Range($0.range, in: t).map { String(t[$0]) } }
        }
    }

    /// Dota's own top bar read off the screen: kills either side of the clock (Radiant left, Dire right),
    /// the moment when Slam's API has no live map for the game.
    static func screenMoment(in pb: CVPixelBuffer) -> Moment? {
        let t = texts(in: pb)
        guard let ci = t.firstIndex(where: { $0.range(of: #"^\d{1,2}:\d{2}$"#, options: .regularExpression) != nil }) else { return nil }
        var m = Moment(source: "screen")
        m.clock = t[ci]
        func num(_ i: Int) -> Int? { t.indices.contains(i) ? Int(t[i].trimmingCharacters(in: .whitespaces)) : nil }
        if let a = num(ci - 1), let b = num(ci + 1) { m.kills = [a, b] }
        return m
    }
}
