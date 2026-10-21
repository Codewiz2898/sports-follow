import AVFoundation
import CoreMedia
import Foundation
import Testing
@testable import Slam

@Suite struct MomentTests {
    @Test func dotasTopBarReadsAsAMoment() {
        let m = TopBar.moment(from: ["10", "15:22", "5"])
        #expect(m?.kills == [10, 5])
        #expect(m?.clock == "15:22")
        #expect(m?.line == "10–5 · 15:22")
        #expect(TopBar.moment(from: ["<1k v", "3:06", "1"])?.kills == nil)  // the clock, without both kills
        #expect(TopBar.moment(from: ["SHOW FIGHT RECAP"]) == nil)
    }

    @Test func aMomentInWords() {
        let m = Moment.sample
        #expect(m.line == "TY 21–18 GL · TY +4.4k gold · 26:00 · Game 2")
        #expect(m.headline == "TY v GL")
        #expect(m.kicker == "DOTA 2 · PARI UNIVERSE 2026 · GAME 2")
        #expect(Moment().isEmpty && Moment().headline == "Dota 2")
    }

    @Test func matchLinks() {
        #expect(MatchFeed.parse("https://stann.in/match/pandascore_esports/1753729") == "pandascore_esports/1753729")
        #expect(MatchFeed.parse("https://stann.in/match/pandascore_esports/1753729?s=share#clips") == "pandascore_esports/1753729")
        #expect(MatchFeed.parse("pandascore_esports/1753729") == "pandascore_esports/1753729")
        #expect(MatchFeed.parse("1753729") == nil)  // which source's id? the app doesn't name sources
        #expect(MatchFeed.parse("https://example.com/watch?v=1") == nil)
    }

    @Test func clocksCompare() {
        #expect(MatchFeed.seconds("26:00") == 1560)
        #expect(MatchFeed.seconds("3:06") == 186)
        #expect(MatchFeed.seconds("draft") == nil && MatchFeed.seconds(nil) == nil)
    }
}

@Suite struct PublishingTests {
    @Test func onlyAKnownGamesWindowIsPublishable() {
        #expect(ClipSource.of(bundleIDs: ["com.valvesoftware.dota2"]) == .gameClient)
        #expect(ClipSource.of(bundleIDs: ["com.google.Chrome"]) == .browser)
        #expect(ClipSource.of(bundleIDs: ["com.google.Chrome.app.abcdef"]) == .browser)  // a web app
        #expect(ClipSource.of(bundleIDs: ["com.apple.Safari.WebApp.1234"]) == .browser)
        #expect(ClipSource.of(bundleIDs: ["com.colliderli.iina"]) == .other)  // a player might show a broadcast
        #expect(ClipSource.of(bundleIDs: ["com.valvesoftware.dota2", "com.google.Chrome"]) == .browser)
        #expect(ClipSource.of(bundleIDs: []) == .other)
        #expect(ClipSource.gameClient.publishable && !ClipSource.browser.publishable && !ClipSource.other.publishable)
        #expect(ClipSource.other.refusal != nil && ClipSource.gameClient.refusal == nil)
    }

    @Test func whatTheServerIsTold() throws {
        let body = Publisher.Request(match: "pandascore_esports/1753729", moment: Publisher.momentBody(.sample), caption: "What a fight",
                                     source: ClipSource.gameClient.rawValue, seconds: 44, bytes: 1000, width: 1280, height: 832)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(body)) as! [String: Any]
        #expect(json["source"] as? String == "game_client")
        #expect(json["match"] as? String == "pandascore_esports/1753729")
        let moment = json["moment"] as! [String: Any]
        #expect(moment["line"] as? String == Moment.sample.line)
        #expect(moment["kills"] as? [Int] == [21, 18])
    }
}

@Suite struct CutTests {
    /// Synthetic frames and a tone through the encoders into a ring.
    static func ring(seconds: Int) throws -> ReplayRing {
        let fps = 30, w = 640, h = 360
        let ring = ReplayRing(seconds: 30)
        let video = try VideoEncoder(width: w, height: h, hevc: false, bitrate: 2_000_000, fps: fps, realTime: false) { ring.appendVideo($0) }
        let audio = try AudioEncoder { ring.appendAudio($0) }
        var pool: CVPixelBufferPool?
        let attrs: [CFString: Any] = [kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                                      kCVPixelBufferWidthKey: w, kCVPixelBufferHeightKey: h,
                                      kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary]
        CVPixelBufferPoolCreate(nil, nil, attrs as CFDictionary, &pool)
        let t0 = CMClockGetTime(CMClockGetHostTimeClock())
        let per = Int(AudioEncoder.rate) / fps
        let tone = (0..<per).map { Float(sin(Double($0) * 0.05)) * 0.2 }
        for i in 0..<(seconds * fps) {
            var pb: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool!, &pb)
            video.encode(pb!, pts: t0 + CMTime(value: CMTimeValue(i), timescale: CMTimeScale(fps)), duration: CMTime(value: 1, timescale: CMTimeScale(fps)))
            tone.withUnsafeBufferPointer { p in
                audio.push(pts: t0 + CMTime(value: CMTimeValue(i * per), timescale: CMTimeScale(AudioEncoder.rate)), left: p, right: p)
            }
        }
        video.finish()
        return ring
    }

    /// An 8 s clip of them, written as a save writes it.
    static func clip(seconds: Int = 8) async throws -> URL {
        let snap = try #require(try ring(seconds: seconds).snapshot())
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("slam-test-\(UUID().uuidString).mp4")
        _ = try await ClipWriter.write(snap, to: url)
        return url
    }

    @Test func theRingKeepsThirtyToThirtyTwoSecondsFromAKeyframe() throws {
        let snap = try #require(try Self.ring(seconds: 40).snapshot())
        #expect(snap.seconds >= 29.9 && snap.seconds <= 32.1)
        #expect(snap.video.first?.isKeyframe == true)
        let start = snap.video[0].presentationTimeStamp.seconds
        #expect((snap.audio.first?.presentationTimeStamp.seconds ?? 0) >= start - 0.001)  // no sound before the first frame
        #expect(snap.audio.count > 1000)  // ~30 s of AAC packets (47 a second)
    }

    @Test func theCutIsClipReplayAndEndCardOnOneSoundTrack() async throws {
        let clip = try await Self.clip()
        let cut = FileManager.default.temporaryDirectory.appendingPathComponent("slam-test-cut-\(UUID().uuidString).mp4")
        defer { try? FileManager.default.removeItem(at: clip); try? FileManager.default.removeItem(at: cut) }
        let made = try await ClipEditor.make(from: clip, to: cut, moment: .sample)
        let asset = AVURLAsset(url: cut)
        let total = try await asset.load(.duration).seconds
        let audio = try await asset.loadTracks(withMediaType: .audio)
        // 8 s, then its last 5 s at half speed (10 s), then the 2 s end card.
        #expect(abs(total - 20) < 0.2)
        #expect(abs(made.replayStart - 8) < 0.1)
        #expect(audio.count == 1)  // the end card's sound on the clip's own track: most players play only one
        let audioEnd = try await audio[0].load(.timeRange).end.seconds
        #expect(audioEnd > total - 0.3)  // and it runs to the end
    }
}
