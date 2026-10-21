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

    static func make(from source: URL, to url: URL, strip: String?, replaySeconds: Double = 5,
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
        let total = duration + slow

        let fps = rate > 0 ? Int32(rate.rounded()) : 30
        let vc = try await AVMutableVideoComposition.videoComposition(withPropertiesOf: comp)
        vc.renderSize = size
        vc.frameDuration = CMTime(value: 1, timescale: fps)
        let (parent, video) = overlay(size: size, strip: strip, replayFrom: duration.seconds, total: total.seconds)
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

    /// Core Animation layers over the video: the strip along the bottom the whole time, the REPLAY tag
    /// top left only while the slow part plays. y goes up from the bottom here.
    private static func overlay(size: CGSize, strip: String?, replayFrom: Double, total: Double) -> (CALayer, CALayer) {
        let parent = CALayer()
        parent.frame = CGRect(origin: .zero, size: size)
        let video = CALayer()
        video.frame = parent.frame
        parent.addSublayer(video)
        let unit = size.height / 900  // sizes below are for a 900-high frame

        if let strip, !strip.isEmpty {
            let h = 54 * unit
            let bar = CALayer()
            bar.frame = CGRect(x: 0, y: 0, width: size.width, height: h)
            bar.backgroundColor = ink
            parent.addSublayer(bar)
            bar.addSublayer(text(strip, size: 30 * unit, color: .white,
                                 frame: CGRect(x: 24 * unit, y: 9 * unit, width: size.width - 200 * unit, height: 40 * unit)))
            let tag = CALayer()
            tag.frame = CGRect(x: size.width - 132 * unit, y: 10 * unit, width: 112 * unit, height: 34 * unit)
            tag.backgroundColor = blue
            tag.cornerRadius = 8 * unit
            tag.addSublayer(text("SLAM", size: 24 * unit, color: .white, frame: tag.bounds.insetBy(dx: 0, dy: 2 * unit), center: true))
            bar.addSublayer(tag)
        }

        let replay = CALayer()
        replay.frame = CGRect(x: 24 * unit, y: size.height - 76 * unit, width: 230 * unit, height: 52 * unit)
        replay.backgroundColor = red
        replay.cornerRadius = 8 * unit
        replay.addSublayer(text("REPLAY ½×", size: 32 * unit, color: .white, frame: replay.bounds.insetBy(dx: 0, dy: 4 * unit), center: true))
        replay.opacity = 0
        let show = CAKeyframeAnimation(keyPath: "opacity")
        show.values = [0, 0, 1, 1]
        show.keyTimes = [0, NSNumber(value: replayFrom / total), NSNumber(value: replayFrom / total), 1]
        show.calculationMode = .discrete
        show.beginTime = AVCoreAnimationBeginTimeAtZero
        show.duration = total
        show.isRemovedOnCompletion = false
        show.fillMode = .forwards
        replay.add(show, forKey: "show")
        parent.addSublayer(replay)
        return (parent, video)
    }

    /// Text drawn into an image (a CATextLayer comes out blank in an export).
    private static func text(_ s: String, size: CGFloat, color: NSColor, frame: CGRect, center: Bool = false) -> CALayer {
        let layer = CALayer()
        layer.frame = frame
        let w = Int(frame.width.rounded(.up)), h = Int(frame.height.rounded(.up))
        guard w > 0, h > 0, let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                               space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                               bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return layer }
        let font = NSFont(name: "Impact", size: size) ?? NSFont.boldSystemFont(ofSize: size)
        let para = NSMutableParagraphStyle()
        para.alignment = center ? .center : .left
        para.lineBreakMode = .byTruncatingTail
        let str = NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color, .paragraphStyle: para])
        let gc = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = gc
        let lineH = font.ascender - font.descender
        str.draw(in: NSRect(x: 0, y: (CGFloat(h) - lineH) / 2, width: CGFloat(w), height: lineH))
        NSGraphicsContext.restoreGraphicsState()
        layer.contents = ctx.makeImage()
        layer.contentsGravity = .resize
        return layer
    }
}
