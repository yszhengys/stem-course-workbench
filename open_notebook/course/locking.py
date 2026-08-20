"""Domain-scoped serialization for course jobs (PDR-003, decision 6).

Heavy course jobs (Docling extraction, local-LLM generation) must not run
concurrently with each other, but the global surreal-commands worker keeps its
default concurrency for upstream jobs. Callers acquire this lock inside the
worker command body; it is process-local by design — the worker is a single
process, and the lock is not meant to survive restarts (jobs are re-queued by
the worker anyway).
"""

import asyncio
from types import TracebackType
from typing import Self


class ReentrantAsyncLock:
    """Task-reentrant wrapper around an asyncio lock.

    The worker owns the complete generation operation while the Ollama adapter
    also protects its local model invocation. Reentrancy keeps those two
    trusted layers serialized without deadlocking the same task.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None
        self._depth = 0

    async def acquire(self) -> bool:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Course lock requires an asyncio task")
        if self._owner is task:
            self._depth += 1
            return True
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return True

    def release(self) -> None:
        task = asyncio.current_task()
        if task is None or self._owner is not task:
            raise RuntimeError("Course lock can only be released by its owner")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.release()


_course_job_lock = ReentrantAsyncLock()


def course_job_lock() -> ReentrantAsyncLock:
    """The shared lock serializing course-domain heavy jobs."""
    return _course_job_lock
