import AppKit
import ScreenCaptureKit

/// The menu-bar app: a status item, the ⌃⇧S hotkey, `kill -USR1` to save from a script,
/// and a stats line in the log every few seconds.
final class AppController: NSObject, NSApplicationDelegate, NSMenuDelegate, SCContentSharingPickerObserver {
    let opts: Options
    let probeOnly: Bool
    private var recorder: Recorder?
    private var probe: FPSProbe?
    private var item: NSStatusItem!
    private var statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    private var hotKey: HotKey?
    private var signals: [DispatchSourceSignal] = []
    private var statsTimer: Timer?
    private let proc = ProcessStats()
    private var lastStats = Date()
    private var saveChain: Task<Void, Never>?
    private var saves = 0
    private let started = Date()
    private var attempts = 0
    private var edit: Bool
    private let feed: MatchFeed
    private var lastClip: URL?
    private var editItem: NSMenuItem?
    private var matchItem: NSMenuItem?

    init(opts: Options, probeOnly: Bool) {
        self.opts = opts
        self.probeOnly = probeOnly
        edit = opts.edit
        feed = MatchFeed(path: opts.match)
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        Log.json("launch", ["target": opts.target, "probeOnly": probeOnly, "picker": opts.picker,
                            "preflight": CGPreflightScreenCaptureAccess(),
                            "pid": Int(getpid()), "bundle": Bundle.main.bundleIdentifier ?? "-"])
        buildMenu()
        hotKey = HotKey { [weak self] in DispatchQueue.main.async { self?.save(trigger: "hotkey") } }
        onSignal(SIGUSR1) { [weak self] in self?.save(trigger: "signal") }
        onSignal(SIGTERM) { [weak self] in self?.quit() }
        onSignal(SIGINT) { [weak self] in self?.quit() }
        statsTimer = Timer.scheduledTimer(withTimeInterval: opts.statsEvery, repeats: true) { [weak self] _ in
            self?.logStats()
        }
        if opts.fpsProbe || probeOnly {
            probe = FPSProbe(target: opts.target)
            probe?.start()
        }
        if probeOnly {
            statusLine.title = "Probe only (no recorder)"
            return
        }
        if opts.picker { presentPicker() } else { tryStart() }
    }

    // MARK: starting

    private func tryStart() {
        attempts += 1
        let r = Recorder(opts: opts)
        r.onStateChange = { [weak self] s in DispatchQueue.main.async { self?.setState(s) } }
        Task {
            do {
                try await r.startWithPermission()
                await MainActor.run { self.recorder = r }
            } catch {
                let ns = error as NSError
                Log.json("start-failed", ["attempt": attempts, "error": error.localizedDescription,
                                          "domain": ns.domain, "code": ns.code,
                                          "preflight": CGPreflightScreenCaptureAccess()])
                // Not running yet: look again soon. Refused (-3801): don't nag, look again slowly.
                let wait: Double = ns.code == SCStreamError.userDeclined.rawValue ? 15 : 5
                await MainActor.run {
                    self.setState(error.localizedDescription)
                    DispatchQueue.main.asyncAfter(deadline: .now() + wait) { self.tryStart() }
                }
            }
        }
    }

    private func presentPicker() {
        let picker = SCContentSharingPicker.shared
        var cfg = SCContentSharingPickerConfiguration()
        cfg.allowedPickerModes = [.singleApplication, .singleWindow]
        picker.defaultConfiguration = cfg
        picker.add(self)
        picker.isActive = true
        picker.present()
        setState("Pick the game in the picker")
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        Log.json("picker", ["picked": Recorder.describe(filter.contentRect), "preflight": CGPreflightScreenCaptureAccess()])
        let r = Recorder(opts: opts)
        r.onStateChange = { [weak self] s in DispatchQueue.main.async { self?.setState(s) } }
        Task {
            await recorder?.stop()
            do {
                try await r.start(pickedFilter: filter)
                await MainActor.run { self.recorder = r }
            } catch {
                Log.line("picker start failed: \(error)")
            }
        }
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Log.json("picker", ["cancelled": true])
        setState("Picker cancelled")
    }

