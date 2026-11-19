#Requires AutoHotkey v2.0
#NoTrayIcon
; Headless tests for app/scripts/lib/meeting_logic.ahk. Run by app/tests/test_ahk.py.
; Prints "ALL PASSED" and exits 0, or prints each failure and exits 1.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\meeting_logic.ahk

OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

Failures := 0

Check(name, actual, expected) {
    global Failures
    if (actual == expected)
        return
    Failures += 1
    FileAppend("FAIL " name ": got [" actual "] expected [" expected "]`n", "*")
}

Kinds(notices) {
    out := ""
    for n in notices
        out .= (out = "" ? "" : ",") n.kind ":" n.title
    return out
}

Check("elapsed zero", FormatElapsed(0), "0:00:00")
Check("elapsed minutes", FormatElapsed(754), "0:12:34")
Check("elapsed hours", FormatElapsed(4 * 3600 + 5), "4:00:05")
Check("elapsed negative", FormatElapsed(-3), "0:00:00")

Check("tip", MeetingTip(65, false, true), "Ghost Pepper: capturing meeting 0:01:05")
Check("tip muted", MeetingTip(65, true, true), "Ghost Pepper: capturing meeting 0:01:05 (mic track muted)")
Check("tip call only", MeetingTip(65, false, false, true), "Ghost Pepper: capturing meeting 0:01:05 (call audio only) (dry run)")

Check("shell: taskbar", IsShellWindow("Shell_TrayWnd"), true)
Check("shell: menu", IsShellWindow("#32768"), true)
Check("shell: Teams", IsShellWindow("TeamsWebView"), false)

seen := Map()
base := '{"ok": true, "capturing": false, "folder": "", "done_seq": 2, "failed_seq": 1, "stopped_seq": 3}'
Check("first status announces nothing", Kinds(MeetingNotices(base, seen)), "")
Check("first status records seqs", seen["done"] "/" seen["failed"] "/" seen["stopped"], "2/1/3")
Check("same status again: nothing", Kinds(MeetingNotices(base, seen)), "")

capturing := '{"capturing": true, "folder": "2026-11-18 1405", "no_call_audio": true, "done_seq": 2, "failed_seq": 1, "stopped_seq": 3}'
Check("no call audio: one tip", Kinds(MeetingNotices(capturing, seen)), "tip:")
Check("no call audio: only once per capture", Kinds(MeetingNotices(capturing, seen)), "")

done := '{"capturing": false, "folder": "", "stopped_seq": 4, "stopped_reason": "hotkey", "done_seq": 3, "done_status": "done",'
    . ' "done_note": "C:/GhostPepperWindows/notes/meetings/2026-11-18 1405 Q4 plan.md", "failed_seq": 1}'
notices := MeetingNotices(done, seen)
Check("stopped by hotkey is not announced; the note is", Kinds(notices), "toast:Meeting note ready")
Check("the toast opens the note", notices[1].target, "C:\GhostPepperWindows\notes\meetings\2026-11-18 1405 Q4 plan.md")

capped := '{"stopped_seq": 5, "stopped_reason": "max_hours", "done_seq": 4, "done_status": "empty",'
    . ' "done_message": "No speech was heard on either track, so no note was written.", "failed_seq": 2,'
    . ' "failed_message": "Meeting summary failed: Could not reach OpenAI.", "failed_folder": "C:/GPW/meetings/2026-11-18 1405"}'
notices := MeetingNotices(capped, seen)
Check("limit, empty and failed all announced", Kinds(notices),
    "toast:Meeting capture stopped,toast:Meeting capture,toast:Meeting note failed")
Check("empty has its message", notices[2].text, "No speech was heard on either track, so no note was written.")
Check("failed opens the folder", notices[3].target, "C:\GPW\meetings\2026-11-18 1405")
Check("failed says how to retry", InStr(notices[3].text, "Retry failed meeting") > 0, true)

if Failures {
    FileAppend(Failures " FAILED`n", "*")
    ExitApp(1)
}
FileAppend("ALL PASSED`n", "*")
ExitApp(0)
