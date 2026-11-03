"""EA-025: whether a OneLake lease has expired is judged by OneLake's clock (the lease file's
Last-Modified against the reply's Date), never by comparing one PC's clock with the expiry another
PC wrote. A PC whose clock runs fast can't take a live lease; one whose clock runs slow still takes
over a lease its holder left to expire."""

from email.utils import formatdate

import pytest
import requests

from fabric_desktop.leases import Leases, OneLakeBackend

KEY = "LH_Gold/dbo.fact_sales"
START = 1_800_000_000.0  # OneLake's clock when the test starts


class Dfs:
    """Conditional lease files, with OneLake's own clock: Last-Modified when a file is written, Date
    on every reply."""

    def __init__(self):
        self.files, self.n, self.now = {}, 0, START

    def _resp(self, status, etag="", content=b"", modified=None):
        r = requests.Response()
        r.status_code, r._content = status, content
        r.headers["Date"] = formatdate(self.now, usegmt=True)
        if etag:
            r.headers["ETag"] = etag
        if modified is not None:
            r.headers["Last-Modified"] = formatdate(modified, usegmt=True)
        return r

    def put(self, url, params=None, headers=None, timeout=None):
        cur = self.files.get(url)
        if headers.get("If-None-Match") == "*" and cur:
            return self._resp(409)
        if headers.get("If-Match") and (not cur or cur[1] != headers["If-Match"]):
            return self._resp(412)
        self.n += 1
        self.files[url] = (b"", f'"{self.n}"', self.now)
        return self._resp(201, f'"{self.n}"')

    def patch(self, url, params=None, data=None, headers=None, timeout=None):
        body, etag, modified = self.files[url]
        if params["action"] == "append":
            self.files[url] = (data, etag, modified)
            return self._resp(202)
        if headers.get("If-Match") and headers["If-Match"] != etag:
            return self._resp(412)
        self.n += 1
        self.files[url] = (body, f'"{self.n}"', self.now)
        return self._resp(200, f'"{self.n}"')

    def get(self, url, headers=None, timeout=None):
        cur = self.files.get(url)
        return self._resp(200, cur[1], cur[0], cur[2]) if cur else self._resp(404)

    def delete(self, url, headers=None, timeout=None):
        cur = self.files.get(url)
        if not cur or cur[1] != headers.get("If-Match"):
            return self._resp(412)
        del self.files[url]
        return self._resp(200)


def pc(dfs, owner, skew):
    """A PC whose clock is ``skew`` seconds ahead of OneLake's (behind, when negative)."""
    return Leases(OneLakeBackend("ws", "meta-lh", lambda: "tok", session=dfs), owner, ttl=120, wait=0,
                  clock=lambda: dfs.now + skew)


def test_a_pc_whose_clock_runs_fast_cant_take_a_live_lease():
    dfs = Dfs()
    assert pc(dfs, "a", 0).try_acquire(KEY)
    dfs.now += 10  # the lease is 10 s old, with 110 s to go
    assert pc(dfs, "b", 300).try_acquire(KEY) is None


def test_a_pc_whose_clock_runs_slow_still_takes_over_an_expired_lease():
    dfs = Dfs()
    assert pc(dfs, "a", 0).try_acquire(KEY)  # and then a never renews it: its PC went to sleep
    dfs.now += 130
    assert pc(dfs, "c", -300).try_acquire(KEY)


def test_a_lease_written_by_a_pc_with_a_slow_clock_is_still_live():
    dfs = Dfs()
    assert pc(dfs, "a", -300).try_acquire(KEY)  # its body says it expired 180 s ago
    dfs.now += 10
    assert pc(dfs, "b", 0).try_acquire(KEY) is None


def test_a_renewal_counts_from_when_onelake_wrote_it():
    dfs = Dfs()
    a = pc(dfs, "a", 0)
    etag = a.try_acquire(KEY)
    dfs.now += 100
    assert a.backend.replace(KEY, a._body(), etag)  # renewed at 100 s
    dfs.now += 100  # 200 s after the lease was taken, 100 s after its renewal
    assert pc(dfs, "b", 300).try_acquire(KEY) is None
    dfs.now += 30
    assert pc(dfs, "b", 300).try_acquire(KEY)


def test_without_a_date_from_onelake_the_body_decides_as_before():
    dfs = Dfs()
    real = dfs._resp

    def no_date(*a, **k):
        r = real(*a, **k)
        del r.headers["Date"]
        return r
    dfs._resp = no_date
    assert pc(dfs, "a", 0).try_acquire(KEY)
    dfs.now += 130
    assert pc(dfs, "b", 0).try_acquire(KEY)


# ---- the mailbox's grace periods (a half-written request, a run Fabric couldn't look up)

def test_a_control_files_age_is_on_onelakes_clock():
    from fabric_desktop.leases import Stored
    from fabric_desktop.mailbox import _age
    # Written 10 s ago by OneLake's clock; this PC's clock is 300 s fast.
    assert _age(Stored(b"", "e", START - 10, START), START + 300) == 10
    # A folder on this PC: its own clock.
    assert _age(Stored(b"", "e", START - 10), START) == 10


def test_a_half_written_request_in_onelake_gets_its_grace_on_a_fast_pc():
    from fabric_desktop.mailbox import OneLakeControl, WRITING_GRACE, _age
    dfs = Dfs()
    control = OneLakeControl("ws", "meta-lh", lambda: "tok", session=dfs)
    assert control.create("requests/r1.json", b'{"env": "de')
    dfs.now += 1
    stored = control.read("requests/r1.json")
    assert _age(stored, dfs.now + 300) < WRITING_GRACE  # still being written, perhaps: look again later


@pytest.mark.parametrize("ttl", ["NaN", "Infinity", "-Infinity", 0, -5, 1e400])
def test_a_lease_length_that_isnt_a_positive_number_is_this_writers(ttl):
    import json as _json
    from fabric_desktop.leases import Stored
    leases = pc(Dfs(), "b", 0)
    body = '{"owner": "a", "expires": 1, "ttl": %s}' % (_json.dumps(ttl) if not isinstance(ttl, str) else ttl)
    assert leases._length(Stored(body.encode(), "e", START, START)) == leases.ttl
