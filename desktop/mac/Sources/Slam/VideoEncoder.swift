import CoreMedia
import VideoToolbox

/// Hardware H.264 (or HEVC) through VideoToolbox. No B-frames, so decode order is presentation order
/// and the ring can cut at any keyframe. A keyframe every 2 s keeps a saved clip within 2 s of 30 s.
final class VideoEncoder {
    let width: Int32
    let height: Int32
    private var session: VTCompressionSession?
    private let onSample: (CMSampleBuffer) -> Void
    private(set) var hardware = false
    private let lock = NSLock()
    private var _encoded = 0
    private var _dropped = 0
    private var lastKey: CMTime?  // our own 2 s keyframe clock: the encoder's limits slip when frames come slowly

    var encoded: Int { lock.withLock { _encoded } }
    var dropped: Int { lock.withLock { _dropped } }

    init(width: Int, height: Int, hevc: Bool, bitrate: Int, fps: Int, realTime: Bool,
         onSample: @escaping (CMSampleBuffer) -> Void) throws {
        self.width = Int32(width)
        self.height = Int32(height)
        self.onSample = onSample
        let spec: [CFString: Any] = [
            kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: true,
        ]
        var s: VTCompressionSession?
        let status = VTCompressionSessionCreate(
            allocator: nil, width: Int32(width), height: Int32(height),
            codecType: hevc ? kCMVideoCodecType_HEVC : kCMVideoCodecType_H264,
            encoderSpecification: spec as CFDictionary, imageBufferAttributes: nil,
            compressedDataAllocator: nil, outputCallback: nil, refcon: nil,
            compressionSessionOut: &s)
        guard status == noErr, let session = s else {
            throw NSError(domain: "VideoEncoder", code: Int(status),
                          userInfo: [NSLocalizedDescriptionKey: "VTCompressionSessionCreate failed \(status)"])
        }
        self.session = session
        let props: [CFString: Any] = [
            kVTCompressionPropertyKey_RealTime: realTime,
            kVTCompressionPropertyKey_ProfileLevel: hevc ? kVTProfileLevel_HEVC_Main_AutoLevel
                                                         : kVTProfileLevel_H264_High_AutoLevel,
            kVTCompressionPropertyKey_AverageBitRate: bitrate,
            kVTCompressionPropertyKey_ExpectedFrameRate: fps,
            kVTCompressionPropertyKey_MaxKeyFrameInterval: fps * 2,
            kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration: 2.0,
            kVTCompressionPropertyKey_AllowFrameReordering: false,
        ]
        for (k, v) in props {
            let st = VTSessionSetProperty(session, key: k, value: v as CFTypeRef)
            if st != noErr { Log.line("video encoder: property \(k) refused (\(st))") }
        }
        var hw: CFTypeRef?
        VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
                              allocator: nil, valueOut: &hw)
        hardware = (hw as? Bool) ?? false
        VTCompressionSessionPrepareToEncodeFrames(session)
    }

    func encode(_ pixelBuffer: CVPixelBuffer, pts: CMTime, duration: CMTime = .invalid) {
        guard let session else { return }
        var props: CFDictionary?
        if lastKey == nil || (pts - lastKey!).seconds >= 2 {
            lastKey = pts
            props = [kVTEncodeFrameOptionKey_ForceKeyFrame: true] as CFDictionary
        }
        let st = VTCompressionSessionEncodeFrame(
            session, imageBuffer: pixelBuffer, presentationTimeStamp: pts, duration: duration,
            frameProperties: props, infoFlagsOut: nil
        ) { [weak self] status, flags, sample in
            guard let self else { return }
            if status != noErr || flags.contains(.frameDropped) || sample == nil {
                self.lock.withLock { self._dropped += 1 }
                return
            }
            self.lock.withLock { self._encoded += 1 }
            self.onSample(sample!)
        }
        if st != noErr { lock.withLock { _dropped += 1 } }
    }

    func finish() {
        guard let session else { return }
        VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)
    }

    func invalidate() {
        guard let session else { return }
        VTCompressionSessionInvalidate(session)
        self.session = nil
    }

    deinit { invalidate() }
}

extension CMSampleBuffer {
    var isKeyframe: Bool {
        guard let atts = CMSampleBufferGetSampleAttachmentsArray(self, createIfNecessary: false)
                as? [[CFString: Any]], let first = atts.first else { return true }
        return !((first[kCMSampleAttachmentKey_NotSync] as? Bool) ?? false)
    }
    var byteCount: Int { CMSampleBufferGetTotalSampleSize(self) }
}
