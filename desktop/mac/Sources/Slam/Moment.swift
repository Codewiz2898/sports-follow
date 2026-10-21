import Foundation

/// What was going on at the tap: what the overlays draw (the title card, the scorebug, the replay bar)
/// and what the montage titles say. From Slam's match API when it has the map, else Dota's own top bar
/// read off the screen (kills either side of the clock), else nothing but the game's name.
struct Moment: Codable {
    var teams: [String]?      // short names, page order ("TY", "GL"); nil when only the screen was read
    var kills: [Int]?         // same order
    var gold: String?         // "TY +4.4k gold", "level on gold"
    var clock: String?        // "26:00"
    var game: Int?            // the map in the series
    var competition: String?  // "PARI Universe 2026"
    var series: String?       // "TY 1–0 GL in maps"
    var source = "none"       // "api", "screen" or "none"

    var sides: [String] { teams ?? ["Radiant", "Dire"] }

    /// "TY 21–18 GL · TY +4.4k gold · 26:00 · Game 2", or what of it is known.
    var line: String {
        var parts: [String] = []
        if let k = kills, k.count == 2 {
            parts.append(teams.map { "\($0[0]) \(k[0])–\(k[1]) \($0[1])" } ?? "\(k[0])–\(k[1])")
        } else if let s = series {
            parts.append(s)
        }
        if let gold { parts.append(gold) }
        if let clock { parts.append(clock) }
        if let game { parts.append("Game \(game)") }
        return parts.joined(separator: " · ")
    }

    /// "TY v GL", or the kills, or "Dota 2".
    var headline: String {
        if let t = teams, t.count == 2 { return "\(t[0]) v \(t[1])" }
        if let k = kills, k.count == 2 { return "\(k[0])–\(k[1])" }
        return "Dota 2"
    }

    /// "DOTA 2 · PARI UNIVERSE 2026 · GAME 2"
    var kicker: String {
        (["Dota 2", competition, game.map { "Game \($0)" }].compactMap { $0 }).joined(separator: " · ").uppercased()
    }

    var isEmpty: Bool { kills == nil && clock == nil && teams == nil }

    /// Made-up numbers for the self-test and design checks.
    static let sample = Moment(teams: ["TY", "GL"], kills: [21, 18], gold: "TY +4.4k gold", clock: "26:00", game: 2,
                               competition: "PARI Universe 2026", series: "TY 1–0 GL in maps", source: "sample")
}
