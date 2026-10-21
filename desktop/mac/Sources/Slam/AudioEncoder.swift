import AVFoundation
import CoreMedia

/// 48 kHz stereo float PCM in, AAC-LC packets out, each wrapped in its own CMSampleBuffer so the ring
/// can hold them next to the video and AVAssetWriter can pass them through untouched.
///
/// The packets are timed by counting samples from the first input buffer, not by trusting each
/// buffer's timestamp: gaps in the input (ScreenCaptureKit may send nothing while the game is quiet)
/// are filled with silence so the AAC stream stays continuous and in step with the video clock.
final class AudioEncoder {
    static let rate = 48_000.0
    static let framesPerPacket = 1024

    private let pcmFormat = AVAudioFormat(standardFormatWithSampleRate: AudioEncoder.rate, channels: 2)!
    private let aacFormat: AVAudioFormat
    private let converter: AVAudioConverter
    private var format: CMAudioFormatDescription?
    private let onSample: (CMSampleBuffer) -> Void
    private let lock = NSLock()

    private var left: [Float] = []
    private var right: [Float] = []
    private var anchor: CMTime?          // presentation time of input frame 0
    private var framesIn: Int64 = 0      // frames queued since the anchor, silence included
    private var packetsOut: Int64 = 0
    private let leadingFrames: Int64
    private var outBuffer: AVAudioCompressedBuffer

    // Level meter since the last read (dBFS of the input, like BHA-351's capture reading).
    private var peak: Float = 0
    private var sumSquares: Double = 0
    private var meterFrames = 0
    private(set) var buffersIn = 0
    private(set) var silenceFilled: Int64 = 0
    private var warned = false

