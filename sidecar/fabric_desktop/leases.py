"""Per-table write leases, so two writers never read-merge-overwrite the same table at once.

The metadata engine writes a table by reading it, merging new rows in memory and committing
the result. Two writers doing that at once can each miss the other's rows. A lease makes the
second writer wait: before running an entity, a worker creates a small lease file for its
table, keeps renewing it while it works, and deletes it when done. A worker that dies stops
renewing; once the lease expires, the next writer takes it over.

Every OneLake table write also holds a lease in the table's own lakehouse (``table_lease``), so a
notebook or job writing a table and a metadata run writing the same table wait for each other.

For an environment in OneLake the lease files live in its ``LH_Meta`` lakehouse under
``Files/_fabric_desktop/leases/`` and every change is conditional (``If-None-Match: *`` to
create, ``If-Match: <etag>`` to renew, take over or delete), so agents on different machines
see the same leases with no extra service. For a local environment they are files in its
``.local`` folder.

A writer checks its leases right before it commits (``check_held``): a lease that was taken over,
or that went unrenewed long enough that it may expire during the commit, stops the write with
nothing written (HD-016).

Expiry times are the holder's clock, so keep agent clocks in sync (NTP); the lease length
(default 120 s) is far longer than normal clock drift.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import socket
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import requests

from . import http_session
from .onelake import DFS_HOST, _onelake_error
from .redact import describe
from .retrying import retry_after, send_with_retry

_log = logging.getLogger(__name__)

LEASE_DIR = "Files/_fabric_desktop/leases"

_HELD: ContextVar[tuple["Held", ...]] = ContextVar("held_leases", default=())  # the leases this code is inside


class LeaseBusy(RuntimeError):
    """Another writer holds the table. Worth retrying later."""


class LeaseLost(RuntimeError):
    """This writer's lease expired or was taken over while it worked."""


@dataclass
class Stored:
    data: bytes
    etag: str
    modified: float  # epoch seconds


class Backend(Protocol):
    def create(self, key: str, data: bytes) -> str | None: ...           # new etag, or None if it exists
    def read(self, key: str) -> Stored | None: ...
    def replace(self, key: str, data: bytes, etag: str) -> str | None: ...  # new etag, or None if it changed
    def delete(self, key: str, etag: str) -> bool: ...


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """An exclusive lock on ``path`` held across processes (the app, scheduled runs, agents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for about 10 s, then raises
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _while_shared(change: Callable[[], Any], tries: int = 50) -> None:
    """Replace or delete a lease file. On Windows that fails (WinError 32) while another writer is
    reading the file, which writers waiting for the lease do all the time; it is retried, for up to
    about a second, so the lease is let go rather than left to expire."""
    for attempt in range(tries):
        try:
            change()
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.02)


class LocalBackend:
    """Lease files in a folder on this machine (local environments, and tests)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        # Read, compare and replace must be one step for every process on the machine, or two of
        # them taking over the same expired lease both win. The thread lock keeps threads of one
        # process off the file lock, which on Windows is per process.
        self._lock = threading.Lock()

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._lock, _file_lock(self.folder / ".lock"):
            yield

    def _path(self, key: str) -> Path:
        return self.folder / (key.replace("/", "__") + ".json")

    @staticmethod
    def _etag(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()

    def create(self, key: str, data: bytes) -> str | None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and linked into place, so nobody ever reads the file half written.
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return None
        except OSError:  # a file system without hard links: create in place
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return None
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        finally:
            tmp.unlink(missing_ok=True)
        return self._etag(data)

    def read(self, key: str) -> Stored | None:
        path = self._path(key)
        for attempt in range(10):
            try:
                data = path.read_bytes()
                return Stored(data, self._etag(data), path.stat().st_mtime)
            except FileNotFoundError:
                return None
            except PermissionError:  # Windows: another writer is deleting or replacing it right now
                if attempt == 9:
                    raise
                time.sleep(0.02)
        return None

    def replace(self, key: str, data: bytes, etag: str) -> str | None:
        with self._exclusive():
            now = self.read(key)
            if now is None or now.etag != etag:
                return None
            tmp = self._path(key).with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data)
            _while_shared(lambda: os.replace(tmp, self._path(key)))
            return self._etag(data)

    def delete(self, key: str, etag: str) -> bool:
        with self._exclusive():
            now = self.read(key)
            if now is None or now.etag != etag:
                return False
            _while_shared(lambda: self._path(key).unlink(missing_ok=True))
            return True


