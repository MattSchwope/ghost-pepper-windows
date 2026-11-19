#Requires AutoHotkey v2.0
#SingleInstance Force
#Include %A_ScriptDir%\lib\dictation_logic.ahk
#Include %A_ScriptDir%\lib\service_client.ahk
#Include %A_ScriptDir%\lib\recent_logic.ahk
#Include %A_ScriptDir%\lib\meeting_logic.ahk
#Include %A_ScriptDir%\lib\inbox_logic.ahk

; Ghost Pepper Windows hotkeys. The Python service (gpw serve) records and transcribes;
; this script owns the keys, the cue sounds, the tray icon and the paste (ADR-003).
;   Hold RCtrl, talk, release: the text is pasted where the cursor is.
;   Double-tap RCtrl: hands-free. Tap RCtrl once more to stop and paste.
;   Esc while recording or transcribing: cancel.
;   Ctrl+Alt+D with a word selected: add it to the personal dictionary.
;   Say "insert signature" as a whole dictation: the service answers with that snippet's text.
;   Ctrl+Alt+H or a click on the tray icon: the last 10 dictations, with copy buttons.
;   Ctrl+Alt+L: paste the latest dictation again, after it went to the wrong window.
;   Ctrl+Alt+M or the tray menu: start or stop meeting capture. The note is written after the call.
;   Drop a recording in the inbox folder (tray menu: Open inbox folder): a note is written, and a notice says so.
; Keys and timings come from config.yaml (hotkeys:, dictation:) through the service's /health.
; Start it with app\scripts\start_dictation.ps1. Pass --dry-run to paste a fixed sentence
; instead of calling OpenAI.

InstallKeybdHook()
Persistent()

SplitPath(A_ScriptDir, , &AppDir)
SplitPath(AppDir, , &RepoRoot)
ConfigPath := EnvGet("GPW_CONFIG") || RepoRoot "\app\config.example.yaml"
PythonExe := RepoRoot "\.venv\Scripts\python.exe"
DryRun := false
for arg in A_Args
    DryRun := DryRun || (arg = "--dry-run")

