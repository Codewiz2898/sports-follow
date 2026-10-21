import AppKit
import QuartzCore

/// The overlays Bharath picked on the design canvas (21 Oct): the B1 scorebug during the clip, the E1 title
/// card over its first 2 s, the E2 replay slate as the slow part starts, and C2's cinema bars through it.
/// Sizes are the canvas's, for a 624-high frame, scaled by `u`. Core Animation's y goes up from the bottom.
struct Overlays {
    let W: CGFloat
    let H: CGFloat
    var unit: CGFloat?  // set for a 9:16 frame, where the height would make everything too big
    var u: CGFloat { unit ?? H / 624 }

    static let crimson = NSColor(srgbRed: 0xa3 / 255, green: 0x21 / 255, blue: 0x3a / 255, alpha: 1)  // Dota 2's colour on Slam
    static let ink = NSColor(srgbRed: 0x0d / 255, green: 0x0d / 255, blue: 0x0d / 255, alpha: 1)
    static let paper = NSColor(srgbRed: 0xf3 / 255, green: 0xf3 / 255, blue: 0xef / 255, alpha: 1)
    static let blue = NSColor(srgbRed: 0x1f / 255, green: 0x5b / 255, blue: 0xef / 255, alpha: 1)
    static let red = NSColor(srgbRed: 0xdc / 255, green: 0x20 / 255, blue: 0x16 / 255, alpha: 1)
    static let replayRed = NSColor(srgbRed: 0xff / 255, green: 0x4d / 255, blue: 0x42 / 255, alpha: 1)
    static let muted = NSColor(srgbRed: 0x55 / 255, green: 0x57 / 255, blue: 0x5c / 255, alpha: 1)
    static let radiant = NSColor(srgbRed: 0x8f / 255, green: 0xd1 / 255, blue: 0x6a / 255, alpha: 1)
    static let dire = NSColor(srgbRed: 0xff / 255, green: 0x7a / 255, blue: 0x6b / 255, alpha: 1)

    /// Anton, slanted like the app's display type (skewX(-8deg) on the web).
    static let slant = CGAffineTransform(a: 1, b: 0, c: tan(8 * .pi / 180), d: 1, tx: 0, ty: 0)

    private func text(_ s: String, _ font: String, _ size: CGFloat, _ color: NSColor, kern: CGFloat = 0) -> CALayer {
        ClipEditor.textLayer(s, size: size * u, color: color, frame: .zero, font: font, kern: kern * u)
    }

    private func box(_ color: NSColor, _ frame: CGRect, radius: CGFloat = 0) -> CALayer {
        let l = CALayer()
        l.frame = frame
        l.backgroundColor = color.cgColor
        l.cornerRadius = radius * u
        return l
    }

    /// Puts `t` in `parent` at x, centred vertically in a row from y to y + h.
    @discardableResult
    private func place(_ t: CALayer, in parent: CALayer, x: CGFloat, y: CGFloat, h: CGFloat) -> CGFloat {
        t.frame.origin = CGPoint(x: x, y: y + (h - t.bounds.height) / 2)
        parent.addSublayer(t)
        return x + t.bounds.width
    }

    // MARK: B1 · the scorebug, top left under Dota's own menu icons and K/D/A box

    func scorebug(_ m: Moment) -> CALayer? {
        guard m.kills != nil || m.clock != nil else { return nil }
        let root = CALayer()
        let rowH = 36 * u, subH = 22 * u, pad = 10 * u, gap = 10 * u
        var x: CGFloat = 0
        let y = subH  // row one sits on top of the gold row
        var blocks: [CALayer] = []
        func block(_ color: NSColor, _ parts: [CALayer], edge: NSColor? = nil, minW: CGFloat = 0) {
            let inner = parts.reduce(0) { $0 + $1.bounds.width } + gap * CGFloat(max(0, parts.count - 1))
            let edgeW: CGFloat = edge == nil ? 0 : 4 * u
            let w = max(minW, inner + pad * 2 + edgeW)
            let b = box(color, CGRect(x: x, y: y, width: w, height: rowH))
            if let edge { b.addSublayer(box(edge, CGRect(x: 0, y: 0, width: edgeW, height: rowH))) }
            var cx = edgeW + (w - edgeW - inner) / 2
            for p in parts { cx = place(p, in: b, x: cx, y: 0, h: rowH) + gap }
            blocks.append(b)
            x += w
        }
        if let g = m.game {
            block(Overlays.crimson, [text("G\(g)", "Anton", 16, .white)], minW: 38 * u)
        }
        if let k = m.kills {
            let s = m.sides
            block(Overlays.ink, [text(short(s[0]), "Anton", 18, .white), text("\(k[0])", "Anton", 22, .white)], edge: Overlays.radiant)
            block(Overlays.ink, [text("\(k[1])", "Anton", 22, .white), text(short(s[1]), "Anton", 18, .white)], edge: Overlays.dire)
        }
        if let c = m.clock {
            block(Overlays.paper, [text(c, "Anton", 18, Overlays.ink)])
        }
        let width = max(x, 150 * u)
        let sub = box(Overlays.ink.withAlphaComponent(0.82), CGRect(x: 0, y: 0, width: width, height: subH))
        place(text((m.gold ?? m.competition ?? "Dota 2").uppercased(), "Manrope", 11, .white, kern: 0.6), in: sub, x: pad, y: 0, h: subH)
        let tagText = text("slam", "Fredoka", 12, .white)
        let tag = box(Overlays.blue, CGRect(x: 0, y: 0, width: tagText.bounds.width + 14 * u, height: 16 * u), radius: 4)
        place(tagText, in: tag, x: 7 * u, y: 0, h: tag.bounds.height)
        tag.frame.origin = CGPoint(x: width - tag.bounds.width - 6 * u, y: (subH - tag.bounds.height) / 2)
        sub.addSublayer(tag)
        root.addSublayer(sub)
        blocks.forEach(root.addSublayer)
        root.frame = CGRect(x: 14 * u, y: H - (74 * u) - (rowH + subH), width: width, height: rowH + subH)
        return root
    }

