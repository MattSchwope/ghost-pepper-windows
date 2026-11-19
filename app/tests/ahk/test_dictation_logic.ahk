#Requires AutoHotkey v2.0
#NoTrayIcon
; Headless tests for app/scripts/lib/dictation_logic.ahk. Run by app/tests/test_ahk.py.
; Prints "ALL PASSED" and exits 0, or prints each failure and exits 1.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk

; Print runtime errors and exit instead of showing a dialog that would hang the test run.
OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

Failures := 0

Check(name, actual, expected) {
    global Failures
    if (actual == expected)
        return
    Failures += 1
    FileAppend("FAIL " name ": got [" actual "] expected [" expected "]`n", "*")
}

RepeatText(text, times) {
    out := ""
    loop times
        out .= text
    return out
}

S(state, event, held := 0, combo := false) {
    step := NextStep(state, event, held, combo, 250)
    return step[1] "/" step[2]
}

; Hold to talk
Check("hold: key down starts", S("idle", "down"), "held/start")
Check("hold: long release stops", S("held", "up", 900), "busy/stop")
Check("hold: exactly tap length counts as a hold", S("held", "up", 250), "busy/stop")
Check("hold: a shortcut cancels quietly", S("held", "up", 900, true), "idle/cancel_quiet")
Check("hold: Esc cancels", S("held", "cancel"), "idle/cancel")
Check("hold: limit stops and pastes", S("held", "max_timeout"), "busy/stop")

; Double-tap for hands-free
Check("tap: short release waits for a second tap", S("held", "up", 120), "tapped/arm_tap")
Check("tap: a single tap times out quietly", S("tapped", "tap_timeout"), "idle/cancel_quiet")
Check("tap: Esc while waiting", S("tapped", "cancel"), "idle/cancel")
Check("tap: second press", S("tapped", "down"), "held2/disarm_tap")
Check("tap: second tap enters hands-free", S("held2", "up", 100), "handsfree/handsfree")
Check("tap: tap then hold acts like hold", S("held2", "up", 800), "busy/stop")
Check("tap: tap then shortcut cancels", S("held2", "up", 100, true), "idle/cancel_quiet")
Check("hands-free: press waits for release", S("handsfree", "down"), "handsfree_down/none")
Check("hands-free: release stops", S("handsfree_down", "up", 80), "busy/stop")
Check("hands-free: release stops even after a shortcut", S("handsfree_down", "up", 80, true), "busy/stop")
Check("hands-free: Esc cancels", S("handsfree", "cancel"), "idle/cancel")
Check("hands-free: Esc while pressed cancels", S("handsfree_down", "cancel"), "idle/cancel")
Check("hands-free: limit stops and pastes", S("handsfree", "max_timeout"), "busy/stop")

; While transcribing
Check("busy: key down ignored", S("busy", "down"), "busy/none")
Check("busy: key up ignored", S("busy", "up", 50), "busy/none")
Check("busy: Esc aborts", S("busy", "cancel"), "idle/abort")
Check("busy: done", S("busy", "done"), "idle/none")
Check("busy: limit ignored", S("busy", "max_timeout"), "busy/none")

; Voice edit (Phase 3): the dictate key held, then the voice-edit key
Check("edit: voice-edit key while holding starts an edit", S("held", "edit"), "edit/edit")
Check("edit: also from the second press of a double tap", S("held2", "edit"), "edit/edit")
Check("edit: key repeat is ignored", S("edit", "edit"), "edit/none")
Check("edit: letting go of the dictate key stops", S("edit", "up", 900), "busy/stop")
Check("edit: letting go of the voice-edit key stops", S("edit", "edit_up"), "busy/stop")
Check("edit: a shortcut such as RCtrl+RShift+Left cancels quietly", S("edit", "edit_up", 0, true), "idle/cancel_quiet")
Check("edit: Esc cancels", S("edit", "cancel"), "idle/cancel")
Check("edit: limit stops", S("edit", "max_timeout"), "busy/stop")
Check("edit: late done is ignored", S("edit", "done"), "edit/none")
Check("edit: voice-edit key alone does nothing", S("idle", "edit"), "idle/none")
Check("edit: not while waiting for a second tap", S("tapped", "edit"), "tapped/none")
Check("edit: not in hands-free", S("handsfree", "edit"), "handsfree/none")
Check("edit: not while transcribing", S("busy", "edit"), "busy/none")
Check("edit: release after the edit was dropped", S("idle", "edit_up"), "idle/none")
Check("edit: second release while transcribing", S("busy", "edit_up"), "busy/none")

