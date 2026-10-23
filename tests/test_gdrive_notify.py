"""scripts/gdrive.py and scripts/notify.py against a local fake Google Drive / notify endpoint (no network)."""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gdrive  # noqa: E402
import notify  # noqa: E402


class FakeDrive:
    def __init__(self):
        self.files: dict[str, dict] = {}   # id -> {name, parents, mimeType, data}
        self.sessions: dict[str, dict] = {}
        self.log: list[tuple[str, str]] = []
        self.n = 0

    def new_id(self):
        self.n += 1
        return f"id{self.n}"


def make_server(fake: FakeDrive):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj=None, headers=None):
            body = json.dumps(obj).encode() if obj is not None else b""
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def do_POST(self):
            self._route("POST")

        def do_GET(self):
            self._route("GET")

        def do_PATCH(self):
            self._route("PATCH")

        def do_PUT(self):
            self._route("PUT")

        def _route(self, method):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            fake.log.append((method, u.path))
            body = self._body()
            if u.path == "/token":
                form = urllib.parse.parse_qs(body.decode())
                assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt"]
                return self._send(200, {"access_token": "at", "expires_in": 3600})
            if self.headers.get("Authorization") not in ("Bearer at", None) or (u.path != "/token" and not u.path.startswith("/sess")
                                                                                and self.headers.get("Authorization") != "Bearer at"):
                return self._send(401, {"error": "auth"})
            if u.path == "/drive/files" and method == "GET":
                q = qs["q"][0]
                name = q.split("name = '")[1].split("' and")[0].replace("\\'", "'")
                parent = q.split("and '")[1].split("' in parents")[0]
                want_folder = "mimeType = '" in q
                hits = [{"id": i, "name": f["name"]} for i, f in fake.files.items()
                        if f["name"] == name and parent in f["parents"] and (f["mimeType"] == gdrive.FOLDER_MIME) == want_folder]
                return self._send(200, {"files": hits})
            if u.path == "/drive/files" and method == "POST":
                meta = json.loads(body)
                i = fake.new_id()
                fake.files[i] = {**meta, "data": b""}
                return self._send(200, {"id": i})
            if u.path.startswith("/upload/files"):
                fid = u.path.split("/upload/files")[1].strip("/") or None
                if qs["uploadType"] == ["multipart"]:
                    boundary = self.headers["Content-Type"].split("boundary=")[1]
                    parts = body.split(f"--{boundary}".encode())
                    meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1])
                    data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
                    return self._send(200, self._store(fid, meta, data))
                sid = f"/sess{len(fake.sessions)}"
                fake.sessions[sid] = {"fid": fid, "meta": json.loads(body), "data": b"",
                                      "size": int(self.headers["X-Upload-Content-Length"])}
                return self._send(200, {}, {"Location": f"http://127.0.0.1:{self.server.server_address[1]}{sid}"})
            if u.path in fake.sessions and method == "PUT":
                s = fake.sessions[u.path]
                s["data"] += body
                if len(s["data"]) < s["size"]:
                    return self._send(308, None, {"Range": f"bytes=0-{len(s['data']) - 1}"})
                return self._send(200, self._store(s["fid"], s["meta"], s["data"]))
            return self._send(404, {"error": self.path})

        def _store(self, fid, meta, data):
            if fid:
                fake.files[fid]["data"] = data
            else:
                fid = fake.new_id()
                fake.files[fid] = {"name": meta["name"], "parents": meta["parents"], "mimeType": "x", "data": data}
            return {"id": fid, "webViewLink": f"https://drive.example/{fid}", "size": str(len(data))}

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    srv = make_server(fake)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("GDRIVE_TOKEN_URL", f"{base}/token")
    monkeypatch.setenv("GDRIVE_API", f"{base}/drive")
    monkeypatch.setenv("GDRIVE_UPLOAD_API", f"{base}/upload")
    for k, v in (("GDRIVE_CLIENT_ID", "cid"), ("GDRIVE_CLIENT_SECRET", "cs"), ("GDRIVE_REFRESH_TOKEN", "rt")):
        monkeypatch.setenv(k, v)
    yield fake
    srv.shutdown()


