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

    /// What a clip's save knew (its sidecar), if anything.
    static func moment(for clip: URL) -> Moment? {
        let side = clip.deletingPathExtension().appendingPathExtension("json")
        guard let data = try? Data(contentsOf: side),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let m = obj["moment"], let md = try? JSONSerialization.data(withJSONObject: m) else { return nil }
        return try? JSONDecoder().decode(Moment.self, from: md)
    }

    /// The title a clip carries: its moment ("TY 21–18 GL · 26:00"), else "Clip n".
    static func title(for clip: URL, n: Int) -> String {
        if let m = moment(for: clip), !m.line.isEmpty { return m.line }
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
                                    title: title(for: clip, n: i + 1)))
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
        let T = total.seconds
        let firstBand = band(first.size)
        var o = Overlays(W: render.width, H: render.height)
        if opts.vertical { o.unit = render.width / 960 * 1.6 }
        for (i, seg) in segments.enumerated() {
            let chip = o.montageChip(n: i + 1, of: segments.count, title: seg.title)
            if opts.vertical {  // above the band, centred
                chip.frame.origin = CGPoint(x: (render.width - chip.bounds.width) / 2,
                                            y: render.height - firstBand.minY + 28 * o.u)
            }
            chip.opacity = 0
            let s = seg.range.start.seconds
            chip.add(ClipEditor.visible(from: s, to: min(s + 2.5, seg.range.end.seconds), total: T), forKey: "show")
            parent.addSublayer(chip)
        }
        if opts.vertical {
            let logo = ClipEditor.textLayer("slam", size: 64 * o.u, color: Overlays.blue, frame: .zero, font: "Fredoka")
            logo.frame.origin = CGPoint(x: (render.width - logo.bounds.width) / 2,
                                        y: render.height - firstBand.maxY - 60 * o.u - logo.bounds.height)
            parent.addSublayer(logo)
        }
        // One match across the clips: its name on the end card.
        let kickers = Set(clips.compactMap { moment(for: $0) }.filter { $0.teams != nil }.map { "\($0.headline) · \($0.kicker)" })
        let end = o.endCard(ClipEditor.endStyle, line: kickers.count == 1 ? kickers.first : nil, at: cardRange.start.seconds)
        end.opacity = 0
        end.add(ClipEditor.visible(from: cardRange.start.seconds, to: T, total: T), forKey: "show")
        parent.addSublayer(end)

        vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: video, in: parent)
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHighestQuality) else {
            throw NSError(domain: "Montage", code: 4)
        }
        export.videoComposition = vc
        try await export.export(to: url, as: .mp4)
        let bytes = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        return Result(url: url, seconds: T, bytes: bytes, ms: Date().timeIntervalSince(t0) * 1000, clips: segments.count)
    }

}
