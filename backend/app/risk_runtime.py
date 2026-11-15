"""Bounded per-worker risk execution and exact physics reuse for the API."""

import asyncio
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np

from .descent import terrain_height_m
from .physics_cache import PhysicsCache
from .risk import (
    MIN_SLOPE_MAP_HALF_WIDTH_M,
    RiskCancelled,
    load_slope_map,
    run_monte_carlo,
)


class RiskBusy(RuntimeError):
    """The bounded request queue is full."""


class RiskClosed(RuntimeError):
    """The owning worker has shut down or changed process."""


def _json_scalar(value):
    """Preserve NumPy scalar values in immutable standard JSON payloads."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported physics payload type: {type(value).__name__}")


class BoundedPhysicsCache(PhysicsCache):
    """Immutable JSON payloads bounded by bytes and count, one owner worker.

    The byte limit includes stored payload and digest bytes, not whole-process
    RSS. Entries are never persisted; code changes require a process restart.
    """

    def __init__(
        self,
        *,
        enabled=True,
        max_entries=128,
        max_bytes=32 * 1024 * 1024,
        revision="r6-runtime-v1",
    ):
        super().__init__(enabled=enabled, max_entries=max_entries)
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if not isinstance(revision, str) or not revision:
            raise ValueError("A nonempty model revision is required")
        self.max_bytes, self.revision = max_bytes, revision
        self._lock = threading.RLock()
        self._bytes = self._oversize = 0
        self._capacity_bypass = 0
        self._protected = None

    def _lookup(self, kind, inputs, compute):
        # One critical section makes invalidation atomic with a computation.
        # The API already serializes work; this also protects diagnostics/clear.
        with self._lock:
            started = perf_counter()
            key = hashlib.sha256(
                json.dumps(
                    [self.revision, kind, inputs],
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).digest()
            stat = self._stats[kind]
            compute_s = 0.0
            try:
                if self.enabled and key in self._entries:
                    stat["hits"] += 1
                    self._entries.move_to_end(key)
                    if self._protected is not None:
                        self._protected.add(key)
                    return json.loads(self._entries[key])
                stat["misses"] += 1
                mark = perf_counter()
                try:
                    result = compute()
                finally:
                    compute_s = perf_counter() - mark
                    stat["compute_s"] += compute_s
                if self.enabled:
                    payload = json.dumps(
                        result,
                        allow_nan=False,
                        separators=(",", ":"),
                        default=_json_scalar,
                    ).encode()
                    size = len(key) + len(payload)
                    if size <= self.max_bytes:
                        while self._entries and (
                            len(self._entries) >= self.max_entries
                            or self._bytes + size > self.max_bytes
                        ):
                            old_key = next(
                                (
                                    candidate
                                    for candidate in self._entries
                                    if self._protected is None
                                    or candidate not in self._protected
                                ),
                                None,
                            )
                            if old_key is None:
                                break
                            old_payload = self._entries.pop(old_key)
                            self._bytes -= len(old_key) + len(old_payload)
                        if (
                            len(self._entries) < self.max_entries
                            and self._bytes + size <= self.max_bytes
                        ):
                            self._entries[key] = payload
                            self._bytes += size
                            if self._protected is not None:
                                self._protected.add(key)
                        else:
                            self._capacity_bypass += 1
                    else:
                        self._oversize += 1
                return result
            finally:
                stat["overhead_s"] += perf_counter() - started - compute_s

    def clear(self, *, revision=None):
        """Discard all entries atomically; invalidation never reuses old entries."""
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise ValueError("A nonempty model revision is required")
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            if self._protected is not None:
                self._protected.clear()
            if revision is not None:
                self.revision = revision

    @contextmanager
    def batch(self):
        """Protect reused/new entries against scan eviction within one request."""
        with self._lock:
            if self._protected is not None:
                raise RuntimeError("Cache batches cannot overlap")
            self._protected = set()
        try:
            yield
        finally:
            with self._lock:
                self._protected = None

    def snapshot(self):
        """Read local cumulative counters without exposing cached trajectories."""
        with self._lock:
            return super().snapshot() | dict(
                retained_bytes=self._bytes,
                max_bytes=self.max_bytes,
                max_entries=self.max_entries,
                revision=self.revision,
                bypassed_oversize=self._oversize,
                bypassed_capacity=self._capacity_bypass,
                enabled=self.enabled,
            )


@dataclass(frozen=True)
class RiskTicket:
    future: object
    stop: threading.Event

    def cancel(self):
        """Signal running work and cancel queued work; never kill integration."""
        self.stop.set()
        self.future.cancel()


class RiskRuntime:
    """One computation worker, bounded admission, cooperative cancellation."""

    def __init__(
        self,
        data_dir,
        *,
        enabled=True,
        max_bytes=32 * 1024 * 1024,
        max_entries=128,
        max_requests=2,
    ):
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        self.data_dir = Path(data_dir)
        self.cache = BoundedPhysicsCache(
            enabled=enabled, max_entries=max_entries, max_bytes=max_bytes
        )
        self._owner = os.getpid()
        self._max_requests = max_requests
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="r6-risk")
        self._pending = {}
        self._closed = False

    def _compute(self, body, stop):
        if stop.is_set():
            raise RiskCancelled("Risk evaluation cancelled")
        # Terrain decisions are never memoized; read verified current inputs.
        height = terrain_height_m(
            self.data_dir, body.latitude_deg, body.longitude_deg_east
        )
        half = max(5 * body.uncertainty.navigation_sigma_m, MIN_SLOPE_MAP_HALF_WIDTH_M)
        slope_map = load_slope_map(
            self.data_dir, body.latitude_deg, body.longitude_deg_east, half
        )
        with self.cache.batch():
            result = run_monte_carlo(
                body, height, slope_map, physics_cache=self.cache, cancelled=stop.is_set
            )
        if stop.is_set():
            raise RiskCancelled("Risk evaluation cancelled before delivery")
        return result

    def submit(self, body):
        """Snapshot inputs and return a cancellable ticket, or fail admission."""
        with self._lock:
            if self._closed or self._owner != os.getpid():
                raise RiskClosed("Risk worker unavailable; initialize a new app worker")
            self._pending = {
                f: event for f, event in self._pending.items() if not f.done()
            }
            if len(self._pending) >= self._max_requests:
                raise RiskBusy("Risk queue is full; wait for the running request")
            stop = threading.Event()
            future = self._executor.submit(
                self._compute, body.model_copy(deep=True), stop
            )
            self._pending[future] = stop
            future.add_done_callback(self._finished)
            return RiskTicket(future, stop)

    def _finished(self, future):
        with self._lock:
            self._pending.pop(future, None)

    async def evaluate(self, body, request=None):
        """Await a ticket while watching client disconnect/coroutine cancellation."""
        ticket = self.submit(body)
        future = asyncio.wrap_future(ticket.future)
        try:
            while not future.done():
                await asyncio.wait((future,), timeout=0.1)
                if request is not None and await request.is_disconnected():
                    ticket.cancel()
                    raise RiskCancelled("Client disconnected")
            return await future
        except BaseException:
            # Includes asyncio cancellation. Do not leave orphaned queued work.
            ticket.cancel()
            future.cancel()
            raise

    def close(self):
        """Cancel queued/active work, join the worker, and release cache storage."""
        with self._lock:
            self._closed = True
            for future, stop in list(self._pending.items()):
                stop.set()
                future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.cache.clear()

    def snapshot(self):
        """Local diagnostics; not an API that exposes request contents."""
        with self._lock:
            pending = sum(not f.done() for f in self._pending)
            closed = self._closed
        return dict(
            pending=pending,
            closed=closed,
            owner_pid=self._owner,
            cache=self.cache.snapshot(),
        )
