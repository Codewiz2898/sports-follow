import Carbon.HIToolbox

/// A system-wide hotkey through Carbon's RegisterEventHotKey: no Accessibility permission needed,
/// and it fires while a full-screen game is in front (unless something holds secure input).
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let action: () -> Void

    /// Default ⌃⇧S.
    init?(keyCode: Int = kVK_ANSI_S, modifiers: Int = controlKey | shiftKey, action: @escaping () -> Void) {
        self.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let me = Unmanaged.passUnretained(self).toOpaque()
        let st = InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData else { return noErr }
            Unmanaged<HotKey>.fromOpaque(userData).takeUnretainedValue().action()
            return noErr
        }, 1, &spec, me, &handler)
        let id = EventHotKeyID(signature: OSType(0x534C_414D), id: 1)  // 'SLAM'
        let st2 = RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), id, GetApplicationEventTarget(), 0, &ref)
        if st != noErr || st2 != noErr {
            Log.line("hotkey: install \(st), register \(st2)")
            return nil
        }
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}
