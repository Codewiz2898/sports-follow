import Foundation
import Security

/// What a clip was recorded from, which decides whether it may be published (the server's clips.RIGHTS,
/// BHA-369): a game's own window yes; a browser window, which shows a stream (a broadcaster's production),
/// is saved and shared but not published while Slam's own admins post clips.
enum ClipSource: String, Codable {
    case gameClient = "game_client"
    case browser
    case file

    static let browsers: Set<String> = [
        "com.apple.Safari", "com.apple.SafariTechnologyPreview", "com.google.Chrome", "com.google.Chrome.canary",
        "org.mozilla.firefox", "com.brave.Browser", "com.microsoft.edgemac", "company.thebrowser.Browser",
        "com.operasoftware.Opera", "com.vivaldi.Vivaldi", "org.chromium.Chromium",
    ]

    /// From the apps a capture shows: any browser makes it a browser clip.
    static func of(bundleIDs: [String]) -> ClipSource {
        bundleIDs.contains(where: browsers.contains) ? .browser : .gameClient
    }

    var publishable: Bool { self != .browser }
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
        get {
            var out: CFTypeRef?
            let q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                                      kSecReturnData: true, kSecMatchLimit: kSecMatchLimitOne]
            guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
            return String(data: data, encoding: .utf8)
        }
        set {
            let q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: service]
            SecItemDelete(q as CFDictionary)
            guard let newValue, !newValue.isEmpty else { return }
            var add = q
            add[kSecValueData] = Data(newValue.utf8)
            add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
            SecItemAdd(add as CFDictionary, nil)
        }
    }
}
