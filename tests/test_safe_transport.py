from pathlib import Path
import ssl
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research_mcp.safe_fetch import Deadline, FetchLimits, PinnedRequest, SafeFetchError, SafeFetcher
from research_mcp.safe_transport import PinnedIPTransport


PUBLIC = "93.184.216.34"
PUBLIC6 = "2606:4700:4700::1111"


class FakeSocket:
    def __init__(self, response: bytes, *, peer: str = PUBLIC, recv_chunk: int = 4096):
        self.response = bytearray(response)
        self.peer, self.recv_chunk = peer, recv_chunk
        self.connected = None
        self.sent = bytearray()
        self.timeouts = []
        self.closed = False
        self.handshakes = 0

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected = address

    def do_handshake(self):
        self.handshakes += 1

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, size):
        count = min(size, self.recv_chunk, len(self.response))
        data = bytes(self.response[:count])
        del self.response[:count]
        return data

    def getpeername(self):
        return self.peer, 443

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.check_hostname = False
        self.verify_mode = None
        self.wrap_args = None

    def wrap_socket(self, sock, **kwargs):
        self.wrap_args = kwargs
        return sock


def make_request(address=PUBLIC, hostname="example.com", url="https://example.com/a?q=1"):
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    return PinnedRequest(url, hostname, address, hostname, host_header)


class PinnedIPTransportTest(unittest.TestCase):
    def run_fetch(self, response, *, address=PUBLIC, hostname="example.com", url="https://example.com/a?q=1",
                  peer=None, recv_chunk=4096):
        fake = FakeSocket(response, peer=peer or address, recv_chunk=recv_chunk)
        context = FakeContext()
        now = [100.0]
        fetcher = SafeFetcher(
            resolver=lambda _host, *, deadline: [address],
            transport=PinnedIPTransport(),
            limits=FetchLimits(total_seconds=5),
            clock=lambda: now[0],
        )
        request_url = url
        if address == PUBLIC6 and hostname == "example.com":
            request_url = f"https://[{PUBLIC6}]/a?q=1"
            hostname = PUBLIC6
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake) as socket_factory, \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context), \
             patch("socket.getaddrinfo", side_effect=AssertionError("transport must not resolve DNS")) as resolver:
            result = fetcher.fetch(request_url)
        return result, fake, context, socket_factory, resolver

    def test_connects_to_pinned_ip_and_verifies_original_tls_host(self):
        response = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    b"Content-Length: 11\r\n\r\nhello world")
        result, fake, context, socket_factory, resolver = self.run_fetch(response, recv_chunk=19)
        self.assertEqual(result.body, b"hello world")
        self.assertEqual(fake.connected, (PUBLIC, 443))
        self.assertEqual(context.wrap_args, {
            "server_hostname": "example.com", "do_handshake_on_connect": False,
        })
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(fake.handshakes, 1)
        self.assertIn(b"Host: example.com\r\n", fake.sent)
        self.assertIn(b"Accept-Encoding: identity\r\n", fake.sent)
        self.assertNotIn(b"Cookie:", fake.sent)
        self.assertNotIn(b"Proxy-Authorization:", fake.sent)
        self.assertTrue(fake.closed)
        self.assertGreaterEqual(len(fake.timeouts), 4)
        self.assertTrue(all(timeout > 0 for timeout in fake.timeouts))
        socket_factory.assert_called_once()
        resolver.assert_not_called()

    def test_ipv6_uses_literal_sockaddr_and_bracketed_host_header(self):
        fake = FakeSocket(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1\r\n\r\nx", peer=PUBLIC6)
        context = FakeContext()
        request = make_request(PUBLIC6, PUBLIC6, f"https://[{PUBLIC6}]/")
        deadline = Deadline(5, lambda: 100.0)
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context), \
             patch("socket.getaddrinfo", side_effect=AssertionError("unexpected DNS")):
            response = PinnedIPTransport()(request, deadline=deadline)
        self.assertEqual(fake.connected, (PUBLIC6, 443, 0, 0))
        self.assertEqual(context.wrap_args["server_hostname"], PUBLIC6)
        self.assertIn(f"Host: [{PUBLIC6}]\r\n".encode(), fake.sent)
        self.assertEqual(response.peer_address, PUBLIC6)
        self.assertEqual(response.tls_verified_hostname, PUBLIC6)
        self.assertEqual(response.read(1, deadline=deadline), b"x")
        response.close()
        self.assertTrue(fake.closed)

    def test_chunked_body_is_decoded_and_read_stays_bounded(self):
        raw = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
               b"4;fixture=yes\r\nWiki\r\n5\r\npedia\r\n0\r\nX-Trace: ignored\r\n\r\n")
        fake = FakeSocket(raw, recv_chunk=3)
        context = FakeContext()
        deadline = Deadline(5, lambda: 100.0)
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context):
            response = PinnedIPTransport()(make_request(), deadline=deadline)
        self.assertEqual(response.read(3, deadline=deadline), b"Wik")
        self.assertEqual(response.read(3, deadline=deadline), b"i")
        self.assertEqual(response.read(10, deadline=deadline), b"pedia")
        self.assertEqual(response.read(10, deadline=deadline), b"")
        response.close()
        self.assertTrue(fake.closed)

    def test_headers_are_bounded_and_failed_open_closes_socket(self):
        fake = FakeSocket(b"HTTP/1.1 200 OK\r\nX-Large: " + b"a" * 9000 + b"\r\n\r\n")
        context = FakeContext()
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context):
            with self.assertRaises(SafeFetchError) as caught:
                PinnedIPTransport()(make_request(), deadline=Deadline(5, lambda: 100.0))
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")
        self.assertTrue(fake.closed)

    def test_framing_ambiguity_and_wrong_peer_fail_closed(self):
        for raw in (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1\r\n"
            b"content-length: 1\r\n\r\nx",
        ):
            fake = FakeSocket(raw)
            context = FakeContext()
            with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
                 patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context):
                with self.assertRaises(SafeFetchError) as caught:
                    PinnedIPTransport()(make_request(), deadline=Deadline(5, lambda: 100.0))
            self.assertEqual(caught.exception.code, "INVALID_RESPONSE")
            self.assertTrue(fake.closed)

        raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1\r\n\r\nx"
        fake, context = FakeSocket(raw, peer="8.8.8.8"), FakeContext()
        fetcher = SafeFetcher(resolver=lambda _host, *, deadline: [PUBLIC], transport=PinnedIPTransport())
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context):
            with self.assertRaises(SafeFetchError) as caught:
                fetcher.fetch("https://example.com/a?q=1")
        self.assertEqual(caught.exception.code, "UNSAFE_ADDRESS")
        self.assertTrue(fake.closed)

    def test_request_mismatch_fails_before_socket_creation(self):
        request = make_request(address="127.0.0.1")
        with patch("research_mcp.safe_transport.socket.socket") as socket_factory:
            with self.assertRaises(SafeFetchError):
                PinnedIPTransport()(request, deadline=Deadline(5, lambda: 100.0))
        socket_factory.assert_not_called()

    def test_response_requires_same_deadline(self):
        fake = FakeSocket(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 1\r\n\r\nx")
        context = FakeContext()
        deadline = Deadline(5, lambda: 100.0)
        with patch("research_mcp.safe_transport.socket.socket", return_value=fake), \
             patch("research_mcp.safe_transport.ssl.create_default_context", return_value=context):
            response = PinnedIPTransport()(make_request(), deadline=deadline)
        with self.assertRaises(TypeError):
            response.read(1, deadline=Deadline(5, lambda: 100.0))
        response.close()


if __name__ == "__main__":
    unittest.main()