    private func short(_ name: String) -> String {
        name == "Radiant" ? "RAD" : name == "Dire" ? "DIRE" : name
    }

    // MARK: C2 · cinema bars through the slow part

    func cinemaBars(_ m: Moment) -> CALayer {
        let root = CALayer()
        root.frame = CGRect(x: 0, y: 0, width: W, height: H)
        let barH = 58 * u
        root.addSublayer(box(Overlays.ink, CGRect(x: 0, y: H - barH, width: W, height: barH)))
        let bottom = box(Overlays.ink, CGRect(x: 0, y: 0, width: W, height: barH))
        let replay = text("REPLAY", "Anton", 30, Overlays.replayRed, kern: 0.5)
        replay.setAffineTransform(Overlays.slant)
        var x = place(replay, in: bottom, x: 18 * u, y: 0, h: barH) + 14 * u
        var words = ["½ speed"]
        if let k = m.kills { words.append(m.teams.map { "\($0[0]) \(k[0])–\(k[1]) \($0[1])" } ?? "\(k[0])–\(k[1])") }
        if let c = m.clock { words.append(c) }
        x = place(text(words.joined(separator: " · "), "Manrope", 13, .white), in: bottom, x: x, y: 0, h: barH)
        let logo = text("slam", "Fredoka", 20, .white)
        place(logo, in: bottom, x: W - 18 * u - logo.bounds.width, y: 0, h: barH)
        root.addSublayer(bottom)
        return root
    }

    // MARK: E2 · the replay slate

    func slate() -> CALayer {
        let root = CALayer()
        root.frame = CGRect(x: 0, y: 0, width: W, height: H)
        let bandH = 240 * u
        let band = box(Overlays.red, CGRect(x: -0.1 * W, y: H - 270 * u - bandH / 2, width: W * 1.2, height: bandH))
        let big = text("REPLAY", "Anton", 140, .white, kern: 2)
        big.setAffineTransform(Overlays.slant)
        let small = text("HALF SPEED · LAST 5 SECONDS", "Manrope", 18, .white, kern: 1)
        let stackH = big.bounds.height + small.bounds.height
        big.frame.origin = CGPoint(x: (band.bounds.width - big.bounds.width) / 2, y: (bandH - stackH) / 2 + small.bounds.height)
        small.frame.origin = CGPoint(x: (band.bounds.width - small.bounds.width) / 2, y: (bandH - stackH) / 2)
        band.addSublayer(big)
        band.addSublayer(small)
        band.setAffineTransform(CGAffineTransform(rotationAngle: 6 * .pi / 180))  // the canvas's rotate(-6deg), y up
        root.addSublayer(band)  // the cinema bar under it already carries the slam name
        return root
    }

    // MARK: E1 · the title card over the first 2 s

