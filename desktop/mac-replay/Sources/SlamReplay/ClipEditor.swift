import AVFoundation
import AppKit
import QuartzCore

/// The shareable cut of a saved clip: the clip, then its last few seconds again at half speed under a
/// "REPLAY" tag, with the stat strip burned in along the bottom. A re-encode (the hardware encoder,
/// through AVAssetExportSession), done after the plain clip is already on disk.
enum ClipEditor {
    struct Result {
        let url: URL
        let seconds: Double
        let bytes: Int
        let ms: Double
    }

    static let ink = NSColor(srgbRed: 0x0d / 255, green: 0x0d / 255, blue: 0x0d / 255, alpha: 0.88).cgColor
    static let red = NSColor(srgbRed: 0xdc / 255, green: 0x20 / 255, blue: 0x16 / 255, alpha: 1).cgColor
    static let blue = NSColor(srgbRed: 0x1f / 255, green: 0x5b / 255, blue: 0xef / 255, alpha: 1).cgColor
    static let inkSolid = NSColor(srgbRed: 0x0d / 255, green: 0x0d / 255, blue: 0x0d / 255, alpha: 1).cgColor
    static let paper = NSColor(srgbRed: 0xf3 / 255, green: 0xf3 / 255, blue: 0xef / 255, alpha: 1).cgColor

    static let endSeconds = 2.0
    static var endStyle = Overlays.EndStyle.stamp

    /// The cut: the clip with the title card over its first 2 s and the scorebug after, then the last
    /// `replaySeconds` at half speed (the slate as it starts, cinema bars through it), then the end card.
    static func make(from source: URL, to url: URL, moment: Moment, replaySeconds: Double = 5,
                     slowdown: Double = 2) async throws -> Result {
        let t0 = Date()
        let asset = AVURLAsset(url: source)
        let duration = try await asset.load(.duration)
        guard let vSrc = try await asset.loadTracks(withMediaType: .video).first else {
            throw NSError(domain: "ClipEditor", code: 1, userInfo: [NSLocalizedDescriptionKey: "no video"])
        }
        let aSrc = try await asset.loadTracks(withMediaType: .audio).first
        let (size, rate) = try await vSrc.load(.naturalSize, .nominalFrameRate)

        let comp = AVMutableComposition()
        guard let v = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw NSError(domain: "ClipEditor", code: 2)
        }
        let a = aSrc == nil ? nil : comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
        let whole = CMTimeRange(start: .zero, duration: duration)
        let tailLength = CMTime(seconds: min(replaySeconds, duration.seconds), preferredTimescale: 600)
        let tail = CMTimeRange(start: duration - tailLength, duration: tailLength)
        try v.insertTimeRange(whole, of: vSrc, at: .zero)
        try v.insertTimeRange(tail, of: vSrc, at: duration)
        if let a, let aSrc {
            try a.insertTimeRange(whole, of: aSrc, at: .zero)
            try a.insertTimeRange(tail, of: aSrc, at: duration)
        }
        let slow = CMTime(seconds: tailLength.seconds * slowdown, preferredTimescale: 600)
        comp.scaleTimeRange(CMTimeRange(start: duration, duration: tailLength), toDuration: slow)
        // Under the end card, real video (an empty stretch would end the export early), hidden by the card.
        let end = CMTime(seconds: min(endSeconds, duration.seconds), preferredTimescale: 600)
        try v.insertTimeRange(CMTimeRange(start: duration - end, duration: end), of: vSrc, at: duration + slow)
        let total = duration + slow + end
        try await Sting.add(to: a, style: endStyle, at: duration + slow, seconds: end.seconds)

