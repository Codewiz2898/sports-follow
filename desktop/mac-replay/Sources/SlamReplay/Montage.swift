import AVFoundation
import AppKit
import QuartzCore

/// One video from several saved clips: each trimmed to its last few seconds (a clip ends at the tap,
/// just after the moment), joined with straight cuts, a small title on each, and a Slam card at the end.
/// Kept to about a minute. Landscape as captured, or 9:16 with the game in a band across the middle.
enum Montage {
    struct Options {
        var each = 8.0
        var maxTotal = 60.0
        var card = 2.5
        var vertical = false
    }

    struct Result {
        let url: URL
        let seconds: Double
        let bytes: Int
        let ms: Double
        let clips: Int
    }

    /// Today's plain clips in ~/Movies/Slam, oldest first (not the replay cuts, montages or self-tests).
    static func todaysClips() -> [URL] {
        let fm = FileManager.default
        let day = Calendar.current.startOfDay(for: Date())
        let names = (try? fm.contentsOfDirectory(atPath: ClipWriter.folder.path)) ?? []
        return names.filter { $0.hasSuffix(".mp4") && !$0.contains(".replay.") && !$0.contains("montage")
                              && !$0.contains("selftest") }
            .map { ClipWriter.folder.appendingPathComponent($0) }
            .filter { ((try? fm.attributesOfItem(atPath: $0.path)[.creationDate] as? Date) ?? .distantPast) >= day }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// The title a clip carries: its stat line from the save, else "Clip n".
    static func title(for clip: URL, n: Int) -> String {
        let side = clip.deletingPathExtension().appendingPathExtension("json")
        if let data = try? Data(contentsOf: side),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let strip = obj["strip"] as? String, !strip.isEmpty {
            return strip
        }
        return "Clip \(n)"
    }

    static func make(clips all: [URL], to url: URL, opts: Options) async throws -> Result {
        let t0 = Date()
        guard !all.isEmpty else {
            throw NSError(domain: "Montage", code: 1, userInfo: [NSLocalizedDescriptionKey: "no clips"])
        }
        // Fit a minute: shorter cuts first (not under 4 s), then only the latest clips.
        let room = opts.maxTotal - opts.card
        var each = min(opts.each, room / Double(all.count))
        var clips = all
        if each < 4 {
            each = 4
            clips = Array(all.suffix(Int(room / 4)))
        }

        let comp = AVMutableComposition()
        guard let v = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
              let a = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw NSError(domain: "Montage", code: 2)
        }
        struct Segment { let range: CMTimeRange; let size: CGSize; let title: String }
        var segments: [Segment] = []
        var cursor = CMTime.zero
        var fps: Float = 30
        var lastVideo: (track: AVAssetTrack, end: CMTime, size: CGSize)?
        for (i, clip) in clips.enumerated() {
            let asset = AVURLAsset(url: clip)
            let duration = try await asset.load(.duration)
            guard let vt = try await asset.loadTracks(withMediaType: .video).first else { continue }
            let (size, rate) = try await vt.load(.naturalSize, .nominalFrameRate)
            fps = max(fps, rate)
            let take = CMTime(seconds: min(each, duration.seconds), preferredTimescale: 600)
            let src = CMTimeRange(start: duration - take, duration: take)
            try v.insertTimeRange(src, of: vt, at: cursor)
            lastVideo = (vt, duration, size)
            if let at = try await asset.loadTracks(withMediaType: .audio).first {
                try a.insertTimeRange(src, of: at, at: cursor)
            }
            segments.append(Segment(range: CMTimeRange(start: cursor, duration: take), size: size,
                                    title: "\(i + 1)/\(clips.count) · \(title(for: clip, n: i + 1))"))
            cursor = cursor + take
        }
        guard let first = segments.first else {
            throw NSError(domain: "Montage", code: 3, userInfo: [NSLocalizedDescriptionKey: "no video in the clips"])
        }
        let card = CMTime(seconds: opts.card, preferredTimescale: 600)
        let cardRange = CMTimeRange(start: cursor, duration: card)
        // Under the card, real video (an empty stretch would end the export early): the last clip's
        // closing seconds, hidden by the card. No sound under it.
        if let lv = lastVideo {
            let hold = CMTime(seconds: min(opts.card, lv.end.seconds), preferredTimescale: 600)
            try v.insertTimeRange(CMTimeRange(start: lv.end - hold, duration: hold), of: lv.track, at: cursor)
            if hold < card { v.insertEmptyTimeRange(CMTimeRange(start: cursor + hold, duration: card - hold)) }
        }
        let total = cursor + card

        let render = opts.vertical ? CGSize(width: 1080, height: 1920) : first.size
        // Where the game sits, in video coordinates (y down): the whole frame, or a band across a 9:16.
        func band(_ size: CGSize) -> CGRect {
            let s = min(render.width / size.width, render.height / size.height)
            let w = size.width * s, h = size.height * s
            let y = opts.vertical ? (render.height - h) / 2 - 80 : (render.height - h) / 2
            return CGRect(x: (render.width - w) / 2, y: y, width: w, height: h)
        }
        let vc = AVMutableVideoComposition()
        vc.renderSize = render
        vc.frameDuration = CMTime(value: 1, timescale: CMTimeScale(fps.rounded()))
        var instructions: [AVVideoCompositionInstructionProtocol] = []
        for seg in segments {
            let ins = AVMutableVideoCompositionInstruction()
            ins.timeRange = seg.range
            ins.backgroundColor = ClipEditor.inkSolid
            let li = AVMutableVideoCompositionLayerInstruction(assetTrack: v)
            let b = band(seg.size)
            li.setTransform(CGAffineTransform(scaleX: b.width / seg.size.width, y: b.height / seg.size.height)
                .concatenating(CGAffineTransform(translationX: b.minX, y: b.minY)), at: seg.range.start)
            ins.layerInstructions = [li]
            instructions.append(ins)
        }
        let cardIns = AVMutableVideoCompositionInstruction()
        cardIns.timeRange = cardRange
        cardIns.backgroundColor = ClipEditor.inkSolid
        if let lv = lastVideo {
            let li = AVMutableVideoCompositionLayerInstruction(assetTrack: v)
            let b = band(lv.size)
            li.setTransform(CGAffineTransform(scaleX: b.width / lv.size.width, y: b.height / lv.size.height)
                .concatenating(CGAffineTransform(translationX: b.minX, y: b.minY)), at: cardRange.start)
            cardIns.layerInstructions = [li]
        }
        instructions.append(cardIns)
        vc.instructions = instructions

        // Titles and the end card, in Core Animation (y up from the bottom).
        let parent = CALayer()
        parent.frame = CGRect(origin: .zero, size: render)
        let video = CALayer()
        video.frame = parent.frame
        parent.addSublayer(video)
        let unit = (opts.vertical ? render.width / 1.6 : render.height) / 900
        let T = total.seconds
        let firstBand = band(first.size)
        for seg in segments {
            let title = ClipEditor.textLayer(seg.title, size: (opts.vertical ? 44 : 26) * unit, color: .white,
                                             frame: .zero)
            let w = min(render.width - 48 * unit, title.bounds.width + 28 * unit)
            let h = (opts.vertical ? 64 : 40) * unit
            let chip = CALayer()
            if opts.vertical {  // above the band, centred
                chip.frame = CGRect(x: (render.width - w) / 2, y: render.height - firstBand.minY + 24 * unit, width: w, height: h)
            } else {  // top left, under Dota's own menu icons and K/D/A box
                chip.frame = CGRect(x: 22 * unit, y: render.height - 112 * unit - h, width: w, height: h)
            }
            chip.backgroundColor = ClipEditor.ink
            chip.cornerRadius = 8 * unit
            title.frame = CGRect(x: 14 * unit, y: (h - title.bounds.height) / 2, width: w - 28 * unit, height: title.bounds.height)
            chip.addSublayer(title)
            chip.opacity = 0
            let s = seg.range.start.seconds
            chip.add(ClipEditor.visible(from: s, to: min(s + 2.5, seg.range.end.seconds), total: T), forKey: "show")
            parent.addSublayer(chip)
        }
        if opts.vertical {
            let logo = ClipEditor.textLayer("slam", size: 64 * unit, color: NSColor(cgColor: ClipEditor.blue) ?? .systemBlue,
                                            frame: CGRect(x: 0, y: render.height - firstBand.maxY - 140 * unit, width: render.width, height: 90 * unit),
                                            center: true, font: "Fredoka")
            parent.addSublayer(logo)
        }
        parent.addSublayer(endCard(size: render, unit: unit, from: cardRange.start.seconds, total: T))

        vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: video, in: parent)
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHighestQuality) else {
            throw NSError(domain: "Montage", code: 4)
        }
        export.videoComposition = vc
        try await export.export(to: url, as: .mp4)
        let bytes = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        return Result(url: url, seconds: T, bytes: bytes, ms: Date().timeIntervalSince(t0) * 1000, clips: segments.count)
    }

    /// The last 2.5 s: paper, the Slam name in its blue, and one line.
    private static func endCard(size: CGSize, unit: CGFloat, from: Double, total: Double) -> CALayer {
        let card = CALayer()
        card.frame = CGRect(origin: .zero, size: size)
        card.backgroundColor = ClipEditor.paper
        let logo = ClipEditor.textLayer("slam", size: 150 * unit, color: NSColor(cgColor: ClipEditor.blue) ?? .systemBlue,
                                        frame: CGRect(x: 0, y: size.height / 2 - 40 * unit, width: size.width, height: 190 * unit),
                                        center: true, font: "Fredoka")
        let line = ClipEditor.textLayer("Clipped on Slam", size: 34 * unit, color: NSColor(white: 0.05, alpha: 1),
                                        frame: CGRect(x: 0, y: size.height / 2 - 110 * unit, width: size.width, height: 56 * unit),
                                        center: true, font: "Manrope")
        card.addSublayer(logo)
        card.addSublayer(line)
        card.opacity = 0
        card.add(ClipEditor.visible(from: from, to: total, total: total), forKey: "show")
        return card
    }
}
