from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.task_backend import SurrealCommandTaskBackend
from open_notebook.exceptions import InvalidInputError


@pytest.mark.asyncio
async def test_task_backend_rejects_cross_table_ids_before_service_access() -> None:
    service = AsyncMock()
    backend = SurrealCommandTaskBackend(command_service=service)

    with pytest.raises(InvalidInputError, match="command record ID"):
        await backend.get("course:one")

    service.get_command_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_cannot_mutate_a_cross_table_record(monkeypatch) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_v2_cancel_owner", "course_v2_cancel_owner")
    await database.query("CREATE course:one SET status = 'new';")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    backend = SurrealCommandTaskBackend(command_service=AsyncMock())

    with pytest.raises(InvalidInputError, match="command record ID"):
        await backend.cancel("course:one")

    assert await database.query("SELECT status FROM course:one;") == [
        {"status": "new"}
    ]
    await database.close()