    init(bitrate: Int = 128_000, onSample: @escaping (CMSampleBuffer) -> Void) throws {
        self.onSample = onSample
        var desc = AudioStreamBasicDescription(
            mSampleRate: AudioEncoder.rate, mFormatID: kAudioFormatMPEG4AAC, mFormatFlags: 0,
            mBytesPerPacket: 0, mFramesPerPacket: UInt32(AudioEncoder.framesPerPacket), mBytesPerFrame: 0,
            mChannelsPerFrame: 2, mBitsPerChannel: 0, mReserved: 0)
        guard let aac = AVAudioFormat(streamDescription: &desc),
              let conv = AVAudioConverter(from: pcmFormat, to: aac) else {
            throw NSError(domain: "AudioEncoder", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "no AAC converter"])
        }
        conv.bitRate = bitrate
        aacFormat = aac
        converter = conv
        let lead = Int64(conv.primeInfo.leadingFrames)
        leadingFrames = lead > 0 ? lead : 2112
        outBuffer = AVAudioCompressedBuffer(format: aac, packetCapacity: 16,
                                            maximumPacketSize: max(conv.maximumOutputPacketSize, 1536))
    }

    /// A ScreenCaptureKit audio buffer (float32, usually non-interleaved).
    func push(_ sb: CMSampleBuffer) {
        guard let fd = sb.formatDescription,
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(fd)?.pointee else { return }
        let isFloat = asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0
        guard asbd.mFormatID == kAudioFormatLinearPCM, isFloat, asbd.mBitsPerChannel == 32,
              asbd.mSampleRate == AudioEncoder.rate else {
            if !warned {
                warned = true
                Log.line("audio: unexpected input format \(asbd), skipping audio")
            }
            return
        }
        let frames = sb.numSamples
        let channels = Int(asbd.mChannelsPerFrame)
        let nonInterleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        try? sb.withAudioBufferList { abl, _ in
            if nonInterleaved {
                let l = UnsafeBufferPointer(start: abl[0].mData?.assumingMemoryBound(to: Float.self), count: frames)
                let r = abl.count > 1
                    ? UnsafeBufferPointer(start: abl[1].mData?.assumingMemoryBound(to: Float.self), count: frames)
                    : l
                push(pts: sb.presentationTimeStamp, left: l, right: r)
            } else {
                let all = UnsafeBufferPointer(start: abl[0].mData?.assumingMemoryBound(to: Float.self),
                                              count: frames * channels)
                var l = [Float](repeating: 0, count: frames)
                var r = [Float](repeating: 0, count: frames)
                for i in 0..<frames {
                    l[i] = all[i * channels]
                    r[i] = all[i * channels + min(1, channels - 1)]
                }
                l.withUnsafeBufferPointer { lp in
                    r.withUnsafeBufferPointer { rp in push(pts: sb.presentationTimeStamp, left: lp, right: rp) }
                }
            }
        }
    }

    func push(pts: CMTime, left l: UnsafeBufferPointer<Float>, right r: UnsafeBufferPointer<Float>) {
        lock.lock(); defer { lock.unlock() }
        buffersIn += 1
        var src = 0
        if let anchor {
            let expected = anchor.seconds + Double(framesIn) / AudioEncoder.rate
            let gap = Int64(((pts.seconds - expected) * AudioEncoder.rate).rounded())
            if gap > 480 {  // > 10 ms missing: fill with silence, at most a minute of it
                let fill = Int(min(gap, Int64(AudioEncoder.rate) * 60))
                left.append(contentsOf: repeatElement(0, count: fill))
                right.append(contentsOf: repeatElement(0, count: fill))
                framesIn += Int64(fill)
                silenceFilled += Int64(fill)
                if gap > Int64(AudioEncoder.rate) * 60 {  // a long pause: move the clock instead
                    self.anchor = pts - CMTime(value: framesIn, timescale: CMTimeScale(AudioEncoder.rate))
                }
            } else if gap < -480 {  // overlaps what we already have: drop the overlap
                src = Int(min(-gap, Int64(l.count)))
            }
        } else {
            anchor = pts
        }
        for i in src..<l.count {
            let a = l[i], b = r[i]
            let m = max(abs(a), abs(b))
            if m > peak { peak = m }
            sumSquares += Double(a * a + b * b) / 2
        }
        meterFrames += l.count - src
        left.append(contentsOf: l[src...])
        right.append(contentsOf: r[src...])
        framesIn += Int64(l.count - src)
        drain()
    }

    private func drain() {
        let n = AudioEncoder.framesPerPacket
        while left.count >= n {
            var err: NSError?
            let status = converter.convert(to: outBuffer, error: &err) { [self] _, inStatus in
                guard left.count >= n,
                      let pcm = AVAudioPCMBuffer(pcmFormat: pcmFormat, frameCapacity: AVAudioFrameCount(n)) else {
                    inStatus.pointee = .noDataNow
                    return nil
                }
                pcm.frameLength = AVAudioFrameCount(n)
                left.withUnsafeBufferPointer { pcm.floatChannelData![0].update(from: $0.baseAddress!, count: n) }
                right.withUnsafeBufferPointer { pcm.floatChannelData![1].update(from: $0.baseAddress!, count: n) }
                left.removeFirst(n)
                right.removeFirst(n)
                inStatus.pointee = .haveData
                return pcm
            }
            emitPackets()
            if status == .error {
                Log.line("audio: convert error \(err?.localizedDescription ?? "?")")
                return
            }
        }
    }

    private func emitPackets() {
        guard outBuffer.packetCount > 0, let anchor, let descs = outBuffer.packetDescriptions else { return }
        if format == nil { format = makeFormat() }
        guard let format else { return }
        let base = outBuffer.data
        for i in 0..<Int(outBuffer.packetCount) {
            var d = descs[i]
            let start = Int(d.mStartOffset)
            let size = Int(d.mDataByteSize)
            d.mStartOffset = 0
            let frame = packetsOut * Int64(AudioEncoder.framesPerPacket) - leadingFrames
            packetsOut += 1
            let pts = anchor + CMTime(value: frame, timescale: CMTimeScale(AudioEncoder.rate))
            var block: CMBlockBuffer?
            guard CMBlockBufferCreateWithMemoryBlock(
                allocator: nil, memoryBlock: nil, blockLength: size, blockAllocator: nil,
                customBlockSource: nil, offsetToData: 0, dataLength: size,
                flags: kCMBlockBufferAssureMemoryNowFlag, blockBufferOut: &block) == noErr, let block,
                CMBlockBufferReplaceDataBytes(with: base + start, blockBuffer: block,
                                              offsetIntoDestination: 0, dataLength: size) == noErr
            else { continue }
            var sb: CMSampleBuffer?
            guard CMAudioSampleBufferCreateReadyWithPacketDescriptions(
                allocator: nil, dataBuffer: block, formatDescription: format, sampleCount: 1,
                presentationTimeStamp: pts, packetDescriptions: &d, sampleBufferOut: &sb) == noErr,
                let sb else { continue }
            onSample(sb)
        }
        outBuffer.packetCount = 0
        outBuffer.byteLength = 0
    }

    private func makeFormat() -> CMAudioFormatDescription? {
        var asbd = aacFormat.streamDescription.pointee
        var fd: CMAudioFormatDescription?
        let cookie = converter.magicCookie ?? Data()
        let st = cookie.withUnsafeBytes { raw in
            CMAudioFormatDescriptionCreate(
                allocator: nil, asbd: &asbd, layoutSize: 0, layout: nil,
                magicCookieSize: cookie.count, magicCookie: cookie.isEmpty ? nil : raw.baseAddress,
                extensions: nil, formatDescriptionOut: &fd)
        }
        if st != noErr { Log.line("audio: format description failed \(st)") }
        Log.line("audio: AAC \(converter.bitRate / 1000) kbps, cookie \(cookie.count) B, leading \(leadingFrames) frames")
        return fd
    }

    /// Peak and RMS of the input since the last call, in dBFS (-120 is silence).
    func readMeter() -> (peak: Double, rms: Double, buffers: Int) {
        lock.lock(); defer { lock.unlock() }
        func db(_ x: Double) -> Double { x > 1e-6 ? 20 * log10(x) : -120 }
        let rms = meterFrames > 0 ? (sumSquares / Double(meterFrames)).squareRoot() : 0
        let out = (db(Double(peak)), db(rms), buffersIn)
        peak = 0; sumSquares = 0; meterFrames = 0; buffersIn = 0
        return out
    }
}
