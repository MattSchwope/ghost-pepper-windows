#Requires AutoHotkey v2.0
; Ghost Pepper Windows: pure helpers for the long-recording inbox in hotkey.ahk. No side effects, so
; app/tests/ahk/test_inbox_logic.ahk can test them. Uses JsonValue from dictation_logic.ahk.

; The extra tray tooltip line while a recording is being processed, or "" when none is.
; The whole tooltip must stay under 128 characters, so long file names are shortened.
InboxTipLine(json) {
    file := JsonValue(json, "processing")
    if (file = "")
        return ""
    if (StrLen(file) > 40)
        file := SubStr(file, 1, 37) "..."
    step := JsonValue(json, "step")
    line := "Recording: " file (step != "" ? ", " step : "")
    queued := Integer(JsonValue(json, "queued", 0))
    if (queued > 0)
        line .= " (+" queued " waiting)"
    return SubStr(line, 1, 90)
}

; What an /inbox/status answer means for the user, given the sequence numbers already seen.
; `seen` is a Map with done and failed. Returns an array of {kind, title, text, target} toasts;
; clicking one opens target (the note, or the _failed folder).
; The first call (seen.Count = 0) only records what is there, so old results are not announced again.
InboxNotices(json, seen) {
    notices := []
    doneSeq := Integer(JsonValue(json, "done_seq", 0))
    failedSeq := Integer(JsonValue(json, "failed_seq", 0))
    if !seen.Count {
        seen["done"] := doneSeq, seen["failed"] := failedSeq
        return notices
    }
    if (doneSeq > seen["done"]) {
        seen["done"] := doneSeq
        notices.Push({kind: "toast", title: "Recording note ready",
            text: JsonValue(json, "done_file") "`nClick to open the note.",
            target: StrReplace(JsonValue(json, "done_note"), "/", "\")})
    }
    if (failedSeq > seen["failed"]) {
        seen["failed"] := failedSeq
        notices.Push({kind: "toast", title: "Recording failed",
            text: JsonValue(json, "failed_file") ": " JsonValue(json, "failed_message")
                . "`nIt is in the inbox's _failed folder. Drop it back in to retry.",
            target: StrReplace(JsonValue(json, "failed_folder"), "/", "\")})
    }
    return notices
}
