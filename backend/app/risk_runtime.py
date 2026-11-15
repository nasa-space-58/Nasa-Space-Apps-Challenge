"""Bounded per-worker risk execution and exact physics reuse for the API."""

from .physics_cache import PhysicsCache


class RiskBusy(RuntimeError):
    """The bounded request queue is full."""


class RiskClosed(RuntimeError):
    """The owning worker has shut down or changed process."""


class BoundedPhysicsCache(PhysicsCache):
    """Byte-bounded immutable payloads, isolated copies, explicit invalidation."""

    def __init__(self, *, enabled=True, max_entries=128,
                 max_bytes=32 * 1024 * 1024, revision="r6-runtime-v1"):
        raise NotImplementedError

    def clear(self, *, revision=None):
        """Discard all entries, optionally selecting a new model namespace."""
        raise NotImplementedError


class RiskRuntime:
    """One computation worker, bounded admission, cooperative cancellation."""

    def __init__(self, data_dir, *, enabled=True, max_bytes=32 * 1024 * 1024,
                 max_entries=128, max_requests=2):
        raise NotImplementedError

    def submit(self, body):
        """Snapshot inputs and return a cancellable ticket, or fail admission."""
        raise NotImplementedError

    async def evaluate(self, body, request=None):
        """Await a ticket while watching client disconnect/coroutine cancellation."""
        raise NotImplementedError

    def close(self):
        """Cancel queued/active work, join the worker, and release cache storage."""
        raise NotImplementedError

    def snapshot(self):
        """Read bounded local diagnostics; never exposes credentials or results."""
        raise NotImplementedError

