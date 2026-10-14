"""The worker stopping (a deploy, a restart): its long jobs end at their next step instead of running on
until the process is killed (BHA-51).

arq cancels a running job when the worker is told to stop and puts it back in the queue, but the thread
doing the work can't be cancelled: it would poll, build or import on, holding its lock, until the process
was killed, and the next worker would find the work locked for as long as the lock had left (twenty
minutes for a card build). So the loops look at `stopping` between steps and wait with `sleep`, which
wakes when it's set; the worker sets it on shutdown (worker.wind_down), then frees whatever locks are still
held (bus.release_held), and takes no new ones (bus.try_lock).
"""

from __future__ import annotations

import threading

stopping = threading.Event()


def sleep(seconds: float) -> bool:
    """Wait `seconds`, or less if the worker is stopping. Returns whether it's still running."""
    return not stopping.wait(max(0.0, seconds))
