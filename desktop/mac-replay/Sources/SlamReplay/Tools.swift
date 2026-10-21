import AVFoundation
import CoreMedia
import CoreVideo

/// `--selftest`: everything but the capture. Synthetic 1600×900 frames and a 440 Hz tone go through
/// the same encoders, ring and writer, then the clip is read back. Needs no permission.
enum SelfTest {
    static func run(seconds: Int, hevc: Bool) async throws {
        let w = 1600, h = 900, fps = 60
        let ring = ReplayRing(seconds: 30)
        let video = try VideoEncoder(width: w, height: h, hevc: hevc, bitrate: 10_000_000, fps: fps,
                                     realTime: false) { ring.appendVideo($0) }
        let audio = try AudioEncoder { ring.appendAudio($0) }
        print("hardware encoder: \(video.hardware)")
        let attrs: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
            kCVPixelBufferWidthKey: w, kCVPixelBufferHeightKey: h,
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
        ]
        var pool: CVPixelBufferPool?
        CVPixelBufferPoolCreate(nil, nil, attrs as CFDictionary, &pool)
        let t0 = CMClockGetTime(CMClockGetHostTimeClock())
        let perFrame = Int(AudioEncoder.rate) / fps
        var phase = 0.0
        var l = [Float](repeating: 0, count: perFrame), r = l
        let started = Date()
        for i in 0..<(seconds * fps) {
            var pb: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool!, &pb)
            guard let pb else { continue }
            paint(pb, frame: i)
            let pts = t0 + CMTime(value: CMTimeValue(i), timescale: CMTimeScale(fps))
            video.encode(pb, pts: pts, duration: CMTime(value: 1, timescale: CMTimeScale(fps)))
            for k in 0..<perFrame {
                // a tone, with one second of silence every fifth second
                let on = (i / fps) % 5 != 4
                let s = on ? Float(sin(phase)) * 0.25 : 0
                phase += 2 * .pi * 440 / AudioEncoder.rate
                l[k] = s; r[k] = s
            }
            l.withUnsafeBufferPointer { lp in
                r.withUnsafeBufferPointer { rp in
                    audio.push(pts: t0 + CMTime(value: CMTimeValue(i * perFrame), timescale: CMTimeScale(AudioEncoder.rate)),
                               left: lp, right: rp)
                }
            }
        }
        video.finish()
        let encodeS = Date().timeIntervalSince(started)
        let st = ring.stats()
        print(String(format: "encoded %d s in %.2f s (%.0f fps); ring %.1f s, %.1f MB video, %.2f MB audio, %d GOPs",
                     seconds, encodeS, Double(seconds * fps) / encodeS, st.seconds, st.videoMB, st.audioMB, st.gops))
        guard let snap = ring.snapshot() else { throw NSError(domain: "selftest", code: 1) }
        let url = ClipWriter.newURL(label: "selftest")
        let res = try await ClipWriter.write(snap, to: url)
        print(String(format: "saved %@: %.2f s clip, %.1f MB, %d frames, %d AAC packets, write %.0f ms",
                     url.lastPathComponent, res.seconds, Double(res.bytes) / 1_048_576, res.videoFrames,
                     res.audioPackets, res.writeMs))
        try await Probe.run(url)
        let cut = url.deletingPathExtension().appendingPathExtension("replay.mp4")
        let e = try await ClipEditor.make(from: url, to: cut, strip: "YNDX 21–18 GL · YNDX +4.4k gold · 26:00")
        print(String(format: "cut %@: %.2f s, %.1f MB, made in %.0f ms", cut.lastPathComponent, e.seconds,
                     Double(e.bytes) / 1_048_576, e.ms))
        try await Probe.run(cut)
    }

    private static func paint(_ pb: CVPixelBuffer, frame: Int) {
        CVPixelBufferLockBaseAddress(pb, [])
        defer { CVPixelBufferUnlockBaseAddress(pb, []) }
        let w = CVPixelBufferGetWidthOfPlane(pb, 0), h = CVPixelBufferGetHeightOfPlane(pb, 0)
        let y = CVPixelBufferGetBaseAddressOfPlane(pb, 0)!.assumingMemoryBound(to: UInt8.self)
        let yStride = CVPixelBufferGetBytesPerRowOfPlane(pb, 0)
        let bar = (frame * 12) % w
        for row in 0..<h {
            let p = y + row * yStride
            memset(p, Int32(16 + (row + frame * 3) % 200), w)
            memset(p + bar, 235, min(40, w - bar))
        }
        let uv = CVPixelBufferGetBaseAddressOfPlane(pb, 1)!
        memset(uv, 128, CVPixelBufferGetBytesPerRowOfPlane(pb, 1) * CVPixelBufferGetHeightOfPlane(pb, 1))
    }
}

