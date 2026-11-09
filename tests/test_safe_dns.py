"""Controlled real child processes; all DNS answers are explicitly synthetic."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_mcp import safe_dns
from research_mcp.safe_fetch import Deadline, FetchLimits, SafeFetchError, SafeFetcher

PUBLIC = "93.184.216.34"
PUBLIC6 = "2606:4700:4700::1111"




def fixture_children(monkeypatch, *, answers=None, program=None):
    """Replace only the child's resolver function/program, never call real DNS."""
    original = subprocess.Popen
    children, invocations = [], []
    synthetic = answers if answers is not None else [PUBLIC, PUBLIC6, PUBLIC]
    prelude = (
        "import socket\n"
        "def denied(*args, **kwargs): raise RuntimeError('real sockets forbidden')\n"
        "socket.socket.connect = denied\n"
        "socket.socket.connect_ex = denied\n"
        f"fixture = {synthetic!r}\n"
        "def fake_dns(host, port, family, kind, protocol):\n"
        "    assert (host, port, family, kind, protocol) == ('example.invalid', 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)\n"
        "    return [(socket.AF_INET6 if ':' in value else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (value, 443)) for value in fixture]\n"
        "socket.getaddrinfo = fake_dns\n"
    )
    def launch(argv, **kwargs):
        invocations.append((argv[:], kwargs.copy()))
        assert argv[:4] == [sys.executable, "-I", "-S", "-c"]
        assert argv[4] == safe_dns._WORKER
        assert kwargs["env"] == {"LANG": "C", "LC_ALL": "C"}
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["close_fds"] is True and kwargs["cwd"] == "/"
        actual = argv[:]
        actual[4] = prelude + (program if program is not None else argv[4])
        process = original(actual, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(safe_dns.subprocess, "Popen", launch)
    return children, invocations


def resolve(seconds=2):
    return safe_dns.SubprocessResolver()("example.invalid", deadline=Deadline(seconds, time.monotonic))


def reaped(children):
    assert children
    for child in children:
        assert child.poll() is not None and child.stdout.closed
        with unittest.TestCase().assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)


def _case_test_real_child_returns_all_unique_families_and_uses_minimal_environment(monkeypatch):
    monkeypatch.setenv("SECRET_TEST_PROVIDER_KEY", "must-not-inherit")
    monkeypatch.setenv("HTTPS_PROXY", "http://secret.invalid")
    children, invocations = fixture_children(monkeypatch)
    assert resolve() == [PUBLIC, PUBLIC6]
    assert len(invocations) == 1
    reaped(children)


def _case_test_worker_refuses_more_than_sixteen_answers_without_truncating(monkeypatch):
    children, _ = fixture_children(monkeypatch, answers=[f"8.8.8.{i}" for i in range(1, 18)])
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED"
    reaped(children)


def _case_test_worker_refuses_invalid_or_oversized_raw_answers(monkeypatch, answers):
    children, _ = fixture_children(monkeypatch, answers=answers)
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED"
    reaped(children)


def _case_test_mixed_private_answer_remains_visible_to_authoritative_policy(monkeypatch):
    children, _ = fixture_children(monkeypatch, answers=[PUBLIC, "127.0.0.1"])
    assert resolve() == [PUBLIC, "127.0.0.1"]
    called = []
    fetcher = SafeFetcher(resolver=safe_dns.SubprocessResolver(), transport=lambda *a, **k: called.append(a))
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        fetcher.fetch("https://example.invalid/")
    assert caught.exception.code == "UNSAFE_ADDRESS" and called == []
    reaped(children)


def _case_test_invalid_hostname_never_spawns(monkeypatch, hostname):
    monkeypatch.setattr(safe_dns.subprocess, "Popen", lambda *a, **k: unittest.TestCase().fail("must not spawn"))
    with unittest.TestCase().assertRaises(SafeFetchError):
        safe_dns.SubprocessResolver()(hostname, deadline=Deadline(1, time.monotonic))


