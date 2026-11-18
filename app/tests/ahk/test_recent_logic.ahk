#Requires AutoHotkey v2.0
#NoTrayIcon
; Headless tests for app/scripts/lib/recent_logic.ahk. Run by app/tests/test_ahk.py.
; Prints "ALL PASSED" and exits 0, or prints each failure and exits 1.
#Include %A_ScriptDir%\..\..\scripts\lib\dictation_logic.ahk
#Include %A_ScriptDir%\..\..\scripts\lib\recent_logic.ahk

; Print runtime errors and exit instead of showing a dialog that would hang the test run.
OnError((err, *) => (FileAppend("ERROR line " err.Line ": " err.Message "`n", "*"), ExitApp(2)))

Failures := 0

Check(name, actual, expected) {
    global Failures
    if (actual == expected)
        return
    Failures += 1
    FileAppend("FAIL " name ": got [" actual "] expected [" expected "]`n", "*", "UTF-8-RAW")
}

; Parsing the /recent body
body := "14:05`tOUTLOOK`t`tDear Sam,\n\nSee you at 9.\tDone\\.`n"
    . "13:58`t`t[snippet: signature]`tMatt\nTitle`n"
    . "`n"
    . "broken line without enough fields`n"
items := ParseRecent(body)
Check("parse: count skips blank and broken lines", items.Length, 2)
Check("parse: time", items[1].time, "14:05")
Check("parse: app", items[1].app, "OUTLOOK")
Check("parse: text keeps line breaks, tabs and backslashes", items[1].text, "Dear Sam,`n`nSee you at 9.`tDone\.")
Check("parse: no app", items[2].app, "")
Check("parse: snippet label", items[2].label, "[snippet: signature]")
Check("parse: snippet text", items[2].text, "Matt`nTitle")
Check("parse: CRLF body", ParseRecent("09:00`tCode`t`tHi`r`n")[1].text, "Hi")
Check("parse: empty body", ParseRecent("").Length, 0)

Check("unescape: plain", RecentUnescape("plain"), "plain")
Check("unescape: backslash before n stays a backslash", RecentUnescape("C:\\new"), "C:\new")
Check("unescape: CR LF", RecentUnescape("a\r\nb"), "a`r`nb")

; The copy button: the exact text, with Windows line breaks
Check("copy: line breaks become CR LF", ClipboardText(items[1].text), "Dear Sam,`r`n`r`nSee you at 9.`tDone\.")

; Row preview
P(text, label := "") => RecentPreview({text: text, label: label})
Check("preview: one line", P("Short one."), "Short one.")
Check("preview: first two lines with text", P("Dear Sam,`n`nSee you at 9.`n`nMatt"), "Dear Sam,`nSee you at 9.…")
Check("preview: exactly two lines has no ellipsis", P("One`nTwo"), "One`nTwo")
Check("preview: trailing blank lines do not count", P("One`nTwo`n`n"), "One`nTwo")
Check("preview: long text is cut", P("word " StrRepeat("x", 200)), "word " StrRepeat("x", 104) "…")
Check("preview: snippet shows its label", P("Matt`nTitle", "[snippet: signature]"), "[snippet: signature]")

; Row header
Check("header: first row", RecentHeader({time: "14:05", app: "OUTLOOK"}, 1), "1   14:05  ·  OUTLOOK")
Check("header: tenth row is key 0", RecentHeader({time: "08:00", app: ""}, 10), "0   08:00")

; Number keys
Check("key: 1", RecentRowForKey("1"), 1)
Check("key: 9", RecentRowForKey("9"), 9)
Check("key: 0 is row 10", RecentRowForKey("0"), 10)
Check("key: numpad", RecentRowForKey("Numpad3"), 3)
Check("key: numpad 0", RecentRowForKey("Numpad0"), 10)
Check("key: not a number", RecentRowForKey("a"), 0)

; Hover text
Check("tip: short stays", WrapForTip("Hello there"), "Hello there")
Check("tip: wraps at spaces", WrapForTip("aaa bbb ccc", 7), "aaa bbb`nccc")
Check("tip: keeps line breaks", WrapForTip("one`ntwo", 7), "one`ntwo")
Check("tip: breaks a long word", WrapForTip("abcdefghij", 4), "abcd`nefgh`nij")
Check("tip: cut when very long", WrapForTip(StrRepeat("y", 20), 80, 10), StrRepeat("y", 10) "…")

; Window position: next to the tray
Pos(args*) {
    p := RecentWindowPos(args*)
    return p[1] "," p[2]
}
Check("pos: taskbar at the bottom", Pos(400, 300, 0, 0, 0, 0, 1920, 1040), "1508,728")
Check("pos: taskbar at the top", Pos(400, 300, 0, 0, 0, 40, 1920, 1080), "1508,52")
Check("pos: taskbar on the left", Pos(400, 300, 0, 0, 60, 0, 1920, 1080), "72,768")
Check("pos: second monitor coordinates", Pos(400, 300, -1920, 0, -1920, 0, 0, 1040), "-412,728")
Check("pos: never off the top of a short screen", Pos(400, 2000, 0, 0, 0, 0, 1920, 1040), "1508,0")

StrRepeat(s, n) {
    out := ""
    loop n
        out .= s
    return out
}

if Failures {
    FileAppend(Failures " FAILED`n", "*")
    ExitApp(1)
}
FileAppend("ALL PASSED`n", "*")
ExitApp(0)