; Which releases count as a shortcut
Check("combo: dictate key alone", IsCombo("RControl", "RControl", "RShift"), false)
Check("combo: voice-edit key last", IsCombo("RShift", "RControl", "RShift"), false)
Check("combo: another key", IsCombo("c", "RControl", "RShift"), true)
Check("combo: RShift with voice edit off", IsCombo("RShift", "RControl", ""), true)
Check("combo: nothing pressed before", IsCombo("", "RControl", ""), true)

; Voice-edit selection checks
Check("words: none", WordCount(" `r`n "), 0)
Check("words: lines and tabs", WordCount("Make it`r`nshorter,`tplease"), 4)
Check("words: 2,000", WordCount(RepeatText("word ", 2000)), 2000)
Check("selection: empty", VoiceEditProblem("", 3000), "Select text first, then hold the voice-edit keys and say the change.")
Check("selection: only a line break", VoiceEditProblem("`r`n", 3000), "Select text first, then hold the voice-edit keys and say the change.")
Check("selection: 2,000 words is fine", VoiceEditProblem(RepeatText("word ", 2000), 3000), "")
Check("selection: over the limit", VoiceEditProblem(RepeatText("word ", 3001), 3000),
    "The selection is 3001 words. Voice edit takes up to 3000, so nothing was changed.")

; Idle
Check("idle: stray release", S("idle", "up", 50), "idle/none")
Check("idle: Esc", S("idle", "cancel"), "idle/none")
Check("idle: late tap timer", S("idle", "tap_timeout"), "idle/none")
Check("idle: late done", S("idle", "done"), "idle/none")
Check("held: late done is ignored", S("held", "done"), "held/none")

; A whole double-tap session, event by event, as the hotkeys drive it
state := "idle"
trail := ""
for ev in [["down", 0], ["up", 90], ["down", 0], ["up", 80], ["down", 0], ["up", 70], ["done", 0]] {
    step := NextStep(state, ev[1], ev[2], false, 250)
    state := step[1]
    trail .= step[2] " "
}
Check("double-tap session", Trim(trail), "start arm_tap disarm_tap handsfree none stop none")
Check("double-tap session ends idle", state, "idle")

; Hotkey prefixes
Check("prefix RCtrl", HotkeyPrefix("RCtrl"), "~*")
Check("prefix LWin", HotkeyPrefix("LWin"), "~*")
Check("prefix rshift", HotkeyPrefix("rshift"), "~*")
Check("prefix F13", HotkeyPrefix("F13"), "*")
Check("prefix CapsLock", HotkeyPrefix("CapsLock"), "*")

; service.port from config.yaml
cfg := "app:`n  data_dir: x`n`nservice:`n  # comment`n  port: 9123`n`nhotkeys:`n  port: 1`n"
Check("port from service block", ReadPort(cfg), 9123)
Check("port with CRLF line ends", ReadPort(StrReplace(cfg, "`n", "`r`n")), 9123)
Check("port ignored outside service", ReadPort("app:`n  port: 1`n"), 8765)
Check("port from empty file", ReadPort(""), 8765)

; Flat /health JSON
json := '{"ok": true, "service": "gpw", "pid": 4321, "tap_ms": 250, "assets_dir": "C:/GP/assets", "mic": "Mic \"USB\" \u00e9\\x", "dry_run": false}'
Check("json string", JsonValue(json, "service"), "gpw")
Check("json number", JsonValue(json, "pid"), "4321")
Check("json bool", JsonValue(json, "dry_run"), "false")
Check("json escapes", JsonValue(json, "mic"), 'Mic "USB" ' Chr(0xE9) '\x')
Check("json path", JsonValue(json, "assets_dir"), "C:/GP/assets")
Check("json missing key", JsonValue(json, "nope", "fallback"), "fallback")

; Add-word hotkey label and form encoding
Check("label ctrl alt d", HotkeyLabel("^!d"), "Ctrl+Alt+D")
Check("label win shift F9", HotkeyLabel("#+F9"), "Win+Shift+F9")
Check("label with prefixes", HotkeyLabel("~*^d"), "Ctrl+D")
Check("encode plain", UriEncode("Kavaliro"), "Kavaliro")
Check("encode reserved", UriEncode("TS/SCI & CI Poly+1"), "TS%2FSCI%20%26%20CI%20Poly%2B1")
Check("encode utf-8", UriEncode("Zoë"), "Zo%C3%AB")

; Line breaks on the clipboard
Check("clipboard: LF becomes CRLF", ClipboardText("Matt`nKavaliro`n`nCall me"), "Matt`r`nKavaliro`r`n`r`nCall me")
Check("clipboard: CRLF stays as it is", ClipboardText("a`r`nb"), "a`r`nb")
Check("clipboard: one line untouched", ClipboardText("one line"), "one line")

FileAppend(Failures ? Failures " failed`n" : "ALL PASSED`n", "*")
ExitApp(Failures ? 1 : 0)
