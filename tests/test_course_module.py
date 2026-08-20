"""Course module tests (PDR-003): state machines, approval gate, domain
models, serialization lock, and a DB-free router characterization.

All tests are database-free: domain-model tests mock save(), router tests
mock the model classmethods, mirroring the project's no-DB-in-CI convention.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.course_service import CourseService
from open_notebook.course import state_machine as sm
from open_notebook.course.locking import course_job_lock
from open_notebook.course.models import (
    Chapter,
    Course,
    CourseVersion,
    Progress,
)
from open_notebook.exceptions import InvalidInputError


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


class TestStateMachine:
    def test_legal_transition(self):
        assert sm.transition("course", "draft", "indexing") == "indexing"

    def test_illegal_transition_raises(self):
        with pytest.raises(InvalidInputError):
            sm.transition("course", "draft", "published")

    def test_unknown_state_raises(self):
        with pytest.raises(InvalidInputError):
            sm.transition("course", "draft", "nonsense")

    def test_terminal_states(self):
        assert sm.is_terminal("chapter", "published")
        assert sm.is_terminal("run", "failed")
        assert not sm.is_terminal("course", "draft")

    def test_failed_course_can_retry_appropriate_stage(self):
        assert sm.transition("course", "failed", "generating") == "generating"

    def test_review_escalation_path(self):
        assert sm.transition("chapter_review", "pending", "escalated") == "escalated"
        assert sm.transition("chapter_review", "escalated", "passed") == "passed"

    def test_attempt_flow(self):
        checked = sm.transition("attempt", "submitted", "checked")
        assert sm.transition("attempt", checked, "passed") == "passed"

    def test_evidence_retry_flow(self):
        assert sm.transition("evidence", "processing", "failed") == "failed"
        assert sm.transition("evidence", "failed", "pending") == "pending"


class TestApprovalGate:
    def test_exact_match(self):
        assert sm.approval_matches("第一章 极限\n第二章 导数", "第一章 极限\n第二章 导数")

    def test_trailing_newlines_tolerated(self):
        assert sm.approval_matches("A\nB", "A\nB\n")
        assert not sm.approval_matches("A\nB", "A\nB\n\n")

    def test_nfc_normalization(self):
        # composed vs decomposed 'é' are the same text
        assert sm.approval_matches("café", "cafe\u0301")

    def test_internal_whitespace_still_matters(self):
        assert not sm.approval_matches("A\nB", "A\n B")

    def test_punctuation_still_matters(self):
        assert not sm.approval_matches("A。B", "A.B")

    def test_missing_line_fails(self):
        assert not sm.approval_matches("A\nB\nC", "A\nC")


class TestOutlineValidation:
    def test_valid_outline(self):
        sm.validate_outline_approval_payload({"chapters": [{"title": "第一章"}]})

    def test_missing_chapters_rejected(self):
        with pytest.raises(InvalidInputError):
            sm.validate_outline_approval_payload({})

    def test_blank_chapter_title_rejected(self):
        with pytest.raises(InvalidInputError):
            sm.validate_outline_approval_payload({"chapters": [{"title": "  "}]})

    def test_dependency_graph_must_be_object(self):
        with pytest.raises(InvalidInputError):
            sm.validate_outline_approval_payload(
                {"chapters": [{"title": "x"}], "dependency_graph": []}
            )


class TestDomainModels:
    @pytest.mark.asyncio
    async def test_course_transition_saves(self, monkeypatch):
        course = Course(title="Calculus", notebook="notebook:1")
        save_mock = AsyncMock()
        monkeypatch.setattr(Course, "save", save_mock)
        await course.transition_to(sm.CourseStatus.INDEXING)
        assert course.status == "indexing"
        save_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chapter_review_transition_saves(self, monkeypatch):
        chapter = Chapter(
            course_version="course_version:1",
            chapter_no=1,
            chapter_key="limits",
            title="Limits",
        )
        save_mock = AsyncMock()
        monkeypatch.setattr(Chapter, "save", save_mock)
        await chapter.transition_review("passed")
        assert chapter.review_status == "passed"
        save_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_version_publish_stamps_timestamp(self, monkeypatch):
        version = CourseVersion(course="course:1", version_no=1)
        monkeypatch.setattr(CourseVersion, "save", AsyncMock())
        await version.transition_to("generating")
        assert version.published_at is None
        await version.transition_to("published")
        assert version.published_at is not None

    @pytest.mark.asyncio
    async def test_progress_transition(self, monkeypatch):
        progress = Progress(course="course:1")
        monkeypatch.setattr(Progress, "save", AsyncMock())
        await progress.transition_to("in_progress")
        assert progress.status == "in_progress"


class TestSerializationLock:
    @pytest.mark.asyncio
    async def test_lock_serializes_jobs(self):
        order: list[str] = []

        async def job(name: str, delay: float) -> None:
            async with course_job_lock():
                order.append(f"{name}-start")
                await asyncio.sleep(delay)
                order.append(f"{name}-end")

        await asyncio.gather(job("a", 0.05), job("b", 0.01))
        assert order == ["a-start", "a-end", "b-start", "b-end"]


class TestCourseRouter:
    def test_create_course(self, client, monkeypatch):
        monkeypatch.setattr(
            CourseService,
            "create_course",
            AsyncMock(
                return_value=Course(
                    id="course:1", title="Calculus I", notebook="notebook:1"
                )
            ),
        )
        response = client.post("/api/courses", json={"title": "Calculus I"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Calculus I"
        assert body["status"] == "draft"

    def test_create_course_requires_title(self, client):
        response = client.post("/api/courses", json={"title": ""})
        assert response.status_code == 422  # pydantic validation

    def test_list_courses_empty(self, client, monkeypatch):
        async def fake_get_all(order_by=None):
            return []

        monkeypatch.setattr(Course, "get_all", fake_get_all)
        response = client.get("/api/courses")
        assert response.status_code == 200
        assert response.json() == []

    def test_generic_transition_bypass_is_absent(self, client):
        response = client.post(
            "/api/courses/course:1/status", json={"status": "published"}
        )
        assert response.status_code == 404

    def test_approve_outline_rejects_bad_confirmation(self, client):
        response = client.post(
            "/api/courses/course:1/outline/approve",
            json={"version_id": "course_version:1", "confirmation": "确认"},
        )
        assert response.status_code == 422

    def test_approve_outline_calls_service(self, client, monkeypatch):
        approved = CourseVersion(
            id="course_version:1",
            course="course:1",
            version_no=1,
            approved_at="2026-08-18T00:00:00Z",
            confirmation="确认大纲",
        )
        monkeypatch.setattr(
            CourseService, "approve_outline", AsyncMock(return_value=approved)
        )
        response = client.post(
            "/api/courses/course:1/outline/approve",
            json={
                "version_id": "course_version:1",
                "confirmation": "确认大纲",
            },
        )
        assert response.status_code == 200
        assert response.json()["confirmation"] == "确认大纲"

    def test_unknown_lab_type_rejected(self, client, monkeypatch):
        version = CourseVersion(course="course:1", version_no=1)

        async def fake_get(_id):
            return version

        monkeypatch.setattr(CourseVersion, "get", fake_get)
        response = client.post(
            "/api/versions/course_version:1/labs",
            json={
                "lab_type": "arbitrary_code_exec",
                "payload": {"x": 1},
            },
        )
        assert response.status_code == 422
        assert any(
            "lab_type" in error["loc"] for error in response.json()["detail"]
        )