    func contentSharingPickerStartDidFailWithError(_ error: Error) {
        Log.line("picker failed: \(error)")
    }

    // MARK: saving

    func save(trigger: String) {
        guard let recorder else {
            Log.json("save-skipped", ["trigger": trigger, "reason": "not recording"])
            return
        }
        let tap = Date()
        let t0 = DispatchTime.now().uptimeNanoseconds
        guard let snap = recorder.ring.snapshot() else {
            Log.json("save-skipped", ["trigger": trigger, "reason": "ring empty"])
            return
        }
        let snapMs = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
        let url = ClipWriter.newURL(label: opts.label)
        let previous = saveChain
        let edit = self.edit, feed = self.feed
        let frame = edit ? recorder.latestFrame : nil
        saves += 1
        flash("Saving…")
        // The strip's numbers and the clock on screen, both as of the tap, while the clip is written.
        let line = edit ? Task.detached { await feed.line() } : nil
        let screen = Task.detached { frame.map(FPSProbe.clocks(in:)) ?? [] }
        saveChain = Task.detached {
            await previous?.value  // saves queue; each keeps the 30 s before its own tap
            do {
                let r = try await ClipWriter.write(snap, to: url)
                Log.json("save", ["trigger": trigger, "file": r.url.lastPathComponent, "seconds": round2(r.seconds),
                                  "MB": round2(Double(r.bytes) / 1_048_576), "frames": r.videoFrames,
                                  "audioPackets": r.audioPackets, "snapshotMs": round2(snapMs),
                                  "writeMs": round2(r.writeMs),
                                  "sinceTapMs": round2(Date().timeIntervalSince(tap) * 1000)])
                await MainActor.run {
                    self.lastClip = r.url
                    NSSound(named: "Tink")?.play()
                    self.flash(edit ? "Saved ✓ · cutting…" : "Saved ✓")
                }
                guard edit else { return }
                let strip = await line?.value
                let clocks = await screen.value
                Log.json("strip", ["text": strip?.text ?? "", "apiClock": strip?.clock ?? "", "screenClocks": clocks,
                                   "apiMs": round1(strip?.ms ?? 0), "match": feed.path ?? ""])
                let cut = url.deletingPathExtension().appendingPathExtension("replay.mp4")
                let e = try await ClipEditor.make(from: r.url, to: cut, strip: strip?.text)
                Log.json("cut", ["file": cut.lastPathComponent, "seconds": round2(e.seconds),
                                 "MB": round2(Double(e.bytes) / 1_048_576), "ms": round1(e.ms),
                                 "sinceTapMs": round2(Date().timeIntervalSince(tap) * 1000)])
                await MainActor.run {
                    self.lastClip = cut
                    self.flash("Replay ✓")
                }
            } catch {
                Log.json("save-failed", ["error": error.localizedDescription])
                await MainActor.run { self.flash("Save failed") }
            }
        }
    }

    // MARK: stats

    private func logStats() {
        let now = Date()
        let dt = now.timeIntervalSince(lastStats)
        lastStats = now
        let (cpu, mw) = proc.sample()
        var f: [String: Any] = ["t": Int(now.timeIntervalSince(started)), "cpu": round1(cpu), "mW": round1(mw),
                                "footprintMB": round1(ProcessStats.footprintMB())]
        if let recorder {
            let c = recorder.takeCounters()
            let r = recorder.ring.stats()
            f["captureFps"] = round1(Double(c.complete) / dt)
            f["idleFps"] = round1(Double(c.idle) / dt)
            f["encodedFps"] = round1(Double(c.encoded) / dt)
            f["dropped"] = c.dropped
            f["ringS"] = round1(r.seconds)
            f["ringMB"] = round1(r.videoMB + r.audioMB)
            f["gops"] = r.gops
            f["resets"] = recorder.ring.resets
            if let m = recorder.audioMeter() {
                f["audioPeak"] = round1(m.peak)
                f["audioRms"] = round1(m.rms)
                f["audioBuffers"] = m.buffers
            }
            statusLine.title = String(format: "Recording %@ · %.0f s in memory · %.0f MB · %.0f fps",
                                      opts.label, r.seconds, r.videoMB + r.audioMB, Double(c.encoded) / dt)
        }
        Log.json("stats", f)
    }

