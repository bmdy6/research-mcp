"""Opt-in, process-bounded DNS dependency for SafeFetcher; no default wiring.

System getaddrinfo has no cancellable Python thread API. Run only that operation
in an isolated child, cap its output, and kill/reap it on deadline/cancellation.
The policy layer still validates ALL returned addresses before opening sockets.
"""
from __future__ import annotations

import ipaddress
import json
import os
import selectors
import subprocess
import sys

from research_mcp.safe_fetch import Deadline, SafeFetchError, _url

MAX_OUTPUT_BYTES = 1024
CLEANUP_SECONDS = 1.0

# Constant program, never assembled from hostname or caller-controlled commands.
# Only standard library modules are loaded (-I -S); no inherited Python paths,
# site hooks, provider keys, proxies or user-home configuration is supplied.
_WORKER = r'''
import ipaddress, json, os, socket, sys
try:
    rows = socket.getaddrinfo(sys.argv[1], 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    if not rows or len(rows) > 128:
        raise ValueError()
    addresses = []
    for row in rows:
        if row[0] not in (socket.AF_INET, socket.AF_INET6) or '%' in row[4][0]:
            raise ValueError()
        address = str(ipaddress.ip_address(row[4][0]))
        if address not in addresses:
            addresses.append(address)
        if len(addresses) > 16:
            raise ValueError()
    payload = json.dumps(addresses, separators=(',', ':')).encode('ascii')
    if not addresses or len(payload) > 1024:
        raise ValueError()
    os.write(1, payload)
except Exception:
    sys.exit(2)
'''


def _hostname(value):
    if not isinstance(value, str):
        raise SafeFetchError("DNS_FAILED")
    try:
        _canonical, host, literal = _url("https://" + value + "/")
    except SafeFetchError:
        raise SafeFetchError("DNS_FAILED") from None
    if host != value or literal is not None:
        raise SafeFetchError("DNS_FAILED")
    return host


def _parse_answers(raw):
    try:
        answers = json.loads(raw)
        if (not isinstance(answers, list) or not 1 <= len(answers) <= 16
                or any(not isinstance(address, str) or '%' in address for address in answers)
                or len(set(answers)) != len(answers)):
            raise ValueError
        if any(str(ipaddress.ip_address(address)) != address for address in answers):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise SafeFetchError("DNS_FAILED") from None
    # Do not filter private addresses here: dropping one would turn a mixed DNS
    # answer into an apparently safe one. SafeFetcher rejects the entire set.
    return answers


class SubprocessResolver:
    """POSIX resolver adapter. Constructing/importing it performs no I/O.

    The caller must pass the same Deadline used by SafeFetcher for the entire
    request. Cleanup is bounded separately; process creation and OS scheduling
    are not a hard-real-time guarantee. No thread is abandoned in the background.
    """

    def __call__(self, hostname: str, *, deadline: Deadline) -> list[str]:
        hostname = _hostname(hostname)
        if not isinstance(deadline, Deadline):
            raise TypeError("A shared Deadline is required")
        deadline.remaining()  # expired requests must not create a process
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", _WORKER, hostname],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"LANG": "C", "LC_ALL": "C"}, cwd="/", close_fds=True,
            )
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    for key, _events in selector.select(deadline.remaining()):
                        # Never communicate() into an unbounded in-memory buffer.
                        chunk = os.read(key.fd, min(4096, MAX_OUTPUT_BYTES + 1 - len(output)))
                        deadline.remaining()
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            raise SafeFetchError("DNS_FAILED")
            code = process.wait(timeout=deadline.remaining())
            deadline.remaining()
            if code != 0:
                raise SafeFetchError("DNS_FAILED")
            return _parse_answers(bytes(output))
        except (subprocess.TimeoutExpired, TimeoutError):
            raise SafeFetchError("DEADLINE_EXCEEDED") from None
        except SafeFetchError:
            raise
        except Exception:
            raise SafeFetchError("DNS_FAILED") from None
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=CLEANUP_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    # Never claim successful resolution if child cleanup failed.
                    raise SafeFetchError("DNS_FAILED") from None
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
