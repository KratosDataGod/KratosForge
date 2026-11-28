"""kernel_proc.py's Job Object list at its exact edge (mutation batch 163, a boundary re-score of batch
21, docs/quality/mutation-testing.md): a failed read whose count would have fitted is another failure,
so the list isn't read again."""

from fabric_desktop import kernel_proc


def test_a_failed_read_whose_count_exactly_fits_is_not_read_again(monkeypatch):
    calls = []

    class Kernel32:
        @staticmethod
        def QueryInformationJobObject(job, kind, buffer, size, _returned):  # noqa: N802
            listed = buffer._obj
            calls.append(len(listed.ProcessIdList))
            if len(calls) > 1:  # a second read would succeed, so reading again would be seen
                listed.NumberOfProcessIdsInList = 1
                listed.ProcessIdList[0] = 42
                return True
            listed.NumberOfAssignedProcesses = len(listed.ProcessIdList)  # exactly the room there was
            return False

    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: Kernel32)
    assert kernel_proc._job_pids(1) is None
    assert calls == [kernel_proc.JOB_PIDS]


class _Proc:
    """A process that is alive for ``rounds`` checks of the watcher."""

    def __init__(self, rounds: int):
        self.rounds, self.pid = rounds, 4242

    def poll(self):
        self.rounds -= 1
        return None if self.rounds >= 0 else 0


def _watched(monkeypatch, *, rounds=1, now=0.0, memory=0):
    k = kernel_proc.ProcessKernel()
    proc = _Proc(rounds)
    k._proc, k.pid = proc, proc.pid
    ends, asked = [], []
    monkeypatch.setattr(kernel_proc.time, "sleep", lambda s: None)
    monkeypatch.setattr(kernel_proc.time, "monotonic", lambda: now)
    monkeypatch.setattr(kernel_proc, "awake_monotonic", lambda: now)  # the time limit's clock
    monkeypatch.setattr(k, "_memory", lambda p, pid: memory)
    monkeypatch.setattr(k, "_end", lambda p, why: ends.append(why))
    monkeypatch.setattr(k, "_request", lambda what, **kw: asked.append(what))
    return k, proc, ends, asked


def test_memory_of_exactly_the_limit_is_still_allowed(monkeypatch):
    k, proc, ends, _ = _watched(monkeypatch, memory=1000)
    k.memory_limit = 1000
    k._watch(proc)
    assert ends == [] and k.limit_hit == ""
    k, proc, ends, _ = _watched(monkeypatch, memory=1001)
    k.memory_limit = 1000
    k._watch(proc)
    assert len(ends) == 1 and k.limit_hit == "memory"


def test_a_cell_at_exactly_its_time_limit_is_not_interrupted_yet(monkeypatch):
    k, proc, ends, asked = _watched(monkeypatch, now=30.0)
    k.time_limit, k._running, k._started_at = 30, True, 0.0
    k._watch(proc)
    assert asked == [] and k.limit_hit == "" and k._interrupted_at is None
    k, proc, ends, asked = _watched(monkeypatch, now=30.5)
    k.time_limit, k._running, k._started_at = 30, True, 0.0
    k._watch(proc)
    assert asked == ["interrupt"] and k.limit_hit == "time" and k._interrupted_at == 30.5


def test_a_cell_is_ended_only_after_more_than_the_grace_since_its_interrupt(monkeypatch):
    grace = kernel_proc.TIME_GRACE_SECONDS
    k, proc, ends, _ = _watched(monkeypatch, now=100.0 + grace)
    k.time_limit, k._running, k._started_at, k._interrupted_at = 30, True, 0.0, 100.0
    k._watch(proc)
    assert ends == []
    k, proc, ends, _ = _watched(monkeypatch, now=100.5 + grace)
    k.time_limit, k._running, k._started_at, k._interrupted_at = 30, True, 0.0, 100.0
    k._watch(proc)
    assert len(ends) == 1 and "didn't stop when interrupted" in ends[0]


def _clock_at_deadline(monkeypatch):
    """time.monotonic as 0 first, then exactly 5 s later for good; time.sleep counted, and stopped after
    a few, so a wait that goes on at its deadline fails the test instead of hanging it."""
    times = iter([0.0])
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) > 3:
            raise AssertionError("still waiting at the deadline")

    monkeypatch.setattr(kernel_proc.time, "monotonic", lambda: next(times, 5.0))
    monkeypatch.setattr(kernel_proc.time, "sleep", sleep)
    return slept


def test_stop_waits_for_the_reader_only_until_its_deadline(monkeypatch):
    class Proc:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    class Tree:
        def kill(self):
            pass

    k = kernel_proc.ProcessKernel()
    k._proc, k._tree = Proc(), Tree()  # the reader never marks it gone
    slept = _clock_at_deadline(monkeypatch)
    assert k.stop() == {"stopped": True}
    assert slept == []  # at exactly the deadline, no more waiting


def test_a_cell_that_ends_its_process_at_once_is_waited_for_only_until_the_deadline(monkeypatch):
    k = kernel_proc.ProcessKernel()

    def gone(what, **kw):
        raise kernel_proc.KernelGone("gone")

    monkeypatch.setattr(k, "_sync_lakehouse", lambda: None)
    monkeypatch.setattr(k, "_request", gone)
    monkeypatch.setattr(k, "poll", lambda *a: {"status": "error"})
    slept = _clock_at_deadline(monkeypatch)
    assert k.start("import os; os._exit(1)", time_limit=False) == {"status": "error"}
    assert slept == []
