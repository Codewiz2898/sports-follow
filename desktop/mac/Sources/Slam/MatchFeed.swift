import Foundation

/// The stat strip's words, from Slam's own match page API (the same numbers the stann.in match page shows):
/// "YNDX 21–18 GL · YNDX +4.4k gold · 26:00". Read once per save, never polled.
final class MatchFeed {
    /// The match set by hand (--match, or the menu's Stat strip match…): used for every save until changed.
    /// Without one, each save looks for the live Dota 2 game afresh (findLive), and the app only takes it when
    /// its clock agrees with the one on screen (AppController.save).
    var pinned: String?
    private(set) var title: String?

    init(pinned: String? = nil) { self.pinned = pinned.flatMap(MatchFeed.parse) }

    /// A match page's address, "<source>/<game id>", from a match page link (query and fragment ignored) or as it is.
    static func parse(_ text: String) -> String? {
        var t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.contains("://") {
            guard let c = URLComponents(string: t), c.path.contains("/match/") else { return nil }
            t = c.path
        }
        guard let r = t.range(of: #"[a-z0-9_]+/[A-Za-z0-9][A-Za-z0-9_.:-]*$"#, options: .regularExpression) else { return nil }
        return String(t[r])
    }

    /// The seconds a clock like "26:00" says, for comparing the API's with the screen's.
    static func seconds(_ clock: String?) -> Int? {
        guard let parts = clock?.split(separator: ":"), parts.count == 2, let m = Int(parts[0]), let s = Int(parts[1]) else { return nil }
        return m * 60 + s
    }

    struct Line {
        let moment: Moment
        let match: String  // the match page it was read from
        let auto: Bool  // found as the live game, rather than set by hand
        let fetchedAt: Date
        let ms: Double
        var text: String { moment.line }
        var clock: String? { moment.clock }
    }

    /// The moment for now, or nil (no match, or the API didn't answer within 3 s). A match with no map
    /// being played gives its teams and series score only.
    func line() async -> Line? {
        let t0 = Date()
        let found = pinned == nil
        var onNow: String?
        if found { onNow = await findLive() }
        guard let path = pinned ?? onNow, let resp = await get("/api/match/\(path)") else { return nil }
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
        var m = Moment(source: "api")
        if teams.count == 2 {
            m.teams = [teams[0].short, teams[1].short]
            if let a = teams[0].score, let b = teams[1].score { m.series = "\(teams[0].short) \(a)–\(b) \(teams[1].short) in maps" }
        }
        m.competition = (resp["competition"] as? String) ?? (live?["competition"] as? String)
        let done = { Line(moment: m, match: path, auto: found, fetchedAt: Date(), ms: Date().timeIntervalSince(t0) * 1000) }
        guard let board = (state?["dota"] as? [String: Any])?["live"] as? [String: Any] else {
            Log.json("strip", ["match": path, "note": "no live map on the page", "is_live": live?["is_live"] ?? false])
            return m.teams == nil ? nil : done()
        }
        m.game = board["n"] as? Int
        let clock = board["clock"] as? String
        guard clock != "draft" else { m.clock = "draft"; return done() }
        m.clock = clock
        var sides = [board["radiant"], board["dire"]].compactMap { $0 as? [String: Any] }
        if sides.count == 2, let first = teams.first?.id, (sides[1]["id"] as? String) == first { sides.reverse() }
        if sides.count == 2 {
            m.kills = [sides[0]["kills"] as? Int ?? 0, sides[1]["kills"] as? Int ?? 0]
            if m.teams == nil {
                m.teams = sides.map { short($0["id"] as? String) ?? ($0["name"] as? String) ?? "?" }
            }
        }
        if let lead = board["gold_lead"] as? [String: Any], let amount = lead["amount"] as? Int {
            m.gold = amount >= 1000 ? short(lead["id"] as? String).map { String(format: "%@ +%.1fk gold", $0, Double(amount) / 1000) }
                : "level on gold"
        }
        return done()
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
        var req = URLRequest(url: Settings.server.appendingPathComponent(path), timeoutInterval: 3)  // where clips go too
        req.setValue("SlamMac/1", forHTTPHeaderField: "User-Agent")
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