def _case_test_expired_deadline_does_not_spawn(monkeypatch):
    monkeypatch.setattr(safe_dns.subprocess, "Popen", lambda *a, **k: unittest.TestCase().fail("must not spawn"))
    now = [0.0]; deadline = Deadline(1, lambda: now[0]); now[0] = 1.0
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        safe_dns.SubprocessResolver()("example.invalid", deadline=deadline)
    assert caught.exception.code == "DEADLINE_EXCEEDED"


def _case_test_timeout_kills_and_reaps_real_child_including_slow_drip(monkeypatch, program):
    children, _ = fixture_children(monkeypatch, program=program)
    started = time.monotonic()
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve(0.15)
    assert caught.exception.code == "DEADLINE_EXCEEDED"
    assert time.monotonic() - started < 2
    reaped(children)


def _case_test_oversized_output_is_detected_before_unbounded_capture(monkeypatch):
    children, _ = fixture_children(monkeypatch, program="import os, time; os.write(1,b'x'*500000); time.sleep(60)\n")
    original = safe_dns.os.read
    sizes, chunks = [], []
    def bounded_read(fd, size):
        collector = bool(children) and fd == children[-1].stdout.fileno()
        if collector:
            assert 1 <= size <= safe_dns.MAX_OUTPUT_BYTES + 1 - sum(map(len, chunks))
            sizes.append(size)
        data = original(fd, size)
        if collector:
            chunks.append(data)
        return data
    monkeypatch.setattr(safe_dns.os, "read", bounded_read)
    started = time.monotonic()
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED" and time.monotonic() - started < 2
    # Exclude Popen's private exec-error pipe and measure actual collector bytes.
    assert sizes and sum(map(len, chunks)) == safe_dns.MAX_OUTPUT_BYTES + 1
    reaped(children)


def _case_test_invalid_child_output_is_redacted(monkeypatch, payload):
    children, _ = fixture_children(monkeypatch, program=f"import os; os.write(1, {payload.encode()!r})\n")
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED"
    assert payload not in str(caught.exception) if payload else True
    reaped(children)


def _case_test_worker_failure_does_not_leak_stderr(monkeypatch):
    children, _ = fixture_children(monkeypatch, program="import sys; sys.stderr.write('SECRET-private-path'); sys.exit(3)\n")
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED" and "SECRET" not in str(caught.exception)
    reaped(children)


def _case_test_interrupt_reaps_child_without_converting_cancellation_to_success(monkeypatch):
    children, _ = fixture_children(monkeypatch, program="import time; time.sleep(60)\n")
    class InterruptedSelector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def register(self, *args): pass
        def get_map(self): return {1: True}
        def select(self, *args): raise KeyboardInterrupt
    monkeypatch.setattr(safe_dns.selectors, "DefaultSelector", InterruptedSelector)
    with unittest.TestCase().assertRaises(KeyboardInterrupt):
        resolve()
    reaped(children)


def _case_test_no_cache_and_shared_redirect_deadline(monkeypatch):
    children, _ = fixture_children(monkeypatch)
    deadlines = []
    class Response:
        peer_address = PUBLIC
        tls_verified_hostname = "example.invalid"
        def __init__(self, status, headers): self.status, self.headers, self.closed = status, headers, False
        def read(self, size, *, deadline): return b""
        def close(self): self.closed = True
    first = Response(302, [("Location", "/next")])
    second = Response(200, [("Content-Type", "text/plain"), ("Content-Length", "0")])
    responses = [first, second]
    def transport(request, *, deadline):
        deadlines.append(deadline)
        return responses.pop(0)
    fetcher = SafeFetcher(resolver=safe_dns.SubprocessResolver(), transport=transport, limits=FetchLimits(total_seconds=3))
    assert fetcher.fetch("https://example.invalid/start").body == b""
    assert deadlines[0] is deadlines[1]
    assert len(children) == 2 and first.closed and second.closed
    reaped(children)


