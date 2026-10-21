import AppKit
import CoreMedia
import ScreenCaptureKit

struct Options {
    var target = "com.valvesoftware.dota2"
    var label = "Dota2"
    var seconds = 30.0
    var fps = 30
    var maxWidth = 1600
    var maxHeight = 900
    var bitrate = 6_000_000
    var hevc = false
    var audio = true
    var picker = false      // consent through the system picker instead of the Screen Recording permission
    var fpsProbe = false    // read the game's own FPS counter every few seconds (OCR of the top strip)
    var statsEvery = 5.0
}

/// Captures one app's window (and only that app's sound) with ScreenCaptureKit, encodes it and keeps
/// the last N seconds in a ReplayRing.
final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let opts: Options
    let ring: ReplayRing
    private(set) var stream: SCStream?
    private var config = SCStreamConfiguration()
    private var video: VideoEncoder?
    private var audio: AudioEncoder?
    private let videoQ = DispatchQueue(label: "capture.video", qos: .userInteractive)
    private let audioQ = DispatchQueue(label: "capture.audio", qos: .userInitiated)
    private let lock = NSLock()
    private var complete = 0, idle = 0, other = 0
    private var windowFrame: CGRect = .zero
    private var displayFrame: CGRect = .zero
    private var followTimer: Timer?
    var onStateChange: ((String) -> Void)?
    private(set) var outputSize = CGSize.zero

    init(opts: Options) {
        self.opts = opts
        ring = ReplayRing(seconds: opts.seconds)
    }

    // MARK: start

    /// Finds the target's biggest window and starts capturing it.
    func startWithPermission() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let app = content.applications.first(where: { $0.bundleIdentifier == opts.target }) else {
            throw RecorderError.notRunning(opts.target)
        }
        guard let win = Recorder.mainWindow(of: opts.target, in: content) else {
            throw RecorderError.noWindow(opts.target)
        }
        let display = content.displays.max {
            $0.frame.intersection(win.frame).area < $1.frame.intersection(win.frame).area
        } ?? content.displays[0]
        let filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
        windowFrame = win.frame
        displayFrame = display.frame
        Log.json("target", ["app": app.applicationName, "pid": Int(app.processID),
                            "window": Recorder.describe(win.frame), "layer": win.windowLayer,
                            "onScreen": win.isOnScreen, "display": Recorder.describe(display.frame),
                            "scale": Double(filter.pointPixelScale)])
        try await start(filter: filter, sourceRect: win.frame.offsetBy(dx: -display.frame.minX, dy: -display.frame.minY),
                        pointSize: win.frame.size, scale: CGFloat(filter.pointPixelScale))
        DispatchQueue.main.async { self.followWindow() }
    }

    /// Starts on a filter the player picked in the system's sharing picker (no Screen Recording permission).
    func start(pickedFilter filter: SCContentFilter) async throws {
        Log.json("target", ["picked": true, "rect": Recorder.describe(filter.contentRect),
                            "scale": Double(filter.pointPixelScale)])
        try await start(filter: filter, sourceRect: nil, pointSize: filter.contentRect.size,
                        scale: CGFloat(filter.pointPixelScale))
    }

    private func start(filter: SCContentFilter, sourceRect: CGRect?, pointSize: CGSize, scale: CGFloat) async throws {
        let px = CGSize(width: pointSize.width * scale, height: pointSize.height * scale)
        let fit = min(1, CGFloat(opts.maxWidth) / px.width, CGFloat(opts.maxHeight) / px.height)
        let w = Int((px.width * fit / 2).rounded()) * 2
        let h = Int((px.height * fit / 2).rounded()) * 2
        outputSize = CGSize(width: w, height: h)

        let cfg = SCStreamConfiguration()
        cfg.width = w
        cfg.height = h
        if let sourceRect { cfg.sourceRect = sourceRect }
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(opts.fps))
        cfg.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange  // what the encoder eats, no conversion
        cfg.colorMatrix = CGDisplayStream.yCbCrMatrix_ITU_R_709_2
        cfg.queueDepth = 6
        cfg.showsCursor = true
        cfg.capturesAudio = opts.audio
        cfg.sampleRate = Int(AudioEncoder.rate)
        cfg.channelCount = 2
        cfg.excludesCurrentProcessAudio = true
        config = cfg

        let ring = self.ring
        video = try VideoEncoder(width: w, height: h, hevc: opts.hevc, bitrate: opts.bitrate, fps: opts.fps,
                                 realTime: true) { ring.appendVideo($0) }
        if opts.audio { audio = try AudioEncoder { ring.appendAudio($0) } }
        Log.json("encoder", ["width": w, "height": h, "codec": opts.hevc ? "hevc" : "h264",
                             "hardware": video?.hardware ?? false, "bitrate": opts.bitrate, "fps": opts.fps])

        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: videoQ)
        if opts.audio { try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQ) }
        try await s.startCapture()
        stream = s
        onStateChange?("recording")
        Log.line("capture started \(w)x\(h) @\(opts.fps) fps, audio \(opts.audio)")
    }

    func stop() async {
        followTimer?.invalidate()
        try? await stream?.stopCapture()
        stream = nil
        video?.finish()
    }

    static func mainWindow(of bundleID: String, in content: SCShareableContent) -> SCWindow? {
        let mine = content.windows.filter {
            $0.owningApplication?.bundleIdentifier == bundleID && $0.frame.width >= 320 && $0.frame.height >= 240
        }
        // On-screen first, then the biggest. Any layer: a game in exclusive fullscreen sits above layer 0.
        return mine.max {
            ($0.isOnScreen ? 1 : 0, $0.frame.area) < ($1.isOnScreen ? 1 : 0, $1.frame.area)
        }
    }

    /// The game may move, resize or go fullscreen: keep the crop on its window. The output size stays,
    /// so the encoder and the ring carry on (the picture is letterboxed if the shape changes).
    private func followWindow() {
        followTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            guard let self, let stream = self.stream else { return }
            Task {
                guard let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false),
                      let win = Recorder.mainWindow(of: self.opts.target, in: content),
                      win.frame != self.windowFrame else { return }
                self.windowFrame = win.frame
                self.config.sourceRect = win.frame.offsetBy(dx: -self.displayFrame.minX, dy: -self.displayFrame.minY)
                do {
                    try await stream.updateConfiguration(self.config)
                    Log.json("window", ["moved": Recorder.describe(win.frame)])
                } catch {
                    Log.line("window follow failed: \(error)")
                }
            }
        }
    }

    // MARK: SCStreamOutput

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        switch type {
        case .screen:
            guard let atts = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false)
                    as? [[SCStreamFrameInfo: Any]],
                  let raw = atts.first?[.status] as? Int, let status = SCFrameStatus(rawValue: raw) else { return }
            guard status == .complete, let pb = sb.imageBuffer else {
                lock.withLock { if status == .idle { idle += 1 } else { other += 1 } }
                return
            }
            lock.withLock { complete += 1 }
            video?.encode(pb, pts: sb.presentationTimeStamp)
        case .audio:
            audio?.push(sb)
        default:
            break
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Log.line("stream stopped: \(error.localizedDescription)")
        self.stream = nil
        onStateChange?("stopped: \(error.localizedDescription)")
    }

    // MARK: stats

    struct Counters { let complete, idle, other, encoded, dropped: Int }
    private var lastEncoded = 0, lastDropped = 0

    /// Frame counts since the last call.
    func takeCounters() -> Counters {
        let (c, i, o) = lock.withLock { () -> (Int, Int, Int) in
            defer { complete = 0; idle = 0; other = 0 }
            return (complete, idle, other)
        }
        let enc = video?.encoded ?? 0, drop = video?.dropped ?? 0
        defer { lastEncoded = enc; lastDropped = drop }
        return Counters(complete: c, idle: i, other: o, encoded: enc - lastEncoded, dropped: drop - lastDropped)
    }

    func audioMeter() -> (peak: Double, rms: Double, buffers: Int)? { audio?.readMeter() }

    static func describe(_ r: CGRect) -> String {
        "\(Int(r.minX)),\(Int(r.minY)) \(Int(r.width))x\(Int(r.height))"
    }
}

enum RecorderError: LocalizedError {
    case notRunning(String), noWindow(String)
    var errorDescription: String? {
        switch self {
        case .notRunning(let id): return "\(id) isn't running"
        case .noWindow(let id): return "\(id) has no window yet"
        }
    }
}

extension CGRect {
    var area: CGFloat { isNull ? 0 : width * height }
}
