"""Domain-scoped serialization for course jobs (PDR-003, decision 6).

Heavy course jobs (Docling extraction, local-LLM generation) must not run
concurrently with each other, but the global surreal-commands worker keeps its
default concurrency for upstream jobs. Callers acquire this lock inside the
worker command body; it is process-local by design — the worker is a single
process, and the lock is not meant to survive restarts (jobs are re-queued by
the worker anyway).
"""

import asyncio

# A plain module-level lock: the worker runs commands in one event loop, so an
# asyncio.Lock is the right primitive (no cross-process coordination needed).
_course_job_lock = asyncio.Lock()


def course_job_lock() -> asyncio.Lock:
    """The shared lock serializing course-domain heavy jobs."""
    return _course_job_lock