def _case_test_spawn_failure_is_fixed_dns_error(monkeypatch):
    def fail(*args, **kwargs): raise OSError("SECRET-spawn-detail")
    monkeypatch.setattr(safe_dns.subprocess, "Popen", fail)
    with unittest.TestCase().assertRaises(SafeFetchError) as caught:
        resolve()
    assert caught.exception.code == "DNS_FAILED" and "SECRET" not in str(caught.exception)


class ResolverTest(unittest.TestCase):
    def setUp(self):
        def denied(*args, **kwargs):
            raise AssertionError("No actual network/DNS allowed in resolver tests")
        self.setattr(socket, "getaddrinfo", denied)
        self.setattr(socket.socket, "connect", denied)
        self.setattr(socket.socket, "connect_ex", denied)

    def setattr(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setenv(self, name, value):
        patcher = patch.dict(os.environ, {name: value})
        patcher.start()
        self.addCleanup(patcher.stop)


def _test_case(function, values):
    def test(self):
        function(self, *values)
    return test

ResolverTest.test_real_child_returns_all_unique_families_and_uses_minimal_environment = _test_case(_case_test_real_child_returns_all_unique_families_and_uses_minimal_environment, ())
ResolverTest.test_worker_refuses_more_than_sixteen_answers_without_truncating = _test_case(_case_test_worker_refuses_more_than_sixteen_answers_without_truncating, ())
for _index, _value in enumerate([[], [PUBLIC] * 129, ["fe80::1%eth0"], ["bad-address"]]):
    setattr(ResolverTest, "test_worker_refuses_invalid_or_oversized_raw_answers_" + str(_index), _test_case(_case_test_worker_refuses_invalid_or_oversized_raw_answers, (_value,)))
ResolverTest.test_mixed_private_answer_remains_visible_to_authoritative_policy = _test_case(_case_test_mixed_private_answer_remains_visible_to_authoritative_policy, ())
for _index, _value in enumerate([None, "", "localhost", "EXAMPLE.invalid", "example.invalid/path",
    "example.invalid:443", "example.invalid\ncommand", "example.invalid;echo", "user@example.invalid",
    "127.0.0.1", "8.8.8.8", "example.invalid.", "x" * 4097]):
    setattr(ResolverTest, "test_invalid_hostname_never_spawns_" + str(_index), _test_case(_case_test_invalid_hostname_never_spawns, (_value,)))
ResolverTest.test_expired_deadline_does_not_spawn = _test_case(_case_test_expired_deadline_does_not_spawn, ())
for _index, _value in enumerate([
    "import time; time.sleep(60)\n",
    "import os, time; os.close(1); time.sleep(60)\n",
    "import os, time\nfor i in range(100):\n os.write(1,b' '); time.sleep(0.03)\n",
]):
    setattr(ResolverTest, "test_timeout_kills_and_reaps_real_child_including_slow_drip_" + str(_index), _test_case(_case_test_timeout_kills_and_reaps_real_child_including_slow_drip, (_value,)))
ResolverTest.test_oversized_output_is_detected_before_unbounded_capture = _test_case(_case_test_oversized_output_is_detected_before_unbounded_capture, ())
for _index, _value in enumerate(["", "not-json", "{}", "[]", "[true]", '["bad-address"]',
    '["93.184.216.34","93.184.216.34"]', '["fe80::1%eth0"]', '["026.1.1.1"]']):
    setattr(ResolverTest, "test_invalid_child_output_is_redacted_" + str(_index), _test_case(_case_test_invalid_child_output_is_redacted, (_value,)))
ResolverTest.test_worker_failure_does_not_leak_stderr = _test_case(_case_test_worker_failure_does_not_leak_stderr, ())
ResolverTest.test_interrupt_reaps_child_without_converting_cancellation_to_success = _test_case(_case_test_interrupt_reaps_child_without_converting_cancellation_to_success, ())
ResolverTest.test_no_cache_and_shared_redirect_deadline = _test_case(_case_test_no_cache_and_shared_redirect_deadline, ())
ResolverTest.test_spawn_failure_is_fixed_dns_error = _test_case(_case_test_spawn_failure_is_fixed_dns_error, ())
