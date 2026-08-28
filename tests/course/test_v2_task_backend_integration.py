from contextlib import asynccontextmanager

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.task_backend import SurrealCommandTaskBackend


@pytest.mark.asyncio
async def test_queued_cancel_is_a_real_conditional_surreal_transition(
    monkeypatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_v2_cancel", "course_v2_cancel")
    await database.query("CREATE command:one SET status = 'new';")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    backend = SurrealCommandTaskBackend(command_service=None)

    await backend.cancel("command:one")

    rows = await database.query("SELECT status, error_message FROM command:one;")
    assert rows == [
        {
            "status": "canceled",
            "error_message": "Cancelled before execution",
        }
    ]
    await database.close()
