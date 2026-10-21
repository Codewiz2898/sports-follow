import AVFoundation
import CoreMedia

/// Writes a ring snapshot to an MP4 with no re-encode: both inputs pass the samples through.
enum ClipWriter {
    static let folder = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Movies/Slam", isDirectory: true)

    struct Result {
        let url: URL
        let seconds: Double
        let bytes: Int
        let writeMs: Double
        let videoFrames: Int
        let audioPackets: Int
    }

    static func newURL(label: String) -> URL {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var url = folder.appendingPathComponent("Slam-\(label)-\(f.string(from: Date())).mp4")
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = folder.appendingPathComponent("Slam-\(label)-\(f.string(from: Date()))-\(n).mp4")
            n += 1
        }
        return url
    }

    static func write(_ snap: ReplayRing.Snapshot, to url: URL) async throws -> Result {
        let t0 = DispatchTime.now()
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: nil, sourceFormatHint: snap.videoFormat)
        vIn.expectsMediaDataInRealTime = false
        writer.add(vIn)
        var aIn: AVAssetWriterInput?
        if let af = snap.audioFormat, !snap.audio.isEmpty {
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: nil, sourceFormatHint: af)
            input.expectsMediaDataInRealTime = false
            writer.add(input)
            aIn = input
        }
        guard writer.startWriting() else { throw writer.error ?? NSError(domain: "ClipWriter", code: 1) }
        let start = snap.video[0].presentationTimeStamp
        writer.startSession(atSourceTime: start)

        // The usual pull loop: each input takes samples while it's ready, on one queue.
        let queue = DispatchQueue(label: "clip.writer")
        let group = DispatchGroup()
        func feed(_ input: AVAssetWriterInput, _ samples: [CMSampleBuffer]) {
            group.enter()
            var i = 0
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData && i < samples.count {
                    if !input.append(samples[i]) {
                        i = samples.count
                        break
                    }
                    i += 1
                }
                if i >= samples.count {
                    input.markAsFinished()
                    group.leave()
                }
            }
        }
        feed(vIn, snap.video)
        if let aIn { feed(aIn, snap.audio) }
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            group.notify(queue: queue) { c.resume() }
        }
        if let last = snap.video.last {
            let lastDur = CMTime(value: 1, timescale: 30)
            writer.endSession(atSourceTime: last.presentationTimeStamp + lastDur)
        }
        await writer.finishWriting()
        if writer.status != .completed {
            throw writer.error ?? NSError(domain: "ClipWriter", code: 2,
                                          userInfo: [NSLocalizedDescriptionKey: "writer status \(writer.status.rawValue)"])
        }
        let ms = Double(DispatchTime.now().uptimeNanoseconds - t0.uptimeNanoseconds) / 1e6
        let bytes = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        return Result(url: url, seconds: snap.seconds, bytes: bytes, writeMs: ms,
                      videoFrames: snap.video.count, audioPackets: snap.audio.count)
    }
}
