"""Fail-closed public HTTPS fetch orchestration, with explicit I/O dependencies.

There is deliberately no default resolver or transport and no legacy fallback.
The trusted resolver/transport must implement the deadline and pinning contract
below; this policy layer cannot interrupt a blocking or malicious dependency.
Importing this module does not resolve hosts, open sockets or read user state.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
import os
import re
import time
from typing import Callable, Protocol, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit


_MESSAGES = {
    "UNSAFE_URL": "URL 不符合公开 HTTPS 读取策略",
    "UNSAFE_ADDRESS": "目标地址不是公网地址，已拦截",
    "DNS_FAILED": "无法安全解析目标地址",
    "DEADLINE_EXCEEDED": "公开读取超过总时间预算",
    "TRANSPORT_FAILED": "公开读取传输失败",
    "INVALID_RESPONSE": "上游响应不符合安全读取契约",
    "RESPONSE_TOO_LARGE": "上游正文超过读取预算",
    "REDIRECT_LIMIT": "重定向循环或次数超过预算",
    "UPSTREAM_HTTP_ERROR": "上游未返回成功正文",
    "UNSUPPORTED_CONTENT": "上游内容类型或编码不受支持",
}
FAKE_IP_MESSAGE = (
    "目标域名解析到 198.18.0.0/15，常见于代理软件的 fake-ip 模式；"
    "确认是代理造成的，可以设置 RESEARCH_MCP_ALLOW_FAKE_IP=1"
)
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/html"})
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_REQUEST_HEADERS = (
    ("User-Agent", "research-public-fetch/1"),
    ("Accept", "text/plain, text/markdown, text/html"),
    ("Accept-Encoding", "identity"),
    ("Connection", "close"),
)


class SafeFetchError(Exception):
    """Only fixed categories can leave this boundary; never raw URL/IP/errors."""

    def __init__(self, code: str, *, fake_ip: bool = False) -> None:
        self.code = code if code in _MESSAGES else "TRANSPORT_FAILED"
        message = FAKE_IP_MESSAGE if self.code == "UNSAFE_ADDRESS" and fake_ip is True else _MESSAGES[self.code]
        super().__init__(message)


@dataclass(frozen=True)
class FetchLimits:
    max_bytes: int = 262_144
    total_seconds: float = 10.0
    max_redirects: int = 3

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1_048_576:
            raise ValueError("max_bytes must be between 1 and 1048576")
        if (type(self.total_seconds) not in (int, float) or not math.isfinite(self.total_seconds)
                or not 0 < self.total_seconds <= 30):
            raise ValueError("total_seconds must be positive and at most 30")
        if type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 5:
            raise ValueError("max_redirects must be between 0 and 5")


class Deadline:
    """One monotonic deadline shared by DNS, connect/TLS, headers and every read."""

    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._last = self._now()
        self.expires_at = self._last + seconds

    def _now(self) -> float:
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise SafeFetchError("DEADLINE_EXCEEDED")
        return now

    def remaining(self) -> float:
        now = self._now()
        if now < self._last or now >= self.expires_at:
            raise SafeFetchError("DEADLINE_EXCEEDED")
        self._last = now
        return self.expires_at - now


@dataclass(frozen=True)
class PinnedRequest:
    url: str
    hostname: str
    address: str
    tls_server_name: str
    host_header: str
    port: int = 443
    headers: tuple[tuple[str, str], ...] = _REQUEST_HEADERS


@dataclass(frozen=True)
class FetchDocument:
    url: str
    media_type: str
    body: bytes


class Resolver(Protocol):
    def __call__(self, hostname: str, *, deadline: Deadline) -> Sequence[str]:
        """Return ALL A/AAAA answers, with bounded DNS I/O; no hostname fallback."""


class Response(Protocol):
    status: int
    headers: Sequence[tuple[str, str]]
    peer_address: str
    tls_verified_hostname: str

    def read(self, size: int, *, deadline: Deadline) -> bytes:
        """Return <=size bytes, b'' at EOF; bound every I/O by remaining().

        HTTP chunk framing is decoded, Content-Encoding is NOT decompressed.
        The header parser must also enforce a hard time/size bound before open
        returns. Never hide auto-followed redirects or retry another address.
        """

    def close(self) -> None:
        """Release this response without draining the body or doing network I/O."""


class Transport(Protocol):
    def __call__(self, request: PinnedRequest, *, deadline: Deadline) -> Response:
        """GET the pinned IP directly, without a second DNS lookup.

        Verify TLS certificates/SNI against hostname; set Host to hostname.
        Use ONLY request.headers; no proxy environment, netrc, cookies, auth,
        user certs, retries, redirects or connection reuse across fetch calls.
        Return the actual socket peer and successfully verified TLS hostname.
        Connect/TLS/header reads must honor the same absolute deadline. This
        contract is a trusted-code boundary, not a sandbox for arbitrary code.
        """


def _public_ip(value: object) -> str:
    if not isinstance(value, str) or "%" in value:
        raise SafeFetchError("UNSAFE_ADDRESS")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise SafeFetchError("UNSAFE_ADDRESS") from None
    # One explicit exception only; all callers (including redirect/peer checks)
    # share this policy. Never relax validation for any other private range.
    if address.version == 4 and address in _FAKE_IP_NETWORK:
        if os.environ.get("RESEARCH_MCP_ALLOW_FAKE_IP") == "1":
            return str(address)
        raise SafeFetchError("UNSAFE_ADDRESS", fake_ip=True)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_loopback or address.is_link_local or address.is_unspecified):
        raise SafeFetchError("UNSAFE_ADDRESS")
    # Conservative explicit ranges avoid Python-version differences and
    # translation/tunnel routes which can embed non-public IPv4 destinations.
    if address.version == 4:
        if any(address in ipaddress.ip_network(net) for net in ("192.0.0.0/24", "192.88.99.0/24")):
            raise SafeFetchError("UNSAFE_ADDRESS")
    elif (address not in ipaddress.ip_network("2000::/3")
          or address in ipaddress.ip_network("2001::/23")
          or address in ipaddress.ip_network("2001:db8::/32")
          or address in ipaddress.ip_network("2002::/16")
          or address in ipaddress.ip_network("3fff::/20")
          or address.ipv4_mapped is not None):
        raise SafeFetchError("UNSAFE_ADDRESS")
    return str(address)


def _url(value: object) -> tuple[str, str, str | None]:
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value):
        raise SafeFetchError("UNSAFE_URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or "%" in parsed.netloc or parsed.netloc.endswith(":")):
            raise ValueError
    except ValueError:
        raise SafeFetchError("UNSAFE_URL") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Require an unambiguous fully qualified ASCII DNS name. In particular,
        # shorthand/decimal/hex/octal IPv4 and single-label local names fail.
        if (len(host) > 253 or "." not in host or host.rsplit(".", 1)[1].isdigit()
                or any(not _LABEL.fullmatch(label) for label in host.split("."))
                or any(host == suffix or host.endswith("." + suffix)
                       for suffix in ("localhost", "local", "internal", "lan", "home.arpa", "onion"))):
            raise SafeFetchError("UNSAFE_URL")
        literal = None
    else:
        literal = _public_ip(host)
        host = literal
    authority = f"[{host}]" if ":" in host else host
    canonical = urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))
    return canonical, host, literal


def _headers(response: Response) -> dict[str, str]:
    pairs = response.headers
    if not isinstance(pairs, (list, tuple)) or len(pairs) > 64:
        raise SafeFetchError("INVALID_RESPONSE")
    headers: dict[str, str] = {}
    size = 0
    for pair in pairs:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(not isinstance(value, str) for value in pair)):
            raise SafeFetchError("INVALID_RESPONSE")
        name, value = pair
        size += len(name) + len(value)
        if (size > 8192 or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or not value.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise SafeFetchError("INVALID_RESPONSE")
        key = name.lower()
        if key == "location" and value != value.strip():
            raise SafeFetchError("UNSAFE_URL")
        if key in headers and key in {"location", "content-length", "content-type", "content-encoding", "transfer-encoding"}:
            raise SafeFetchError("INVALID_RESPONSE")
        headers[key] = value.strip()
    if "transfer-encoding" in headers and (
        headers["transfer-encoding"].lower() != "chunked" or "content-length" in headers
    ):
        raise SafeFetchError("INVALID_RESPONSE")
    return headers


class SafeFetcher:
    """No ambient identity or persistent cache; all dependencies are explicit."""

    def __init__(self, *, resolver: Resolver, transport: Transport,
                 limits: FetchLimits = FetchLimits(), clock: Callable[[], float] = time.monotonic) -> None:
        if not callable(resolver) or not callable(transport) or not callable(clock):
            raise TypeError("explicit resolver, transport and clock are required")
        if not isinstance(limits, FetchLimits):
            raise TypeError("FetchLimits is required")
        self._resolver, self._transport, self._limits, self._clock = resolver, transport, limits, clock

    def fetch(self, url: object) -> FetchDocument:
        try:
            return self._fetch(url)
        except SafeFetchError:
            raise
        except (TimeoutError,):
            raise SafeFetchError("DEADLINE_EXCEEDED") from None
        except Exception:
            raise SafeFetchError("TRANSPORT_FAILED") from None

    def _fetch(self, url: object) -> FetchDocument:
        deadline = Deadline(self._limits.total_seconds, self._clock)
        seen: set[str] = set()
        for hop in range(self._limits.max_redirects + 1):
            canonical, host, literal = _url(url)
            deadline.remaining()
            if canonical in seen:
                raise SafeFetchError("REDIRECT_LIMIT")
            seen.add(canonical)
            if literal is None:
                try:
                    answers = self._resolver(host, deadline=deadline)
                except TimeoutError:
                    raise SafeFetchError("DEADLINE_EXCEEDED") from None
                except SafeFetchError:
                    raise
                except Exception:
                    raise SafeFetchError("DNS_FAILED") from None
                deadline.remaining()
                if not isinstance(answers, (list, tuple)) or not 1 <= len(answers) <= 16:
                    raise SafeFetchError("DNS_FAILED")
                addresses = [_public_ip(answer) for answer in answers]
                address = addresses[0]
            else:
                address = literal
            authority = f"[{host}]" if ":" in host else host
            request = PinnedRequest(canonical, host, address, host, authority)
            response = self._transport(request, deadline=deadline)
            try:
                deadline.remaining()
                if (_public_ip(response.peer_address) != address
                        or response.tls_verified_hostname != host):
                    raise SafeFetchError("UNSAFE_ADDRESS")
                if type(response.status) is not int:
                    raise SafeFetchError("INVALID_RESPONSE")
                headers = _headers(response)
                if response.status in _REDIRECTS:
                    location = headers.get("location")
                    if not location:
                        raise SafeFetchError("INVALID_RESPONSE")
                    if hop == self._limits.max_redirects:
                        raise SafeFetchError("REDIRECT_LIMIT")
                    # Validate before urljoin can silently discard leading whitespace.
                    if (len(location) > 4096 or "\\" in location
                            or any(ord(char) <= 32 or ord(char) == 127 for char in location)):
                        raise SafeFetchError("UNSAFE_URL")
                    url = urljoin(canonical, location)
                    continue
                if response.status != 200:
                    raise SafeFetchError("UPSTREAM_HTTP_ERROR")
                media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if (media_type not in _TEXT_TYPES
                        or headers.get("content-encoding", "identity").lower() != "identity"):
                    raise SafeFetchError("UNSUPPORTED_CONTENT")
                expected = headers.get("content-length")
                if expected is not None:
                    if not re.fullmatch(r"[0-9]{1,10}", expected):
                        raise SafeFetchError("INVALID_RESPONSE")
                    expected = int(expected)
                    if expected > self._limits.max_bytes:
                        raise SafeFetchError("RESPONSE_TOO_LARGE")
                body = bytearray()
                while True:
                    deadline.remaining()
                    size = min(8192, self._limits.max_bytes - len(body) + 1)
                    chunk = response.read(size, deadline=deadline)
                    deadline.remaining()
                    if not isinstance(chunk, bytes) or len(chunk) > size:
                        raise SafeFetchError("INVALID_RESPONSE")
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > self._limits.max_bytes:
                        raise SafeFetchError("RESPONSE_TOO_LARGE")
                    if expected is not None and len(body) > expected:
                        raise SafeFetchError("INVALID_RESPONSE")
                if expected is not None and len(body) != expected:
                    raise SafeFetchError("INVALID_RESPONSE")
                return FetchDocument(canonical, media_type, bytes(body))
            finally:
                # close must be local and nonblocking; never drain an unsafe body.
                response.close()
        raise SafeFetchError("REDIRECT_LIMIT")