# A lease change OneLake throttles (429) did nothing, and every change is conditional, so it is sent
# again: it can't take a lease from anyone. A resent create or flush that had in fact gone through gets
# 409 or 412 (None, a lost race), a resent release gets 404 (False); an append isn't conditional, but
# it only takes effect at the If-Match flush. Waits are short and few, so a long throttle fails while
# the lease is still held instead of outliving it: one change (a create, renewal, take-over or
# release) stops retrying once three request timeouts (a third of the lease) have gone by. A 5xx or
# a dropped connection is still sent once.
THROTTLE_ATTEMPTS = 3
THROTTLE_WAIT_CAP = 5.0


class OneLakeBackend:
    """Lease files in a lakehouse's Files area, changed only with ADLS Gen2 conditional requests."""

    def __init__(self, workspace_id: str, lakehouse_id: str, token: Callable[[], str],
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 60, clock: Callable[[], float] = time.monotonic):
        self.workspace_id, self.lakehouse_id = workspace_id, lakehouse_id
        # Per request; keep it at a ninth of the lease's length or less (lease_timeout). A renewal is
        # three requests, and its throttling retries stop once 3 x timeout (a third of the lease) has
        # gone by, so a renewal ends within about two thirds of the lease: one that can't get through
        # fails while the lease is still held, never late at commit.
        self.timeout = timeout
        self._token = token
        self._http = session or http_session.session()
        self._sleep = sleep
        self._clock = clock

    def _deadline(self) -> float:
        return self._clock() + 3 * self.timeout

    def _url(self, key: str) -> str:
        return f"https://{DFS_HOST}/{self.workspace_id}/{self.lakehouse_id}/{LEASE_DIR}/{key}.json"

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}", "x-ms-version": "2023-11-03"} | extra

    def _change(self, send: Callable[[], requests.Response], deadline: float) -> requests.Response:
        """Send one request of a lease change; wait out a 429 (OneLake did nothing) while the
        change's deadline allows. Anything else, a 5xx included, is the answer."""
        for attempt in range(THROTTLE_ATTEMPTS):
            resp = send()
            if resp.status_code != 429 or attempt == THROTTLE_ATTEMPTS - 1:
                return resp
            wait = min(retry_after(resp.headers, 2.0), THROTTLE_WAIT_CAP)
            if self._clock() + wait >= deadline:
                return resp  # no time left in this change: fail while the lease is still held
            close = getattr(resp, "close", None)
            if callable(close):
                close()
            self._sleep(wait)
        raise AssertionError("unreachable")

    def _write(self, key: str, data: bytes, condition: dict[str, str]) -> str | None:
        url = self._url(key)
        what = f"write the lease for {key}"
        deadline = self._deadline()
        resp = self._change(lambda: self._http.put(url, params={"resource": "file"},
                                                          headers=self._headers(**condition), timeout=self.timeout), deadline)
        if resp.status_code in (409, 412):
            return None  # it exists already (create), or changed since we read it (replace)
        if resp.status_code >= 400:
            raise _onelake_error(resp, what)
        etag = resp.headers.get("ETag", "")
        resp = self._change(lambda: self._http.patch(url, params={"action": "append", "position": "0"},
                                                     data=data, headers=self._headers(), timeout=self.timeout), deadline)
        if resp.status_code >= 400:
            raise _onelake_error(resp, what)
        resp = self._change(lambda: self._http.patch(
            url, params={"action": "flush", "position": str(len(data))},
            headers=self._headers(**({"If-Match": etag} if etag else {})), timeout=self.timeout), deadline)
        if resp.status_code == 412:
            return None  # someone replaced it between our create and flush
        if resp.status_code >= 400:
            raise _onelake_error(resp, what)
        return resp.headers.get("ETag", etag)

    def create(self, key: str, data: bytes) -> str | None:
        return self._write(key, data, {"If-None-Match": "*"})

    def read(self, key: str) -> Stored | None:
        # A read is retried on 429/5xx; a change only on 429 (_change), and a lost race is None.
        resp = send_with_retry(lambda: self._http.get(self._url(key), headers=self._headers(), timeout=self.timeout),
                               "GET", sleep=self._sleep)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise _onelake_error(resp, f"read the lease for {key}")
        try:
            modified = parsedate_to_datetime(resp.headers["Last-Modified"]).timestamp()
        except (KeyError, TypeError, ValueError):
            modified = time.time()
        return Stored(resp.content, resp.headers.get("ETag", ""), modified)

    def replace(self, key: str, data: bytes, etag: str) -> str | None:
        return self._write(key, data, {"If-Match": etag})

    def delete(self, key: str, etag: str) -> bool:
        resp = self._change(lambda: self._http.delete(self._url(key), headers=self._headers(**{"If-Match": etag}),
                                                      timeout=self.timeout), self._deadline())
        if resp.status_code in (404, 409, 412):
            return False
        if resp.status_code >= 400:
            raise _onelake_error(resp, f"release the lease for {key}")
        return True


