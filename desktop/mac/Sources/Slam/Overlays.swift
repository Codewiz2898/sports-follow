import AppKit
import AVFoundation
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

    enum EndStyle: String { case stamp, ink }  // canvas F3/G1 and F6/G2, picked 21 Oct

    /// The animated end card, starting `t0` seconds into the video: the blue stamp (F3) or ink + Anton (F6).
    /// `line` is the match line the ink card ends on ("TY 21–18 GL · DOTA 2 · GAME 2"), if any.
    func endCard(_ style: EndStyle, line: String?, at t0: Double) -> CALayer {
        style == .stamp ? stampCard(at: t0) : inkCard(line: line, at: t0)
    }

    /// A keyframe animation at seconds after t0 (the video's own clock), holding its first value before
    /// and its last after.
    private func kf(_ path: String, _ values: [Any], _ times: [Double], at t0: Double,
                    ease: CAMediaTimingFunctionName = .easeOut) -> CAKeyframeAnimation {
        let a = CAKeyframeAnimation(keyPath: path)
        let start = times.first ?? 0, d = max(0.001, (times.last ?? 0) - start)
        a.values = values
        a.keyTimes = times.map { NSNumber(value: ($0 - start) / d) }
        a.timingFunctions = Array(repeating: CAMediaTimingFunction(name: ease), count: max(1, values.count - 1))
        a.beginTime = AVCoreAnimationBeginTimeAtZero + t0 + start
        a.duration = d
        a.fillMode = .both
        a.isRemovedOnCompletion = false
        return a
    }

    private func stampCard(at t0: Double) -> CALayer {
        let root = box(Overlays.ink, CGRect(x: 0, y: 0, width: W, height: H))
        let panel = box(Overlays.blue, root.bounds)
        panel.add(kf("transform.translation.x", [W, 0], [0, 0.16], at: t0), forKey: "wipe")
        root.addSublayer(panel)
        let group = CALayer()
        group.frame = root.bounds
        let kicker = text("CLIPPED ON", "Manrope", 20, .white, kern: 6)
        let word = text("SLAM", "Anton", 230, .white, kern: 4)
        word.setAffineTransform(Overlays.slant)
        let slam = CALayer()  // scaled from outside; the slant stays on the word inside
        slam.frame = CGRect(origin: .zero, size: word.bounds.size)
        slam.addSublayer(word)
        let bar = box(.white, CGRect(x: 0, y: 0, width: 120 * u, height: 8 * u))
        let total = kicker.bounds.height + 4 * u + word.bounds.height + 10 * u + bar.bounds.height
        var y = H / 2 + total / 2
        y -= kicker.bounds.height
        kicker.frame.origin = CGPoint(x: (W - kicker.bounds.width) / 2, y: y)
        y -= 4 * u + word.bounds.height
        slam.frame.origin = CGPoint(x: (W - word.bounds.width) / 2, y: y)
        y -= 10 * u + bar.bounds.height
        bar.frame.origin = CGPoint(x: (W - bar.bounds.width) / 2, y: y)
        slam.add(kf("transform.scale", [1.8, 0.93, 1.03, 1.0], [0.22, 0.42, 0.54, 0.67], at: t0), forKey: "slam")
        slam.add(kf("opacity", [0, 1], [0.22, 0.4], at: t0), forKey: "in")
        kicker.add(kf("opacity", [0, 1], [0.45, 0.8], at: t0), forKey: "in")
        kicker.add(kf("transform.translation.y", [18 * u, 0], [0.45, 0.8], at: t0), forKey: "drop")
        bar.add(kf("transform.scale.x", [0, 1], [0.64, 0.99], at: t0), forKey: "draw")
        // The impact: a small shake as SLAM lands.
        group.add(kf("transform.translation", [CGSize.zero, CGSize(width: -7 * u, height: -5 * u), CGSize(width: 6 * u, height: 4 * u),
                                               CGSize(width: -3 * u, height: -2 * u), CGSize.zero].map { NSValue(size: $0) },
                     [0.42, 0.45, 0.51, 0.58, 0.64], at: t0, ease: .linear), forKey: "shake")
        group.addSublayer(kicker)
        group.addSublayer(slam)
        group.addSublayer(bar)
        root.addSublayer(group)
        return root
    }

    private func inkCard(line: String?, at t0: Double) -> CALayer {
        let root = box(Overlays.ink, CGRect(x: 0, y: 0, width: W, height: H))
        let left = 96 * u
        let kicker = text("clipped on", "Manrope", 22, NSColor.white.withAlphaComponent(0.78))
        let letters = ["S", "L", "A", "M"].map { text($0, "Anton", 200, .white) }
        let rowH = (letters.map(\.bounds.height).max() ?? 0)
        let rowW = letters.reduce(0) { $0 + $1.bounds.width }
        let badge = CALayer()
        badge.contents = Overlays.badge(size: 72 * u * 2)
        badge.frame = CGRect(x: 0, y: 0, width: 72 * u, height: 72 * u)
        let underline = box(Overlays.blue, CGRect(x: 0, y: 0, width: 420 * u, height: 14 * u))
        var chip: CALayer?
        if let line, !line.isEmpty {
            let t = text(line.uppercased(), "Manrope", 14, Overlays.crimson, kern: 1.5)
            let c = box(Overlays.paper, CGRect(x: 0, y: 0, width: t.bounds.width + 20 * u, height: t.bounds.height + 8 * u))
            t.frame.origin = CGPoint(x: 10 * u, y: 4 * u)
            c.addSublayer(t)
            chip = c
        }
        let total = kicker.bounds.height + 6 * u + rowH + 6 * u + underline.bounds.height + (chip.map { 20 * u + $0.bounds.height } ?? 0)
        var y = H / 2 + total / 2 - kicker.bounds.height
        kicker.frame.origin = CGPoint(x: left, y: y)
        y -= 6 * u + rowH
        // The letters rise from behind a mask, one after another, under the app's slant.
        let mask = CALayer()
        mask.frame = CGRect(x: left, y: y, width: rowW, height: rowH)
        mask.masksToBounds = true
        let slanted = CALayer()
        slanted.frame = mask.bounds
        var x: CGFloat = 0
        for (i, l) in letters.enumerated() {
            l.frame.origin = CGPoint(x: x, y: 0)
            x += l.bounds.width
            let b = 0.29 + Double(i) * 0.095
            l.add(kf("transform.translation.y", [-rowH * 1.1, 0], [b, b + 0.32], at: t0), forKey: "rise")
            slanted.addSublayer(l)
        }
        mask.setAffineTransform(Overlays.slant)
        mask.addSublayer(slanted)
        badge.frame.origin = CGPoint(x: left + rowW + 22 * u, y: y + 26 * u)
        y -= 6 * u + underline.bounds.height
        underline.anchorPoint = CGPoint(x: 0, y: 0.5)
        underline.frame.origin = CGPoint(x: left, y: y)
        if let chip {
            y -= 20 * u + chip.bounds.height
            chip.frame.origin = CGPoint(x: left, y: y)
            chip.add(kf("opacity", [0, 1], [1.12, 1.41], at: t0), forKey: "in")
            chip.add(kf("transform.translation.x", [-28 * u, 0], [1.12, 1.41], at: t0), forKey: "slide")
        }
        kicker.add(kf("opacity", [0, 1], [0.19, 0.45], at: t0), forKey: "in")
        kicker.add(kf("transform.translation.y", [-10 * u, 0], [0.19, 0.45], at: t0), forKey: "up")
        underline.add(kf("transform.scale.x", [0, 1], [0.8, 1.12], at: t0), forKey: "sweep")
        badge.add(kf("transform.scale", [0, 1.18, 1], [0.93, 1.12, 1.25], at: t0), forKey: "pop")
        badge.add(kf("transform.rotation.z", [40 * Double.pi / 180, -6 * Double.pi / 180, 0], [0.93, 1.12, 1.25], at: t0), forKey: "twist")
        [kicker, mask, badge, underline].forEach(root.addSublayer)
        if let chip { root.addSublayer(chip) }
        return root
    }

    /// Slam's badge (web/public/icons/mark.svg): a blue disc edged with 14 bumps, a white Fredoka "s".
    static func badge(size: CGFloat) -> CGImage? {
        let n = Int(size.rounded(.up))
        guard n > 0, let ctx = CGContext(data: nil, width: n, height: n, bitsPerComponent: 8, bytesPerRow: 0,
                                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let k = CGFloat(n) / 512, c = CGPoint(x: CGFloat(n) / 2, y: CGFloat(n) / 2)
        ctx.setFillColor(blue.cgColor)
        ctx.fillEllipse(in: CGRect(x: c.x - 207.4 * k, y: c.y - 207.4 * k, width: 414.8 * k, height: 414.8 * k))
        for i in 0..<14 {
            let a = CGFloat(i) * 2 * .pi / 14
            let p = CGPoint(x: c.x + cos(a) * 201.2 * k, y: c.y + sin(a) * 201.2 * k)
            ctx.fillEllipse(in: CGRect(x: p.x - 54.8 * k, y: p.y - 54.8 * k, width: 109.6 * k, height: 109.6 * k))
        }
        let font = Fonts.named("Fredoka", size: 300 * k) ?? NSFont.boldSystemFont(ofSize: 300 * k)
        let s = NSAttributedString(string: "s", attributes: [.font: font, .foregroundColor: NSColor.white])
        let sz = s.size()
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
        s.draw(at: CGPoint(x: c.x - sz.width / 2, y: c.y - sz.height / 2 + font.descender / 2))
        NSGraphicsContext.restoreGraphicsState()
        return ctx.makeImage()
    }
}
