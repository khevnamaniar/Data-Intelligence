"""Minimal synchronous ASGI test client (no external dependencies).

Supports JSON bodies, multipart file uploads and a cookie jar, which is all the tests need.
"""
from __future__ import annotations

import asyncio
import json as _json
import uuid
from http.cookies import SimpleCookie
from urllib.parse import urlencode


class Response:
    def __init__(self, status: int, headers: list[tuple[bytes, bytes]], body: bytes):
        self.status_code = status
        self.raw_headers = headers
        self.headers = {k.decode().lower(): v.decode() for k, v in headers}
        self.content = body

    def json(self):
        return _json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class Client:
    def __init__(self, app, csrf: bool = True):
        self.app = app
        self.cookies: dict[str, str] = {}
        self.csrf = csrf

    def _run(self, method, path, body=b"", headers=None, query=None):
        headers = dict(headers or {})
        if self.csrf and method not in ("GET", "HEAD"):
            headers.setdefault("x-requested-with", "dip")
        if self.cookies:
            headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        headers.setdefault("content-length", str(len(body)))
        scope = {
            "type": "http", "http_version": "1.1", "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": urlencode(query or {}).encode(), "root_path": "",
            "headers": [(k.lower().encode(), str(v).encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        }
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        out = {"status": 500, "headers": [], "body": b""}

        async def receive():
            if messages:
                return messages.pop(0)
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"], out["headers"] = msg["status"], msg.get("headers", [])
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")

        asyncio.run(self.app(scope, receive, send))
        resp = Response(out["status"], out["headers"], out["body"])
        for k, v in out["headers"]:
            if k.lower() == b"set-cookie":
                c = SimpleCookie()
                c.load(v.decode())
                for name, morsel in c.items():
                    if morsel["max-age"] == "0" or morsel.value in ("", '""'):
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = morsel.value
        return resp

    def get(self, path, params=None, headers=None):
        return self._run("GET", path, headers=headers, query=params)

    def delete(self, path, headers=None):
        return self._run("DELETE", path, headers=headers)

    def post(self, path, json=None, files=None, data=None, headers=None):
        return self._send("POST", path, json, files, data, headers)

    def patch(self, path, json=None, headers=None):
        return self._send("PATCH", path, json, None, None, headers)

    def _send(self, method, path, json, files, data, headers):
        headers = dict(headers or {})
        if files is not None:
            boundary = uuid.uuid4().hex
            parts = []
            for field, value in (data or {}).items():
                for v in value if isinstance(value, list) else [value]:
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{v}\r\n'.encode())
            for field, (filename, content, ctype) in files:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                             f'Content-Type: {ctype}\r\n\r\n'.encode() + content + b"\r\n")
            body = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["content-type"] = f"multipart/form-data; boundary={boundary}"
        else:
            body = _json.dumps(json or {}).encode()
            headers["content-type"] = "application/json"
        return self._run(method, path, body, headers)
