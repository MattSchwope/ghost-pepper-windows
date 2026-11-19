#Requires AutoHotkey v2.0
#NoTrayIcon
; Phase 3: drives a real `gpw` HTTP server (fake mic, engine and text model) through a voice edit with
; the hotkey script's own HTTP client and form encoding. Run by app/tests/test_ahk.py with three
; arguments: the server's base URL, a UTF-8 file holding the selection, and a file for the pasted text.
; Prints one line per step for the Python side to check.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\service_client.ahk

; Print runtime errors and exit instead of showing a dialog that would hang the test run.
OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

base := A_Args[1]
selection := FileRead(A_Args[2], "UTF-8")
out := ""

r := GpwHttp(base, "GET", "/health")
maxWords := Integer(JsonValue(r.body, "voice_edit_max_words", 0))
out .= "health " JsonValue(r.body, "voice_edit_key") " " maxWords "`n"
out .= "words " WordCount(selection) " problem [" VoiceEditProblem(selection, maxWords) "]`n"

GpwHttp(base, "POST", "/start")
r := GpwHttpAbortable(base, "/voice-edit", "selection=" UriEncode(selection), 30000, () => false)
out .= "edit " r.status " " GpwHeader(r.whr, "X-Dictation-Status") " " GpwHeader(r.whr, "X-Dictation-Id") "`n"
FileAppend(ClipboardText(r.body), A_Args[3], "UTF-8-RAW")  ; what PasteText would put on the clipboard

; Nothing selected: the script checks first, and the service refuses too.
out .= "empty problem [" VoiceEditProblem("`r`n", maxWords) "]`n"
GpwHttp(base, "POST", "/start")
r := GpwHttpAbortable(base, "/voice-edit", "selection=", 30000, () => false)
out .= "empty " r.status " " GpwHeader(r.whr, "X-Dictation-Status") " " r.body "`n"

FileAppend(out, "*", "UTF-8-RAW")
ExitApp(0)
