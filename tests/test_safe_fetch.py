"""Offline security matrix: every DNS/transport result is synthetic."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_mcp.safe_fetch import Deadline, FetchLimits, SafeFetchError, SafeFetcher
from research_mcp import service


PUBLIC = "93.184.216.34"
PUBLIC6 = "2606:4700:4700::1111"


class FakeResponse:
    def __init__(self, body=b"public fixture", *, status=200, headers=None,
                 peer=PUBLIC, tls_host="example.com", read_hook=None):
        self.status = status
        self.headers = [("Content-Type", "text/plain; charset=utf-8")] if headers is None else headers
        self.peer_address, self.tls_verified_hostname = peer, tls_host
        self.body, self.offset, self.closed = body, 0, False
        self.read_sizes = []
        self.read_hook = read_hook

    def read(self, size, *, deadline):
        self.read_sizes.append(size)
        if self.read_hook:
            self.read_hook(deadline)
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class SafeFetchTest(unittest.TestCase):
    def fetcher(self, response=None, *, addresses=None, limits=None, clock=None):
        resolver = Mock(return_value=addresses if addresses is not None else [PUBLIC])
        transport = Mock(return_value=response if response is not None else FakeResponse())
        fetcher = SafeFetcher(resolver=resolver, transport=transport,
                              limits=limits or FetchLimits(), clock=clock or (lambda: 100.0))
        return fetcher, resolver, transport

    def assert_code(self, code, action):
        with self.assertRaises(SafeFetchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_requires_explicit_io_dependencies(self):
        with self.assertRaises(TypeError):
            SafeFetcher()
        for kwargs in ({"resolver": None, "transport": Mock()}, {"resolver": Mock(), "transport": None}):
            with self.assertRaises(TypeError):
                SafeFetcher(**kwargs)

    def test_success_pins_dns_and_keeps_original_tls_and_host_identity(self):
        response = FakeResponse("公开正文".encode())
        fetcher, resolver, transport = self.fetcher(response)
        result = fetcher.fetch("https://EXAMPLE.com:443/article?q=fixture")
        self.assertEqual(result.url, "https://example.com/article?q=fixture")
        self.assertEqual(result.body.decode(), "公开正文")
        request = transport.call_args.args[0]
        self.assertEqual((request.hostname, request.tls_server_name, request.host_header), ("example.com",) * 3)
        self.assertEqual((request.address, request.port), (PUBLIC, 443))
        self.assertEqual(dict(request.headers)["Accept-Encoding"], "identity")
        self.assertTrue(response.closed)
        with self.assertRaises(FrozenInstanceError):
            request.address = "127.0.0.1"
        self.assertEqual(resolver.call_count, 1)
        self.assertIs(resolver.call_args.kwargs["deadline"], transport.call_args.kwargs["deadline"])

    def test_url_rejections_happen_before_dns_or_transport(self):
        values = [None, "", "http://example.com", "file:///etc/passwd", "https://example.com:444/",
                  "https://user:secret@example.com/", "https://@example.com/", "https://example.com:/",
                  "https://example.com/#fragment", " https://example.com/", "https://example.com/\nprivate",
                  "https://example.com\\@127.0.0.1/", "https://%31%32%37.0.0.1/", "https://localhost/",
                  "https://a.localhost/", "https://a.local/", "https://a.internal/", "https://router.lan/",
                  "https://a.home.arpa/", "https://a.onion/", "https://example.com./", "https://example..com/",
                  "https://-example.com/", "https://example_.com/", "https://例子.com/", "https://[fe80::1%25eth0]/",
                  "https://2130706433/", "https://127.1/", "https://0177.0.0.1/", "https://0x7f000001/",
                  "https://0x7f.0.0.1/", "https://example.com/" + "a" * 4096]
        for url in values:
            fetcher, resolver, transport = self.fetcher()
            with self.subTest(url=url):
                self.assert_code("UNSAFE_URL", lambda: fetcher.fetch(url))
                resolver.assert_not_called()
                transport.assert_not_called()

    def test_private_special_ipv4_ipv6_literals_and_mixed_dns_are_rejected(self):
        addresses = ["0.0.0.0", "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                     "100.64.0.1", "192.0.0.9", "192.88.99.1", "192.0.2.1", "198.18.0.1", "198.51.100.1",
                     "203.0.113.1", "224.0.0.1", "240.0.0.1", "255.255.255.255", "::", "::1", "fe80::1",
                     "fc00::1", "ff02::1", "2001:db8::1", "::ffff:93.184.216.34", "64:ff9b::7f00:1",
                     "2002:7f00:1::", "2001::1", "2001:20::1", "3fff::1", "fe80::1%eth0"]
        for address in addresses:
            with self.subTest(address=address):
                fetcher, _, transport = self.fetcher(addresses=[PUBLIC, address])
                self.assert_code("UNSAFE_ADDRESS", lambda: fetcher.fetch("https://example.com"))
                transport.assert_not_called()
                if "%" not in address:
                    host = f"[{address}]" if ":" in address else address
                    fetcher, resolver, transport = self.fetcher()
                    self.assert_code("UNSAFE_ADDRESS", lambda: fetcher.fetch("https://" + host + "/"))
                    resolver.assert_not_called()
                    transport.assert_not_called()

    def test_dns_answer_shape_and_failure_fail_closed(self):
        for answers in ([], None, PUBLIC, [PUBLIC] * 17, (x for x in [PUBLIC])):
            fetcher, resolver, transport = self.fetcher()
            resolver.return_value = answers
            self.assert_code("DNS_FAILED", lambda: fetcher.fetch("https://example.com"))
            transport.assert_not_called()
        for invalid in (None, "not-an-ip", "127.1", 123):
            fetcher, _, transport = self.fetcher(addresses=[invalid])
            self.assert_code("UNSAFE_ADDRESS", lambda: fetcher.fetch("https://example.com"))
            transport.assert_not_called()
        fetcher, resolver, transport = self.fetcher()
        resolver.side_effect = OSError("SECRET-token /private/path")
        self.assert_code("DNS_FAILED", lambda: fetcher.fetch("https://example.com"))
        transport.assert_not_called()

    def test_public_ip_literals_do_not_trigger_dns(self):
        for address in (PUBLIC, PUBLIC6):
            host = f"[{address}]" if ":" in address else address
            fetcher, resolver, transport = self.fetcher(FakeResponse(peer=address, tls_host=address))
            self.assertEqual(fetcher.fetch("https://" + host).body, b"public fixture")
            resolver.assert_not_called()
            self.assertEqual(transport.call_args.args[0].host_header, host)

    def test_peer_pinning_and_tls_identity_are_checked_separately(self):
        for peer, tls in (("8.8.8.8", "example.com"), ("127.0.0.1", "example.com"), (PUBLIC, "evil.example"),
                          (PUBLIC, ""), (PUBLIC6, "example.com")):
            response = FakeResponse(peer=peer, tls_host=tls)
            fetcher, _, _ = self.fetcher(response)
            self.assert_code("UNSAFE_ADDRESS", lambda: fetcher.fetch("https://example.com"))
            self.assertTrue(response.closed)
            self.assertEqual(response.read_sizes, [])

    def test_redirects_revalidate_dns_even_for_same_host_and_never_read_body(self):
        first = FakeResponse(status=302, headers=[("Location", "/next"), ("Set-Cookie", "private=discard")])
        second = FakeResponse()
        fetcher, resolver, transport = self.fetcher()
        resolver.side_effect = [[PUBLIC], [PUBLIC]]
        transport.side_effect = [first, second]
        self.assertEqual(fetcher.fetch("https://example.com/start").url, "https://example.com/next")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(first.read_sizes, [])
        self.assertTrue(first.closed and second.closed)
        requests = [call.args[0] for call in transport.call_args_list]
        self.assertEqual(requests[0].headers, requests[1].headers)
        self.assertFalse({name.lower() for name, _ in requests[1].headers} & {"authorization", "cookie", "proxy-authorization", "referer"})
        self.assertIs(transport.call_args_list[0].kwargs["deadline"], transport.call_args_list[1].kwargs["deadline"])

    def test_redirect_rebinding_and_unsafe_locations_do_not_reach_transport(self):
        for location in ("http://example.com/", "https://127.0.0.1/", "https://[::1]/", "https://a:b@example.com/",
                         "//example.com:8443/", " https://example.com/", "https://example.com\\@localhost/", "file:///private"):
            response = FakeResponse(status=302, headers=[("Location", location)])
            fetcher, _, transport = self.fetcher(response)
            with self.subTest(location=location), self.assertRaises(SafeFetchError):
                fetcher.fetch("https://example.com/start")
            self.assertEqual(transport.call_count, 1)
            self.assertTrue(response.closed)
        first = FakeResponse(status=302, headers=[("Location", "/next")])
        fetcher, resolver, transport = self.fetcher(first)
        resolver.side_effect = [[PUBLIC], ["127.0.0.1"]]
        self.assert_code("UNSAFE_ADDRESS", lambda: fetcher.fetch("https://example.com/start"))
        self.assertEqual(transport.call_count, 1)

    def test_redirect_loops_and_hop_budget(self):
        for location, redirects in (("/", 3), ("/next", 0)):
            response = FakeResponse(status=302, headers=[("Location", location)])
            fetcher, _, transport = self.fetcher(response, limits=FetchLimits(max_redirects=redirects))
            self.assert_code("REDIRECT_LIMIT", lambda: fetcher.fetch("https://example.com/"))
            self.assertEqual(transport.call_count, 1)
            self.assertTrue(response.closed)

    def test_cross_host_redirect_gets_new_identity_without_reusing_headers(self):
        first = FakeResponse(status=307, headers=[("Location", "https://other.example.com/next")])
        fetcher, resolver, transport = self.fetcher()
        transport.side_effect = [first, FakeResponse(tls_host="other.example.com")]
        result = fetcher.fetch("https://example.com/")
        self.assertEqual(result.url, "https://other.example.com/next")
        self.assertEqual(resolver.call_args_list[1].args[0], "other.example.com")
        self.assertEqual(transport.call_args_list[1].args[0].tls_server_name, "other.example.com")

    def test_content_length_and_stream_caps_are_enforced_before_return(self):
        for headers, body, code in (
            ([("Content-Type", "text/plain"), ("Content-Length", "11")], b"", "RESPONSE_TOO_LARGE"),
            ([("Content-Type", "text/plain")], b"a" * 11, "RESPONSE_TOO_LARGE"),
            ([("Content-Type", "text/plain"), ("Content-Length", "5")], b"a" * 6, "INVALID_RESPONSE"),
            ([("Content-Type", "text/plain"), ("Content-Length", "5")], b"a" * 4, "INVALID_RESPONSE"),
        ):
            response = FakeResponse(body, headers=headers)
            fetcher, _, _ = self.fetcher(response, limits=FetchLimits(max_bytes=10))
            self.assert_code(code, lambda: fetcher.fetch("https://example.com"))
            self.assertTrue(response.closed)
        response = FakeResponse(b"a" * 10, headers=[("Content-Type", "text/plain"), ("Content-Length", "10")])
        fetcher, _, _ = self.fetcher(response, limits=FetchLimits(max_bytes=10))
        self.assertEqual(fetcher.fetch("https://example.com").body, b"a" * 10)
        self.assertEqual(response.read_sizes, [11, 1])

    def test_header_framing_and_content_validation(self):
        invalid = [
            [("Content-Type", "text/plain"), ("Content-Length", "-1")],
            [("Content-Type", "text/plain"), ("Content-Length", "3,3")],
            [("Content-Type", "text/plain"), ("Content-Length", "999999999999")],
            [("Content-Length", "3"), ("content-length", "3")],
            [("Content-Type", "text/plain"), ("Content-Type", "text/plain")],
            [("Location", "/a"), ("location", "/b")],
            [("Transfer-Encoding", "chunked"), ("Content-Length", "3")],
            [("Transfer-Encoding", "gzip")], [("x-header", "a\rb")], [("x-header", "a\nb")],
            [("x-header", "a" * 8193)], [("bad name", "x")], [("X", "x")] * 65, "not-pairs",
        ]
        for headers in invalid:
            response = FakeResponse(headers=headers)
            fetcher, _, _ = self.fetcher(response)
            self.assert_code("INVALID_RESPONSE", lambda: fetcher.fetch("https://example.com"))
            self.assertTrue(response.closed)
        for headers in ([], [("Content-Type", "application/octet-stream")],
                        [("Content-Type", "text/plain"), ("Content-Encoding", "gzip")],
                        [("Content-Type", "text/plain"), ("Content-Encoding", "br")]):
            fetcher, _, _ = self.fetcher(FakeResponse(headers=headers))
            self.assert_code("UNSUPPORTED_CONTENT", lambda: fetcher.fetch("https://example.com"))

    def test_response_status_and_malformed_chunks(self):
        for status in (True, "200"):
            fetcher, _, _ = self.fetcher(FakeResponse(status=status))
            self.assert_code("INVALID_RESPONSE", lambda: fetcher.fetch("https://example.com"))
        for status in (101, 204, 206, 304, 401, 403, 404, 429, 500):
            fetcher, _, _ = self.fetcher(FakeResponse(status=status))
            self.assert_code("UPSTREAM_HTTP_ERROR", lambda: fetcher.fetch("https://example.com"))
        for chunk in ("string", None, b"a" * 12):
            response = FakeResponse()
            response.read = Mock(return_value=chunk)
            fetcher, _, _ = self.fetcher(response, limits=FetchLimits(max_bytes=10))
            self.assert_code("INVALID_RESPONSE", lambda: fetcher.fetch("https://example.com"))

    def test_same_absolute_deadline_covers_dns_open_stream_and_redirects(self):
        for stage in ("dns", "open", "read"):
            now = [0.0]
            response = FakeResponse()
            fetcher, resolver, transport = self.fetcher(response, clock=lambda: now[0], limits=FetchLimits(total_seconds=1))
            if stage == "dns":
                def resolve(*args, **kwargs):
                    now[0] = 1.0
                    return [PUBLIC]
                resolver.side_effect = resolve
            elif stage == "open":
                def open_response(*args, **kwargs):
                    now[0] = 1.0
                    return response
                transport.side_effect = open_response
            else:
                response.read_hook = lambda deadline: now.__setitem__(0, 1.0)
            self.assert_code("DEADLINE_EXCEEDED", lambda: fetcher.fetch("https://example.com"))
            if stage == "dns":
                transport.assert_not_called()
            else:
                self.assertTrue(response.closed)
        now = [0.0]
        first = FakeResponse(status=302, headers=[("Location", "/next")])
        second = FakeResponse(read_hook=lambda deadline: now.__setitem__(0, now[0] + 0.4))
        fetcher, resolver, transport = self.fetcher(clock=lambda: now[0], limits=FetchLimits(total_seconds=1))
        def resolve(*args, **kwargs):
            now[0] += 0.4
            return [PUBLIC]
        resolver.side_effect = resolve
        transport.side_effect = [first, second]
        self.assert_code("DEADLINE_EXCEEDED", lambda: fetcher.fetch("https://example.com"))
        self.assertTrue(first.closed and second.closed)

    def test_bad_clock_and_io_timeout_fail_closed(self):
        for value in (float("nan"), float("inf"), True, "100"):
            fetcher, resolver, transport = self.fetcher(clock=lambda: value)
            self.assert_code("DEADLINE_EXCEEDED", lambda: fetcher.fetch("https://example.com"))
            resolver.assert_not_called()
            transport.assert_not_called()
        now = [100.0]
        deadline = Deadline(10, lambda: now[0])
        now[0] = 99.0
        self.assert_code("DEADLINE_EXCEEDED", deadline.remaining)
        for stage in ("dns", "open", "read"):
            response = FakeResponse()
            fetcher, resolver, transport = self.fetcher(response)
            operation = {"dns": resolver, "open": transport, "read": response}[stage]
            if stage == "read":
                response.read = Mock(side_effect=TimeoutError("SECRET"))
            else:
                operation.side_effect = TimeoutError("SECRET")
            self.assert_code("DEADLINE_EXCEEDED", lambda: fetcher.fetch("https://example.com"))

    def test_cancellation_closes_response_and_is_not_disguised_as_retry(self):
        response = FakeResponse()
        response.read = Mock(side_effect=KeyboardInterrupt())
        fetcher, _, _ = self.fetcher(response)
        with self.assertRaises(KeyboardInterrupt):
            fetcher.fetch("https://example.com")
        self.assertTrue(response.closed)

    def test_no_ambient_network_identity_cache_or_secret_lookup(self):
        response = FakeResponse()
        fetcher, resolver, transport = self.fetcher(response)
        with patch("socket.getaddrinfo", side_effect=AssertionError("unexpected DNS")), \
             patch("socket.socket", side_effect=AssertionError("unexpected socket")), \
             patch("subprocess.run", side_effect=AssertionError("unexpected process")), \
             patch.object(Path, "read_text", side_effect=AssertionError("unexpected file")), \
             patch.dict("os.environ", {"HTTPS_PROXY": "http://private.proxy", "GH_TOKEN": "SECRET", "NETRC": "/private"}):
            fetcher.fetch("https://example.com")
            transport.return_value = FakeResponse()
            fetcher.fetch("https://example.com")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(transport.call_count, 2)
        self.assertNotIn("SECRET", repr(transport.call_args))

    def test_configuration_is_hard_bounded(self):
        for field, values in (("max_bytes", (0, True, 1.1, 1_048_577)),
                              ("total_seconds", (0, True, "1", 31, float("inf"), float("nan"))),
                              ("max_redirects", (-1, True, 1.1, 6))):
            for value in values:
                with self.assertRaises(ValueError):
                    replace(FetchLimits(), **{field: value})


class PublicServiceTest(unittest.TestCase):
    def test_public_search_requires_explicit_anonymous_scope(self):
        with patch.object(service.R, "search") as core:
            for providers in (None, [], ["github_search"], ["exa"], ["bilibili"], ["hacker_news", "github_search"],
                              ["hacker_news", "hacker_news"], [None], "hacker_news"):
                result = service.search_public("fixture", providers=providers)
                self.assertEqual(result["errors"][0]["code"], "SCOPE_DENIED")
            core.assert_not_called()

    def test_public_search_keeps_bounds_and_forces_rerank_off(self):
        with patch.object(service, "search", return_value={"ok": True}) as core:
            for query, limit in (("", 1), ("a" * 501, 1), ("fixture", 21), ("fixture", True)):
                self.assertEqual(service.search_public(query, providers=["hacker_news"], limit=limit)["errors"][0]["code"], "INVALID_INPUT")
            for url in ("http://example.com", "HTTPS://example.com"):
                self.assertEqual(service.search_public(url, providers=["hacker_news"])["errors"][0]["code"], "CAPABILITY_DISABLED")
            core.assert_not_called()
            service.search_public("fixture", providers=["hacker_news"], request_id="synthetic")
            core.assert_called_once_with("fixture", providers=["hacker_news"], limit=12, rerank=False, request_id="synthetic")

    def test_public_search_passes_only_confirmed_age_windows(self):
        with patch.object(service, "search", return_value={"ok": True}) as core:
            for age in (True, 365, 0, "90"):
                result = service.search_public("fixture", providers=["hacker_news"], max_age_days=age)
                self.assertEqual(result["errors"][0]["code"], "INVALID_INPUT")
            core.assert_not_called()
            service.search_public("fixture", providers=["hacker_news"], max_age_days=90)
            core.assert_called_once_with("fixture", limit=12, providers=["hacker_news"], rerank=False,
                                         request_id=None, max_age_days=90)

    def test_public_fetch_never_falls_back_to_personal_gh_or_legacy_fetch(self):
        with patch.object(service.R, "fetch_url", side_effect=AssertionError("private identity")) as private:
            self.assertEqual(service.fetch_public("https://github.com/private/repo")["errors"][0]["code"], "CAPABILITY_DISABLED")
            resolver = Mock(return_value=[PUBLIC])
            transport = Mock(return_value=FakeResponse(tls_host="github.com"))
            fetcher = SafeFetcher(resolver=resolver, transport=transport)
            result = service.fetch_public("https://github.com/public/repo", fetcher=fetcher)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["data"]["via"], "public_https_raw")
            self.assertNotIn("metadata", result["data"])
            transport.side_effect = OSError("SECRET-token /private/path")
            result = service.fetch_public("https://github.com/public/repo", fetcher=fetcher)
            self.assertEqual(result["errors"][0]["code"], "TRANSPORT_FAILED")
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertNotIn("/private/", json.dumps(result))
            private.assert_not_called()

    def test_public_fetch_decoding_errors_and_empty_body_are_not_success(self):
        for body, code in ((b"\xff", "UNSUPPORTED_CONTENT"), (b"hello\x00", "UNSUPPORTED_CONTENT"), (b"", "INVALID_RESULT")):
            fetcher = SafeFetcher(resolver=lambda *a, **kw: [PUBLIC], transport=lambda *a, **kw: FakeResponse(body))
            result = service.fetch_public("https://example.com", fetcher=fetcher)
            self.assertEqual(result["errors"][0]["code"], code)
            self.assertFalse(result["ok"])

    def test_public_fetch_http_200_challenge_is_not_article_and_never_retries(self):
        body = (b'<html><script src="https://lf-waf-js.byted-static.com/obj/waf-jschallenge/out-sha256.js">'
                b'</script><body>Please wait...</body></html>')
        response = FakeResponse(body, headers=[("Content-Type", "text/html; charset=utf-8")])
        transport = Mock(return_value=response)
        fetcher = SafeFetcher(resolver=lambda *a, **kw: [PUBLIC], transport=transport)
        with patch.object(service.R, "fetch_url", side_effect=AssertionError("private fallback")):
            result = service.fetch_public("https://example.com/article", fetcher=fetcher,
                                          request_id="challenge-fixture")
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "ACCESS_CHALLENGE")
        self.assertEqual(result["request_id"], "challenge-fixture")
        self.assertNotIn("Please wait", json.dumps(result))
        self.assertNotIn("content", result.get("data") or {})
        transport.assert_called_once()
        self.assertTrue(response.closed)

    def test_public_fetch_keeps_regular_html_and_markdown_challenge_discussions(self):
        examples = [
            ("text/html", b'<article>Please wait for the saved task.</article>'),
            ("text/html", b'<article>&lt;script src="https://lf-waf-js.byted-static.com/obj/waf-jschallenge/a.js"&gt;</article>'),
            ("text/markdown", b'Example: <script src="https://lf-waf-js.byted-static.com/obj/waf-jschallenge/a.js"></script>'),
        ]
        for media_type, body in examples:
            with self.subTest(media_type=media_type, body=body):
                response = FakeResponse(body, headers=[("Content-Type", media_type)])
                fetcher = SafeFetcher(resolver=lambda *a, **kw: [PUBLIC],
                                      transport=lambda *a, **kw: response)
                result = service.fetch_public("https://example.com/article", fetcher=fetcher)
                self.assertTrue(result["ok"])
                self.assertEqual(result["data"]["content"], body.decode())


if __name__ == "__main__":
    unittest.main()
