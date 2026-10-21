import Foundation
import Security

/// What a clip was recorded from, which decides whether it may be published (the server's clips.RIGHTS,
/// BHA-369). Only a game's own window is: a browser shows a stream (a broadcaster's production), and any other
/// app (a web app, a video player) might be showing one too, so those are saved and shared but not published.
enum ClipSource: String, Codable {
    case gameClient = "game_client"
    case browser
    case other  // an app that's neither a known game nor a browser; never sent to the server

    /// The games whose own windows may be published, by bundle id.
    static let games: Set<String> = ["com.valvesoftware.dota2"]
    /// Browsers, by bundle id prefix (their web apps and channels too): for the note the panel shows.
    static let browserPrefixes = [
        "com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox", "com.brave.Browser", "com.microsoft.edgemac",
        "company.thebrowser", "com.operasoftware.Opera", "com.vivaldi.Vivaldi", "org.chromium.Chromium",
        "com.duckduckgo", "com.kagi.kagimacOS",
    ]

    /// From the apps a capture shows: a known game's alone, else a browser if any is one, else other.
    static func of(bundleIDs: [String]) -> ClipSource {
        if !bundleIDs.isEmpty, bundleIDs.allSatisfy(games.contains) { return .gameClient }
        if bundleIDs.contains(where: { id in browserPrefixes.contains { id.hasPrefix($0) } }) { return .browser }
        return .other
    }

    var publishable: Bool { self == .gameClient }

    /// Why a clip can't be published, for the panel.
    var refusal: String? {
        switch self {
        case .gameClient: return nil
        case .browser: return "Recorded from a browser, so it shows a broadcast, which is its organiser's: save or share it. Slam doesn't publish broadcasts yet."
        case .other: return "Only clips recorded from a game's own window are published for now (Dota 2): save or share this one."
        }
    }
}

/// Where clips are published, and the publishing key that lets this Mac do it (made on the server with
/// `python -m sports_follow.clips key "<name>"`). The key lives in the login Keychain, the address in defaults.
enum Settings {
    static let defaultServer = URL(string: "https://stann.in")!
    private static let service = "io.slam.mac.publishing-key"

    static var server: URL {
        get { UserDefaults.standard.string(forKey: "server").flatMap(URL.init(string:)) ?? defaultServer }
        set { UserDefaults.standard.set(newValue.absoluteString, forKey: "server") }
    }

    static var publishingKey: String? {
        var out: CFTypeRef?
        let q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                  kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Keeps the key, replacing the one kept (never removed first, so a failure leaves the old one): nil
    /// when it's kept, else what went wrong.
    static func keep(publishingKey key: String) -> String? {
        let q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service]
        let data = Data(key.utf8)
        var status = SecItemUpdate(q as CFDictionary, [kSecValueData: data] as CFDictionary)
        if status == errSecItemNotFound {
            var add = q
            add[kSecValueData] = data
            add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            return "The key couldn't be kept in the Keychain: \(SecCopyErrorMessageString(status, nil) as String? ?? "error \(status)")."
        }
        return nil
    }
}
