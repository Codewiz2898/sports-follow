import AVFoundation
import AppKit

/// Publishing a cut to Slam (the server's clips.py): a share-size copy and a poster frame are made here, the
/// server is asked for upload URLs, both files go straight into its bucket, and the server is told they're up.
struct Publisher {
    let server: URL
    let key: String

    struct Published: Decodable {
        let id: String
        let video: String
        let poster: String
        let match: String?
    }

    struct Failure: LocalizedError {
        let message: String
        var status = 0  // the server's answer, when it gave one
        var errorDescription: String? { message }
    }

    /// What the server is told of the clip (POST /api/clips).
    struct Request: Encodable {
        let match: String?
        let moment: [String: AnyCodable]?
        let caption: String
        let source: String
        let seconds: Double
        let bytes: Int
        let width: Int
        let height: Int
    }

    private struct Upload: Decodable {
        let id: String
        let video_put: String
        let poster_put: String
        let video_type: String
        let poster_type: String
    }

    /// Publishes `cut`; `posterAt` is the second its poster frame is taken from (the replay's action).
    func publish(cut: URL, moment: Moment?, match: String?, caption: String, source: ClipSource, posterAt: Double,
                 progress: @escaping @Sendable (String, Double) -> Void) async throws -> Published {
        guard source.publishable else {
            throw Failure(message: "Clips recorded from a browser (a broadcast) aren't published yet: save or share it instead.")
        }
        progress("Making a share-size copy…", 0.05)
        let small = try await Publisher.shareSize(cut)
        defer { try? FileManager.default.removeItem(at: small.url) }
        let poster = try await Publisher.poster(of: small.url, at: posterAt)
        defer { try? FileManager.default.removeItem(at: poster) }

        progress("Asking Slam for upload links…", 0.15)
        let body = Request(match: match, moment: moment.map(Publisher.momentBody), caption: caption,
                           source: source.rawValue, seconds: small.seconds, bytes: small.bytes,
                           width: Int(small.size.width), height: Int(small.size.height))
        let up: Upload = try await call("POST", "/api/clips", body: try JSONEncoder().encode(body))

        progress("Uploading…", 0.2)
        try await put(small.url, to: up.video_put, type: up.video_type) { sent in progress("Uploading…", 0.2 + 0.72 * sent) }
        try await put(poster, to: up.poster_put, type: up.poster_type) { _ in }
        progress("Putting it live…", 0.95)
        // Asked again if the answer is lost on the way: the server takes it twice.
        var done: Published?
        for attempt in 0..<3 where done == nil {
            do {
                done = try await call("POST", "/api/clips/\(up.id)/done", body: nil)
            } catch let e as URLError where attempt < 2 {
                _ = e
                try await Task.sleep(nanoseconds: UInt64(attempt + 1) * 1_000_000_000)
            }
        }
        guard let done else { throw Failure(message: "Slam didn't confirm the clip.") }
        progress("Published", 1)
        return done
    }

    /// Whether the key works: an empty clip is refused for what's missing (400) once the key passes.
    func check() async -> String {
        do {
            let _: Upload = try await call("POST", "/api/clips", body: Data("{}".utf8))
            return "Key works."
        } catch let f as Failure {
            return f.status == 400 ? "Key works." : f.message
        } catch {
            return error.localizedDescription
        }
    }

    // MARK: the server

