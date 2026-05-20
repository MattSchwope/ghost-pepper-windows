#Requires AutoHotkey v2.0
#SingleInstance Force

; Ghost Pepper Windows MVP hotkey script
; Default: Ctrl+Alt+Space records for 8 seconds, copies cleaned text, then pastes.

RepoRoot := A_ScriptDir "\..\.."
RunScript := RepoRoot "\app\scripts\run_dictation.ps1"

^!Space::
{
    ToolTip "Recording for 8 seconds..."
    ExitCode := RunWait('powershell.exe -ExecutionPolicy Bypass -File "' RunScript '" -Duration 8 -Profile general_cleanup', , "Hide")
    ToolTip
    if (ExitCode = 0) {
        Send "^v"
    } else {
        MsgBox "Dictation failed. Check logs in C:\GhostPepperWindows\logs."
    }
}
