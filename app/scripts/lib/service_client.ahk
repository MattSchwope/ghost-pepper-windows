#Requires AutoHotkey v2.0
; Ghost Pepper Windows: HTTP client for the local dictation service (gpw serve).
; Kept apart from hotkey.ahk so app/tests/test_ahk.py can run it against a real server.

; Blocking request. Returns {status, body, whr}; throws if the service is not running.
GpwHttp(baseUrl, method, path, body := "", timeoutMs := 5000) {
    whr := ComObject("WinHttp.WinHttpRequest.5.1")
    whr.Open(method, baseUrl path, false)
    GpwPrepare(whr, body, timeoutMs)
    whr.Send(body)
    return {status: whr.Status, body: GpwBodyText(whr), whr: whr}
}

; Like GpwHttp, but returns "" as soon as isAborted() is true (the Esc key), and lets hotkeys run meanwhile.
GpwHttpAbortable(baseUrl, path, body, timeoutMs, isAborted) {
    whr := ComObject("WinHttp.WinHttpRequest.5.1")
    whr.Open("POST", baseUrl path, true)
    GpwPrepare(whr, body, timeoutMs)
    whr.Send(body)
    while !whr.WaitForResponse(0.02) {
        if isAborted() {
            whr.Abort()
            return ""
        }
        Sleep(-1)
    }
    return {status: whr.Status, body: GpwBodyText(whr), whr: whr}
}

GpwPrepare(whr, body, timeoutMs) {
    whr.SetProxy(1)  ; direct: never route localhost through a proxy
    whr.SetTimeouts(1000, 1000, timeoutMs, timeoutMs)
    whr.SetRequestHeader("X-GPW-Client", "ahk")
    if (body != "")
        whr.SetRequestHeader("Content-Type", "application/x-www-form-urlencoded")
}

; Decode the body as UTF-8 here, so accents and smart quotes survive whatever WinHttp guesses.
GpwBodyText(whr) {
    try {
        bytes := whr.ResponseBody
        size := bytes.MaxIndex() + 1
        if (size > 0)
            return StrGet(NumGet(ComObjValue(bytes), 8 + A_PtrSize, "Ptr"), size, "UTF-8")
    }
    return ""
}

GpwHeader(whr, name) {
    try return whr.GetResponseHeader(name)
    return ""
}
