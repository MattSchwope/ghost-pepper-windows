#Requires AutoHotkey v2.0
#SingleInstance Force
#Include %A_ScriptDir%\lib\dictation_logic.ahk
#Include %A_ScriptDir%\lib\service_client.ahk

; Ghost Pepper Windows hotkeys. The Python service (gpw serve) records and transcribes;
; this script owns the keys, the cue sounds, the tray icon and the paste (ADR-003).
;   Hold RCtrl, talk, release: the text is pasted where the cursor is.
;   Double-tap RCtrl: hands-free. Tap RCtrl once more to stop and paste.
;   Esc while recording or transcribing: cancel.
;   Ctrl+Alt+D with a word selected: add it to the personal dictionary.
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
DictionaryPath := ""
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
        MsgBox("hotkeys.dictate, cancel or add_word in config.yaml is not a key AutoHotkey knows.`n`n" bindError.Message,
            "Ghost Pepper", "Iconx")
        ExitApp(1)
    }
    OnExit(OnScriptExit)
    SetIcon("idle")
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
    DictionaryPath := StrReplace(JsonValue(health, "dictionary_path"), "/", "\")
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
        saved := ClipboardAll()
        A_Clipboard := text
        if !ClipWait(1)
            throw Error("the clipboard did not take the text")
        Send("^v")
        pastedTick := A_TickCount
        Sleep(RestoreMs)  ; give the app time to read the clipboard before putting the old one back
        restored := RestoreClipboard(saved)
    } catch as e {
        PlayCue("error")
        ShowTip("Paste failed: " e.Message, 4000)
        return
    }
    if (note != "")
        ShowTip(note, 5000)
    body := Format("id={}&release_to_paste_ms={}&paste_ms={}&clipboard_restored={}",
        id, pastedTick - ReleaseTick, pastedTick - gotTick, restored ? 1 : 0)
    r := ""
    try r := Http("POST", "/pasted", body, 2000)
    learnMs := (r != "") ? Integer(JsonValue(r.body, "learn_ms", 0)) : 0
    if (learnMs > 0)
        SetTimer(CheckLearned, -(learnMs + 700))  ; a later paste restarts the wait; nothing is lost
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

; -- tray, sounds, tips --

SetupTray() {
    A_IconTip := "Ghost Pepper: starting"
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Open today's dictation log", OpenDailyLog)
    A_TrayMenu.Add("Open dictionary", OpenDictionary)
    A_TrayMenu.Add("Open log folder", OpenLogs)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Restart (reloads config)", RestartAll)
    A_TrayMenu.Add("Exit", (*) => ExitApp())
}

SetIcon(name) {
    static tips := Map("idle", "ready", "recording", "listening", "handsfree", "listening (hands-free)", "busy", "transcribing")
    try TraySetIcon(AssetsDir "\" name ".ico")
    A_IconTip := "Ghost Pepper: " tips[name] (DryRun ? " (dry run)" : "")
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

OpenLogs(*) {
    if DirExist(LogsDir)
        Run(LogsDir)
}

RestartAll(*) {
    global
    if ServicePid
        try ProcessClose(ServicePid)
    Reload()
}

OnScriptExit(reason, code) {
    global
    if (reason = "Reload")
        return
    if (StartedService && ServicePid)
        try ProcessClose(ServicePid)
}