    // MARK: menu

    private func buildMenu() {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "● Slam"
        let menu = NSMenu()
        menu.delegate = self
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        let save = NSMenuItem(title: "Save last \(Int(opts.seconds)) s   (⌃⇧S)", action: #selector(saveClicked), keyEquivalent: "")
        save.target = self
        menu.addItem(save)
        let last = NSMenuItem(title: "Play last clip", action: #selector(openLast), keyEquivalent: "")
        last.target = self
        menu.addItem(last)
        let open = NSMenuItem(title: "Open clips folder", action: #selector(openFolder), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        menu.addItem(.separator())
        let pick = NSMenuItem(title: "Choose window to clip…", action: #selector(pickAgain), keyEquivalent: "")
        pick.target = self
        menu.addItem(pick)
        let e = NSMenuItem(title: "Add half-speed replay + stat strip", action: #selector(toggleEdit), keyEquivalent: "")
        e.target = self
        e.state = edit ? .on : .off
        menu.addItem(e)
        editItem = e
        let m = NSMenuItem(title: matchTitle(), action: #selector(setMatch), keyEquivalent: "")
        m.target = self
        menu.addItem(m)
        matchItem = m
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit", action: #selector(quitClicked), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
    }

    @objc private func saveClicked() { save(trigger: "menu") }
    @objc private func openFolder() {
        try? FileManager.default.createDirectory(at: ClipWriter.folder, withIntermediateDirectories: true)
        NSWorkspace.shared.open(ClipWriter.folder)
    }
    @objc private func pickAgain() {
        if opts.picker { SCContentSharingPicker.shared.present() } else { presentPicker() }
    }
    @objc private func openLast() {
        if let lastClip { NSWorkspace.shared.open(lastClip) } else { openFolder() }
    }
    @objc private func toggleEdit() {
        edit.toggle()
        editItem?.state = edit ? .on : .off
    }
    @objc private func setMatch() {
        let alert = NSAlert()
        alert.messageText = "Match for the stat strip"
        alert.informativeText = "Paste the stann.in match page link (or a PandaScore match id). Leave it empty to use the live Dota 2 game."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 380, height: 24))
        field.stringValue = feed.path.map { "https://stann.in/match/\($0)" } ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "Use")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let text = field.stringValue.trimmingCharacters(in: .whitespaces)
        feed.path = text.isEmpty ? nil : MatchFeed.parse(text)
        matchItem?.title = matchTitle()
        Log.json("strip", ["set": feed.path ?? "live Dota game"])
    }
    private func matchTitle() -> String {
        "Stat strip match: " + (feed.path.map { $0.replacingOccurrences(of: "pandascore_esports/", with: "#") } ?? "the live Dota 2 game") + "…"
    }
    @objc private func quitClicked() { quit() }

    private func setState(_ s: String) {
        statusLine.title = s
        Log.json("state", ["state": s])
    }

    private func flash(_ s: String) {
        item.button?.title = s
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.item.button?.title = "● Slam" }
    }

    private func onSignal(_ sig: Int32, _ action: @escaping () -> Void) {
        signal(sig, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        src.setEventHandler(handler: action)
        src.resume()
        signals.append(src)
    }

    private func quit() {
        Task {
            await saveChain?.value
            await recorder?.stop()
            Log.json("quit", ["saves": saves, "uptime": Int(Date().timeIntervalSince(started))])
            Log.flush()
            exit(0)
        }
    }
}

func round1(_ x: Double) -> Double { (x * 10).rounded() / 10 }
func round2(_ x: Double) -> Double { (x * 100).rounded() / 100 }
