import AppKit
import CoreImage
import CoreVideo
import Foundation
import Vision

/// Dota's own top bar read off a frame: the kills either side of the clock (Radiant left, Dire right). It's the
/// moment a clip carries when Slam's API has no live map for the game, and the clock it's compared with.
enum TopBar {
    private static let ci = CIContext()

    /// The text in the middle of the top bar, where Dota keeps its clock: that strip cut out and scaled 3×
    /// first, as the clock is only ~14 px high in a 900-high frame and Vision misses it at that size.
    static func texts(in pb: CVPixelBuffer) -> [String] {
        let img = CIImage(cvPixelBuffer: pb)
        let e = img.extent
        let crop = CGRect(x: e.minX + e.width * 0.4, y: e.maxY - e.height * 0.06, width: e.width * 0.2, height: e.height * 0.06)
        let big = img.cropped(to: crop).transformed(by: CGAffineTransform(translationX: -crop.minX, y: -crop.minY))
            .transformed(by: CGAffineTransform(scaleX: 3, y: 3))
        guard let cg = ci.createCGImage(big, from: big.extent) else { return [] }
        return read(cg)
    }

    private static func read(_ cg: CGImage) -> [String] {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try? VNImageRequestHandler(cgImage: cg).perform([req])
        return (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    }

    /// macOS sets Vision's text model up the first time each new build of the app reads text: 40 to 80 s on
    /// 21 Oct with Dota running, and the first clip waited for it. So it's done at launch, on a drawn clock,
    /// while the game is picked. A blank picture wouldn't do: with no text found, nothing is read.
    static func warmUp() -> [String] {
        let w = 240, h = 60
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return [] }
        ctx.setFillColor(CGColor(gray: 0, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSAttributedString(string: "12  15:22  9", attributes: [.font: NSFont.boldSystemFont(ofSize: 30),
                                                                 .foregroundColor: NSColor.white]).draw(at: CGPoint(x: 12, y: 12))
        NSGraphicsContext.restoreGraphicsState()
        guard let cg = ctx.makeImage() else { return [] }
        return read(cg)
    }

    /// The moment in what was read: ["10", "15:22", "5"] is Radiant 10, Dire 5, at 15:22.
    static func moment(from texts: [String]) -> Moment? {
        guard let ci = texts.firstIndex(where: { $0.range(of: #"^\d{1,2}:\d{2}$"#, options: .regularExpression) != nil }) else { return nil }
        var m = Moment(source: "screen")
        m.clock = texts[ci]
        func num(_ i: Int) -> Int? { texts.indices.contains(i) ? Int(texts[i].trimmingCharacters(in: .whitespaces)) : nil }
        if let a = num(ci - 1), let b = num(ci + 1) { m.kills = [a, b] }
        return m
    }

    static func moment(in pb: CVPixelBuffer) -> Moment? { moment(from: texts(in: pb)) }
}
