import Foundation

/// The stat strip's words, from Slam's own match page API (the same numbers the stann.in match page shows):
/// "YNDX 21–18 GL · YNDX +4.4k gold · 26:00". Read once per save, never polled.
final class MatchFeed {
    static let base = URL(string: "https://stann.in")!

    /// "pandascore_esports/<id>", from --match, the menu, or the only live Dota 2 game in /api/live.
    var path: String?
    private(set) var title: String?

    init(path: String? = nil) { self.path = path.flatMap(MatchFeed.parse) }

    /// A stann.in match URL, "pandascore_esports/<id>", or a bare PandaScore id.
    static func parse(_ text: String) -> String? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let r = t.range(of: #"[a-z_]+/[A-Za-z0-9_.:-]+$"#, options: .regularExpression),
           t.contains("/match/") || !t.contains("://") {
            return String(t[r])
        }
        if t.range(of: #"^\d+$"#, options: .regularExpression) != nil { return "pandascore_esports/\(t)" }
        return nil
    }

    struct Line {
        let text: String
        let clock: String?
        let fetchedAt: Date
        let ms: Double
    }

    /// The line for now, or nil (no match, no live map, or the API didn't answer within 3 s).
    func line() async -> Line? {
        let t0 = Date()
        if path == nil { path = await findLive() }
        guard let path, let resp = await get("/api/match/\(path)") else { return nil }
        let live = resp["live"] as? [String: Any]
        let state = live?["state"] as? [String: Any]
        // Teams: the page's (once it has map detail), else the board's (abbr, and the series score).
        let pageTeams = ((resp["page"] as? [String: Any])?["teams"] as? [[String: Any]]) ?? []
        let stateTeams = (state?["teams"] as? [[String: Any]]) ?? []
        let teams: [(id: String, short: String, score: Int?)] = (pageTeams.isEmpty ? stateTeams : pageTeams).compactMap { t in
            guard let id = t["id"] as? String else { return nil }
            let short = (t["short"] as? String) ?? (t["abbr"] as? String) ?? (t["name"] as? String) ?? id
            let score = stateTeams.first { ($0["id"] as? String) == id }?["score"] as? Int
            return (id, short, score)
        }
        func short(_ id: String?) -> String? { teams.first { $0.id == id }?.short }
        title = teams.map(\.short).joined(separator: " v ")
        let ms = { Date().timeIntervalSince(t0) * 1000 }
        var series: String?
        if teams.count == 2, let a = teams[0].score, let b = teams[1].score {
            series = "\(teams[0].short) \(a)–\(b) \(teams[1].short) in maps"
        }
        guard let board = (state?["dota"] as? [String: Any])?["live"] as? [String: Any] else {
            // No map being played on the page: the series line, so the strip still says whose game it is.
            Log.json("strip", ["match": path, "note": "no live map on the page", "is_live": live?["is_live"] ?? false])
            guard let series else { return nil }
            return Line(text: series, clock: nil, fetchedAt: Date(), ms: ms())
        }
        let clock = board["clock"] as? String
        let game = (board["n"] as? Int).map { "Game \($0)" }
        if clock == "draft" {
            return Line(text: [title ?? "", game, "draft"].compactMap { $0 }.joined(separator: " · "), clock: clock,
                        fetchedAt: Date(), ms: ms())
        }
        var sides = [board["radiant"], board["dire"]].compactMap { $0 as? [String: Any] }
        if sides.count == 2, let first = teams.first?.id, (sides[1]["id"] as? String) == first { sides.reverse() }
        var parts: [String] = []
        if sides.count == 2 {
            let a = short(sides[0]["id"] as? String) ?? (sides[0]["name"] as? String) ?? "?"
            let b = short(sides[1]["id"] as? String) ?? (sides[1]["name"] as? String) ?? "?"
            parts.append("\(a) \(sides[0]["kills"] as? Int ?? 0)–\(sides[1]["kills"] as? Int ?? 0) \(b)")
        }
        if let lead = board["gold_lead"] as? [String: Any], let amount = lead["amount"] as? Int {
            if amount >= 1000, let who = short(lead["id"] as? String) {
                parts.append(String(format: "%@ +%.1fk gold", who, Double(amount) / 1000))
            } else {
                parts.append("level on gold")
            }
        }
        if let clock { parts.append(clock) }
        if let game { parts.append(game) }
        return Line(text: parts.joined(separator: " · "), clock: clock, fetchedAt: Date(), ms: ms())
    }

    /// The live Dota 2 game in /api/live, if there's one (the first, if there are several).
    private func findLive() async -> String? {
        guard let live = await get("/api/live"), let sports = live["sports"] as? [[String: Any]] else { return nil }
        for sport in sports {
            let key = "\(sport["key"] ?? "") \(sport["label"] ?? "")".lowercased()
            guard key.contains("dota") else { continue }
            let games = (sport["games"] as? [[String: Any]]) ?? []
            if let g = games.first(where: { ($0["live"] as? Bool) ?? false }) ?? games.first,
               let match = g["match"] as? String {
                Log.json("strip", ["found": match, "title": g["title"] ?? "", "of": games.count])
                return match
            }
        }
        return nil
    }

    private func get(_ path: String) async -> [String: Any]? {
        var req = URLRequest(url: MatchFeed.base.appendingPathComponent(path), timeoutInterval: 3)
        req.setValue("SlamReplay-spike/0.1", forHTTPHeaderField: "User-Agent")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                Log.json("strip", ["get": path, "status": (resp as? HTTPURLResponse)?.statusCode ?? -1])
                return nil
            }
            return try JSONSerialization.jsonObject(with: data) as? [String: Any]
        } catch {
            Log.json("strip", ["get": path, "error": error.localizedDescription])
            return nil
        }
    }
}
