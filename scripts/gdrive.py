#!/usr/bin/env python3
"""Copy episode files into David's Google Drive under a folder path such as "Ask Eve/2026-10-23_walk".

Stdlib only (runs in ingest.yml before and after the heavy pipeline). Credentials come from env:
GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN: an OAuth refresh token for the scope
https://www.googleapis.com/auth/drive.file, so it can only see and change files this app created
(the "Ask Eve" folder tree), never the rest of the Drive. If any of the three is missing it prints
{"skipped": ...} and exits 0, so a missing secret never blocks an ingest.

  python scripts/gdrive.py upload --folder "Ask Eve/<stem>" --out drive.json FILE [FILE ...]

Re-running is idempotent: an existing file with the same name in the folder is updated in place.
Prints (and writes to --out) {"folder": {"id", "url"}, "files": [{"name", "id", "url", "bytes"}]}.
Endpoints are overridable for tests: GDRIVE_TOKEN_URL, GDRIVE_API, GDRIVE_UPLOAD_API.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024          # resumable chunk size (must be a multiple of 256 KiB)
SIMPLE_MAX = 5 * 1024 * 1024     # files up to this size go in one multipart request
TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".mov": "video/quicktime",
         ".wav": "audio/wav", ".md": "text/markdown", ".json": "application/json"}


class DriveError(Exception):
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def token_url() -> str: return _env("GDRIVE_TOKEN_URL", "https://oauth2.googleapis.com/token")
def api() -> str: return _env("GDRIVE_API", "https://www.googleapis.com/drive/v3")
def upload_api() -> str: return _env("GDRIVE_UPLOAD_API", "https://www.googleapis.com/upload/drive/v3")


def _req(method: str, url: str, *, token: str | None = None, data: bytes | None = None,
         headers: dict | None = None, ok=(200, 201)) -> tuple[int, dict, bytes]:
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (https Google endpoints or test stub)
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, dict(e.headers), e.read()
        raise DriveError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:300]!r}") from None


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret,
                                   "refresh_token": refresh_token, "grant_type": "refresh_token"}).encode()
    _, _, raw = _req("POST", token_url(), data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    tok = json.loads(raw).get("access_token")
    if not tok:
        raise DriveError("token endpoint returned no access_token")
    return tok


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def find(token: str, name: str, parent: str, folder: bool) -> str | None:
    q = f"name = '{_q(name)}' and '{_q(parent)}' in parents and trashed = false"
    q += f" and mimeType {'=' if folder else '!='} '{FOLDER_MIME}'"
    url = f"{api()}/files?" + urllib.parse.urlencode({"q": q, "fields": "files(id,name)", "spaces": "drive", "pageSize": "10"})
    _, _, raw = _req("GET", url, token=token)
    files = json.loads(raw).get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(token: str, path: str) -> str:
    parent = "root"
    for part in [p for p in path.split("/") if p.strip()]:
        fid = find(token, part, parent, folder=True)
        if not fid:
            meta = json.dumps({"name": part, "mimeType": FOLDER_MIME, "parents": [parent]}).encode()
            _, _, raw = _req("POST", f"{api()}/files?fields=id", token=token, data=meta,
                             headers={"Content-Type": "application/json; charset=UTF-8"})
            fid = json.loads(raw)["id"]
        parent = fid
    return parent


def upload(token: str, path: Path, folder_id: str) -> dict:
    name = path.name
    mime = TYPES.get(path.suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    size = path.stat().st_size
    existing = find(token, name, folder_id, folder=False)
    meta = {"name": name} if existing else {"name": name, "parents": [folder_id]}
    method = "PATCH" if existing else "POST"
    target = f"{upload_api()}/files" + (f"/{existing}" if existing else "")
    if size <= SIMPLE_MAX:
        boundary = "askeve-boundary-7f3a"
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        _, _, raw = _req(method, f"{target}?uploadType=multipart&fields=id,webViewLink,size", token=token, data=body,
                         headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        res = json.loads(raw)
    else:
        _, hdrs, _ = _req(method, f"{target}?uploadType=resumable&fields=id,webViewLink,size", token=token,
                          data=json.dumps(meta).encode(),
                          headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime,
                                   "X-Upload-Content-Length": str(size)})
        session = {k.lower(): v for k, v in hdrs.items()}.get("location")
        if not session:
            raise DriveError("resumable upload: no session Location header")
        res = None
        with path.open("rb") as f:
            off = 0
            while off < size:
                chunk = f.read(CHUNK)
                end = off + len(chunk) - 1
                status, _, raw = _req("PUT", session, data=chunk, ok=(200, 201, 308),
                                      headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {off}-{end}/{size}"})
                off = end + 1
                if status in (200, 201):
                    res = json.loads(raw)
        if res is None:
            raise DriveError("resumable upload finished without a file resource")
    fid = res["id"]
    return {"name": name, "id": fid, "bytes": size,
            "url": res.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("--folder", required=True, help='folder path under My Drive, e.g. "Ask Eve/<stem>"')
    up.add_argument("--out", help="also write the JSON result here")
    up.add_argument("files", nargs="+")
    a = ap.parse_args(argv)

    creds = [os.environ.get(k, "") for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN")]
    if not all(creds):
        result = {"skipped": "GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN not set"}
    else:
        files = [Path(f) for f in a.files]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            print(f"error: not a file: {', '.join(missing)}", file=sys.stderr)
            return 2
        tok = access_token(*creds)
        folder = ensure_folder(tok, a.folder)
        result = {"folder": {"id": folder, "path": a.folder, "url": f"https://drive.google.com/drive/folders/{folder}"},
                  "files": [upload(tok, f, folder) for f in files]}
    text = json.dumps(result, indent=2)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DriveError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
