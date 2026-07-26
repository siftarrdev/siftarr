"""Same-origin reverse proxy for the qBittorrent Web UI.

The dashboard embeds the qBittorrent Web UI in an iframe. Pointing that iframe
straight at qBittorrent fails because it is a different origin: qBittorrent
sends ``X-Frame-Options``/``frame-ancestors``, rejects cross-origin API calls as
CSRF, and its ``SameSite`` session cookie is never sent back. Serving it under
``/qbit`` on Siftarr's own origin removes all three problems.

No server-side qBittorrent credentials are used; the user's own browser login is
relayed through the proxy.
"""

import logging
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Request, Response

from app.siftarr.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qbit", tags=["qbit-proxy"])

PROXY_PREFIX = "/qbit"

#: Hop-by-hop headers must never be forwarded in either direction.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailers",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
    }
)

#: Response headers dropped so the ASGI server recomputes/decodes correctly.
DROPPED_RESPONSE_HEADERS = frozenset({"content-length", "content-encoding", "x-frame-options"})

#: Request headers we always regenerate rather than relay from the browser.
DROPPED_REQUEST_HEADERS = frozenset({"host", "origin", "referer", "content-length"})

_FRAME_ANCESTORS_RE = re.compile(r"\s*frame-ancestors[^;]*;?", re.IGNORECASE)
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_BASE_RE = re.compile(r"<base\b", re.IGNORECASE)

_client: httpx.AsyncClient | None = None


def get_proxy_client() -> httpx.AsyncClient:
    """Return the module-level proxy client, creating it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    return _client


async def close_proxy_client() -> None:
    """Close the proxy client on application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _strip_frame_ancestors(csp: str) -> str:
    """Remove only the ``frame-ancestors`` directive from a CSP header value."""
    cleaned = _FRAME_ANCESTORS_RE.sub("", csp).strip().strip(";").strip()
    return cleaned


def _inject_base_href(body: bytes) -> bytes:
    """Insert ``<base href="/qbit/">`` after ``<head>`` unless one already exists."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    if _BASE_RE.search(text):
        return body
    match = _HEAD_RE.search(text)
    if not match:
        return body
    injected = f'{text[: match.end()]}<base href="{PROXY_PREFIX}/">{text[match.end() :]}'
    return injected.encode("utf-8")


def _rewrite_set_cookie(value: str, *, secure_request: bool) -> str:
    """Re-scope an upstream cookie so the browser keeps it for ``/qbit``."""
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return value
    rewritten = [parts[0]]
    for attribute in parts[1:]:
        name = attribute.split("=", 1)[0].strip().lower()
        if name in {"path", "samesite", "domain"}:
            continue
        if name == "secure" and not secure_request:
            continue
        rewritten.append(attribute)
    rewritten.append(f"Path={PROXY_PREFIX}")
    rewritten.append("SameSite=Lax")
    return "; ".join(rewritten)


def _build_upstream_url(base_url: str, path: str, query: str) -> str:
    """Join the configured qBittorrent base URL with the proxied path and query."""
    split = urlsplit(base_url)
    base_path = split.path.rstrip("/")
    full_path = f"{base_path}/{path.lstrip('/')}" if path else f"{base_path}/"
    return urlunsplit((split.scheme, split.netloc, full_path, query, ""))


def _upstream_request_headers(request: Request, base_url: str) -> dict[str, str]:
    """Build outgoing headers, forcing qBittorrent's own origin for CSRF checks."""
    split = urlsplit(base_url)
    origin = f"{split.scheme}://{split.netloc}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in DROPPED_REQUEST_HEADERS and key.lower() not in HOP_BY_HOP_HEADERS
    }
    headers["host"] = split.netloc
    headers["origin"] = origin
    headers["referer"] = f"{origin}/"
    return headers


def _response_headers(upstream: httpx.Response, *, secure_request: bool) -> list[tuple[str, str]]:
    """Filter upstream headers and re-scope cookies/framing controls."""
    headers: list[tuple[str, str]] = []
    for key, value in upstream.headers.multi_items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS or lowered in DROPPED_RESPONSE_HEADERS:
            continue
        if lowered == "set-cookie":
            headers.append((key, _rewrite_set_cookie(value, secure_request=secure_request)))
            continue
        if lowered == "content-security-policy":
            cleaned = _strip_frame_ancestors(value)
            if cleaned:
                headers.append((key, cleaned))
            continue
        headers.append((key, value))
    return headers


@router.api_route("", methods=["GET", "POST", "HEAD"])
@router.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def proxy_qbittorrent(request: Request, path: str = "") -> Response:
    """Proxy a request to the configured qBittorrent Web UI on Siftarr's origin."""
    base_url = get_settings().qbittorrent_url
    if not base_url:
        return Response(
            content="qBittorrent URL is not configured.",
            status_code=503,
            media_type="text/plain",
        )

    url = _build_upstream_url(base_url, path, request.url.query)
    body = await request.body()

    try:
        upstream = await get_proxy_client().request(
            request.method,
            url,
            headers=_upstream_request_headers(request, base_url),
            content=body,
        )
    except httpx.RequestError as exc:
        logger.warning("qBittorrent proxy request to %s failed: %s", url, exc)
        return Response(
            content="Unable to reach qBittorrent.",
            status_code=502,
            media_type="text/plain",
        )

    content = upstream.content
    if "text/html" in upstream.headers.get("content-type", "").lower():
        content = _inject_base_href(content)

    response = Response(content=content, status_code=upstream.status_code)
    headers = _response_headers(upstream, secure_request=request.url.scheme == "https")
    response.raw_headers = [(b"content-length", str(len(content)).encode())] + [
        (key.encode("latin-1"), value.encode("latin-1")) for key, value in headers
    ]
    return response
