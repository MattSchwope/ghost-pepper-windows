#Requires AutoHotkey v2.0
#NoTrayIcon
; Headless tests for app/scripts/lib/inbox_logic.ahk. Run by app/tests/test_ahk.py.
; Prints "ALL PASSED" and exits 0, or prints each failure and exits 1.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\inbox_logic.ahk

OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

Failures := 0

Check(name, actual, expected) {
    global Failures
    if (actual == expected)
        return
    Failures += 1
    FileAppend("FAIL " name ": got [" actual "] expected [" expected "]`n", "*")
}

Titles(notices) {
    out := ""
    for n in notices
        out .= (out = "" ? "" : ",") n.title
    return out
}

Check("idle: no tooltip line", InboxTipLine('{"processing": "", "queued": 0}'), "")
Check("busy", InboxTipLine('{"processing": "talk1.m4a", "step": "transcribing, batch 2 of 6", "queued": 0}'),
    "Recording: talk1.m4a, transcribing, batch 2 of 6")
Check("busy with more waiting", InboxTipLine('{"processing": "talk1.m4a", "step": "", "queued": 2}'),
    "Recording: talk1.m4a (+2 waiting)")
long := InboxTipLine('{"processing": "' StrReplace(Format("{:60}", ""), " ", "x") '.m4a", "step": "writing the summary"}')
Check("long names are cut", SubStr(long, 1, 51), "Recording: " StrReplace(Format("{:37}", ""), " ", "x") "...")
Check("the line stays short", StrLen(long) <= 90, true)

seen := Map()
base := '{"ok": true, "folder": "C:/GPW/inbox", "done_seq": 3, "failed_seq": 1}'
Check("first status announces nothing", Titles(InboxNotices(base, seen)), "")
Check("first status records seqs", seen["done"] "/" seen["failed"], "3/1")
Check("same status again: nothing", Titles(InboxNotices(base, seen)), "")

done := '{"folder": "C:/GPW/inbox", "done_seq": 4, "done_file": "talk1.m4a",'
    . ' "done_note": "C:/GPW/notes/presentation/2026-11-18 Zero trust roadmap.md", "failed_seq": 1}'
notices := InboxNotices(done, seen)
Check("note ready", Titles(notices), "Recording note ready")
Check("the toast opens the note", notices[1].target, "C:\GPW\notes\presentation\2026-11-18 Zero trust roadmap.md")
Check("the toast names the file", InStr(notices[1].text, "talk1.m4a") > 0, true)

failed := '{"folder": "C:/GPW/inbox", "done_seq": 4, "failed_seq": 2, "failed_file": "call.m4a",'
    . ' "failed_message": "Transcription failed: Could not reach OpenAI.", "failed_folder": "C:/GPW/inbox/_failed"}'
notices := InboxNotices(failed, seen)
Check("failure", Titles(notices), "Recording failed")
Check("the failure opens _failed", notices[1].target, "C:\GPW\inbox\_failed")
Check("the failure says why", InStr(notices[1].text, "Could not reach OpenAI.") > 0, true)
Check("announced once", Titles(InboxNotices(failed, seen)), "")

if Failures
    ExitApp(1)
FileAppend("ALL PASSED`n", "*")
ExitApp(0)
