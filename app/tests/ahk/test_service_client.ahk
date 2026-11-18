#Requires AutoHotkey v2.0
#NoTrayIcon
; Drives a real `gpw` HTTP server (fake mic and engine) with the hotkey script's own HTTP client.
; Run by app/tests/test_ahk.py with the server's base URL as the only argument.
; Prints one line per step, as UTF-8, for the Python side to check.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\service_client.ahk

; Print runtime errors and exit instead of showing a dialog that would hang the test run.
OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

base := A_Args[1]
out := ""

r := GpwHttp(base, "GET", "/health")
out .= "health " r.status " " JsonValue(r.body, "service") " " JsonValue(r.body, "dictate_key") "`n"

r := GpwHttp(base, "POST", "/start")
out .= "start " r.status " " JsonValue(r.body, "id") "`n"

r := GpwHttpAbortable(base, "/stop", "mode=hold", 10000, () => false)
out .= "stop " r.status " " GpwHeader(r.whr, "X-Dictation-Status") " " GpwHeader(r.whr, "X-Dictation-Id") "`n"
out .= "text " r.body "`n"

r := GpwHttp(base, "POST", "/pasted", "id=1&release_to_paste_ms=1234&paste_ms=20&clipboard_restored=1")
out .= "pasted " r.status " " JsonValue(r.body, "ok") "`n"

; Esc during transcription: the client gives up at once and the service drops the result.
GpwHttp(base, "POST", "/start")
r := GpwHttpAbortable(base, "/stop", "mode=handsfree", 10000, AbortAfterPolls)
out .= "aborted " (r = "" ? "yes" : "no") "`n"
r := GpwHttp(base, "POST", "/cancel")
out .= "cancel " r.status " " JsonValue(r.body, "result") "`n"

try {
    GpwHttp("http://127.0.0.1:9", "GET", "/health", "", 1000)
    out .= "closed port: no error`n"
} catch {
    out .= "closed port: error`n"
}

FileAppend(out, "*", "UTF-8-RAW")
ExitApp(0)

AbortAfterPolls() {
    static polls := 0
    polls += 1
    return polls > 3
}