/// `--probe file.mp4`: what's in a saved clip, whether every frame decodes, and how loud it is.
enum Probe {
    static func run(_ url: URL) async throws {
        let asset = AVURLAsset(url: url)
        let duration = try await asset.load(.duration)
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        print(String(format: "%@: %.2f s, %.1f MB, %.2f Mbps", url.lastPathComponent, duration.seconds,
                     Double(size) / 1_048_576, Double(size) * 8 / duration.seconds / 1e6))
        for track in try await asset.load(.tracks) {
            let (fmts, range, rate, dataRate) = try await track.load(.formatDescriptions, .timeRange,
                                                                     .nominalFrameRate, .estimatedDataRate)
            let fourcc = fmts.first.map { fourCC(CMFormatDescriptionGetMediaSubType($0)) } ?? "?"
            if track.mediaType == .video {
                let dims = fmts.first.map { CMVideoFormatDescriptionGetDimensions($0) }
                print(String(format: "  video %@ %dx%d, %.1f fps, %.2f Mbps, starts %.3f s, %.2f s long", fourcc,
                             dims?.width ?? 0, dims?.height ?? 0, rate, dataRate / 1e6,
                             range.start.seconds, range.duration.seconds))
                try decodeVideo(asset, track)
            } else if track.mediaType == .audio {
                let asbd = fmts.first.flatMap { CMAudioFormatDescriptionGetStreamBasicDescription($0)?.pointee }
                print(String(format: "  audio %@ %.0f Hz %d ch, %.0f kbps, starts %.3f s, %.2f s long", fourcc,
                             asbd?.mSampleRate ?? 0, asbd?.mChannelsPerFrame ?? 0, dataRate / 1000,
                             range.start.seconds, range.duration.seconds))
                try levels(asset, track)
            }
        }
    }

    private static func decodeVideo(_ asset: AVAsset, _ track: AVAssetTrack) throws {
        let reader = try AVAssetReader(asset: asset)
        let out = AVAssetReaderTrackOutput(track: track, outputSettings: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
        ])
        reader.add(out)
        reader.startReading()
        var frames = 0
        let t0 = Date()
        while let sb = out.copyNextSampleBuffer() {
            if sb.imageBuffer != nil { frames += 1 }
        }
        print(String(format: "    decoded %d frames in %.2f s (reader %@)", frames, Date().timeIntervalSince(t0),
                     reader.status == .completed ? "completed" : "status \(reader.status.rawValue) \(reader.error?.localizedDescription ?? "")"))
    }

    private static func levels(_ asset: AVAsset, _ track: AVAssetTrack) throws {
        let reader = try AVAssetReader(asset: asset)
        let out = AVAssetReaderTrackOutput(track: track, outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false, AVLinearPCMIsBigEndianKey: false,
            AVNumberOfChannelsKey: 2, AVSampleRateKey: 48_000,
        ])
        reader.add(out)
        reader.startReading()
        var perSecond: [Double] = []
        var sum = 0.0, n = 0
        while let sb = out.copyNextSampleBuffer() {
            guard let block = sb.dataBuffer else { continue }
            var len = 0
            var ptr: UnsafeMutablePointer<CChar>?
            CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &len, dataPointerOut: &ptr)
            guard let ptr else { continue }
            let floats = UnsafeRawPointer(ptr).assumingMemoryBound(to: Float.self)
            for i in 0..<(len / 4) {
                sum += Double(floats[i] * floats[i])
                n += 1
                if n == 96_000 {  // one second of stereo
                    perSecond.append(sum / Double(n))
                    sum = 0; n = 0
                }
            }
        }
        let db = perSecond.map { $0 > 1e-12 ? 10 * log10($0) : -120 }
        let line = db.map { String(format: "%.0f", $0) }.joined(separator: " ")
        print("    audio RMS per second (dBFS): \(line)")
    }

    static func fourCC(_ c: FourCharCode) -> String {
        let b = [24, 16, 8, 0].map { UInt8((c >> $0) & 0xFF) }
        return String(bytes: b, encoding: .ascii) ?? "\(c)"
    }
}
