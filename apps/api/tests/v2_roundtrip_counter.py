"""Count PostgreSQL round trips on the wire, for the V2 latency budget.

The hosted API talks to its database across a continent (Render, Oregon → Supabase, São Paulo),
so every time the driver waits for the server costs about 180 ms whatever the query does. The
number worth budgeting is therefore not "statements" but "times the client waited", and the
only place that number is unambiguous is the wire.

:class:`RoundTripProxy` is a loopback TCP proxy between the API's pool and the disposable test
cluster. It forwards bytes unchanged and parses the frontend (client → server) message stream,
counting each point at which the client asks the server to answer and then waits:

* a simple-protocol **Query** (``Q``): psycopg's own ``BEGIN``, ``COMMIT`` and ``ROLLBACK``
  outside pipeline mode, ``DEALLOCATE ALL``, and the pool's health check (an empty query,
  counted again under ``health_checks``);
* a **Sync** (``S``): every extended-protocol statement outside pipeline mode, and every
  pipeline synchronisation — psycopg's ``BEGIN`` in pipeline mode, ``Pipeline.sync()``, the
  pipeline exit;
* a **Flush** (``H``) that does not directly follow a Sync: a result fetched inside a pipeline
  block. psycopg's pipeline exit sends Sync then Flush and waits once, so that Flush is free.

Nothing is inferred from the driver's internals, so the count stays true when psycopg changes.
Connection setup is counted apart (``connects``, ``connect_round_trips``): over the internet a
new connection also pays the TCP and TLS handshakes, which a loopback proxy cannot see. The
proxy answers an ``SSLRequest`` or ``GSSENCRequest`` itself with ``N`` so the stream stays
readable; the client then speaks plaintext to the loopback cluster, as the test DSNs already
allow.

Every count is of protocol messages, never of time, so the numbers are deterministic.
"""

from __future__ import annotations

import socket
import struct
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

_SSL_REQUEST = 80877103
_GSSENC_REQUEST = 80877104
_CANCEL_REQUEST = 80877102


@dataclass
class Measurement:
    """What one measured block sent, filled in when the block ends."""

    round_trips: int = 0
    statements: int = 0
    health_checks: int = 0
    connects: int = 0
    connect_round_trips: int = 0
    checkouts: int = 0
    queries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "round_trips": self.round_trips,
            "statements": self.statements,
            "health_checks": self.health_checks,
            "connects": self.connects,
            "connect_round_trips": self.connect_round_trips,
            "checkouts": self.checkouts,
        }


class RoundTripProxy:
    """Forward ``127.0.0.1:<port>`` to ``upstream`` and count the client's round trips."""

    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self._upstream = (upstream_host, upstream_port)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._lock = threading.Lock()
        self._totals: Counter[str] = Counter()
        self._query_log: list[str] = []
        self._closed = False
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept_loop, name="rtt-proxy-accept", daemon=True).start()

    # ------------------------------------------------------------------ public

    @classmethod
    def for_dsn(cls, dsn: str) -> "RoundTripProxy":
        parts = urlsplit(dsn)
        return cls(parts.hostname or "127.0.0.1", parts.port or 5432)

    def dsn(self, dsn: str) -> str:
        """`dsn` with its host and port replaced by this proxy's loopback address."""
        parts = urlsplit(dsn)
        userinfo, _, _ = parts.netloc.rpartition("@")
        netloc = f"{userinfo}@127.0.0.1:{self.port}" if userinfo else f"127.0.0.1:{self.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def count_checkout(self) -> None:
        with self._lock:
            self._totals["checkouts"] += 1

    def snapshot(self) -> Counter[str]:
        with self._lock:
            return Counter(self._totals)

    @contextmanager
    def measure(self) -> Iterator[Measurement]:
        """Count everything sent between entering and leaving the block."""
        before = self.snapshot()
        with self._lock:
            log_start = len(self._query_log)
        result = Measurement()
        try:
            yield result
        finally:
            after = self.snapshot()
            for name in ("round_trips", "statements", "health_checks", "connects",
                         "connect_round_trips", "checkouts"):
                setattr(result, name, after[name] - before[name])
            with self._lock:
                result.queries = list(self._query_log[log_start:])

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass
        for sock in list(self._sockets):
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ plumbing

    def _bump(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._totals[name] += n

    def _log(self, text: str) -> None:
        with self._lock:
            self._query_log.append(text)

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            self._sockets.append(client)
            threading.Thread(target=self._serve, args=(client,), name="rtt-proxy-conn", daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        try:
            head = _recv_exact(client, 8)
            length, code = struct.unpack("!ii", head)
            while code in (_SSL_REQUEST, _GSSENC_REQUEST):
                client.sendall(b"N")  # no encryption: the stream must stay readable
                head = _recv_exact(client, 8)
                length, code = struct.unpack("!ii", head)
            startup = head + _recv_exact(client, length - 8)
            upstream = socket.create_connection(self._upstream)
            self._sockets.append(upstream)
            if code != _CANCEL_REQUEST:
                self._bump("connects")
                self._bump("connect_round_trips")
            upstream.sendall(startup)
            threading.Thread(target=_pump, args=(upstream, client), name="rtt-proxy-down", daemon=True).start()
            self._pump_up(client, upstream)
        except (OSError, ConnectionError):
            pass
        finally:
            for sock in (client,):
                try:
                    sock.close()
                except OSError:
                    pass

    def _pump_up(self, client: socket.socket, upstream: socket.socket) -> None:
        """Forward client → server, counting each message that makes the client wait."""
        buffer = b""
        authenticating = True
        previous = b""
        while True:
            chunk = client.recv(65536)
            if not chunk:
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            buffer += chunk
            # Count every complete message in the buffer before forwarding the bytes, so a
            # measurement that ends after the server answered has always seen its request.
            while len(buffer) >= 5:
                kind = buffer[:1]
                (length,) = struct.unpack("!i", buffer[1:5])
                if len(buffer) < 1 + length:
                    break
                body = buffer[5:1 + length]
                buffer = buffer[1 + length:]
                if authenticating and kind == b"p":
                    self._bump("connect_round_trips")
                    previous = kind
                    continue
                authenticating = False
                if kind == b"Q":
                    text = body.rstrip(b"\x00").decode("utf-8", "replace").strip()
                    self._bump("round_trips")
                    if text:
                        self._bump("statements")
                    else:
                        self._bump("health_checks")
                    self._log(text or "<health check>")
                elif kind == b"P":
                    # Parse: name\0 query\0 ...
                    _, _, rest = body.partition(b"\x00")
                    self._log(rest.split(b"\x00", 1)[0].decode("utf-8", "replace").strip())
                elif kind == b"E":
                    self._bump("statements")
                elif kind == b"S":
                    self._bump("round_trips")
                elif kind == b"H" and previous != b"S":
                    self._bump("round_trips")
                previous = kind
            upstream.sendall(chunk)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            raise ConnectionError("peer closed")
        data += part
    return data


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            sink.sendall(chunk)
    except OSError:
        pass
    finally:
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
