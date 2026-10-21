import AVFoundation

/// The end card's sound, made here rather than from a file: a whoosh, a thump and a chime for the
/// blue stamp; ticks, a swish, a pop and the chime for ink + Anton. Timed to Overlays' animations
/// (seconds from the card's start), 48 kHz stereo.
enum Sting {
    static let rate = 48_000.0

    private struct Mix {
        var l: [Float], r: [Float]
        init(seconds: Double) {
            let n = Int(seconds * Sting.rate)
            l = Array(repeating: 0, count: n)
            r = l
        }
        /// Adds `f(t)` (t in seconds from `at`) for `length` seconds, panned -1 (left) … 1 (right).
        mutating func add(at: Double, length: Double, pan: (Double) -> Double = { _ in 0 }, _ f: (Double) -> Double) {
            let start = Int(at * Sting.rate), n = Int(length * Sting.rate)
            for i in 0..<n where start + i < l.count {
                let t = Double(i) / Sting.rate
                let v = f(t), p = max(-1, min(1, pan(t)))
                l[start + i] += Float(v * (1 - max(0, p)))
                r[start + i] += Float(v * (1 + min(0, p)))
            }
        }
    }

    /// Repeatable noise, so every cut sounds the same.
    private struct Noise {
        var s: UInt64 = 0x9E37_79B9_7F4A_7C15
        mutating func next() -> Double {
            s ^= s << 13; s ^= s >> 7; s ^= s << 17
            return Double(s % 2_000_001) / 1_000_000 - 1
        }
    }

    /// Filtered noise rising and falling over `length`, its brightness sweeping from `lo` to `hi` Hz.
    private static func whoosh(_ m: inout Mix, at: Double, length: Double, lo: Double, hi: Double, gain: Double,
                               pan: @escaping (Double) -> Double) {
        var noise = Noise(), y = 0.0
        m.add(at: at, length: length, pan: pan) { t in
            let x = t / length
            let cutoff = lo + (hi - lo) * x
            let a = 1 - exp(-2 * .pi * cutoff / rate)
            y += a * (noise.next() - y)
            return y * sin(.pi * x) * gain
        }
    }

    /// A struck note: a sine and its octave, a 4 ms attack, decaying.
    private static func bell(_ m: inout Mix, at: Double, freq: Double, gain: Double, decay: Double = 0.35) {
        m.add(at: at, length: decay * 5) { t in
            let env = min(1, t / 0.004) * exp(-t / decay)
            return (sin(2 * .pi * freq * t) + 0.3 * sin(4 * .pi * freq * t) + 0.12 * sin(6 * .pi * freq * t)) * env * gain
        }
    }

    /// A wood-block tick: a short sine with a click on top.
    private static func tick(_ m: inout Mix, at: Double, freq: Double, gain: Double) {
        var noise = Noise()
        m.add(at: at, length: 0.12) { t in
            sin(2 * .pi * freq * t) * exp(-t / 0.025) * gain + noise.next() * exp(-t / 0.003) * gain * 0.4
        }
    }

    /// The two-note sign-off both cards end on (E5 then B5).
    private static func chime(_ m: inout Mix, at: Double) {
        bell(&m, at: at, freq: 659.26, gain: 0.16)
        bell(&m, at: at + 0.14, freq: 987.77, gain: 0.18, decay: 0.5)
    }

    static func render(_ style: Overlays.EndStyle, seconds: Double) -> AVAudioPCMBuffer {
        var m = Mix(seconds: seconds)
        switch style {
        case .stamp:
            // The blue wipes in from the right: a whoosh panned right to left.
            whoosh(&m, at: 0, length: 0.24, lo: 400, hi: 6000, gain: 0.5, pan: { 0.8 - $0 * 6 })
            // SLAM lands at 0.42 s: a falling sub thump, a crack and a burst of body.
            m.add(at: 0.42, length: 0.6) { t in
                let f = 45 + 95 * exp(-t / 0.06)
                return sin(2 * .pi * f * t) * exp(-t / 0.13) * 0.6
            }
            var crack = Noise(), last = 0.0
            m.add(at: 0.42, length: 0.05) { t in
                let x = crack.next(), d = x - last  // brightened: the difference of white noise
                last = x
                return d * exp(-t / 0.006) * 0.35
            }
            var body = Noise(), y = 0.0
            m.add(at: 0.42, length: 0.3) { t in
                y += 0.25 * (body.next() - y)
                return y * exp(-t / 0.05) * 0.45
            }
            // The bar draws: the chime.
            chime(&m, at: 0.66)
        case .ink:
            // A soft rise under "clipped on".
            whoosh(&m, at: 0.12, length: 0.42, lo: 200, hi: 2500, gain: 0.18, pan: { _ in 0 })
            // A tick as each letter lands, a step higher each time.
            for (i, f) in [740.0, 830.6, 932.3, 1108.7].enumerated() {
                tick(&m, at: 0.29 + Double(i) * 0.095 + 0.24, freq: f, gain: 0.35)
            }
            // The underline sweeps left to right.
            whoosh(&m, at: 0.8, length: 0.3, lo: 800, hi: 7000, gain: 0.22, pan: { -0.7 + $0 * 4.5 })
            // The badge pops.
            m.add(at: 1.08, length: 0.12) { t in
                let f = 300 + 500 * exp(-t / 0.02)
                return sin(2 * .pi * f * t) * exp(-t / 0.035) * 0.45
            }
            chime(&m, at: 1.2)
        }
        // Soft-limit and fade the last 60 ms, so the cut ends clean.
        let n = m.l.count, fade = Int(0.06 * rate)
        let buf = AVAudioPCMBuffer(pcmFormat: AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!,
                                   frameCapacity: AVAudioFrameCount(n))!
        buf.frameLength = AVAudioFrameCount(n)
        for i in 0..<n {
            let g: Float = i > n - fade ? Float(n - i) / Float(fade) : 1
            buf.floatChannelData![0][i] = tanhf(m.l[i] * 1.1) * 0.85 * g
            buf.floatChannelData![1][i] = tanhf(m.r[i] * 1.1) * 0.85 * g
        }
        return buf
    }

    /// The sting as a file the composition can take (cached per style and length).
    static func file(_ style: Overlays.EndStyle, seconds: Double) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("slam-sting-\(style.rawValue)-\(Int(seconds * 1000)).caf")
        if FileManager.default.fileExists(atPath: url.path) { return url }
        let buf = render(style, seconds: seconds)
        let f = try AVAudioFile(forWriting: url, settings: buf.format.settings)
        try f.write(from: buf)
        return url
    }

    /// Puts the sting on the clip's own sound track at `at`, where the game's sound has stopped: a second
    /// track would be left out by most players (an export keeps tracks apart, it doesn't mix them).
    static func add(to track: AVMutableCompositionTrack?, style: Overlays.EndStyle, at: CMTime, seconds: Double) async throws {
        guard enabled, let track else { return }
        let asset = AVURLAsset(url: try file(style, seconds: seconds))
        guard let src = try await asset.loadTracks(withMediaType: .audio).first else { return }
        let d = try await asset.load(.duration)
        try track.insertTimeRange(CMTimeRange(start: .zero, duration: d), of: src, at: at)
    }

    static var enabled = true
}
