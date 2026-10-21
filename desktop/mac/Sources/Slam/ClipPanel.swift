import AVKit
import AppKit

/// The end of the create flow: the cut playing, what it shows (the moment, its match, a caption), and
/// Share, Show in Finder or Publish to Slam.
final class ClipPanel: NSWindowController, NSWindowDelegate {
    struct Clip {
        let cut: URL
        let title: String  // "Clip 17:34" or "Montage"
        var moment: Moment?
        var match: String?  // "pandascore_esports/1753729"
        let source: ClipSource
        let posterAt: Double  // a second in the replay's action, for the poster frame
    }

    private var clip: Clip
    private let player: AVPlayer
    private let line = NSTextField(labelWithString: "")
    private let matchField = NSTextField()
    private let caption = NSTextField()
    private let note = NSTextField(wrappingLabelWithString: "")
    private let bar = NSProgressIndicator()
    private let publishButton = NSButton(title: "Publish to Slam", target: nil, action: nil)
    private var published: Publisher.Published?
    private let onClose: (ClipPanel) -> Void

    init(clip: Clip, onClose: @escaping (ClipPanel) -> Void) {
        self.clip = clip
        self.onClose = onClose
        player = AVPlayer(url: clip.cut)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 620),
                             styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "\(clip.title) ready"
        super.init(window: window)
        window.delegate = self
        window.isReleasedWhenClosed = false
        build()
        refresh()
        window.center()
    }

    required init?(coder: NSCoder) { fatalError("not from a nib") }

    private func build() {
        let view = AVPlayerView()
        view.player = player
        view.controlsStyle = .inline
        view.translatesAutoresizingMaskIntoConstraints = false
        view.heightAnchor.constraint(equalToConstant: 390).isActive = true

        line.font = .boldSystemFont(ofSize: 15)
        line.lineBreakMode = .byTruncatingTail
        matchField.placeholderString = "The match's stann.in link (optional): its clips go in its feeds"
        matchField.stringValue = clip.match.map { "\(Settings.server.absoluteString)/match/\($0)" } ?? ""
        caption.placeholderString = "Say what happened (optional)"
        note.textColor = .secondaryLabelColor
        note.font = .systemFont(ofSize: 12)
        bar.isIndeterminate = false
        bar.minValue = 0
        bar.maxValue = 1
        bar.isHidden = true

        let share = NSButton(title: "Share…", target: self, action: #selector(share(_:)))
        let finder = NSButton(title: "Show in Finder", target: self, action: #selector(showInFinder))
        publishButton.target = self
        publishButton.action = #selector(publish)
        publishButton.keyEquivalent = "\r"
        let buttons = NSStackView(views: [share, finder, NSView(), publishButton])
        buttons.orientation = .horizontal

        let stack = NSStackView(views: [view, line, labelled("Match", matchField), labelled("Caption", caption), note, bar, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        for v in [view, buttons, bar, note] { v.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -32).isActive = true }
        window?.contentView = stack
    }

    private func labelled(_ name: String, _ field: NSTextField) -> NSView {
        let label = NSTextField(labelWithString: name)
        label.alignment = .right
        label.widthAnchor.constraint(equalToConstant: 60).isActive = true
        field.widthAnchor.constraint(equalToConstant: 600).isActive = true
        let row = NSStackView(views: [label, field])
        row.orientation = .horizontal
        return row
    }

    private func refresh() {
        line.stringValue = clip.moment.map(\.line).flatMap { $0.isEmpty ? nil : $0 } ?? "No match numbers on this clip"
        if !clip.source.publishable {
            note.stringValue = "Recorded from a browser, so it shows a broadcast, which is its organiser's: save or share it. Slam doesn't publish broadcasts yet."
            publishButton.isEnabled = false
        } else if Settings.publishingKey == nil {
            note.stringValue = "To publish, add this Mac's publishing key in Publishing settings (the Slam menu)."
            publishButton.title = "Publishing settings…"
        } else if let published {
            note.stringValue = "Published. It's in its match's and its competition's feeds on Slam."
            publishButton.title = "Open on Slam"
            _ = published
        } else {
            note.stringValue = "Publishing puts it on Slam for everyone: in its match's feed, its competition's and its players'."
            publishButton.title = "Publish to Slam"
        }
    }

    func show() {
        showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
        player.isMuted = false
        player.play()
    }

    @objc private func share(_ sender: NSButton) {
        NSSharingServicePicker(items: [clip.cut]).show(relativeTo: sender.bounds, of: sender, preferredEdge: .minY)
    }

    @objc private func showInFinder() {
        NSWorkspace.shared.activateFileViewerSelecting([clip.cut])
    }

    @objc private func publish() {
        if let published {
            let path = published.match.map { "/match/\($0)" } ?? ""
            NSWorkspace.shared.open(Settings.server.appendingPathComponent(path))
            return
        }
        guard let key = Settings.publishingKey else {
            PublishingSettings.show { [weak self] in self?.refresh() }
            return
        }
        let typed = matchField.stringValue.trimmingCharacters(in: .whitespaces)
        if !typed.isEmpty, MatchFeed.parse(typed) == nil {
            note.stringValue = "That isn't a match link: paste a stann.in match page's address, or leave it empty."
            return
        }
        clip.match = typed.isEmpty ? nil : MatchFeed.parse(typed)
        publishButton.isEnabled = false
        bar.isHidden = false
        let clip = self.clip, text = caption.stringValue
        let publisher = Publisher(server: Settings.server, key: key)
        Task {
            do {
                let done = try await publisher.publish(cut: clip.cut, moment: clip.moment, match: clip.match, caption: text,
                                                       source: clip.source, posterAt: clip.posterAt) { [weak self] step, part in
                    DispatchQueue.main.async {
                        self?.note.stringValue = step
                        self?.bar.doubleValue = part
                    }
                }
                Log.json("published", ["id": done.id, "match": done.match ?? "", "video": done.video])
                await MainActor.run {
                    self.published = done
                    self.bar.isHidden = true
                    self.publishButton.isEnabled = true
                    self.refresh()
                }
            } catch {
                Log.json("publish-failed", ["error": error.localizedDescription])
                await MainActor.run {
                    self.note.stringValue = "Not published: \(error.localizedDescription)"
                    self.bar.isHidden = true
                    self.publishButton.isEnabled = true
                }
            }
        }
    }

    func windowWillClose(_ notification: Notification) {
        player.pause()
        onClose(self)
    }
}

/// Where to publish, and this Mac's publishing key.
enum PublishingSettings {
    static func show(then done: @escaping () -> Void = {}) {
        let alert = NSAlert()
        alert.messageText = "Publishing to Slam"
        alert.informativeText = "Paste this Mac's publishing key. An admin makes one on the server with:\npython -m sports_follow.clips key \"<name>\""
        let server = NSTextField(frame: NSRect(x: 0, y: 30, width: 380, height: 24))
        server.stringValue = Settings.server.absoluteString
        server.placeholderString = "https://stann.in"
        let key = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 380, height: 24))
        key.placeholderString = Settings.publishingKey == nil ? "slam_pk_…" : "•••••••• (kept; paste a new one to change it)"
        let box = NSView(frame: NSRect(x: 0, y: 0, width: 380, height: 54))
        box.addSubview(server)
        box.addSubview(key)
        alert.accessoryView = box
        alert.addButton(withTitle: "Save and check")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        if let url = URL(string: server.stringValue.trimmingCharacters(in: .whitespaces)), url.scheme?.hasPrefix("http") == true {
            Settings.server = url
        }
        let typed = key.stringValue.trimmingCharacters(in: .whitespaces)
        if !typed.isEmpty { Settings.publishingKey = typed }
        done()
        guard let k = Settings.publishingKey else { return }
        Task {
            let result = await Publisher(server: Settings.server, key: k).check()
            await MainActor.run {
                let a = NSAlert()
                a.messageText = result
                a.runModal()
            }
        }
    }
}
