#Requires AutoHotkey v2.0
; Ghost Pepper Windows: pure helpers for hotkey.ahk. No side effects, so
; app/tests/ahk/test_dictation_logic.ahk can test them without touching the keyboard.

; One step of the dictation key state machine. Returns [newState, action].
;   States:  idle, held, tapped, held2, handsfree, handsfree_down, busy
;   Events:  down, up, tap_timeout, max_timeout, cancel, done
;   Actions: start, stop, cancel, cancel_quiet, abort, arm_tap, disarm_tap, handsfree, none
; Hold the key to talk and release to paste. Tap twice for hands-free, then tap once to stop.
; Pressing another key with the dictate key (a shortcut such as RCtrl+C) cancels quietly.
NextStep(state, event, heldMs := 0, combo := false, tapMs := 250) {
    static recording := Map("held", 1, "tapped", 1, "held2", 1, "handsfree", 1, "handsfree_down", 1)
    if (event = "cancel") {
        if recording.Has(state)
            return ["idle", "cancel"]
        return (state = "busy") ? ["idle", "abort"] : [state, "none"]
    }
    if (event = "max_timeout")
        return recording.Has(state) ? ["busy", "stop"] : [state, "none"]
    switch state {
        case "idle":
            if (event = "down")
                return ["held", "start"]
        case "held", "held2":
            if (event = "up") {
                if combo
                    return ["idle", "cancel_quiet"]
                if (heldMs >= tapMs)
                    return ["busy", "stop"]
                return (state = "held") ? ["tapped", "arm_tap"] : ["handsfree", "handsfree"]
            }
        case "tapped":
            if (event = "down")
                return ["held2", "disarm_tap"]
            if (event = "tap_timeout")
                return ["idle", "cancel_quiet"]
        case "handsfree":
            if (event = "down")
                return ["handsfree_down", "none"]
        case "handsfree_down":
            if (event = "up")
                return ["busy", "stop"]
        case "busy":
            if (event = "done")
                return ["idle", "none"]
    }
    return [state, "none"]
}

; Modifier keys pass through ("~") so RCtrl+C still copies; other keys are swallowed.
; "*" fires the hotkey even while other modifiers are down.
HotkeyPrefix(key) {
    return RegExMatch(key, "i)^[LR]?(Ctrl|Control|Shift|Alt|Win)$") ? "~*" : "*"
}

; service.port from config.yaml, without a YAML parser: the indented block under "service:".
ReadPort(configText, fallback := 8765) {
    if RegExMatch(configText, "m)^service:[^\n]*\n((?:[ \t]+[^\n]*\n?)*)", &block)
        && RegExMatch(block[1], "m)^[ \t]+port:[ \t]*(\d+)", &m)
        return Integer(m[1])
    return fallback
}

; The value of a top-level key in the service's flat /health JSON, as a string.
JsonValue(json, key, default := "") {
    if RegExMatch(json, '"' key '"\s*:\s*"((?:[^"\\]|\\.)*)"', &m)
        return JsonUnescape(m[1])
    if RegExMatch(json, '"' key '"\s*:\s*([^,}\s]+)', &m)
        return m[1]
    return default
}

JsonUnescape(s) {
    out := ""
    i := 1
    n := StrLen(s)
    while (i <= n) {
        c := SubStr(s, i, 1)
        if (c != "\" || i = n) {
            out .= c
            i += 1
            continue
        }
        e := SubStr(s, i + 1, 1)
        switch e, true {
            case "n": out .= "`n"
            case "t": out .= "`t"
            case "r": out .= "`r"
            case "b": out .= "`b"
            case "f": out .= "`f"
            case "u":
                out .= Chr(Integer("0x" SubStr(s, i + 2, 4)))
                i += 4
            default: out .= e  ; \" \\ \/
        }
        i += 2
    }
    return out
}
