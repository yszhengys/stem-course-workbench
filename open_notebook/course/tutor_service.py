"""Source-grounded chapter tutor boundary for Course V2."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast

from pydantic import Field, ValidationError, field_validator

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .contracts import GenerationRequest, ModelSelection
from .generation_service import CourseGenerationService
from .learning_service import LearningService
from .model_adapters import (
    CourseModelAdapter,
    build_adapter,
    ensure_course_models_selectable,
)
from .task_backend import CourseTaskBackend
from .v2_contracts import (
    LearningEvent,
    TransferTaskPayload,
    TutorModelArtifact,
    TutorResponse,
    TutorTurn,
    V2Contract,
)
from .v2_models import CourseExercise, CourseTutorSession, CourseTutorTurn

TutorIntent = Literal["explain", "diagnose", "hint", "reveal"]
SessionLoader = Callable[[str], Awaitable[CourseTutorSession]]
SessionLister = Callable[[str], Awaitable[tuple[CourseTutorSession, ...]]]
SessionSaver = Callable[[CourseTutorSession], Awaitable[CourseTutorSession | None]]
TurnLoader = Callable[[str], Awaitable[tuple[CourseTutorTurn, ...]]]
TurnAppender = Callable[[CourseTutorTurn, CourseTutorTurn], Awaitable[None]]

_SESSION_ID = re.compile(r"^course_tutor_session:[^:]+$")
_TUTOR_EVIDENCE_LIMIT = 24
_PROMPT_INJECTION = re.compile(
    r"(?is)(?:"
    r"ignore|disregard|override|bypass|forget"
    r")[^\n]{0,100}(?:"
    r"previous|prior|above|system|developer|instruction|prompt"
    r")|(?:system|developer)[ -]?prompt|"
    r"忽略[^\n]{0,40}(?:之前|以上|系统|开发者|指令|提示)|"
    r"泄露[^\n]{0,40}(?:系统|提示|答案|密钥)"
)


class TutorGroundingError(InvalidInputError):
    """Raised when tutor scope, evidence, or output cannot be trusted."""


class TutorEvidence(V2Contract):
    """One server-selected, hash-verified evidence quote."""

    anchor_id: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=4000)
    source_role: Literal["PRIMARY", "SUPPLEMENT"]


class TutorScope(V2Contract):
    """Exact published scope resolved by the server, never by the learner."""

    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_id: str = Field(pattern=r"^chapter:[^:]+$")
    chapter_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    snapshot_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=500)

    @field_validator("allowed_anchor_ids")
    @classmethod
    def anchors_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Tutor scope anchor IDs must be unique")
        return values


@dataclass(slots=True)
class TutorService:
    """Own version-bound tutor sessions and validate grounded responses."""

    task_backend: CourseTaskBackend | None = None
    adapter: CourseModelAdapter | None = None
    learning_service: LearningService | None = None
    session_loader: SessionLoader | None = None
    session_lister: SessionLister | None = None
    session_saver: SessionSaver | None = None
    turn_loader: TurnLoader | None = None
    turn_appender: TurnAppender | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    async def _default_session_loader(self, session_id: str) -> CourseTutorSession:
        return await CourseTutorSession.get(session_id)

    async def _default_session_lister(
        self, course_id: str
    ) -> tuple[CourseTutorSession, ...]:
        rows = await repo_query(
            """
            SELECT * FROM course_tutor_session
            WHERE course = $course ORDER BY created;
            """,
            {"course": ensure_record_id(course_id)},
        )
        sessions = tuple(
            CourseTutorSession(**row)
            for row in rows
            if isinstance(row, dict)
        )
        if any(session.course != course_id for session in sessions):
            raise TutorGroundingError("Tutor session escaped its Course scope.")
        return sessions

    async def _default_session_saver(
        self, session: CourseTutorSession
    ) -> CourseTutorSession:
        await session.save()
        return session

    async def _default_turn_loader(
        self, session_id: str
    ) -> tuple[CourseTutorTurn, ...]:
        rows = await repo_query(
            """
            SELECT * FROM course_tutor_turn
            WHERE session = $tutor_session ORDER BY turn_no;
            """,
            {"tutor_session": ensure_record_id(session_id)},
        )
        return tuple(
            CourseTutorTurn(**row)
            for row in rows
            if isinstance(row, dict)
        )

    async def _default_turn_appender(
        self,
        user_turn: CourseTutorTurn,
        assistant_turn: CourseTutorTurn,
    ) -> None:
        session_id = user_turn.session
        if (
            assistant_turn.session != session_id
            or assistant_turn.turn_no != user_turn.turn_no + 1
        ):
            raise TutorGroundingError("Tutor turn pair is inconsistent.")
        statement = """
        BEGIN TRANSACTION;
        LET $course_scope = (
            SELECT outline_version_id FROM $course LIMIT 1
        );
        IF array::len($course_scope) != 1 {
            THROW 'Tutor Course scope changed'
        };
        LET $pointed_published = (
            SELECT VALUE id FROM $course_scope[0].outline_version_id
            WHERE course = $course AND status = 'published' LIMIT 1
        );
        IF array::len($pointed_published) = 1 {
            IF $pointed_published[0] != $version {
                THROW 'Tutor session requires the current published version'
            };
        } ELSE {
            LET $latest_published = (
                SELECT id, version_no FROM course_version
                WHERE course = $course AND status = 'published'
                ORDER BY version_no DESC LIMIT 1
            );
            IF array::len($latest_published) != 1
               OR $latest_published[0].id != $version {
                THROW 'Tutor session requires the current published version'
            };
        };
        LET $session_scope = (
            SELECT chapter FROM $tutor_session
            WHERE course = $course
              AND course_version = $version
              AND chapter_key = $chapter_key
              AND status = 'active' LIMIT 1
        );
        IF array::len($session_scope) != 1 {
            THROW 'Tutor session is no longer writable'
        };
        LET $chapter_scope = (
            SELECT VALUE id FROM $session_scope[0].chapter
            WHERE course_version = $version
              AND chapter_key = $chapter_key
              AND status = 'published' LIMIT 1
        );
        IF array::len($chapter_scope) != 1 {
            THROW 'Tutor chapter is no longer published'
        };
        LET $current_turns = (
            SELECT VALUE id FROM course_tutor_turn
            WHERE session = $tutor_session
        );
        IF array::len($current_turns) != $expected_turn_count {
            THROW 'Tutor turn history changed'
        };
        CREATE course_tutor_turn CONTENT $user_content;
        CREATE course_tutor_turn CONTENT $assistant_content;
        COMMIT TRANSACTION;
        """
        try:
            await repo_query(
                statement,
                {
                    "course": ensure_record_id(user_turn.course),
                    "version": ensure_record_id(user_turn.course_version),
                    "chapter_key": user_turn.chapter_key,
                    "tutor_session": ensure_record_id(session_id),
                    "expected_turn_count": user_turn.turn_no - 1,
                    "user_content": user_turn._prepare_save_data(),
                    "assistant_content": assistant_turn._prepare_save_data(),
                },
            )
        except RuntimeError as exc:
            raise TutorGroundingError(
                "Tutor session changed before the response could be saved."
            ) from exc

    async def create_session(
        self,
        scope: TutorScope,
        model: ModelSelection,
    ) -> CourseTutorSession:
        await ensure_course_models_selectable([model])
        session = CourseTutorSession(
            course=scope.course_id,
            course_version=scope.course_version_id,
            chapter=scope.chapter_id,
            chapter_key=scope.chapter_key,
            model_selection=model,
            status="active",
        )
        saver = self.session_saver or self._default_session_saver
        saved = await saver(session)
        result = saved or session
        if result.id is None:
            raise TutorGroundingError("Tutor session was not persisted.")
        return result

    async def list_sessions(
        self,
        course_id: str,
        *,
        current_version_id: str,
    ) -> tuple[CourseTutorSession, ...]:
        lister = self.session_lister or self._default_session_lister
        sessions = await lister(course_id)
        normalized: list[CourseTutorSession] = []
        for session in sessions:
            if session.course != course_id:
                raise TutorGroundingError("Tutor session escaped its Course scope.")
            status = (
                "stale"
                if session.course_version != current_version_id
                else session.status
            )
            normalized.append(session.model_copy(update={"status": status}))
        return tuple(normalized)

    async def get_session(self, session_id: str) -> CourseTutorSession:
        if not _SESSION_ID.fullmatch(session_id):
            raise TutorGroundingError("Tutor session ID is invalid.")
        loader = self.session_loader or self._default_session_loader
        session = await loader(session_id)
        if str(session.id) != session_id:
            raise TutorGroundingError("Tutor session identity is invalid.")
        return session

    async def list_turns(
        self,
        course_id: str,
        session_id: str,
    ) -> tuple[CourseTutorTurn, ...]:
        session = await self.get_session(session_id)
        if session.course != course_id:
            raise TutorGroundingError("Tutor session is outside the Course scope.")
        loader = self.turn_loader or self._default_turn_loader
        turns = await loader(session_id)
        self._validate_history(turns, session)
        return turns

    @staticmethod
    def _validate_session_scope(
        session_id: str,
        session: CourseTutorSession,
        scope: TutorScope,
    ) -> None:
        if str(session.id) != session_id or (
            session.course,
            session.course_version,
            session.chapter,
            session.chapter_key,
        ) != (
            scope.course_id,
            scope.course_version_id,
            scope.chapter_id,
            scope.chapter_key,
        ):
            raise TutorGroundingError(
                "Tutor session is outside the current published chapter."
            )
        if session.status != "active":
            raise TutorGroundingError("Tutor session is read-only.")

    @staticmethod
    def _validate_history(
        turns: tuple[CourseTutorTurn, ...],
        session: CourseTutorSession,
    ) -> None:
        expected_numbers = tuple(range(1, len(turns) + 1))
        if tuple(turn.turn_no for turn in turns) != expected_numbers:
            raise TutorGroundingError("Tutor turn history is not contiguous.")
        for index, turn in enumerate(turns):
            expected_role = "user" if index % 2 == 0 else "assistant"
            if (
                turn.session != str(session.id)
                or turn.course != session.course
                or turn.course_version != session.course_version
                or turn.chapter_key != session.chapter_key
                or turn.role != expected_role
            ):
                raise TutorGroundingError("Tutor turn history has invalid ownership.")

    @staticmethod
    def _validated_evidence(
        scope: TutorScope,
        evidence: tuple[TutorEvidence, ...],
    ) -> tuple[TutorEvidence, ...]:
        ids = tuple(item.anchor_id for item in evidence)
        if len(ids) != len(set(ids)):
            raise TutorGroundingError("Tutor evidence anchors must be unique.")
        if not set(ids).issubset(set(scope.allowed_anchor_ids)):
            raise TutorGroundingError(
                "Tutor evidence is outside the current published chapter."
            )
        return evidence

    @staticmethod
    def _select_evidence(
        content: str,
        evidence: tuple[TutorEvidence, ...],
    ) -> tuple[TutorEvidence, ...]:
        """Keep a deterministic, bounded window relevant to the learner question."""

        if len(evidence) <= _TUTOR_EVIDENCE_LIMIT:
            return evidence
        terms = set(
            re.findall(r"[a-z0-9]{2,}|[\u3400-\u9fff]", content.casefold())
        )
        ranked = sorted(
            enumerate(evidence),
            key=lambda item: (
                -len(
                    terms.intersection(
                        re.findall(
                            r"[a-z0-9]{2,}|[\u3400-\u9fff]",
                            item[1].quote.casefold(),
                        )
                    )
                ),
                0 if item[1].source_role == "PRIMARY" else 1,
                item[0],
            ),
        )
        return tuple(
            evidence[index] for index, _item in ranked[:_TUTOR_EVIDENCE_LIMIT]
        )

    @staticmethod
    def _grounded_output(
        generated: TutorModelArtifact,
        *,
        intent: TutorIntent,
        evidence_ids: set[str],
    ) -> TutorModelArtifact:
        try:
            artifact = TutorModelArtifact.model_validate(
                generated.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise TutorGroundingError(
                "Tutor output did not satisfy the citation contract."
            ) from exc
        if any(
            not set(claim.anchor_ids).issubset(evidence_ids)
            for claim in artifact.claims
        ):
            raise TutorGroundingError(
                "Tutor output cited evidence outside the supplied context."
            )
        expected_kind = {
            "explain": "explanation",
            "diagnose": "diagnosis",
            "hint": "hint",
            "reveal": "answer",
        }[intent]
        if not artifact.insufficient_evidence and artifact.response_kind != expected_kind:
            if artifact.response_kind == "answer" and intent != "reveal":
                raise TutorGroundingError(
                    "A complete answer requires an explicit reveal request."
                )
            raise TutorGroundingError(
                "Tutor output did not match the requested learning intent."
            )
        if intent != "reveal" and artifact.answer_revealed:
            raise TutorGroundingError(
                "A complete answer requires an explicit reveal request."
            )
        return artifact

    @staticmethod
    def _response_content(artifact: TutorModelArtifact) -> str:
        if artifact.insufficient_evidence:
            return cast(str, artifact.refusal_message)
        ordered_anchors = tuple(
            dict.fromkeys(
                anchor
                for claim in artifact.claims
                for anchor in claim.anchor_ids
            )
        )
        return "\n\n".join(
            f"{claim.content} "
            f"[{', '.join(str(ordered_anchors.index(anchor) + 1) for anchor in claim.anchor_ids)}]"
            for claim in artifact.claims
        )

    @staticmethod
    def _event_key(
        session_id: str,
        attempt_key: str,
        exercise_key: str,
        kind: str,
    ) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [session_id, attempt_key, exercise_key, kind],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:40]
        return f"tutor-{kind}-{digest}"

    async def _commit_reveal(
        self,
        *,
        scope: TutorScope,
        session_id: str,
        exercise: CourseExercise,
        concept_key: str,
        attempt_key: str,
        occurred_at: datetime,
    ) -> None:
        transfer = exercise.blueprint.transfer_task
        if (
            exercise.course != scope.course_id
            or exercise.course_version != scope.course_version_id
            or exercise.chapter != scope.chapter_id
            or exercise.chapter_key != scope.chapter_key
            or not exercise.is_core
            or transfer is None
            or concept_key not in set(exercise.blueprint.concept_keys)
        ):
            raise TutorGroundingError(
                "Tutor answer reveal requires the current core exercise."
            )
        payload = TransferTaskPayload(
            attempt_key=attempt_key,
            transfer_task_key=transfer.key,
        )
        revealed = LearningEvent(
            event_id=self._event_key(
                session_id, attempt_key, exercise.exercise_key, "reveal"
            ),
            course_id=scope.course_id,
            course_version_id=scope.course_version_id,
            chapter_key=scope.chapter_key,
            concept_key=concept_key,
            exercise_key=exercise.exercise_key,
            kind="answer_revealed",
            payload=payload,
            occurred_at=occurred_at,
        )
        required = LearningEvent(
            event_id=self._event_key(
                session_id, attempt_key, exercise.exercise_key, "transfer"
            ),
            course_id=scope.course_id,
            course_version_id=scope.course_version_id,
            chapter_key=scope.chapter_key,
            concept_key=concept_key,
            exercise_key=exercise.exercise_key,
            kind="transfer_required",
            payload=payload,
            occurred_at=occurred_at,
        )
        learning = self.learning_service or LearningService()
        await learning.append_reveal_events(revealed, required)

    async def respond(
        self,
        *,
        scope: TutorScope,
        session_id: str,
        content: str,
        intent: TutorIntent,
        evidence: tuple[TutorEvidence, ...],
        exercise: CourseExercise | None = None,
        concept_key: str | None = None,
        attempt_key: str | None = None,
    ) -> TutorResponse:
        if not _SESSION_ID.fullmatch(session_id):
            raise TutorGroundingError("Tutor session ID is invalid.")
        if _PROMPT_INJECTION.search(content):
            raise TutorGroundingError("Tutor request contains instruction override text.")
        try:
            TutorTurn(turn_no=1, role="user", content=content)
        except ValidationError as exc:
            raise TutorGroundingError("Tutor request text is invalid.") from exc

        loader = self.session_loader or self._default_session_loader
        session = await loader(session_id)
        self._validate_session_scope(session_id, session, scope)
        selected_evidence = self._select_evidence(
            content,
            self._validated_evidence(scope, evidence),
        )

        turn_loader = self.turn_loader or self._default_turn_loader
        turns = await turn_loader(session_id)
        self._validate_history(turns, session)
        if intent == "reveal" and (
            exercise is None or concept_key is None or attempt_key is None
        ):
            raise TutorGroundingError(
                "An explicit reveal requires an exercise and attempt identity."
            )

        if not selected_evidence:
            artifact = TutorModelArtifact(
                response_kind="refusal",
                claims=(),
                insufficient_evidence=True,
                refusal_message=(
                    "当前章节证据不足，无法给出可靠回答。"
                    if re.search(r"[\u3400-\u9fff]", content)
                    else "The current chapter evidence is insufficient for a reliable answer."
                ),
                answer_revealed=False,
            )
        else:
            evidence_ids = {item.anchor_id for item in selected_evidence}
            evidence_lines = tuple(
                json.dumps(
                    {
                        "anchor_id": item.anchor_id,
                        "source_role": item.source_role,
                        "quote": item.quote,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for item in selected_evidence
            )
            request = GenerationRequest(
                stage="tutor",
                course_id=scope.course_id,
                chapter_key=scope.chapter_key,
                model=session.model_selection,
                anchor_ids=list(evidence_ids),
                prompt_version="v2",
                schema_name="course_tutor_response",
            )
            adapter = self.adapter or build_adapter(session.model_selection)
            generated = await adapter.generate(
                request,
                TutorModelArtifact,
                prompt=CourseGenerationService.prompt_for(
                    "tutor",
                    evidence_lines,
                    "Trusted intent: "
                    f"{intent}.\nUntrusted learner message (JSON): "
                    + json.dumps(content, ensure_ascii=False),
                    format_instructions=CourseGenerationService._format_instructions(
                        TutorModelArtifact
                    ),
                ),
            )
            artifact = self._grounded_output(
                generated,
                intent=intent,
                evidence_ids=evidence_ids,
            )
        occurred_at = self.clock()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise TutorGroundingError("Tutor clock must include a timezone.")
        occurred_at = occurred_at.astimezone(timezone.utc)
        if intent == "reveal" and not artifact.insufficient_evidence:
            await self._commit_reveal(
                scope=scope,
                session_id=session_id,
                exercise=cast(CourseExercise, exercise),
                concept_key=cast(str, concept_key),
                attempt_key=cast(str, attempt_key),
                occurred_at=occurred_at,
            )

        next_turn = len(turns) + 1
        user_turn = CourseTutorTurn(
            course=scope.course_id,
            course_version=scope.course_version_id,
            session=session_id,
            chapter_key=scope.chapter_key,
            turn_no=next_turn,
            role="user",
            content=content,
            anchor_ids=(),
            answer_revealed=False,
            insufficient_evidence=False,
        )
        assistant_turn = CourseTutorTurn(
            course=scope.course_id,
            course_version=scope.course_version_id,
            session=session_id,
            chapter_key=scope.chapter_key,
            turn_no=next_turn + 1,
            role="assistant",
            content=self._response_content(artifact),
            anchor_ids=tuple(
                dict.fromkeys(
                    anchor
                    for claim in artifact.claims
                    for anchor in claim.anchor_ids
                )
            ),
            answer_revealed=artifact.answer_revealed,
            insufficient_evidence=artifact.insufficient_evidence,
        )
        appender = self.turn_appender or self._default_turn_appender
        await appender(user_turn, assistant_turn)
        return TutorResponse(
            session_id=session_id,
            turn=TutorTurn(
                turn_no=assistant_turn.turn_no,
                role="assistant",
                content=assistant_turn.content,
                anchor_ids=assistant_turn.anchor_ids,
                answer_revealed=assistant_turn.answer_revealed,
            ),
            insufficient_evidence=assistant_turn.insufficient_evidence,
        )


__all__ = [
    "TutorEvidence",
    "TutorGroundingError",
    "TutorIntent",
    "TutorScope",
    "TutorService",
]
