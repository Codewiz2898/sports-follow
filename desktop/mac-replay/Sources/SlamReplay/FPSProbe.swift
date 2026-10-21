import AppKit
import ScreenCaptureKit
import Vision

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