def test_upload_creates_folder_tree_and_is_idempotent(drive, tmp_path, monkeypatch, capsys):
    small = tmp_path / "phone-01.m4a"
    small.write_bytes(b"\x00small" * 100)
    big = tmp_path / "ep.mp3"
    big.write_bytes(bytes(range(256)) * (1024 * 23))   # ~5.9 MB -> resumable path, 1 chunk
    monkeypatch.setattr(gdrive, "CHUNK", 2 * 1024 * 1024)   # force several chunks
    out = tmp_path / "d.json"
    assert gdrive.main(["upload", "--folder", "Ask Eve/2026-10-23_walk", "--out", str(out), str(small), str(big)]) == 0
    res = json.loads(out.read_text())
    folders = {i: f for i, f in drive.files.items() if f["mimeType"] == gdrive.FOLDER_MIME}
    assert sorted(f["name"] for f in folders.values()) == ["2026-10-23_walk", "Ask Eve"]
    by_name = {f["name"]: f for f in drive.files.values()}
    assert by_name["phone-01.m4a"]["data"] == small.read_bytes()
    assert by_name["ep.mp3"]["data"] == big.read_bytes()
    assert by_name["ep.mp3"]["parents"] == [res["folder"]["id"]]
    assert [f["name"] for f in res["files"]] == ["phone-01.m4a", "ep.mp3"]
    assert res["folder"]["url"].endswith(res["folder"]["id"])
    n = len(drive.files)
    small.write_bytes(b"changed")
    assert gdrive.main(["upload", "--folder", "Ask Eve/2026-10-23_walk", str(small)]) == 0
    assert len(drive.files) == n                       # no duplicate folder or file
    assert by_name["phone-01.m4a"]["data"] == b"changed"


def test_upload_skips_without_credentials(monkeypatch, tmp_path, capsys):
    for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x")
    assert gdrive.main(["upload", "--folder", "Ask Eve/x", str(f)]) == 0
    assert "skipped" in json.loads(capsys.readouterr().out)


def test_upload_quotes_names(drive, tmp_path):
    f = tmp_path / "it's.md"
    f.write_text("x")
    assert gdrive.main(["upload", "--folder", "Ask Eve/o'brien", str(f)]) == 0
    assert gdrive.main(["upload", "--folder", "Ask Eve/o'brien", str(f)]) == 0
    assert sum(1 for x in drive.files.values() if x["name"] == "o'brien") == 1


def _artifacts(root: Path, stem: str, placeholder=False):
    a = root / "artifacts" / stem
    a.mkdir(parents=True)
    (a / "manifest.json").write_text(json.dumps({"duration_seconds": 1830, "loudness_lufs": -16.0,
                                                 "recording_method": "ios-screen-record", "placeholder_audio": placeholder}))
    (a / "alignment.json").write_text(json.dumps({"david_matched": 40, "david_turns": 44, "alignment_coverage": 0.909}))
    (root / "drive-out.json").write_text(json.dumps({"folder": {"url": "https://drive/f"},
                                                     "files": [{"name": f"{stem}.mp3", "url": "https://drive/mp3"}]}))


def test_notify_success_message(tmp_path):
    _artifacts(tmp_path, "2026-10-23_walk")
    m = notify.build({"STEM": "2026-10-23_walk", "JOB_STATUS": "success", "PR_URL": "https://gh/pr/7",
                      "RUN_URL": "https://gh/run/1", "DRIVE_JSON": "drive-out.json"}, tmp_path)
    assert m["subject"] == "Ask Eve: 2026-10-23_walk is ready"
    t = m["text"]
    for s in ("Listen (Google Drive): https://drive/mp3", "All files (Google Drive): https://drive/f", "Review + merge: https://gh/pr/7",
              "30.5 min", "real xAI TTS", "40/44"):
        assert s in t, s


def test_notify_failure_and_placeholder(tmp_path):
    m = notify.build({"STEM": "2026-10-23_walk", "JOB_STATUS": "failure", "RUN_URL": "https://gh/run/1"}, tmp_path)
    assert m["subject"] == "Ask Eve: ingest failure for 2026-10-23_walk"
    assert "Run log: https://gh/run/1" in m["text"] and "safe in the private bucket." in m["text"]
    _artifacts(tmp_path, "2026-10-23_x", placeholder=True)
    assert "PLACEHOLDER" in notify.build({"STEM": "2026-10-23_x", "JOB_STATUS": "success"}, tmp_path)["text"]


def test_notify_send(monkeypatch, tmp_path):
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append((self.headers["Authorization"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"sent":true}')

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOTIFY_URL", f"http://127.0.0.1:{srv.server_address[1]}/notify")
    monkeypatch.setenv("NOTIFY_TOKEN", "tok")
    monkeypatch.setenv("STEM", "2026-10-23_walk")
    monkeypatch.setenv("JOB_STATUS", "success")
    assert notify.main([]) == 0
    srv.shutdown()
    assert got[0][0] == "Bearer tok" and got[0][1]["subject"].endswith("is ready")
    monkeypatch.delenv("NOTIFY_TOKEN")
    assert notify.main([]) == 0   # no token: skip, never fail the workflow
