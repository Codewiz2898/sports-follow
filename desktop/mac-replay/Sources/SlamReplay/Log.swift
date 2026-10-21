import Foundation

/// Lines go to stdout and to ~/Library/Logs/SlamReplay/replay.log (the app is started with `open`,
/// so stdout goes nowhere then; the file is what the measurements read).
enum Log {
    static let dir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/SlamReplay", isDirectory: true)
    static let url = dir.appendingPathComponent("replay.log")
    private static let queue = DispatchQueue(label: "log")
    private static var handle: FileHandle? = {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let h = try? FileHandle(forWritingTo: url)
        _ = try? h?.seekToEnd()
        return h
    }()
    private static let stamp: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func line(_ s: String) {
        let text = "\(stamp.string(from: Date())) \(s)\n"
        queue.async {
            FileHandle.standardOutput.write(text.data(using: .utf8)!)
            handle?.write(text.data(using: .utf8)!)
        }
    }

    /// One JSON object per line, for the measurement script.
    static func json(_ event: String, _ fields: [String: Any]) {
        var f = fields
        f["event"] = event
        if let data = try? JSONSerialization.data(withJSONObject: f, options: [.sortedKeys]),
           let s = String(data: data, encoding: .utf8) {
            line(s)
        }
    }

    static func flush() {
        queue.sync { try? handle?.synchronize() }
    }
}
