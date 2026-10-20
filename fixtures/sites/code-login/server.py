"""A tiny site that asks for a one-time code before it shows its form (PL-068).

Two pages and one form:

* ``GET /`` and ``GET /form`` -- while no session is verified, both answer the *code page*:
  the heading "Enter the 6-digit code we emailed you" and six single-character boxes with a
  Verify button. The page is the shape the extension's detector recognises, and the only way
  past it is the code the fixture was started with (``246810``).
* ``POST /code`` -- accepts the code (form field ``code``, or ``d0``..``d5`` for the split
  boxes). A wrong code re-renders the page with one plain sentence. A right one sets an
  ``HttpOnly`` session cookie and redirects to ``/form``.
* ``GET /form`` (verified) -- a name field and Save.
* ``POST /save`` -- records the name and redirects to ``/saved``, which shows "Saved".

The golden uses the cookie deliberately: it posts the code once *before recording*, so the
demonstration never types a code and the recording cannot carry one. It then clears the
browser's cookies, so the replay meets the code page for real.

Run it directly to see the JSON handshake line on stdout:

    python server.py --port 0
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CODE = "246810"
COOKIE = "code_login_session"

STYLE = """
body { font-family: system-ui, sans-serif; max-width: 34rem; margin: 4rem auto; color: #1d1d1f; }
h1 { font-size: 1.4rem; }
.boxes { display: flex; gap: .5rem; margin: 1rem 0; }
.boxes input { width: 2.2rem; height: 2.6rem; text-align: center; font-size: 1.3rem; }
label { display: block; margin: .8rem 0 .3rem; }
input[type=text] { width: 100%; padding: .5rem; }
button { margin-top: 1rem; padding: .5rem 1rem; }
.error { color: #b3261e; }
"""

CODE_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Verify</title><style>{style}</style></head>
<body>
<h1>Enter the 6-digit code we emailed you</h1>
{error}
<form method="post" action="/code">
  <div class="boxes">
    {boxes}
  </div>
  <button type="submit">Verify</button>
</form>
</body></html>
"""

BOX = '<input maxlength="1" inputmode="numeric" aria-label="Digit {n}" name="d{index}">'


def code_page(error: str = "") -> bytes:
    boxes = "\n".join(BOX.format(n=n, index=n - 1) for n in range(1, 7))
    note = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return CODE_PAGE.format(style=STYLE, boxes=boxes, error=note).encode("utf-8")


FORM_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Candidate</title><style>{style}</style></head>
<body>
<h1>Candidate details</h1>
<form method="post" action="/save">
  <label for="name">Full name</label>
  <input id="name" name="name" type="text" placeholder="Full name">
  <button type="submit">Save</button>
</form>
</body></html>
"""

SAVED_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Saved</title><style>{style}</style></head>
<body><div role="status">Saved</div><p>{name}</p></body></html>
"""


class State:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.saved: list[str] = []


class Handler(BaseHTTPRequestHandler):
    state = State()

    def log_message(self, *args: object) -> None:  # quiet
        return

    def _send(self, body: bytes, status: int = 200, cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        if cookie is not None:
            self.send_header("set-cookie", f"{COOKIE}={cookie}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("location", location)
        if cookie is not None:
            self.send_header("set-cookie", f"{COOKIE}={cookie}; Path=/; HttpOnly")
        self.send_header("content-length", "0")
        self.end_headers()

    def _verified(self) -> bool:
        raw = self.headers.get("cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value in self.state.sessions:
                return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        path = self.path.split("?", 1)[0]
        if path in ("/", "/form"):
            if not self._verified():
                self._send(code_page())
                return
            self._send(FORM_PAGE.format(style=STYLE).encode("utf-8"))
            return
        if path == "/saved":
            name = html.escape(self.state.saved[-1] if self.state.saved else "")
            self._send(SAVED_PAGE.format(style=STYLE, name=name).encode("utf-8"))
            return
        self._send(b"<h1>Not found</h1>", status=404)

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        length = int(self.headers.get("content-length") or "0")
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        path = self.path.split("?", 1)[0]
        if path == "/code":
            typed = (fields.get("code") or [""])[0] or "".join(
                (fields.get(f"d{index}") or [""])[0] for index in range(6)
            )
            if typed != CODE:
                self._send(code_page("That code is not right. Try again."), status=200)
                return
            token = secrets.token_urlsafe(16)
            self.state.sessions.add(token)
            self._redirect("/form", cookie=token)
            return
        if path == "/save":
            self.state.saved.append((fields.get("name") or [""])[0])
            self._redirect("/saved")
            return
        self._send(b"<h1>Not found</h1>", status=404)


def make_server(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = make_server(args.port)
    host, port = server.server_address[:2]
    print(json.dumps({"url": f"http://{host}:{port}", "port": port}), flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
