import AppKit
import CoreText

/// Slam's own type for the burned-in text: Anton, Manrope and Fredoka (sports_follow/data/fonts), copied
/// into the app by build.sh. Registered for this process only.
enum Fonts {
    static func register() {
        var dirs: [URL] = []
        if let res = Bundle.main.resourceURL { dirs.append(res.appendingPathComponent("Fonts")) }
        // Run from .build/release: the repo's copy, a few levels up.
        var up = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath().deletingLastPathComponent()
        for _ in 0..<10 {
            dirs.append(up.appendingPathComponent("sports_follow/data/fonts"))
            up.deleteLastPathComponent()
        }
        for dir in dirs {
            let names = ["Anton-Regular.ttf", "Manrope.ttf", "Fredoka.ttf"]
            let urls = names.map { dir.appendingPathComponent($0) }.filter { FileManager.default.fileExists(atPath: $0.path) }
            guard !urls.isEmpty else { continue }
            for u in urls { CTFontManagerRegisterFontsForURL(u as CFURL, .process, nil) }
            return
        }
        Log.line("fonts: Slam's fonts not found, using Impact and the system's")
    }

    /// The font by family, at the weight Slam uses it (variable fonts: Manrope 800, Fredoka 600).
    static func named(_ family: String, size: CGFloat) -> NSFont? {
        let weight: Double? = ["Manrope": 800, "Fredoka": 600][family]
        guard let base = NSFont(name: family, size: size) ?? NSFont(name: "\(family)-Regular", size: size) else { return nil }
        guard let weight else { return base }
        let wght = NSNumber(value: 0x7767_6874)  // 'wght'
        let d = base.fontDescriptor.addingAttributes([.variation: [wght: weight]])
        return NSFont(descriptor: d, size: size) ?? base
    }
}
