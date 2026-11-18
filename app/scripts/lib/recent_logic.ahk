#Requires AutoHotkey v2.0
; Ghost Pepper Windows: pure helpers for the recent-dictations window in hotkey.ahk. No side
; effects, so app/tests/ahk/test_recent_logic.ahk can test them without a window or the clipboard.

; The service's /recent body as an array of {time, app, label, text}, newest first.
; One dictation per line, fields split by tabs, with \\ \t \r \n escaped inside fields (gpw/recent.py).
ParseRecent(body) {
    items := []
    for line in StrSplit(body, "`n", "`r") {
        if (line = "")
            continue
        f := StrSplit(line, "`t")
        if (f.Length < 4)
            continue
        items.Push({time: RecentUnescape(f[1]), app: RecentUnescape(f[2]), label: RecentUnescape(f[3]),
            text: RecentUnescape(f[4])})
    }
    return items
}

RecentUnescape(s) {
    static codes := Map("\", "\", "t", "`t", "n", "`n", "r", "`r")
    out := ""
    start := 1
    while (pos := InStr(s, "\", true, start)) {
        out .= SubStr(s, start, pos - start)
        c := SubStr(s, pos + 1, 1)
        out .= codes.Has(c) ? codes[c] : c
        start := pos + 2
    }
    return out SubStr(s, start)
}

; What a row shows: the snippet label, or the first two lines with text, cut to about two lines' width.
RecentPreview(item, maxChars := 110) {
    if (item.label != "")
        return item.label
    lines := []
    for line in StrSplit(item.text, "`n", "`r") {
        if (Trim(line) = "")
            continue
        lines.Push(Trim(line))
        if (lines.Length = 2)
            break
    }
    preview := lines.Length ? lines[1] : ""
    if (lines.Length = 2)
        preview .= "`n" lines[2]
    more := StrLen(preview) > maxChars || (lines.Length = 2 && !EndsWithLine(item.text, lines[2]))
    if (StrLen(preview) > maxChars)
        preview := RTrim(SubStr(preview, 1, maxChars - 1))
    return more ? preview "…" : preview
}

; True if `line` is the last line with text in `text`: nothing more follows it.
EndsWithLine(text, line) => SubStr(RTrim(text, " `t`r`n"), -StrLen(line)) == line

; The row header: number key, time and program, such as "1   14:05  ·  OUTLOOK".
RecentHeader(item, index) {
    key := (index = 10) ? 0 : index
    return key "   " item.time (item.app != "" ? "  ·  " item.app : "")
}

; The row a number key picks: 1-9 are rows 1-9, 0 is row 10. 0 if it is not a row key.
RecentRowForKey(key) {
    key := RegExReplace(key, "i)^Numpad")
    if !RegExMatch(key, "^\d$")
        return 0
    return (key = "0") ? 10 : Integer(key)
}

; Hover text: long lines wrapped at `width` characters, since a tooltip does not wrap on its own,
; and very long dictations cut at maxChars.
WrapForTip(text, width := 80, maxChars := 3000) {
    cut := StrLen(text) > maxChars
    if cut
        text := SubStr(text, 1, maxChars)
    out := ""
    for line in StrSplit(text, "`n", "`r") {
        if (A_Index > 1)
            out .= "`n"
        while (StrLen(line) > width) {
            at := InStr(SubStr(line, 1, width + 1), " ", true, -1)
            if (at <= 1)
                at := width + 1  ; one long word: break it
            out .= RTrim(SubStr(line, 1, at - 1)) "`n"
            line := LTrim(SubStr(line, at))
        }
        out .= line
    }
    return cut ? out "…" : out
}

; Where the window goes: the corner of the work area next to the taskbar's notification area.
; The taskbar is on the side where the work area is smaller than the monitor. Returns [x, y].
RecentWindowPos(w, h, monLeft, monTop, workLeft, workTop, workRight, workBottom, gap := 12) {
    x := (workLeft > monLeft) ? workLeft + gap : workRight - w - gap
    y := (workTop > monTop) ? workTop + gap : workBottom - h - gap
    return [Max(x, workLeft), Max(y, workTop)]
}