    private func call<T: Decodable>(_ method: String, _ path: String, body: Data?) async throws -> T {
        var req = URLRequest(url: server.appendingPathComponent(path), timeoutInterval: 30)
        req.httpMethod = method
        req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("SlamMac/1", forHTTPHeaderField: "User-Agent")
        req.httpBody = body
        let (data, resp) = try await URLSession.shared.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw Failure(message: status == 401 ? "Slam didn't take the publishing key: check it in Publishing settings."
                                                 : detail ?? "Slam answered \(status).", status: status)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// A PUT of a file to a signed URL, its Content-Type as it was signed for; tried three times.
    private func put(_ file: URL, to url: String, type: String, sent: @escaping @Sendable (Double) -> Void) async throws {
        guard let target = URL(string: url) else { throw Failure(message: "Slam gave a bad upload link.") }
        var req = URLRequest(url: target, timeoutInterval: 120)
        req.httpMethod = "PUT"
        req.setValue(type, forHTTPHeaderField: "Content-Type")
        // Tried again only when it might work next time: the network, or the bucket's own trouble (5xx). A refusal
        // (a signature or size that doesn't match, a link run out) won't.
        for attempt in 0..<3 {
            let last = attempt == 2
            do {
                let (_, resp) = try await URLSession.shared.upload(for: req, fromFile: file, delegate: Progress(sent: sent))
                let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                if (200..<300).contains(status) { return }
                if status < 500 || last { throw Failure(message: "The upload was refused (\(status)).", status: status) }
            } catch let e as URLError where !last {
                _ = e
            }
            try await Task.sleep(nanoseconds: UInt64(attempt + 1) * 1_000_000_000)
        }
        throw Failure(message: "The upload failed.")
    }

    private final class Progress: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
        let sent: @Sendable (Double) -> Void
        init(sent: @escaping @Sendable (Double) -> Void) { self.sent = sent }
        func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData _: Int64, totalBytesSent: Int64,
                        totalBytesExpectedToSend: Int64) {
            if totalBytesExpectedToSend > 0 { sent(Double(totalBytesSent) / Double(totalBytesExpectedToSend)) }
        }
    }

    // MARK: the files

    static func momentBody(_ m: Moment) -> [String: AnyCodable] {
        var out: [String: AnyCodable] = ["line": AnyCodable(m.line), "source": AnyCodable(m.source)]
        if let t = m.teams { out["teams"] = AnyCodable(t) }
        if let k = m.kills { out["kills"] = AnyCodable(k) }
        if let g = m.gold { out["gold"] = AnyCodable(g) }
        if let c = m.clock { out["clock"] = AnyCodable(c) }
        if let n = m.game { out["game"] = AnyCodable(n) }
        return out
    }

    struct Small {
        let url: URL
        let seconds: Double
        let bytes: Int
        let size: CGSize
    }

    static let shareBitrate = 5_000_000

    /// A copy small enough to share and stream: 720p at most, H.264 at 5 Mbps (an export preset caps the size
    /// but not the bitrate: a 60 fps cut came out at 11.7 Mbps), its sound passed through as it is.
    static func shareSize(_ cut: URL) async throws -> Small {
        let asset = AVURLAsset(url: cut)
        guard let vt = try await asset.loadTracks(withMediaType: .video).first else { throw Failure(message: "The clip has no picture.") }
        let at = try await asset.loadTracks(withMediaType: .audio).first
        let natural = try await vt.load(.naturalSize)
        let fit = min(1, 1280 / natural.width, 720 / natural.height)
        let w = Int((natural.width * fit / 2).rounded()) * 2, h = Int((natural.height * fit / 2).rounded()) * 2
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("slam-share-\(UUID().uuidString).mp4")

        let reader = try AVAssetReader(asset: asset)
        let vOut = AVAssetReaderTrackOutput(track: vt, outputSettings: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
        reader.add(vOut)
        var aOut: AVAssetReaderTrackOutput?
        var aHint: CMFormatDescription?
        if let at {
            let o = AVAssetReaderTrackOutput(track: at, outputSettings: nil)  // the AAC as it is
            reader.add(o)
            aOut = o
            aHint = try await at.load(.formatDescriptions).first
        }
        let writer = try AVAssetWriter(outputURL: out, fileType: .mp4)
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
            AVVideoScalingModeKey: AVVideoScalingModeResizeAspect,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: shareBitrate, AVVideoMaxKeyFrameIntervalDurationKey: 2,
                                              AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel],
        ])
        vIn.expectsMediaDataInRealTime = false
        writer.add(vIn)
        var aIn: AVAssetWriterInput?
        if aOut != nil {
            let i = AVAssetWriterInput(mediaType: .audio, outputSettings: nil, sourceFormatHint: aHint)
            i.expectsMediaDataInRealTime = false
            writer.add(i)
            aIn = i
        }
        guard reader.startReading(), writer.startWriting() else {
            throw Failure(message: "Couldn't make a share-size copy: \((writer.error ?? reader.error)?.localizedDescription ?? "?")")
        }
        writer.startSession(atSourceTime: .zero)
        let queue = DispatchQueue(label: "share.copy")
        let group = DispatchGroup()
        func pump(_ input: AVAssetWriterInput, _ output: AVAssetReaderTrackOutput) {
            group.enter()
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    guard let sb = output.copyNextSampleBuffer(), input.append(sb) else {
                        input.markAsFinished()
                        group.leave()
                        return
                    }
                }
            }
        }
        pump(vIn, vOut)
        if let aIn, let aOut { pump(aIn, aOut) }
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in group.notify(queue: queue) { c.resume() } }
        await writer.finishWriting()
        guard writer.status == .completed, reader.status != .failed else {
            throw Failure(message: "Couldn't make a share-size copy: \((writer.error ?? reader.error)?.localizedDescription ?? "?")")
        }
        let small = AVURLAsset(url: out)
        let seconds = try await small.load(.duration).seconds
        let bytes = (try? FileManager.default.attributesOfItem(atPath: out.path)[.size] as? Int) ?? 0
        return Small(url: out, seconds: seconds, bytes: bytes, size: CGSize(width: w, height: h))
    }

    /// A JPEG of one frame, at most 1280 wide.
    static func poster(of video: URL, at second: Double) async throws -> URL {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: video))
        gen.maximumSize = CGSize(width: 1280, height: 1280)
        gen.appliesPreferredTrackTransform = true
        let (image, _) = try await gen.image(at: CMTime(seconds: max(0, second), preferredTimescale: 600))
        guard let jpeg = NSBitmapImageRep(cgImage: image).representation(using: .jpeg, properties: [.compressionFactor: 0.82]) else {
            throw Failure(message: "Couldn't make the poster frame.")
        }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("slam-poster-\(UUID().uuidString).jpg")
        try jpeg.write(to: out)
        return out
    }
}

/// Just enough of "any JSON value" to send a moment's mixed fields.
struct AnyCodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}
