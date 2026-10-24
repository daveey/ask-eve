#!/usr/bin/env python3
"""scripts/leak_check.py — Deterministic + optional LLM privacy & secret leak checker.

Inspects outbound draft replies (`to_addr`, `subject`, `body_text`) and public
`reading_list` entries BEFORE they can be marked `leak_check_passed=1`, promoted
to the public site, or sent via Cloudflare Email Routing (`SEND_EMAIL`).

Mirrors `workers/eve-inbox/leak_check.js` — including identical canonical
`leak_check_sha256` calculation (`SHA-256(JSON.stringify([to_addr, subject, body_text]))`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

PUBLIC_ALLOWED_EMAILS = frozenset(
    {
        "eve@askevepodcast.com",
        "reading@askevepodcast.com",
        "podcast@askevepodcast.com",
    }
)

FORBIDDEN_EMAIL_DOMAINS_IN_TEXT = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "dbloom.in",
    }
)

FORBIDDEN_EMAIL_DOMAINS_ANYWHERE = frozenset(
    {
        "dbloom.in",
    }
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

RULE_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "api_key_or_token",
        "xAI API key (xai-...)",
        re.compile(r"\bxai-[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "api_key_or_token",
        "Secret key token (sk-...)",
        re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{12,}\b"),
    ),
    (
        "api_key_or_token",
        "GitHub token (ghp_/gho_/ghu_/ghs_/ghr_/github_pat_)",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b"),
    ),
    (
        "api_key_or_token",
        "Cloudflare or AWS token/key",
        re.compile(r"\b(?:cfut_[A-Za-z0-9_-]{12,}|v1\.[0-9a-fA-F]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b"),
    ),
    (
        "api_key_or_token",
        "Bearer authorization token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    ),
    (
        "api_key_or_token",
        "Zone/service credential identifier",
        re.compile(r"\bzone_fm_[A-Za-z0-9_-]+\b"),
    ),
    (
        "api_key_or_token",
        "32+ character hex secret or resource ID",
        re.compile(r"\b[0-9a-fA-F]{32,64}\b"),
    ),
    (
        "internal_id_or_private_url",
        "Internal UUID identifier",
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    ),
    (
        "internal_id_or_private_url",
        "Internal subagent identifier (sand-subagent-*)",
        re.compile(r"\bsand-subagent-[A-Za-z0-9_-]*\b", re.IGNORECASE),
    ),
    (
        "internal_id_or_private_url",
        "Internal agent store identifier (store-*)",
        re.compile(r"\bstore-[0-9a-fA-F]{4,}(?:-[0-9a-fA-F]{4,})+\b|\bstore-[0-9a-fA-F]{8,}\b", re.IGNORECASE),
    ),
    (
        "internal_id_or_private_url",
        "Private Cursor canvas link (cursor.com/canvas/*)",
        re.compile(r"cursor\.com/canvas(?:/[^\s\"'<>)]*)?", re.IGNORECASE),
    ),
    (
        "internal_id_or_private_url",
        "Private Grok share link (grok.com/share/*)",
        re.compile(r"grok\.com/share(?:/[^\s\"'<>)]*)?|\bc2hhcmQt[A-Za-z0-9_-]+", re.IGNORECASE),
    ),
    (
        "local_filesystem_path",
        "Local sandbox/box filesystem path",
        re.compile(r"/(?:home/box|workspace|cursor/stores|root/\.ssh|private/tmp)(?:/[^\s\"'<>)]*)?|\bfile:///\S+"),
    ),
    (
        "private_hostname_or_infra",
        "Private Tailscale tailnet hostname (*.ts.net)",
        re.compile(r"\b[A-Za-z0-9._-]+\.ts\.net\b", re.IGNORECASE),
    ),
    (
        "private_hostname_or_infra",
        "Private infrastructure or bucket identifier (ask-eve-masters / hermes-hub / R2 endpoint)",
        re.compile(
            r"\b(?:ask-eve-masters|hermes-hub|upload\.askevepodcast\.com)\b|[A-Za-z0-9._-]*\.r2\.cloudflarestorage\.com\b",
            re.IGNORECASE,
        ),
    ),
]


def extract_email_address(raw: str | None) -> str:
    if not raw:
        return ""
    m = EMAIL_RE.search(str(raw))
    return m.group(0).lower() if m else str(raw).strip().lower()


def compute_leak_check_sha256(to_addr: str | None, subject: str | None, body_text: str | None) -> str:
    """Canonical SHA-256 hex digest over `[to_addr, subject, body_text]`.

    Matches `computeLeakCheckSha256` in `workers/eve-inbox/leak_check.js`.
    """
    payload = json.dumps(
        [str(to_addr or ""), str(subject or ""), str(body_text or "")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_private_terms(private_terms: str | list[str] | None = None) -> list[str]:
    if private_terms is None:
        private_terms = os.environ.get("PRIVATE_LEAK_TERMS", "")
    if isinstance(private_terms, str):
        raw_parts = re.split(r"[,\n\r]+", private_terms)
    else:
        raw_parts = [str(x) for x in private_terms]
    out: list[str] = []
    for part in raw_parts:
        cleaned = part.strip()
        if cleaned and len(cleaned) >= 3:
            out.append(cleaned)
    return out


def _scan_text_for_rules(
    text: str,
    *,
    field_name: str,
    allowed_recipient_email: str = "",
    is_recipient_field: bool = False,
    private_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not text:
        return findings

    # 1. Email address rules
    for m in EMAIL_RE.finditer(text):
        full_email = m.group(0).lower()
        domain = m.group(1).lower()

        if domain in FORBIDDEN_EMAIL_DOMAINS_ANYWHERE or any(
            domain.endswith("." + d) for d in FORBIDDEN_EMAIL_DOMAINS_ANYWHERE
        ):
            findings.append(
                {
                    "rule": "unauthorized_email",
                    "field": field_name,
                    "detail": f"Forbidden private domain ({domain}) in {field_name}",
                    "match": m.group(0),
                }
            )
            continue

        if not is_recipient_field and (
            domain in FORBIDDEN_EMAIL_DOMAINS_IN_TEXT
            or any(domain.endswith("." + d) for d in FORBIDDEN_EMAIL_DOMAINS_IN_TEXT)
        ):
            findings.append(
                {
                    "rule": "unauthorized_email",
                    "field": field_name,
                    "detail": f"Personal/forbidden email domain ({domain}) in {field_name}",
                    "match": m.group(0),
                }
            )
            continue

        if full_email in PUBLIC_ALLOWED_EMAILS:
            continue

        if allowed_recipient_email and full_email == allowed_recipient_email:
            continue

        findings.append(
            {
                "rule": "unauthorized_email",
                "field": field_name,
                "detail": f"Email address in {field_name} is not an allowed public Ask Eve address or the recipient's address",
                "match": m.group(0),
            }
        )

    # 2. Regex rules (API keys, UUIDs, canvas/share links, local paths, private hostnames)
    for rule_id, description, pattern in RULE_PATTERNS:
        for m in pattern.finditer(text):
            findings.append(
                {
                    "rule": rule_id,
                    "field": field_name,
                    "detail": f"{description} in {field_name}",
                    "match": m.group(0),
                }
            )

    # 3. Configurable PRIVATE_LEAK_TERMS (from Cloudflare Worker secret / env)
    if private_terms:
        lower_text = text.lower()
        for term in private_terms:
            idx = lower_text.find(term.lower())
            if idx != -1:
                matched = text[idx : idx + len(term)]
                findings.append(
                    {
                        "rule": "private_leak_term",
                        "field": field_name,
                        "detail": f"Matched private blocklist term in {field_name}",
                        "match": matched,
                    }
                )

    return findings


def llm_leak_check(
    text_bundle: str,
    *,
    api_key: str,
    model: str = "grok-4.7",
    endpoint: str = "https://api.x.ai/v1/chat/completions",
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """Optional secondary LLM leak audit using grok-4.7."""
    if not api_key:
        return []
    prompt = (
        "You are a strict security & privacy leak auditor for the Ask Eve podcast. "
        "Inspect the following outbound text and check whether it leaks any private information: "
        "personal email addresses (other than public @askevepodcast.com or the intended recipient), "
        "street addresses, phone numbers, API keys, passwords, internal UUIDs, local filesystem paths, "
        "or private server/bucket names. "
        'Respond ONLY with JSON: {"safe": true, "findings": []} or '
        '{"safe": false, "findings": [{"rule": "llm_privacy_flag", "detail": "...", "match": "..."}]}.'
    )
    req_body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text_bundle},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not parsed.get("safe", True):
            out = []
            for f in parsed.get("findings") or []:
                out.append(
                    {
                        "rule": str(f.get("rule") or "llm_privacy_flag"),
                        "field": str(f.get("field") or "body_text"),
                        "detail": str(f.get("detail") or "Flagged by LLM privacy review"),
                        "match": str(f.get("match") or ""),
                    }
                )
            return out
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        pass
    return []


def leak_check_draft(
    to_addr: str,
    subject: str,
    body_text: str,
    *,
    private_terms: str | list[str] | None = None,
    use_llm: bool = False,
    xai_api_key: str | None = None,
) -> dict[str, Any]:
    """Run deterministic (+ optional LLM) leak checks on a draft reply.

    Returns:
        {
            "passed": bool,
            "leak_check_passed": 1 | 0,
            "findings": list[dict],
            "sha256": str,
        }
    """
    terms = parse_private_terms(private_terms)
    recipient_email = extract_email_address(to_addr)
    findings: list[dict[str, str]] = []

    if not recipient_email or "@" not in recipient_email:
        findings.append(
            {
                "rule": "invalid_recipient",
                "field": "to_addr",
                "detail": "Recipient to_addr must contain a valid email address",
                "match": str(to_addr or ""),
            }
        )

    findings.extend(
        _scan_text_for_rules(
            str(to_addr or ""),
            field_name="to_addr",
            allowed_recipient_email=recipient_email,
            is_recipient_field=True,
            private_terms=terms,
        )
    )
    findings.extend(
        _scan_text_for_rules(
            str(subject or ""),
            field_name="subject",
            allowed_recipient_email=recipient_email,
            is_recipient_field=False,
            private_terms=terms,
        )
    )
    findings.extend(
        _scan_text_for_rules(
            str(body_text or ""),
            field_name="body_text",
            allowed_recipient_email=recipient_email,
            is_recipient_field=False,
            private_terms=terms,
        )
    )

    if use_llm and not findings:
        key = xai_api_key or os.environ.get("XAI_API_KEY", "")
        if key:
            findings.extend(
                llm_leak_check(
                    f"To: {to_addr}\nSubject: {subject}\n\n{body_text}",
                    api_key=key,
                )
            )

    sha256 = compute_leak_check_sha256(to_addr, subject, body_text)
    passed = len(findings) == 0
    return {
        "passed": passed,
        "leak_check_passed": 1 if passed else 0,
        "findings": findings,
        "sha256": sha256,
        "leak_check_sha256": sha256,
    }


leak_check = leak_check_draft
compute_draft_sha256 = compute_leak_check_sha256


def leak_check_reading_item(
    item: dict[str, Any],
    *,
    private_terms: str | list[str] | None = None,
) -> dict[str, Any]:
    """Run leak checks on a public reading_list entry before promotion or site export."""
    terms = parse_private_terms(private_terms)
    findings: list[dict[str, str]] = []
    for field in ("title", "slug", "url", "author_or_source", "submitted_via", "eve_notes", "episode_slug"):
        val = str(item.get(field) or "")
        if not val:
            continue
        findings.extend(
            _scan_text_for_rules(
                val,
                field_name=field,
                allowed_recipient_email="",
                is_recipient_field=False,
                private_terms=terms,
            )
        )
    passed = len(findings) == 0
    return {
        "passed": passed,
        "leak_check_passed": 1 if passed else 0,
        "findings": findings,
    }




def scan_text(text: str, field_name: str = "text", private_terms: list[str] | None = None) -> list[dict[str, str]]:
    """Scan a public text string for any forbidden email, secret, UUID, private URL, path, or infra identifier."""
    return _scan_text_for_rules(
        text,
        field_name=field_name,
        allowed_recipient_email="",
        is_recipient_field=False,
        private_terms=private_terms,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Ask Eve privacy & secret leak check.")
    ap.add_argument("--to", default="listener@example.com", help="Recipient email address")
    ap.add_argument("--subject", default="", help="Draft reply subject")
    ap.add_argument("--body", default="", help="Draft reply body text (or - for stdin)")
    ap.add_argument("--llm", action="store_true", help="Also run optional Grok-4.7 LLM double-check")
    ap.add_argument("--check-repo", action="store_true", help="Scan public index/*.json and site output for leaks")
    args = ap.parse_args(argv)

    if args.check_repo:
        from pathlib import Path as _P
        root = _P(__file__).resolve().parent.parent
        findings = []
        for jf in sorted((root / "index").glob("*.json")):
            if jf.name in ("eve-avatars.json", "clips.json"):
                continue
            raw = jf.read_text(encoding="utf-8")
            # Ignore 40-char git commit sha1s or 64-char sha256s in changelog/splits/sources
            cleaned = re.sub(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b", "<HASH>", raw)
            for f in scan_text(cleaned, field_name=f"index/{jf.name}"):
                findings.append(f)
        dist = root / "site/dist"
        if dist.exists():
            for sub in ("transcripts", "changelog", "growth"):
                for hf in sorted((dist / sub).rglob("*.html")):
                    raw = hf.read_text(encoding="utf-8")
                    cleaned = re.sub(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b", "<HASH>", raw)
                    for f in scan_text(cleaned, field_name=str(hf.relative_to(root))):
                        findings.append(f)
        res = {"passed": len(findings) == 0, "findings": findings}
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["passed"] else 1

    body = sys.stdin.read() if args.body == "-" else args.body
    res = leak_check_draft(args.to, args.subject, body, use_llm=args.llm)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