    func titleCard(_ m: Moment) -> CALayer {
        let root = box(Overlays.paper, CGRect(x: 0, y: 0, width: W, height: H))
        root.addSublayer(box(Overlays.crimson, CGRect(x: 0, y: 0, width: 36 * u, height: H)))
        let left = 36 * u + 56 * u, right = W - 56 * u
        let kicker = text(m.isEmpty ? "CLIPPED ON SLAM" : m.kicker, "Manrope", 15, Overlays.crimson, kern: 1.5)
        kicker.frame.origin = CGPoint(x: left, y: H - 48 * u - kicker.bounds.height)
        root.addSublayer(kicker)
        let head = text(m.headline, "Anton", 118, Overlays.ink)
        head.anchorPoint = CGPoint(x: 0, y: 0.5)
        head.frame.origin = CGPoint(x: left, y: kicker.frame.minY - 6 * u - head.bounds.height)
        head.setAffineTransform(Overlays.slant)
        root.addSublayer(head)

        // Tiles: kills, gold, clock; whichever are known.
        var tiles: [(value: String, label: String, dark: Bool)] = []
        if let k = m.kills {
            tiles.append(("\(k[0])–\(k[1])", "kills, \(m.sides[0]) first", false))
        }
        if let g = m.gold {
            if g == "level on gold" { tiles.append(("Level", "on gold", false)) } else {
                let parts = g.split(separator: " ")  // "TY +4.4k gold"
                tiles.append((parts.count >= 2 ? String(parts[1]) : g, parts.count >= 2 ? "\(parts[0]) up on gold" : "gold", false))
            }
        }
        if let c = m.clock { tiles.append((c, "on the clock", true)) }
        if !tiles.isEmpty {
            let gap = 16 * u, th = 100 * u
            let tw = (right - left - gap * 2) / 3
            for (i, t) in tiles.enumerated() {
                let tile = box(t.dark ? Overlays.ink : .white, CGRect(x: left + CGFloat(i) * (tw + gap), y: 124 * u, width: tw, height: th), radius: 18)
                let v = text(t.value, "Anton", 44, t.dark ? .white : Overlays.ink)
                let l = text(t.label, "Manrope", 13, t.dark ? NSColor.white.withAlphaComponent(0.78) : Overlays.muted)
                l.frame.origin = CGPoint(x: 20 * u, y: 16 * u)
                v.frame.origin = CGPoint(x: 20 * u, y: l.frame.maxY + 2 * u)
                tile.addSublayer(v)
                tile.addSublayer(l)
                root.addSublayer(tile)
            }
        }
        let foot = text("The 30 s before it, then a half-speed replay", "Manrope", 14, Overlays.muted)
        let logo = text("slam", "Fredoka", 40, Overlays.blue)
        logo.frame.origin = CGPoint(x: right - logo.bounds.width, y: 40 * u)
        foot.frame.origin = CGPoint(x: left, y: 40 * u + (logo.bounds.height - foot.bounds.height) / 2)
        root.addSublayer(foot)
        root.addSublayer(logo)
        return root
    }

    // MARK: the montage's pieces

    /// A clip's title in the scorebug's look: [3/5][TY 21–18 GL · 26:00].
    func montageChip(n: Int, of count: Int, title: String) -> CALayer {
        let h = 36 * u, pad = 10 * u
        let num = text("\(n)/\(count)", "Anton", 16, .white)
        let words = text(title, "Anton", 18, .white)
        let nw = max(38 * u, num.bounds.width + pad * 2)
        let ww = min(W - 28 * u - nw, words.bounds.width + pad * 2)
        let root = CALayer()
        let a = box(Overlays.crimson, CGRect(x: 0, y: 0, width: nw, height: h))
        place(num, in: a, x: (nw - num.bounds.width) / 2, y: 0, h: h)
        let b = box(Overlays.ink, CGRect(x: nw, y: 0, width: ww, height: h))
        b.masksToBounds = true
        place(words, in: b, x: pad, y: 0, h: h)
        root.addSublayer(a)
        root.addSublayer(b)
        root.frame = CGRect(x: 14 * u, y: H - 74 * u - h, width: nw + ww, height: h)
        return root
    }

    /// The end card on every cut and montage: "clipped on | slam", with the match line under it on a montage.
    func endCard(kicker: String?) -> CALayer {
        let root = box(Overlays.paper, CGRect(x: 0, y: 0, width: W, height: H))
        root.addSublayer(box(Overlays.crimson, CGRect(x: 0, y: 0, width: 36 * u, height: H)))
        let words = text("clipped on", "Manrope", 30, Overlays.ink)
        let logo = text("slam", "Fredoka", 84, Overlays.blue)
        let gap = 26 * u, rule = 3 * u, ruleH = 70 * u
        let rowW = words.bounds.width + gap + rule + gap + logo.bounds.width
        let x0 = 36 * u + (W - 36 * u - rowW) / 2
        let mid = H / 2 + (kicker == nil ? 0 : 14 * u)
        words.frame.origin = CGPoint(x: x0, y: mid - words.bounds.height / 2)
        root.addSublayer(words)
        root.addSublayer(box(Overlays.ink, CGRect(x: words.frame.maxX + gap, y: mid - ruleH / 2, width: rule, height: ruleH)))
        logo.frame.origin = CGPoint(x: words.frame.maxX + gap + rule + gap, y: mid - logo.bounds.height / 2)
        root.addSublayer(logo)
        if let kicker, !kicker.isEmpty {
            let k = text(kicker, "Manrope", 15, Overlays.crimson, kern: 1.5)
            k.frame.origin = CGPoint(x: 36 * u + (W - 36 * u - k.bounds.width) / 2, y: mid - ruleH / 2 - 22 * u - k.bounds.height)
            root.addSublayer(k)
        }
        return root
    }
}
