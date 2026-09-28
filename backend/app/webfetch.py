"""Outbound HTTP for web_fetch / web_search / install_font.

* Only http(s); hosts that resolve to private, loopback or link-local addresses are refused
  (the backend must not become a proxy into the container or the user's network) unless
  ``LUMA_ALLOW_PRIVATE_FETCH=1`` (tests).
* Size and time limited; redirects are re-checked.
* Fetched text is DATA: callers label it as untrusted and never execute it as instructions.
"""
from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

MAX_BYTES = 3_000_000
UA = "LumaStudio/1.0 (+self-hosted motion studio; web_fetch)"


class FetchError(Exception):
    pass


def _check_host(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("only http(s) URLs are allowed")
    if os.environ.get("LUMA_ALLOW_PRIVATE_FETCH") == "1":
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
            return  # resolution happens at the proxy
        raise FetchError(f"cannot resolve {u.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FetchError(f"{u.hostname} resolves to a private address; refused")


def get(url: str, max_bytes: int = MAX_BYTES, timeout: float = 20.0, accept: str = "*/*") -> tuple[bytes, str, str]:
    """GET with manual redirect handling (each hop re-checked). Returns (body, content_type, final_url)."""
    cur = url
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": UA, "Accept": accept}) as c:
        for _ in range(6):
            _check_host(cur)
            with c.stream("GET", cur) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"HTTP {r.status_code} for {cur}")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise FetchError(f"response larger than {max_bytes // 1_000_000} MB")
                return bytes(buf), r.headers.get("content-type", ""), str(r.url)
    raise FetchError("too many redirects")


def html_to_text(doc: str) -> tuple[str, str]:
    title = re.search(r"<title[^>]*>(.*?)</title>", doc, re.S | re.I)
    body = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", doc)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return html.unescape(title.group(1).strip()) if title else "", body


def fetch_text(url: str, max_chars: int = 20000) -> dict:
    data, ctype, final = get(url)
    if "html" in ctype or data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        title, text = html_to_text(data.decode("utf-8", "replace"))
    elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
        title, text = "", data.decode("utf-8", "replace")
    else:
        raise FetchError(f"{ctype or 'binary'} content is not text")
    return {"url": final, "title": title or final, "text": text[:max_chars], "truncated": len(text) > max_chars, "content_type": ctype}


def search(query: str, max_results: int = 8) -> list[dict]:
    """DuckDuckGo's HTML endpoint (no key). ``LUMA_SEARCH_URL`` overrides the endpoint (tests/self-hosted SearXNG-like pages)."""
    base = os.environ.get("LUMA_SEARCH_URL", "https://html.duckduckgo.com/html/?q=")
    data, _, _ = get(base + quote_plus(query), max_bytes=1_500_000, accept="text/html")
    doc = data.decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="[^"]*result__a|$)', doc, re.S):
        href, title_html, rest = m.group(1), m.group(2), m.group(3)
        if "uddg=" in href:  # DDG redirect links
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        sn = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</', rest, re.S)
        out.append({"title": html_to_text(title_html)[1][:200], "url": html.unescape(href),
                    "snippet": html_to_text(sn.group(1))[1][:400] if sn else ""})
        if len(out) >= max_results:
            break
    return out
