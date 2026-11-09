"""Pinned-IP HTTPS transport for the explicit SafeFetcher contract."""
from __future__ import annotations

import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

from research_mcp.safe_fetch import (
    Deadline,
    PinnedRequest,
    SafeFetchError,
    _REQUEST_HEADERS,
    _public_ip,
    _url,
)


_MAX_HEADER_BYTES = 8192
_MAX_HEADER_LINE = _MAX_HEADER_BYTES
_MAX_HEADERS = 64
_TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_STATUS = re.compile(rb"HTTP/1\.[01] ([0-9]{3})(?: [\x20-\x7e]*)?\r\n\Z")


def _invalid() -> SafeFetchError:
    return SafeFetchError("INVALID_RESPONSE")


class _Reader:
    def __init__(self, sock: socket.socket, deadline: Deadline) -> None:
        self.sock, self.deadline = sock, deadline
        self.buffer = bytearray()

    def _fill(self, limit: int) -> bool:
        self.sock.settimeout(self.deadline.remaining())
        data = self.sock.recv(min(4096, limit))
        self.deadline.remaining()
        if not data:
            return False
        self.buffer.extend(data)
        return True

    def readline(self, limit: int) -> bytes:
        while True:
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end + 1 > limit or end == 0 or self.buffer[end - 1] != 13:
                    raise _invalid()
                line = bytes(self.buffer[:end + 1])
                del self.buffer[:end + 1]
                return line
            if len(self.buffer) >= limit or not self._fill(limit - len(self.buffer)):
                raise _invalid()

    def read_some(self, size: int) -> bytes:
        if not self.buffer and not self._fill(min(4096, size)):
            return b""
        count = min(size, len(self.buffer))
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def read_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.read_some(size - len(result))
            if not chunk:
                raise _invalid()
            result.extend(chunk)
        return bytes(result)


def _parse_headers(reader: _Reader) -> tuple[int, list[tuple[str, str]], str, int | None]:
    status_line = reader.readline(_MAX_HEADER_LINE)
    match = _STATUS.fullmatch(status_line)
    if not match:
        raise _invalid()
    total = len(status_line)
    headers: list[tuple[str, str]] = []
    while True:
        remaining = _MAX_HEADER_BYTES - total
        if remaining < 2:
            raise _invalid()
        line = reader.readline(min(_MAX_HEADER_LINE, remaining))
        total += len(line)
        if line == b"\r\n":
            break
        if len(headers) == _MAX_HEADERS:
            raise _invalid()
        name, separator, value = line[:-2].partition(b":")
        if not separator or not _TOKEN.fullmatch(name):
            raise _invalid()
        value = value.strip(b" ")
        if any(byte < 32 or byte > 126 for byte in value):
            raise _invalid()
        headers.append((name.decode("ascii"), value.decode("ascii")))

    framing: dict[str, list[str]] = {"content-length": [], "transfer-encoding": []}
    for name, value in headers:
        if name.lower() in framing:
            framing[name.lower()].append(value)
    lengths, encodings = framing.values()
    if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
        raise _invalid()
    if encodings:
        if encodings[0].lower() != "chunked":
            raise _invalid()
        mode, content_length = "chunked", None
    elif lengths:
        if not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
            raise _invalid()
        mode, content_length = "length", int(lengths[0])
    else:
        mode, content_length = "close", None
    return int(match.group(1)), headers, mode, content_length


