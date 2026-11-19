#Requires AutoHotkey v2.0
; Ghost Pepper Windows: pure helpers for meeting capture in hotkey.ahk. No side effects, so
; app/tests/ahk/test_meeting_logic.ahk can test them. Uses JsonValue from dictation_logic.ahk.

; Seconds as h:mm:ss, for the tray tooltip: 754 -> "0:12:34".
FormatElapsed(seconds) {
    seconds := Max(0, Integer(seconds))
    return Format("{}:{:02}:{:02}", seconds // 3600, Mod(seconds // 60, 60), Mod(seconds, 60))
}

; The tray tooltip while a meeting is being captured and dictation is idle.
MeetingTip(elapsed, muted, micTrack, dryRun := false) {
    tip := "Ghost Pepper: capturing meeting " FormatElapsed(elapsed)
    if !micTrack
        tip .= " (call audio only)"
    else if muted
        tip .= " (mic track muted)"
    return tip (dryRun ? " (dry run)" : "")
}

; Windows that are part of the taskbar or a menu, not an app: skipped when finding the app in front,
; since starting from the tray menu puts the taskbar in front.
IsShellWindow(cls) {
    static shell := Map("Shell_TrayWnd", 1, "Shell_SecondaryTrayWnd", 1, "NotifyIconOverflowWindow", 1,
        "TopLevelWindowForOverflowXamlIsland", 1, "#32768", 1, "Progman", 1, "WorkerW", 1,
        "Windows.UI.Core.CoreWindow", 1, "tooltips_class32", 1, "AutoHotkeyGUI", 1)
    return shell.Has(cls)
}

; What a /meeting/status answer means for the user, given the sequence numbers already seen.
; `seen` is a Map with done, failed, stopped and warned (the folder already warned about no call audio).
; Updates `seen` and returns an array of notices: {kind, title, text, target}.
;   kind "toast": a Windows notice; clicking it opens target (a note or a folder) when there is one.
;   kind "tip":   a tooltip near the cursor.
; The first call (seen.Count = 0) only records what is there, so old results are not announced again.
MeetingNotices(json, seen) {
    notices := []
    doneSeq := Integer(JsonValue(json, "done_seq", 0))
    failedSeq := Integer(JsonValue(json, "failed_seq", 0))
    stoppedSeq := Integer(JsonValue(json, "stopped_seq", 0))
    folder := JsonValue(json, "folder")
    if !seen.Count {
        seen["done"] := doneSeq, seen["failed"] := failedSeq, seen["stopped"] := stoppedSeq, seen["warned"] := ""
        return notices
    }
    if (JsonValue(json, "no_call_audio") = "true" && folder != "" && seen["warned"] != folder) {
        seen["warned"] := folder
        notices.Push({kind: "tip", title: "", target: "",
            text: "No call audio yet. If the call is on, it may be playing on another device than this PC's default speaker or headset."})
    }
    if (stoppedSeq > seen["stopped"]) {
        seen["stopped"] := stoppedSeq
        if (JsonValue(json, "stopped_reason") = "max_hours")
            notices.Push({kind: "toast", title: "Meeting capture stopped", target: "",
                text: "It reached the meetings.max_hours limit. The note is being written."})
    }
    if (doneSeq > seen["done"]) {
        seen["done"] := doneSeq
        if (JsonValue(json, "done_status") = "done")
            notices.Push({kind: "toast", title: "Meeting note ready", text: "Click to open it.",
                target: StrReplace(JsonValue(json, "done_note"), "/", "\")})
        else
            notices.Push({kind: "toast", title: "Meeting capture", text: JsonValue(json, "done_message"), target: ""})
    }
    if (failedSeq > seen["failed"]) {
        seen["failed"] := failedSeq
        notices.Push({kind: "toast", title: "Meeting note failed",
            text: JsonValue(json, "failed_message") "`nThe audio is kept. Use Retry failed meeting in the tray menu.",
            target: StrReplace(JsonValue(json, "failed_folder"), "/", "\")})
    }
    return notices
}
