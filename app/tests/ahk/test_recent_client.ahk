#Requires AutoHotkey v2.0
#NoTrayIcon
; Reads /recent from a real `gpw` HTTP server with the hotkey script's own client and parser,
; and prints what each row would show and copy. Run by app/tests/test_ahk.py with the base URL.
; Output, UTF-8: one record per row, separated by Chr(30); fields time, app, preview, copied
; text (as the copy button puts it on the clipboard), separated by Chr(31).
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\service_client.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\recent_logic.ahk

OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

r := GpwHttp(A_Args[1], "POST", "/recent")
if (r.status != 200) {
    FileAppend("status " r.status "`n", "*")
    ExitApp(1)
}
out := ""
for item in ParseRecent(r.body)
    out .= item.time Chr(31) item.app Chr(31) RecentPreview(item) Chr(31) ClipboardText(item.text) Chr(30)
FileAppend(out, "*", "UTF-8-RAW")
ExitApp(0)
