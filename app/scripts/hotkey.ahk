#Requires AutoHotkey v2.0
#SingleInstance Force

; Ghost Pepper Windows MVP hotkey script
; Default: Ctrl+Alt+Space records mic audio for 8 seconds and saves a WAV.

RepoRoot := A_ScriptDir "\..\.."
RunScript := RepoRoot "\app\scripts\run_dictation.ps1"

^!Space::
{
    ToolTip "Recording audio for 8 seconds..."
    ExitCode := RunWait('powershell.exe -ExecutionPolicy Bypass -File "' RunScript '" -Duration 8', , "Hide")
    if (ExitCode = 0) {
        ToolTip "Recording saved."
        Sleep 1200
        ToolTip
    } else {
        ToolTip
        MsgBox "Recording failed. Check logs in C:\GhostPepperWindows\logs."
    }
}