class _Response:
    def __init__(self, sock: socket.socket, peer: str, hostname: str,
                 status: int, headers: list[tuple[str, str]], reader: _Reader,
                 mode: str, content_length: int | None, deadline: Deadline) -> None:
        self.status, self.headers = status, headers
        self.peer_address, self.tls_verified_hostname = peer, hostname
        self._sock, self._reader, self._mode = sock, reader, mode
        self._remaining, self._chunk_remaining = content_length, 0
        self._deadline, self._closed, self._done = deadline, False, False

    def _next_chunk(self) -> None:
        line = self._reader.readline(128)
        raw = line[:-2]
        size_text = raw.split(b";", 1)[0]
        if (not size_text or len(size_text) > 16
                or any(byte not in b"0123456789abcdefABCDEF" for byte in size_text)
                or any(byte < 32 or byte > 126 for byte in raw)):
            raise _invalid()
        self._chunk_remaining = int(size_text, 16)
        if self._chunk_remaining:
            return
        total = count = 0
        while True:
            line = self._reader.readline(_MAX_HEADER_BYTES - total)
            total += len(line)
            if line == b"\r\n":
                break
            count += 1
            if count > _MAX_HEADERS:
                raise _invalid()
            name, separator, value = line[:-2].partition(b":")
            if not separator or not _TOKEN.fullmatch(name):
                raise _invalid()
            if any(byte < 32 or byte > 126 for byte in value.strip(b" ")):
                raise _invalid()
            if name.lower() in {b"content-length", b"transfer-encoding", b"content-type", b"content-encoding"}:
                raise _invalid()
        self._done = True

    def read(self, size: int, *, deadline: Deadline) -> bytes:
        if deadline is not self._deadline:
            raise TypeError("The response must use its original Deadline")
        if type(size) is not int or size < 0 or size > 8192:
            raise ValueError("read size must be between 0 and 8192")
        if size == 0 or self._done:
            return b""
        deadline.remaining()
        if self._mode == "length":
            if self._remaining == 0:
                self._done = True
                return b""
            count = min(size, self._remaining)
            body = self._reader.read_exact(count)
            self._remaining -= len(body)
        elif self._mode == "chunked":
            if self._chunk_remaining == 0:
                self._next_chunk()
                if self._done:
                    return b""
            count = min(size, self._chunk_remaining)
            body = self._reader.read_exact(count)
            self._chunk_remaining -= len(body)
            if self._chunk_remaining == 0 and self._reader.read_exact(2) != b"\r\n":
                raise _invalid()
        else:
            body = self._reader.read_some(size)
            if not body:
                self._done = True
        deadline.remaining()
        return body

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._sock.close()


class PinnedIPTransport:
    """Open one anonymous HTTPS GET to the already-resolved public IP."""

    def __call__(self, request: PinnedRequest, *, deadline: Deadline) -> _Response:
        if not isinstance(request, PinnedRequest) or not isinstance(deadline, Deadline):
            raise TypeError("PinnedRequest and shared Deadline are required")
        try:
            canonical, hostname, _literal = _url(request.url)
            address = _public_ip(request.address)
            if (canonical != request.url or hostname != request.hostname
                    or request.tls_server_name != hostname or request.port != 443
                    or request.host_header != (f"[{hostname}]" if ":" in hostname else hostname)
                    or address != request.address or request.headers != _REQUEST_HEADERS):
                raise SafeFetchError("UNSAFE_URL")
            ip = ipaddress.ip_address(address)
            parsed = urlsplit(canonical)
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
        except SafeFetchError:
            raise
        except Exception:
            raise SafeFetchError("UNSAFE_URL") from None

        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sockaddr = (address, 443, 0, 0) if ip.version == 6 else (address, 443)
        raw = tls = None
        try:
            deadline.remaining()
            raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            raw.settimeout(deadline.remaining())
            raw.connect(sockaddr)
            deadline.remaining()

            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            tls = context.wrap_socket(raw, server_hostname=request.tls_server_name,
                                      do_handshake_on_connect=False)
            raw = None
            tls.settimeout(deadline.remaining())
            tls.do_handshake()
            deadline.remaining()

            request_bytes = f"GET {target} HTTP/1.1\r\nHost: {request.host_header}\r\n".encode("ascii")
            request_bytes += b"".join(f"{name}: {value}\r\n".encode("ascii") for name, value in request.headers)
            tls.settimeout(deadline.remaining())
            tls.sendall(request_bytes + b"\r\n")
            deadline.remaining()

            peer_value = tls.getpeername()[0]
            if "%" in peer_value:
                raise SafeFetchError("UNSAFE_ADDRESS")
            peer = str(ipaddress.ip_address(peer_value))
            reader = _Reader(tls, deadline)
            status, headers, mode, content_length = _parse_headers(reader)
            response = _Response(tls, peer, hostname, status, headers,
                                 reader, mode, content_length, deadline)
            return response
        except BaseException:
            if tls is not None:
                tls.close()
            if raw is not None:
                raw.close()
            raise
