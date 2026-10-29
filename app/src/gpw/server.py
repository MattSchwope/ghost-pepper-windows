"""HTTP front for the dictation service, for the AutoHotkey script. Bound to 127.0.0.1 only (ADR-003).

Endpoints:
  GET  /health   service state and the hotkey settings from config.yaml (JSON)
  POST /start    open the mic and start recording (JSON)
  POST /stop     stop, transcribe; the body is the text to paste (text/plain), status in X-Dictation-* headers
  POST /cancel   drop the recording, or the transcription in flight (JSON)
  POST /pasted   the script reports its release-to-paste timing; starts auto-learn (JSON)
  POST /learned  fixes auto-learn saved since the last call, one "wrong -> Right" per line (text/plain)
  POST /dictionary/add  the add-word hotkey: word=<selected text> (JSON)

POSTs must carry the X-GPW-Client header and no Origin header, so a web page
cannot start the mic: browsers add Origin to cross-site requests and cannot add
custom headers without a CORS preflight, which this server never approves.
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .mic import MicError
from .service import DictationService, ServiceBusy

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
CLIENT_HEADER = "X-GPW-Client"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_BODY = 64 * 1024


def _int(form: dict[str, list[str]], key: str, default: int = 0) -> int:
    try:
        return int(form.get(key, [default])[0])
    except (TypeError, ValueError):
        return default


def make_handler(service: DictationService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "gpw"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            logger.debug("http %s", format % args)

        def _send(self, code: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def _refusal(self, post: bool) -> str | None:
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip().lower()
            if host not in ALLOWED_HOSTS:
                return "Unexpected Host header."
            if self.headers.get("Origin"):
                return "Browser requests are not allowed."
            if post and not self.headers.get(CLIENT_HEADER):
                return f"Missing {CLIENT_HEADER} header."
            return None

        def do_GET(self) -> None:
            refusal = self._refusal(post=False)
            if refusal:
                self._json(403, {"ok": False, "error": refusal})
            elif urlsplit(self.path).path == "/health":
                self._json(200, service.health())
            else:
                self._json(404, {"ok": False, "error": "Not found."})

        def do_POST(self) -> None:
            refusal = self._refusal(post=True)
            if refusal:
                self._json(403, {"ok": False, "error": refusal})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json(413, {"ok": False, "error": "Body too large."})
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace")) if length else {}
            path = urlsplit(self.path).path
            try:
                if path == "/start":
                    self._start()
                elif path == "/stop":
                    self._stop(form.get("mode", ["hold"])[0])
                elif path == "/cancel":
                    self._json(200, {"ok": True, "result": service.cancel()})
                elif path == "/pasted":
                    restored = form.get("clipboard_restored", [""])[0]
                    dictation_id = _int(form, "id")
                    ok = service.pasted(
                        dictation_id, _int(form, "release_to_paste_ms"), _int(form, "paste_ms"),
                        None if restored == "" else restored == "1",
                    )
                    learn_ms = service.watch_for_corrections(dictation_id) if ok else 0
                    self._json(200, {"ok": ok, "learn_ms": learn_ms})
                elif path == "/learned":
                    lines = [f"{wrong} -> {right}" for wrong, right in service.take_learned()]
                    self._send(200, "\n".join(lines).encode("utf-8"), "text/plain; charset=utf-8")
                elif path == "/dictionary/add":
                    self._add_word(form.get("word", [""])[0])
                else:
                    self._json(404, {"ok": False, "error": "Not found."})
            except Exception as exc:
                logger.exception("Request %s failed", path)
                self._json(500, {"ok": False, "error": f"Service error ({type(exc).__name__})."})

        def _start(self) -> None:
            try:
                self._json(200, {"ok": True, **service.start()})
            except ServiceBusy as exc:
                self._json(409, {"ok": False, "error": str(exc)})
            except MicError as exc:
                logger.error("Mic error: %s", exc)
                self._json(503, {"ok": False, "error": str(exc)})

        def _add_word(self, word: str) -> None:
            try:
                added, message = service.add_word(word)
                self._json(200, {"ok": True, "added": added, "message": message})
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})

        def _stop(self, mode: str) -> None:
            result = service.stop("".join(ch for ch in mode if ch.isalnum() or ch == "_")[:20] or "hold")
            body = result.text if result.status == "ok" else result.message
            headers = {"X-Dictation-Id": str(result.dictation_id), "X-Dictation-Status": result.status}
            if result.note:
                headers["X-Dictation-Note"] = result.note
            self._send(200, body.encode("utf-8"), "text/plain; charset=utf-8", headers)

    return Handler


class DictationServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port; refuse that instead.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Skip HTTPServer.server_bind: its reverse DNS lookup can stall startup on Windows.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]


def make_server(service: DictationService, port: int) -> DictationServer:
    try:
        return DictationServer((HOST, port), make_handler(service))
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} on 127.0.0.1 is taken ({exc.strerror or exc}). Is Ghost Pepper already running? "
            "Otherwise set service.port in the config."
        ) from exc
