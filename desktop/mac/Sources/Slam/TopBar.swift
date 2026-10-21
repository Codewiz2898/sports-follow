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
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try? VNImageRequestHandler(cgImage: cg).perform([req])
        return (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
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