        let fps = rate > 0 ? Int32(rate.rounded()) : 30
        let vc = try await AVMutableVideoComposition.videoComposition(withPropertiesOf: comp)
        vc.renderSize = size
        vc.frameDuration = CMTime(value: 1, timescale: fps)
        let (parent, video) = overlay(size: size, moment: moment, clip: duration.seconds,
                                      slowEnd: (duration + slow).seconds, total: total.seconds)
        vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: video, in: parent)

        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHighestQuality) else {
            throw NSError(domain: "ClipEditor", code: 3, userInfo: [NSLocalizedDescriptionKey: "no export session"])
        }
        export.videoComposition = vc
        export.audioTimePitchAlgorithm = .spectral  // the slow part keeps its pitch
        try await export.export(to: url, as: .mp4)
        let bytes = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        return Result(url: url, seconds: total.seconds, bytes: bytes, ms: Date().timeIntervalSince(t0) * 1000)
    }

    /// Core Animation layers over the video, each shown for its stretch.
    private static func overlay(size: CGSize, moment m: Moment, clip: Double, slowEnd: Double, total: Double) -> (CALayer, CALayer) {
        let parent = CALayer()
        parent.frame = CGRect(origin: .zero, size: size)
        let video = CALayer()
        video.frame = parent.frame
        parent.addSublayer(video)
        let o = Overlays(W: size.width, H: size.height)
        func add(_ l: CALayer?, _ from: Double, _ to: Double) {
            guard let l, to > from else { return }
            l.opacity = 0
            l.add(visible(from: from, to: to, total: total), forKey: "show")
            parent.addSublayer(l)
        }
        let card = clip > 6 ? 2.0 : 0
        add(o.scorebug(m), card, clip)
        add(o.cinemaBars(m), clip, slowEnd)
        add(o.slate(), clip, min(clip + 0.8, slowEnd))
        if card > 0 { add(o.titleCard(m), 0, card) }
        let line = m.isEmpty ? nil : ([m.kills.map { k in m.teams.map { "\($0[0]) \(k[0])–\(k[1]) \($0[1])" } ?? "\(k[0])–\(k[1])" }, "Dota 2",
                                         m.game.map { "Game \($0)" }].compactMap { $0 }.joined(separator: " · "))
        add(o.endCard(endStyle, line: line, at: slowEnd), slowEnd, total)
        return (parent, video)
    }

    /// Opacity 1 from `from` to `to` (seconds into the video), 0 otherwise.
    static func visible(from: Double, to: Double, total: Double) -> CAKeyframeAnimation {
        let a = CAKeyframeAnimation(keyPath: "opacity")
        a.calculationMode = .discrete
        a.values = [0, 1, 0]  // discrete: one value fewer than key times
        a.keyTimes = [0, NSNumber(value: max(0, from / total)), NSNumber(value: min(1, to / total)), 1]
        a.beginTime = AVCoreAnimationBeginTimeAtZero
        a.duration = total
        a.isRemovedOnCompletion = false
        a.fillMode = .forwards
        return a
    }

    /// Text drawn into an image (a CATextLayer comes out blank in an export). A zero frame sizes the
    /// layer to the text. Fonts: the app's own copies of Anton, Manrope and Fredoka when Fonts.register()
    /// found them, else Impact and the system's bold.
    static func textLayer(_ s: String, size: CGFloat, color: NSColor, frame: CGRect, center: Bool = false,
                          font name: String = "Anton", kern: CGFloat = 0) -> CALayer {
        let font = Fonts.named(name, size: size) ?? (name == "Anton" ? NSFont(name: "Impact", size: size) : nil)
            ?? NSFont.boldSystemFont(ofSize: size)
        let para = NSMutableParagraphStyle()
        para.alignment = center ? .center : .left
        para.lineBreakMode = .byTruncatingTail
        let str = NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color, .paragraphStyle: para, .kern: kern])
        let lineH = (font.ascender - font.descender).rounded(.up)
        var frame = frame
        if frame == .zero { frame.size = CGSize(width: str.size().width.rounded(.up) + 2, height: lineH + 4) }
        let layer = CALayer()
        layer.frame = frame
        let w = Int(frame.width.rounded(.up)), h = Int(frame.height.rounded(.up))
        guard w > 0, h > 0, let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                               space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                               bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return layer }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
        str.draw(in: NSRect(x: 0, y: (CGFloat(h) - lineH) / 2, width: CGFloat(w), height: lineH))
        NSGraphicsContext.restoreGraphicsState()
        layer.contents = ctx.makeImage()
        layer.contentsGravity = center ? .center : .left
        layer.masksToBounds = true
        return layer
    }
}