BaseUrl := ""
State := "idle"
KeyDown := false
DownTick := 0
ReleaseTick := 0
StopMode := "hold"
Aborted := false
StartedService := false
ServicePid := 0
DictateKey := "RCtrl"
DictateKeyName := "RControl"
CancelKey := "Esc"
AddWordKey := ""
RecentKey := ""
PasteLastKey := ""
MeetingKey := ""
MeetingOn := false  ; a meeting is being captured (from the service's /meeting/status)
MeetingElapsed := 0
MeetingMuted := false
MeetingMicTrack := true
MeetingRetry := ""
MeetingSeen := Map()  ; sequence numbers already announced (MeetingNotices)
MeetingMenuItem := "Start meeting capture"
ToastTarget := ""  ; what clicking the last meeting or recording notice opens
InboxSeen := Map()  ; sequence numbers already announced (InboxNotices)
InboxDir := ""  ; from the service's /inbox/status
InboxLine := ""  ; the tray tooltip's line about a recording being processed
RecentGui := ""
RecentItems := []
RecentRows := Map()  ; control hwnd -> row number, for the hover text
RecentHeaders := []  ; the header text control of each row, which shows "Copied"
RecentTipRow := 0
DictionaryPath := ""
SnippetsPath := ""
TapMs := 250
DoubleTapMs := 400
MaxMs := 600000
SoundsOn := true
RestoreMs := 250
AssetsDir := ""
NotesDir := ""
LogsDir := ""

Main()

Main() {
    global
    BaseUrl := "http://127.0.0.1:" ReadPort(FileExist(ConfigPath) ? FileRead(ConfigPath) : "")
    SetupTray()
    health := EnsureService()
    if (health = "") {
        MsgBox("The Ghost Pepper dictation service did not start.`n`n"
            . "Check ghost-pepper-windows.log in the logs folder from config.yaml (C:\GhostPepperWindows\logs by default), "
            . "or run this in PowerShell from the repo folder to see the error:`n.\.venv\Scripts\gpw.exe serve",
            "Ghost Pepper", "Iconx")
        ExitApp(1)
    }
    LoadSettings(health)
    try Http("POST", "/cancel")  ; drop anything a previous run left behind
    try BindHotkeys()
    catch as bindError {
        MsgBox("A key in the hotkeys: block of config.yaml (dictate, cancel, add_word, recent, paste_last or meeting) "
            . "is not a key AutoHotkey knows.`n`n" bindError.Message,
            "Ghost Pepper", "Iconx")
        ExitApp(1)
    }
    OnExit(OnScriptExit)
    OnMessage(0x0006, OnRecentActivate)  ; WM_ACTIVATE
    OnMessage(0x0404, OnTrayNotify)  ; the tray icon's messages, for clicks on a meeting notice
    SetIcon("idle")
    PollMeeting()
    SetTimer(PollMeeting, 1000)
    PollInbox()
    SetTimer(PollInbox, 2000)
    TrayTip("Hold " DictateKey " to dictate. Double-tap it for hands-free." (DryRun ? "`nDry run: nothing goes to OpenAI." : ""),
        "Ghost Pepper is ready", "Mute")
}

; -- service --

EnsureService() {
    global
    health := GetHealth()
    if (health != "" && JsonValue(health, "dry_run") != (DryRun ? "true" : "false")) {
        ; Running in the other mode: stop it and start the mode asked for.
        try ProcessClose(Integer(JsonValue(health, "pid", 0)))
        Sleep(500)
        health := ""
    }
    if (health != "")
        return health
    if !FileExist(PythonExe) {
        MsgBox("Python is not set up in " RepoRoot "\.venv. Run app\scripts\setup.ps1 first.", "Ghost Pepper", "Iconx")
        return ""
    }
    cmd := '"' PythonExe '" -m gpw.cli --config "' ConfigPath '" serve' (DryRun ? " --dry-run" : "")
    Run(cmd, RepoRoot, "Hide", &launcher)
    StartedService := true
    deadline := A_TickCount + 30000
    while (A_TickCount < deadline) {
        Sleep(250)
        health := GetHealth()
        if (health != "")
            return health
        if !ProcessExist(launcher)
            return ""  ; it exited: bad config or no API key; the log says which
    }
    return ""
}

GetHealth() {
    try {
        r := Http("GET", "/health", "", 1500)
        if (r.status = 200 && JsonValue(r.body, "service") = "gpw")
            return r.body
    }
    return ""
}

LoadSettings(health) {
    global
    ServicePid := Integer(JsonValue(health, "pid", 0))
    DictateKey := JsonValue(health, "dictate_key", "RCtrl")
    DictateKeyName := GetKeyName(DictateKey)
    CancelKey := JsonValue(health, "cancel_key", "Esc")
    AddWordKey := JsonValue(health, "add_word_key", "")
    RecentKey := JsonValue(health, "recent_key", "")
    PasteLastKey := JsonValue(health, "paste_last_key", "")
    MeetingKey := JsonValue(health, "meeting_key", "")
    DictionaryPath := StrReplace(JsonValue(health, "dictionary_path"), "/", "\")
    SnippetsPath := StrReplace(JsonValue(health, "snippets_path"), "/", "\")
    TapMs := Integer(JsonValue(health, "tap_ms", 250))
    DoubleTapMs := Integer(JsonValue(health, "double_tap_ms", 400))
    MaxMs := Integer(JsonValue(health, "max_seconds", 600)) * 1000
    SoundsOn := JsonValue(health, "sounds", "true") = "true"
    RestoreMs := Integer(JsonValue(health, "paste_restore_ms", 250))
    AssetsDir := StrReplace(JsonValue(health, "assets_dir"), "/", "\")
    NotesDir := StrReplace(JsonValue(health, "notes_dir"), "/", "\")
    LogsDir := StrReplace(JsonValue(health, "logs_dir"), "/", "\")
}

Http(method, path, body := "", timeoutMs := 5000) => GpwHttp(BaseUrl, method, path, body, timeoutMs)
IsAborted() => Aborted

; -- keys --

BindHotkeys() {
    global
    prefix := HotkeyPrefix(DictateKey)
    Hotkey(prefix DictateKey, OnDictateDown)
    Hotkey(prefix DictateKey " Up", OnDictateUp)
    HotIf(IsActive)
    Hotkey("*" CancelKey, OnCancelKey)
    HotIf()
    if (AddWordKey != "")
        Hotkey(AddWordKey, OnAddWord)
    if (RecentKey != "")
        Hotkey(RecentKey, ToggleRecent)
    if (PasteLastKey != "")
        Hotkey(PasteLastKey, OnPasteLast)
    if (MeetingKey != "")
        Hotkey(MeetingKey, ToggleMeeting)
    ; Number keys pick a row, only while the recent-dictations window is the active window.
    HotIf(RecentIsActive)
    loop 10 {
        Hotkey(A_Index - 1 "", OnRecentNumber)
        Hotkey("Numpad" (A_Index - 1), OnRecentNumber)
    }
    HotIf()
}

IsActive(*) => State != "idle"

OnDictateDown(*) {
    global
    if KeyDown
        return  ; key repeat
    KeyDown := true
    DownTick := A_TickCount
    Dispatch("down")
}

OnDictateUp(*) {
    global
    KeyDown := false
    Dispatch("up", A_TickCount - DownTick, A_PriorKey != DictateKeyName)
}

OnCancelKey(*) => Dispatch("cancel")
OnTapTimeout() => Dispatch("tap_timeout")
OnMaxTimeout() => Dispatch("max_timeout")

Dispatch(event, heldMs := 0, combo := false) {
    global
    before := State
    step := NextStep(State, event, heldMs, combo, TapMs)
    State := step[1]
    switch step[2] {
        case "start":
            StartRecording()
        case "stop":
            ReleaseTick := A_TickCount
            StopMode := (event = "max_timeout") ? "max" : InStr(before, "handsfree") ? "handsfree" : "hold"
            SetTimer(OnMaxTimeout, 0)
            SetTimer(OnTapTimeout, 0)
            SetIcon("busy")
            SetTimer(StopCue, -120)  ; after the mic has closed, so the beep is not recorded
            SetTimer(FinishDictation, -1)
        case "cancel", "cancel_quiet":
            SetTimer(OnMaxTimeout, 0)
            SetTimer(OnTapTimeout, 0)
            try Http("POST", "/cancel")
            SetIcon("idle")
            if (step[2] = "cancel") {
                PlayCue("cancel")
                ShowTip("Cancelled", 1000)
            }
        case "abort":
            Aborted := true
            try Http("POST", "/cancel")
            SetIcon("idle")
            PlayCue("cancel")
            ShowTip("Cancelled", 1000)
        case "arm_tap":
            SetTimer(OnTapTimeout, -DoubleTapMs)
        case "disarm_tap":
            SetTimer(OnTapTimeout, 0)
        case "handsfree":
            PlayCue("handsfree")
            SetIcon("handsfree")
            ShowTip("Hands-free. Tap " DictateKey " to stop and paste. " CancelKey " cancels.", 2500)
    }
}

StartRecording() {
    global
    try r := Http("POST", "/start", "", 8000)
    catch {
        State := "idle"
        PlayCue("error")
        ShowTip("The dictation service stopped. Starting it again; try in a few seconds.", 4000)
        SetTimer(RestartService, -1)
        return
    }
    if (r.status != 200) {
        State := "idle"
        SetIcon("idle")
        PlayCue("error")
        ShowTip(JsonValue(r.body, "error", "Could not start recording."), 4000)
        return
    }
    PlayCue("start")
    SetIcon("recording")
    SetTimer(OnMaxTimeout, -MaxMs)
}

RestartService() {
    global
    health := EnsureService()
    if (health != "")
        LoadSettings(health)
}

FinishDictation() {
    global
    Aborted := false
    r := ""
    try r := GpwHttpAbortable(BaseUrl, "/stop", "mode=" StopMode, 900000, IsAborted)
    catch {
        PlayCue("error")
        ShowTip("Lost contact with the dictation service.", 4000)
    }
    if (r != "" && !Aborted) {
        status := GpwHeader(r.whr, "X-Dictation-Status")
        if (status = "ok")
            PasteAndReport(r.body, GpwHeader(r.whr, "X-Dictation-Id"), GpwHeader(r.whr, "X-Dictation-Note"))
        else if (status = "skipped") {
            PlayCue("empty")
            ShowTip(r.body, 2000)
        } else if (status != "cancelled") {
            PlayCue("error")
            ShowTip(r.body != "" ? r.body : "Dictation failed. See the log.", 4000)
        }
    }
    Dispatch("done")
    if (State = "idle")
        SetIcon("idle")
}

; -- paste --

PasteAndReport(text, id, note := "") {
    try {
        gotTick := A_TickCount
        app := ActiveProgram()  ; for the recent-dictations window
        p := PasteText(text)
    } catch as e {
        PlayCue("error")
        ShowTip("Paste failed: " e.Message, 4000)
        return
    }
    if (note != "")
        ShowTip(note, 5000)
    body := Format("id={}&release_to_paste_ms={}&paste_ms={}&clipboard_restored={}&app={}",
        id, p.tick - ReleaseTick, p.tick - gotTick, p.restored ? 1 : 0, UriEncode(app))
    r := ""
    try r := Http("POST", "/pasted", body, 2000)
    learnMs := (r != "") ? Integer(JsonValue(r.body, "learn_ms", 0)) : 0
    if (learnMs > 0)
        SetTimer(CheckLearned, -(learnMs + 700))  ; a later paste restarts the wait; nothing is lost
}

; Paste through the clipboard, then put the old clipboard back. Throws if the clipboard would not take
; the text. Returns {tick: when Ctrl+V went out, restored: whether the old clipboard came back byte for byte}.
PasteText(text) {
    saved := ClipboardAll()
    A_Clipboard := ClipboardText(text)
    if !ClipWait(1)
        throw Error("the clipboard did not take the text")
    Send("^v")
    tick := A_TickCount
    Sleep(RestoreMs)  ; give the app time to read the clipboard before putting the old one back
    return {tick: tick, restored: RestoreClipboard(saved)}
}

; The program that has focus, such as "OUTLOOK.EXE"; "" when no window has focus.
ActiveProgram() {
    try return WinGetProcessName("A")
    return ""
}

; Auto-learn finished watching the field: show any fix it saved, so a wrong one is easy to spot.
CheckLearned() {
    r := ""
    try r := Http("POST", "/learned", "", 3000)
    if (r != "" && r.status = 200 && Trim(r.body) != "")
        ShowTip("Learned for next time (edit in the dictionary from the tray menu):`n" r.body, 5000)
}

; -- dictionary --

; Copy the selection and add it to dictionary terms. The clipboard is put back afterwards.
OnAddWord(*) {
    if (State != "idle")
        return
    for mod in ["Ctrl", "Alt", "Shift", "LWin", "RWin"]
        KeyWait(mod, "T1")  ; let go of the hotkey first, so Ctrl+C is not Ctrl+Alt+C
    saved := ClipboardAll()
    A_Clipboard := ""
    Send("^c")
    copied := ClipWait(0.6) ? A_Clipboard : ""
    RestoreClipboard(saved)
    word := Trim(copied, " `t`r`n")
    if (word = "") {
        ShowTip("Select a word first, then press " HotkeyLabel(AddWordKey) ".", 2500)
        return
    }
    if InStr(word, "`n") {
        ShowTip("Select just the word or name, not a whole line.", 2500)
        return
    }
    r := ""
    try r := Http("POST", "/dictionary/add", "word=" UriEncode(word), 3000)
    if (r = "") {
        ShowTip("The dictation service is not answering.", 3000)
        return
    }
    ok := r.status = 200
    if !ok
        PlayCue("error")
    ShowTip(JsonValue(r.body, ok ? "message" : "error", "Could not add it."), ok ? 2000 : 4000)
}

; Put the saved clipboard back and check it byte for byte.
RestoreClipboard(saved) {
    A_Clipboard := saved
    after := ClipboardAll()
    if (after.Size != saved.Size)
        return false
    return saved.Size = 0
        || DllCall("ntdll\RtlCompareMemory", "Ptr", saved.Ptr, "Ptr", after.Ptr, "UPtr", saved.Size, "UPtr") = saved.Size
}

; -- recent dictations --

; The last 10 dictations from the service, newest first. "" if the service did not answer.
FetchRecent() {
    r := ""
    try r := Http("POST", "/recent", "", 2000)
    return (r != "" && r.status = 200) ? ParseRecent(r.body) : ""
}

; The paste_last hotkey: paste the latest dictation again, with the usual clipboard save and restore.
OnPasteLast(*) {
    if (State != "idle")
        return
    for mod in ["Ctrl", "Alt", "Shift", "LWin", "RWin"]
        KeyWait(mod, "T1")  ; let go of the hotkey first, so Ctrl+V is not Ctrl+Alt+V
    items := FetchRecent()
    if (items = "") {
        ShowTip("The dictation service is not answering.", 3000)
        return
    }
    if !items.Length {
        ShowTip("No dictation to paste yet.", 2000)
        return
    }
    try p := PasteText(items[1].text)
    catch as e {
        PlayCue("error")
        ShowTip("Paste failed: " e.Message, 4000)
        return
    }
    if !p.restored
        ShowTip("Pasted. The clipboard could not be put back exactly.", 2500)
}

; The recent hotkey opens the window, and closes it when it is already open.
ToggleRecent(*) {
    if (RecentGui != "")
        CloseRecent()
    else
        ShowRecent()
}

; The recent-dictations window, near the tray: newest first, a copy button on each row,
; the whole text on hover. Rebuilt on every open, so it always shows the current list.
ShowRecent(*) {
    global RecentGui, RecentItems, RecentRows, RecentHeaders, RecentTipRow
    static textW := 330, btnW := 36, gap := 8, margin := 12, headerH := 16, previewH := 32
    items := FetchRecent()
    if (items = "") {
        ShowTip("The dictation service is not answering.", 3000)
        return
    }
    CloseRecent()
    RecentItems := items
    RecentRows := Map()
    RecentHeaders := []
    RecentTipRow := 0
    fullW := textW + gap + btnW
    g := Gui("+AlwaysOnTop +ToolWindow -MinimizeBox", "Recent dictations")
    g.MarginX := margin, g.MarginY := margin
    g.BackColor := "FFFFFF"
    g.OnEvent("Escape", CloseRecent)
    g.OnEvent("Close", CloseRecent)
    g.SetFont("s9", "Segoe UI")
    y := margin
    if !items.Length {
        g.Add("Text", "x" margin " y" y " w" fullW, "No dictations yet. The last 10 show up here.")
        y += 20
    }
    for i, item in items {
        if (i > 1) {
            g.Add("Text", "x" margin " y" y " w" fullW " h1 0x10")  ; a line between rows
            y += 9
        }
        header := g.Add("Text", "x" margin " y" y " w" textW " h" headerH " c707070 +0x100", RecentHeader(item, i))
        preview := g.Add("Text", "x" margin " y" (y + headerH + 2) " w" textW " h" previewH " +0x100", RecentPreview(item))
        g.SetFont("s12", "Segoe MDL2 Assets")
        g.SetFont(, "Segoe Fluent Icons")  ; Windows 11 has it; Windows 10 keeps the font above
        btn := g.Add("Button", "x" (margin + textW + gap) " y" (y + 7) " w" btnW " h" btnW, Chr(0xE8C8))  ; Copy icon
        g.SetFont("s9", "Segoe UI")
        btn.OnEvent("Click", CopyRecent.Bind(i, false))
        for ctrl in [header, preview, btn]
            RecentRows[ctrl.Hwnd] := i
        RecentHeaders.Push(header)
        y += headerH + 2 + previewH + 8
    }
    g.SetFont("s8 c909090", "Segoe UI")
    g.Add("Text", "x" margin " y" (y + 4) " w" fullW,
        items.Length ? "1-9 and 0 copy that row and close. Esc closes." : "Esc closes.")
    g.Show("Hide AutoSize")
    g.GetPos(, , &w, &h)
    primary := MonitorGetPrimary()
    MonitorGet(primary, &monL, &monT)
    MonitorGetWorkArea(primary, &workL, &workT, &workR, &workB)
    pos := RecentWindowPos(w, h, monL, monT, workL, workT, workR, workB)
    RecentGui := g
    g.Show("x" pos[1] " y" pos[2])
    try WinActivate(g.Hwnd)
    SetTimer(CheckRecentHover, 150)
}

RecentIsActive(*) => RecentGui != "" && WinActive("ahk_id " RecentGui.Hwnd)

; Put a row's exact text on the clipboard, line breaks included. The copy button shows "Copied"
; on the row and leaves the window open; a number key closes it.
CopyRecent(i, close, *) {
    if (i > RecentItems.Length)
        return
    A_Clipboard := ClipboardText(RecentItems[i].text)
    if close {
        CloseRecent()
        ShowTip("Copied. Paste it with Ctrl+V.", 1500)
        return
    }
    header := RecentHeaders[i]
    header.Text := "Copied"
    SetTimer(RestoreRecentHeader.Bind(header, RecentHeader(RecentItems[i], i)), -1500)
}

RestoreRecentHeader(header, text) {
    try header.Text := text  ; the window may be closed by now
}

OnRecentNumber(hk) {
    row := RecentRowForKey(hk)
    if (row && row <= RecentItems.Length)
        CopyRecent(row, true)
}

CloseRecent(*) {
    global RecentGui
    SetTimer(CheckRecentHover, 0)
    ToolTip(, , , 2)
    if (RecentGui != "") {
        g := RecentGui
        RecentGui := ""
        g.Destroy()
    }
}

; Clicking anywhere else closes the window.
OnRecentActivate(wParam, lParam, msg, hwnd) {
    if (RecentGui != "" && hwnd = RecentGui.Hwnd && (wParam & 0xFFFF) = 0)
        SetTimer(CloseRecent, -1)
}

; Hovering a row shows its whole text, in a second tooltip so the status tips are left alone.
CheckRecentHover() {
    global RecentTipRow
    if (RecentGui = "")
        return
    MouseGetPos(, , &win, &ctrl, 2)
    row := (win = RecentGui.Hwnd && ctrl && RecentRows.Has(ctrl)) ? RecentRows[ctrl] : 0
    if (row = RecentTipRow)
        return
    RecentTipRow := row
    if row
        ToolTip(WrapForTip(RecentItems[row].text), , , 2)
    else
        ToolTip(, , , 2)
}

; -- tray, sounds, tips --

SetupTray() {
    A_IconTip := "Ghost Pepper: starting"
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Recent dictations", ShowRecent)
    A_TrayMenu.Default := "Recent dictations"
    A_TrayMenu.ClickCount := 1  ; a single left-click on the tray icon opens it
    A_TrayMenu.Add()
    A_TrayMenu.Add(MeetingMenuItem, ToggleMeeting)
    A_TrayMenu.Add("Mute my mic track", ToggleMeetingMute)
    A_TrayMenu.Add("Retry failed meeting", RetryMeeting)
    A_TrayMenu.Add("Open meeting notes", OpenMeetingNotes)
    A_TrayMenu.Add("Open inbox folder", OpenInbox)
    A_TrayMenu.Disable("Mute my mic track")
    A_TrayMenu.Disable("Retry failed meeting")
    A_TrayMenu.Add()
    A_TrayMenu.Add("Open today's dictation log", OpenDailyLog)
    A_TrayMenu.Add("Open dictionary", OpenDictionary)
    A_TrayMenu.Add("Open snippets", OpenSnippets)
    A_TrayMenu.Add("Open log folder", OpenLogs)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Restart (reloads config)", RestartAll)
    A_TrayMenu.Add("Exit", ExitAll)
}

SetIcon(name) {
    static tips := Map("idle", "ready", "recording", "listening", "handsfree", "listening (hands-free)", "busy", "transcribing")
    static shown := ""
    icon := (name = "idle" && MeetingOn) ? "meeting" : name
    if (icon != shown) {
        try TraySetIcon(AssetsDir "\" icon ".ico")
        shown := icon
    }
    if (icon = "meeting")
        tip := MeetingTip(MeetingElapsed, MeetingMuted, MeetingMicTrack, DryRun)
    else
        tip := "Ghost Pepper: " tips[name] (DryRun ? " (dry run)" : "")
    A_IconTip := SubStr(tip (InboxLine != "" ? "`n" InboxLine : ""), 1, 127)
}

PlayCue(name) {
    if SoundsOn
        try SoundPlay(AssetsDir "\" name ".wav")
}

StopCue() => PlayCue("stop")

ShowTip(text, ms) {
    ToolTip(text)
    SetTimer(HideTip, -ms)
}

HideTip() => ToolTip()

OpenDailyLog(*) {
    path := NotesDir "\dictation\" FormatTime(, "yyyy-MM-dd") ".md"
    if FileExist(path) {
        try Run(path)
        catch
            Run('notepad.exe "' path '"')
    } else
        ShowTip("No dictations logged today yet.", 2000)
}

OpenDictionary(*) {
    if (DictionaryPath != "" && FileExist(DictionaryPath))
        Run('notepad.exe "' DictionaryPath '"')  ; .yaml has no default app on most PCs
    else
        ShowTip("No dictionary file yet. It is created when the service starts.", 2500)
}

OpenSnippets(*) {
    if (SnippetsPath != "" && FileExist(SnippetsPath))
        Run('notepad.exe "' SnippetsPath '"')
    else
        ShowTip("No snippets file yet. It is created when the service starts.", 2500)
}

OpenLogs(*) {
    if DirExist(LogsDir)
        Run(LogsDir)
}

RestartAll(*) {
    global
    if !StopMeetingFirst("Restart")
        return
    if ServicePid
        try ProcessClose(ServicePid)
    Reload()
}

ExitAll(*) {
    if StopMeetingFirst("Exit")
        ExitApp()
}

; Before the service is stopped: a meeting being captured is stopped first, so its audio is kept
; and its note is written when the service next starts. False if the user said no.
StopMeetingFirst(action) {
    if !MeetingOn
        return true
    answer := MsgBox("A meeting is being captured. " action " stops the capture; the note is written "
        . "the next time Ghost Pepper runs.`n`n" action " anyway?", "Ghost Pepper", "YesNo Icon!")
    if (answer != "Yes")
        return false
    try Http("POST", "/meeting/stop", "", 15000)
    return true
}

; -- meeting capture --

ToggleMeeting(*) {
    if MeetingOn
        StopMeeting()
    else
        StartMeeting()
}

StartMeeting() {
    global MeetingOn, MeetingElapsed
    r := ""
    try r := Http("POST", "/meeting/start", "app=" UriEncode(FrontProgram()), 15000)
    if (r = "") {
        PlayCue("error")
        ShowTip("The dictation service is not answering.", 3000)
        return
    }
    if (r.status != 200) {
        PlayCue("error")
        ShowTip(JsonValue(r.body, "error", "Could not start meeting capture."), 5000)
        PollMeeting()
        return
    }
    MeetingOn := true
    MeetingElapsed := 0
    PlayCue("meeting_start")
    stopWith := MeetingKey != "" ? HotkeyLabel(MeetingKey) " or the tray menu" : "the tray menu"
    ShowTip("Meeting capture started" (JsonValue(r.body, "capture_mic") = "true" ? "" : " (call audio only)")
        . ". Stop it with " stopWith ".", 3000)
    UpdateMeetingUi()
}

StopMeeting() {
    global MeetingOn
    r := ""
    try r := Http("POST", "/meeting/stop", "", 15000)
    if (r = "") {
        PlayCue("error")
        ShowTip("The dictation service is not answering.", 3000)
        return
    }
    MeetingOn := false
    if (r.status = 200) {
        PlayCue("meeting_stop")
        ShowTip("Meeting capture stopped after " FormatElapsed(Round(JsonValue(r.body, "seconds", 0))) ". "
            . "The note is being written; a notice says when it's ready.", 4000)
    } else
        ShowTip(JsonValue(r.body, "error", "Could not stop meeting capture."), 3000)
    PollMeeting()
}

ToggleMeetingMute(*) {
    if !MeetingOn
        return
    r := ""
    try r := Http("POST", "/meeting/mute", "on=" (MeetingMuted ? 0 : 1), 3000)
    if (r != "" && r.status = 200)
        ShowTip(JsonValue(r.body, "muted") = "true" ? "Your mic track is muted. The call is still recorded."
            : "Your mic track is recording again.", 2500)
    PollMeeting()
}

RetryMeeting(*) {
    r := ""
    try r := Http("POST", "/meeting/retry", "", 5000)
    if (r = "")
        ShowTip("The dictation service is not answering.", 3000)
    else if (r.status = 200)
        ShowTip("Retrying meeting " JsonValue(r.body, "folder") ". A notice says when the note is ready.", 3000)
    else
        ShowTip(JsonValue(r.body, "error", "Could not retry it."), 4000)
    PollMeeting()
}

OpenMeetingNotes(*) {
    folder := NotesDir "\meetings"
    if DirExist(folder)
        Run(folder)
    else
        ShowTip("No meeting notes yet.", 2000)
}

; Ask the service how the capture is going: keeps the tray icon, tooltip and menu current,
; and shows the notices (note ready, failed, no call audio).
PollMeeting() {
    global MeetingOn, MeetingElapsed, MeetingMuted, MeetingMicTrack, MeetingRetry, ToastTarget
    r := ""
    try r := Http("POST", "/meeting/status", "", 1500)
    if (r = "" || r.status != 200)
        return
    MeetingOn := JsonValue(r.body, "capturing") = "true"
    MeetingElapsed := Integer(JsonValue(r.body, "elapsed", 0))
    MeetingMuted := JsonValue(r.body, "muted") = "true"
    MeetingMicTrack := JsonValue(r.body, "mic_track") = "true"
    MeetingRetry := JsonValue(r.body, "retry")
    for notice in MeetingNotices(r.body, MeetingSeen) {
        if (notice.kind = "tip")
            ShowTip(notice.text, 8000)
        else {
            ToastTarget := notice.target
            TrayTip(notice.text, notice.title, notice.title = "Meeting note failed" ? "Iconx" : "Iconi")
        }
    }
    UpdateMeetingUi()
}

UpdateMeetingUi() {
    global MeetingMenuItem
    wanted := MeetingOn ? "Stop meeting capture" : "Start meeting capture"
    if (wanted != MeetingMenuItem) {
        A_TrayMenu.Rename(MeetingMenuItem, wanted)
        MeetingMenuItem := wanted
    }
    if (MeetingOn && MeetingMicTrack)
        A_TrayMenu.Enable("Mute my mic track")
    else
        A_TrayMenu.Disable("Mute my mic track")
    if (MeetingOn && MeetingMuted)
        A_TrayMenu.Check("Mute my mic track")
    else
        A_TrayMenu.Uncheck("Mute my mic track")
    if (MeetingRetry != "")
        A_TrayMenu.Enable("Retry failed meeting")
    else
        A_TrayMenu.Disable("Retry failed meeting")
    if (State = "idle")
        SetIcon("idle")
}

; Clicking a meeting notice opens the note, or the folder of a failed capture.
OnTrayNotify(wParam, lParam, msg, hwnd) {
    global ToastTarget
    if ((lParam & 0xFFFF) != 0x405 || ToastTarget = "")  ; NIN_BALLOONUSERCLICK
        return
    target := ToastTarget
    ToastTarget := ""
    if DirExist(target)
        Run(target)
    else if FileExist(target) {
        try Run('"' target '"')
        catch
            Run('notepad.exe "' target '"')
    }
}

; -- long recordings --

OpenInbox(*) {
    if (InboxDir != "" && !DirExist(InboxDir))
        try DirCreate(InboxDir)
    if (InboxDir != "" && DirExist(InboxDir))
        Run(InboxDir)
    else
        ShowTip("The recordings inbox is not available. Check the service log.", 3000)
}

; Ask the service about the inbox: the tooltip line while a file is processed, and the notices
; when a note is ready or a file failed.
PollInbox() {
    global InboxDir, InboxLine, ToastTarget
    r := ""
    try r := Http("POST", "/inbox/status", "", 1500)
    if (r = "" || r.status != 200)
        return
    InboxDir := StrReplace(JsonValue(r.body, "folder"), "/", "")
    line := InboxTipLine(r.body)
    for notice in InboxNotices(r.body, InboxSeen) {
        ToastTarget := notice.target
        TrayTip(notice.text, notice.title, notice.title = "Recording failed" ? "Iconx" : "Iconi")
    }
    if (line != InboxLine) {
        InboxLine := line
        if (State = "idle")
            SetIcon("idle")
    }
}

; The program the call is probably in: the active window, or when that is the taskbar or the tray
; menu (starting from the tray), the next app window down.
FrontProgram() {
    try {
        if !IsShellWindow(WinGetClass("A"))
            return WinGetProcessName("A")
    }
    for hwnd in WinGetList() {  ; top of the z-order first
        try {
            if !(WinGetStyle(hwnd) & 0x10000000) || IsShellWindow(WinGetClass(hwnd)) || WinGetTitle(hwnd) = ""
                continue
            exe := WinGetProcessName(hwnd)
            if !InStr(exe, "AutoHotkey")
                return exe
        }
    }
    return ""
}

OnScriptExit(reason, code) {
    global
    if (reason = "Reload")
        return
    if (StartedService && ServicePid)
        try ProcessClose(ServicePid)
}