class Leases:
    def __init__(self, backend: Backend, owner: str, ttl: float = 120, wait: float = 120,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.backend, self.owner, self.ttl, self.wait = backend, owner, ttl, wait
        self.clock, self.sleep = clock, sleep

    def _body(self, now: float | None = None) -> bytes:
        """A lease body that expires ``ttl`` after ``now``. The holder passes the time it started the
        write, and counts the lease as renewed from that same time, never from when the write ended:
        a slow write then can't make the holder think the lease runs longer than the file says."""
        now = self.clock() if now is None else now
        return json.dumps({"owner": self.owner, "host": socket.gethostname(), "pid": os.getpid(),
                           "acquired_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                           "expires": now + self.ttl}).encode()

    def _expires(self, stored: Stored) -> tuple[float, str]:
        try:
            body = json.loads(stored.data)
            return float(body["expires"]), str(body.get("owner", "?"))
        except (ValueError, KeyError, TypeError):
            # Empty while its writer is between create and flush: treat it as fresh.
            return stored.modified + self.ttl, "?"

    def try_acquire(self, key: str, now: float | None = None) -> str | None:
        """The new lease's etag, or None while someone else holds a live lease. Its body expires
        ``ttl`` after ``now`` (the time the caller started; now by default)."""
        now = self.clock() if now is None else now
        etag = self.backend.create(key, self._body(now))
        if etag:
            return etag
        stored = self.backend.read(key)
        if stored is None:
            return self.backend.create(key, self._body(now))  # released just now
        expires, _ = self._expires(stored)
        if expires < self.clock():
            return self.backend.replace(key, self._body(now), stored.etag)  # take over an expired lease
        return None

    def holder(self, key: str) -> str:
        stored = self.backend.read(key)
        return self._expires(stored)[1] if stored else ""

    def _holder_and_host(self, key: str) -> str:
        """The holder for a busy message, with its machine: every desktop's owner is "KratosForge"."""
        stored = self.backend.read(key)
        if not stored:
            return ""
        owner = self._expires(stored)[1]
        try:
            host = json.loads(stored.data).get("host")
        except (ValueError, AttributeError):
            host = None
        return f"{owner} on {host}" if host else owner

    @contextmanager
    def hold(self, key: str) -> Iterator["Held"]:
        """Hold ``key`` for the block: wait up to ``wait`` seconds for it, renew it every third of
        its length, release it after. Raises LeaseBusy when it stays taken."""
        deadline = self.clock() + self.wait
        delay = 1.0
        while (etag := self.try_acquire(key, started := self.clock())) is None:
            if self.clock() >= deadline:
                raise LeaseBusy(f"{key} is being written by {self._holder_and_host(key) or 'another writer'}; "
                                f"will try again later")
            self.sleep(delay)
            delay = min(delay * 2, 15)
        held = Held(self, key, etag, started)
        held.start()
        token = _HELD.set(_HELD.get() + (held,))
        try:
            yield held
        finally:
            _HELD.reset(token)
            held.stop()
        if held.lost:
            raise LeaseLost(f"The lease on {key} expired while writing, so another writer may have overlapped. "
                            f"Check the table's history before running it again.")


class Held:
    def __init__(self, leases: Leases, key: str, etag: str, renewed: float | None = None):
        self.leases, self.key, self.etag = leases, key, etag
        self.lost = False
        # When the lease's current expiry was counted from: the start of the write that set it.
        self._renewed = leases.clock() if renewed is None else renewed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._renew, daemon=True, name=f"lease-{key}")

    def start(self) -> None:
        self._thread.start()

    def check(self) -> None:
        """Raises LeaseLost unless the lease is still ours with time to commit: it wasn't taken over,
        and it was renewed within the last two thirds of its length (the renewer tries every third)."""
        if self.lost or self.leases.clock() - self._renewed >= self.leases.ttl * 2 / 3:
            raise LeaseLost(f"The lease on {self.key} couldn't be renewed, so another writer could take it over "
                            f"during the commit. Nothing was written; run it again.")

    def _renew(self) -> None:
        while not self._stop.wait(self.leases.ttl / 3):
            started = self.leases.clock()
            try:
                etag = self.leases.backend.replace(self.key, self.leases._body(started), self.etag)
            except Exception:  # a network blip: try again at the next beat while the lease has time
                if self.leases.clock() - self._renewed >= self.leases.ttl:
                    # Unrenewed past its length: another writer may have taken it over by now.
                    self.lost = True
                    return
                continue
            if etag is None:
                self.lost = True
                return
            self.etag, self._renewed = etag, started  # the file expires ttl after started, not after now

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)
        if not self.lost:
            try:
                self.leases.backend.delete(self.key, self.etag)
            except Exception as exc:  # it expires on its own
                _log.warning("Couldn't release the lease; it expires on its own: %s", describe(exc))


def check_held() -> None:
    """Call right before a commit: raises LeaseLost if any lease the caller is inside may no longer be
    its own (Held.check). Outside any lease it does nothing."""
    for held in _HELD.get():
        held.check()


def table_key(lakehouse: str, schema: str, table: str) -> str:
    return f"{lakehouse}/{schema}.{table}"


# Every writer of a OneLake table (a notebook's write_table, a job's publish, a metadata run on this PC
# or an agent) also holds this lease, kept in the table's own lakehouse, so writers that know only
# the table, not its metadata project, still see each other. Metadata runs hold their LH_Meta
# lease (table_key) first and this one second; nothing takes them the other way round.
# A writer that dies mid-write (a killed kernel) leaves its lease for at most TABLE_LEASE_TTL; the next
# writer waits a little longer than that, so it takes the abandoned lease over instead of failing.
TABLE_LEASE_TTL = 60
TABLE_LEASE_WAIT = 90


def table_lease(backend: Backend, schema: str, table: str, owner: str, wait: float | None = None,
                ttl: float | None = None) -> AbstractContextManager["Held"]:
    """Hold the write lease on ``schema.table`` in the lakehouse ``backend`` keeps its files in."""
    return Leases(backend, owner=owner, ttl=TABLE_LEASE_TTL if ttl is None else ttl,
                  wait=TABLE_LEASE_WAIT if wait is None else wait).hold(f"{schema}.{table}")


def lease_timeout(ttl: float) -> float:
    """The per-request timeout for a lease ``ttl`` seconds long (see OneLakeBackend)."""
    return min(60.0, ttl / 9)


def onelake_table_lease(workspace_id: str, lakehouse_id: str, schema: str, table: str,
                        token: Callable[[], str], owner: str, ttl: float | None = None,
                        **kw: Any) -> AbstractContextManager["Held"]:
    ttl = TABLE_LEASE_TTL if ttl is None else ttl
    backend = OneLakeBackend(workspace_id, lakehouse_id, token, timeout=lease_timeout(ttl))
    return table_lease(backend, schema, table, owner, ttl=ttl, **kw)
