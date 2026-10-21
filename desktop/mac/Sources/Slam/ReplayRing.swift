import CoreMedia

/// The last N seconds of encoded samples, in memory only. Video is kept as whole GOPs (keyframe to
/// keyframe) so a clip always starts on a keyframe; the oldest GOP goes once the next one alone still
/// covers N seconds, so a clip is N to N+2 s long. Audio packets older than the first kept frame go too.
final class ReplayRing {
    struct Snapshot {
        let video: [CMSampleBuffer]
        let audio: [CMSampleBuffer]
        let videoFormat: CMFormatDescription
        let audioFormat: CMFormatDescription?
        var seconds: Double {
            guard let a = video.first?.presentationTimeStamp, let b = video.last?.presentationTimeStamp else { return 0 }
            return (b - a).seconds
        }
    }

    private struct GOP {
        var samples: [CMSampleBuffer]
        var bytes: Int
        var start: CMTime
    }

    let window: Double
    private let lock = NSLock()
    private var gops: [GOP] = []
    private var audio: [CMSampleBuffer] = []
    private var audioHead = 0  // index of the first live audio packet (dropping from the front lazily)
    private var videoBytes = 0
    private var audioBytes = 0
    private var videoFormat: CMFormatDescription?
    private var audioFormat: CMFormatDescription?
    private(set) var resets = 0

    init(seconds: Double) { window = seconds }

    func appendVideo(_ sb: CMSampleBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard let fmt = sb.formatDescription else { return }
        if let videoFormat, !CMFormatDescriptionEqual(videoFormat, otherFormatDescription: fmt) {
            // New size or codec settings: what we hold can't go in the same track. Start over.
            gops.removeAll(); videoBytes = 0; resets += 1
        }
        videoFormat = fmt
        let size = sb.byteCount
        if sb.isKeyframe {
            gops.append(GOP(samples: [sb], bytes: size, start: sb.presentationTimeStamp))
        } else if !gops.isEmpty {
            gops[gops.count - 1].samples.append(sb)
            gops[gops.count - 1].bytes += size
        } else {
            return  // wait for the first keyframe
        }
        videoBytes += size
        let newest = sb.presentationTimeStamp.seconds
        while gops.count > 1, gops[1].start.seconds <= newest - window {
            videoBytes -= gops[0].bytes
            gops.removeFirst()
        }
        trimAudio()
    }

    func appendAudio(_ sb: CMSampleBuffer) {
        lock.lock(); defer { lock.unlock() }
        audioFormat = sb.formatDescription
        audio.append(sb)
        audioBytes += sb.byteCount
        trimAudio()
    }

    private func trimAudio() {
        // Keep audio from just before the first kept frame; with no video, the last window of audio.
        let cutoff: Double
        if let first = gops.first?.start.seconds {
            cutoff = first - 0.1
        } else if let last = audio.last?.presentationTimeStamp.seconds {
            cutoff = last - window
        } else { return }
        while audioHead < audio.count, audio[audioHead].presentationTimeStamp.seconds < cutoff {
            audioBytes -= audio[audioHead].byteCount
            audioHead += 1
        }
        if audioHead > 512 {
            audio.removeFirst(audioHead)
            audioHead = 0
        }
    }

    func snapshot() -> Snapshot? {
        lock.lock(); defer { lock.unlock() }
        guard let videoFormat, !gops.isEmpty else { return nil }
        let video = gops.flatMap(\.samples)
        let start = video[0].presentationTimeStamp.seconds
        let a = audio[audioHead...].filter { $0.presentationTimeStamp.seconds >= start - 0.001 }
        return Snapshot(video: video, audio: Array(a), videoFormat: videoFormat, audioFormat: audioFormat)
    }

    struct Stats {
        let seconds: Double
        let videoMB: Double
        let audioMB: Double
        let gops: Int
        let frames: Int
    }

    func stats() -> Stats {
        lock.lock(); defer { lock.unlock() }
        var secs = 0.0
        if let a = gops.first?.start.seconds, let b = gops.last?.samples.last?.presentationTimeStamp.seconds {
            secs = b - a
        }
        return Stats(seconds: secs, videoMB: Double(videoBytes) / 1_048_576, audioMB: Double(audioBytes) / 1_048_576,
                     gops: gops.count, frames: gops.reduce(0) { $0 + $1.samples.count })
    }
}
