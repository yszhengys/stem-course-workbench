"""Source-grounded chapter tutor boundary for Course V2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Literal, cast
from weakref import WeakValueDictionary

from pydantic import Field, ValidationError, field_validator

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .assessment_service import AssessmentService
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
    HintViewedPayload,
    LearningEvent,
    TransferTaskPayload,
    TutorClaim,
    TutorModelArtifact,
    TutorResponse,
    TutorTurn,
    V2Contract,
)
from .v2_models import (
    CourseExercise,
    CourseTutorOperation,
    CourseTutorSession,
    CourseTutorTurn,
)

TutorIntent = Literal["explain", "diagnose", "hint", "reveal"]
SessionLoader = Callable[[str], Awaitable[CourseTutorSession]]
SessionLister = Callable[[str], Awaitable[tuple[CourseTutorSession, ...]]]
SessionSaver = Callable[[CourseTutorSession], Awaitable[CourseTutorSession | None]]
TurnLoader = Callable[[str], Awaitable[tuple[CourseTutorTurn, ...]]]
TurnAppender = Callable[[CourseTutorTurn, CourseTutorTurn], Awaitable[None]]
EventLoader = Callable[[str, str], Awaitable[LearningEvent | None]]
EventListLoader = Callable[
    [str, str, str, str], Awaitable[tuple[LearningEvent, ...]]
]
OperationLoader = Callable[[str, str], Awaitable[CourseTutorOperation | None]]
OperationReserver = Callable[
    [CourseTutorOperation], Awaitable[CourseTutorOperation | None]
]
OperationLeaseAcquirer = Callable[
    [CourseTutorOperation, str, datetime], Awaitable[bool]
]
OperationLeaseRenewer = Callable[
    [CourseTutorOperation, str, datetime], Awaitable[bool]
]
OperationLeaseReleaser = Callable[[CourseTutorOperation, str], Awaitable[None]]

_SESSION_ID = re.compile(r"^course_tutor_session:[^:]+$")
_TUTOR_EVIDENCE_LIMIT = 24
_TUTOR_OPERATION_LEASE = timedelta(minutes=2)
_TUTOR_OPERATION_HEARTBEAT_SECONDS = 30.0
_TUTOR_OPERATION_RENEW_TIMEOUT_SECONDS = 15.0
_TUTOR_OPERATION_RELEASE_TIMEOUT_SECONDS = 5.0
_OPERATION_LEASE_LOCKS: WeakValueDictionary[str, asyncio.Lock] = (
    WeakValueDictionary()
)
_PROMPT_INJECTION = re.compile(
    r"(?is)(?:"
    r"ignore|disregard|override|bypass|forget"
    r")[^\n]{0,100}(?:"
    r"previous|prior|above|system|developer|instruction|prompt"
    r")|(?:system|developer)[ -]?prompt|"
    r"忽略[^\n]{0,40}(?:之前|以上|系统|开发者|指令|提示)|"
    r"泄露[^\n]{0,40}(?:系统|提示|答案|密钥)"
)
_ANSWER_CUE = re.compile(
    r"(?is)(?:answer|solution|result|final\s+value|value|expression|"
    r"simplif(?:y|ies|ied)\s+to|evaluat(?:e|es|ed)\s+to|choose|equals?|"
    r"comes?\s+out\s+to|leaves?|therefore|thus|hence|"
    r"答案|解答|结果|数值|表达式|化简(?:为|得)|"
    r"所以|因此|故)\s*(?:is|are|to|为|是|:|=)?\s*|="
)
_ANSWER_TOKEN = re.compile(
    r"(?<!\w)(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|"
    r"[A-Za-z_][A-Za-z0-9_]*)(?!\w)",
    re.IGNORECASE,
)
_ANSWER_EXPRESSION = re.compile(
    r"(?<!\w)(?:[A-Za-z_][A-Za-z0-9_]*|"
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)|\([^\n;]{1,80}\))"
    r"(?:\s*[+\-*/^]\s*(?:[A-Za-z_][A-Za-z0-9_]*|"
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)|\([^\n;]{1,80}\))){1,8}(?!\w)",
    re.IGNORECASE,
)
_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_FRACTION_DENOMINATORS = {
    2: ("half", "halves"),
    3: ("third", "thirds"),
    4: ("quarter", "quarters"),
    5: ("fifth", "fifths"),
    6: ("sixth", "sixths"),
    7: ("seventh", "sevenths"),
    8: ("eighth", "eighths"),
    9: ("ninth", "ninths"),
    10: ("tenth", "tenths"),
    11: ("eleventh", "elevenths"),
    12: ("twelfth", "twelfths"),
}


def _integer_words(value: int) -> str | None:
    """Return a bounded English spelling used only by the answer leak guard."""

    if abs(value) >= 1_000_000:
        return None
    if value < 0:
        nested = _integer_words(-value)
        return f"minus {nested}" if nested is not None else None
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        return _TENS[tens] + (f"-{_ONES[remainder]}" if remainder else "")
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        suffix = _integer_words(remainder) if remainder else None
        return f"{_ONES[hundreds]} hundred" + (f" {suffix}" if suffix else "")
    thousands, remainder = divmod(value, 1_000)
    prefix = _integer_words(thousands)
    suffix = _integer_words(remainder) if remainder else None
    return f"{prefix} thousand" + (f" {suffix}" if suffix else "")


def _numeric_word_aliases(value: float) -> set[str]:
    aliases: set[str] = set()
    if not math.isfinite(value):
        return aliases
    nearest = round(value)
    if math.isclose(value, nearest, rel_tol=0.0, abs_tol=1e-12):
        words = _integer_words(nearest)
        if words is not None:
            aliases.update({words, words.replace("-", " ")})
    fraction = Fraction(value).limit_denominator(12)
    if (
        fraction.denominator > 1
        and math.isclose(float(fraction), value, rel_tol=0.0, abs_tol=1e-12)
    ):
        numerator = _integer_words(abs(fraction.numerator))
        denominator = _FRACTION_DENOMINATORS.get(fraction.denominator)
        if numerator is not None and denominator is not None:
            denominator_word = denominator[0 if abs(fraction.numerator) == 1 else 1]
            sign = "minus " if fraction.numerator < 0 else ""
            aliases.add(f"{sign}{numerator} {denominator_word}")
            if fraction.numerator == 1:
                aliases.update({f"{sign}a {denominator_word}", f"{sign}{denominator_word}"})
    return aliases


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
    event_loader: EventLoader | None = None
    event_list_loader: EventListLoader | None = None
    operation_loader: OperationLoader | None = None
    operation_reserver: OperationReserver | None = None
    operation_lease_acquirer: OperationLeaseAcquirer | None = None
    operation_lease_renewer: OperationLeaseRenewer | None = None
    operation_lease_releaser: OperationLeaseReleaser | None = None
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
            or assistant_turn.operation_key != user_turn.operation_key
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

    @staticmethod
    def _operation_record_id(session_id: str, operation_identity: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [session_id, operation_identity],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:40]
        return f"course_tutor_operation:{digest}"

    async def _default_operation_loader(
        self,
        session_id: str,
        operation_identity: str,
    ) -> CourseTutorOperation | None:
        rows = await repo_query(
            """
            SELECT * FROM course_tutor_operation
            WHERE session = $tutor_session
              AND operation_identity = $operation_identity
            LIMIT 1;
            """,
            {
                "tutor_session": ensure_record_id(session_id),
                "operation_identity": operation_identity,
            },
        )
        if not rows or not isinstance(rows[0], dict):
            return None
        return CourseTutorOperation(**rows[0])

    async def _default_operation_reserver(
        self,
        operation: CourseTutorOperation,
    ) -> CourseTutorOperation:
        if operation.id is None:
            raise TutorGroundingError("Tutor operation has no stable identity.")
        content = operation._prepare_save_data()
        content.pop("id", None)
        content.pop("updated", None)
        await repo_query(
            """
            BEGIN TRANSACTION;
            LET $scope = (
                SELECT VALUE id FROM $tutor_session
                WHERE course = $course
                  AND course_version = $version
                  AND chapter_key = $chapter_key
                  AND status = 'active' LIMIT 1
            );
            IF array::len($scope) != 1 {
                THROW 'Tutor operation session is no longer writable'
            };
            CREATE ONLY $operation CONTENT $content;
            COMMIT TRANSACTION;
            """,
            {
                "operation": ensure_record_id(operation.id),
                "tutor_session": ensure_record_id(operation.session),
                "course": ensure_record_id(operation.course),
                "version": ensure_record_id(operation.course_version),
                "chapter_key": operation.chapter_key,
                "content": content,
            },
        )
        return operation

    @staticmethod
    def _validate_reserved_operation(
        existing: CourseTutorOperation,
        candidate: CourseTutorOperation,
    ) -> None:
        immutable_fields = (
            "course",
            "course_version",
            "session",
            "chapter_key",
            "operation_identity",
            "operation_key",
            "request_fingerprint",
        )
        if any(
            getattr(existing, field) != getattr(candidate, field)
            for field in immutable_fields
        ):
            raise TutorGroundingError(
                "Tutor message identity already has different trusted request content."
            )

    async def _reserve_message_operation(
        self,
        candidate: CourseTutorOperation,
    ) -> CourseTutorOperation:
        loader = self.operation_loader or self._default_operation_loader
        existing = await loader(candidate.session, candidate.operation_identity)
        if existing is not None:
            self._validate_reserved_operation(existing, candidate)
            return existing
        reserver = self.operation_reserver or self._default_operation_reserver
        try:
            reserved = await reserver(candidate)
        except RuntimeError:
            for delay in (0.0, 0.01, 0.025, 0.05):
                if delay:
                    await asyncio.sleep(delay)
                concurrent = await loader(
                    candidate.session,
                    candidate.operation_identity,
                )
                if concurrent is not None:
                    self._validate_reserved_operation(concurrent, candidate)
                    return concurrent
            raise TutorGroundingError(
                "Tutor message identity could not be reserved."
            )
        result = reserved or candidate
        self._validate_reserved_operation(result, candidate)
        return result

    @staticmethod
    def _operation_lease_record_id(operation_id: str) -> str:
        _, separator, record_key = operation_id.partition(":")
        if not separator or not record_key:
            raise TutorGroundingError("Tutor operation has no stable identity.")
        return f"course_tutor_operation_lease:{record_key}"

    async def _default_operation_lease_acquirer(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
        expires_at: datetime,
    ) -> bool:
        if operation.id is None:
            raise TutorGroundingError("Tutor operation has no stable identity.")
        lease_id = self._operation_lease_record_id(operation.id)
        process_lock = _OPERATION_LEASE_LOCKS.setdefault(
            lease_id, asyncio.Lock()
        )
        async with process_lock:
            try:
                await repo_query(
                    """
                    BEGIN TRANSACTION;
                    DELETE $operation_lease WHERE expires_at <= time::now();
                    CREATE ONLY $operation_lease CONTENT {
                        course: $course,
                        course_version: $version,
                        session: $tutor_session,
                        operation: $operation,
                        lease_token: $lease_token,
                        expires_at: $expires_at
                    };
                    COMMIT TRANSACTION;
                    """,
                    {
                        "operation_lease": ensure_record_id(lease_id),
                        "course": ensure_record_id(operation.course),
                        "version": ensure_record_id(operation.course_version),
                        "tutor_session": ensure_record_id(operation.session),
                        "operation": ensure_record_id(operation.id),
                        "lease_token": lease_token,
                        "expires_at": expires_at,
                    },
                )
            except RuntimeError as exc:
                for delay in (0.0, 0.01, 0.025, 0.05):
                    if delay:
                        await asyncio.sleep(delay)
                    rows = await repo_query(
                        "SELECT lease_token FROM $operation_lease LIMIT 1;",
                        {"operation_lease": ensure_record_id(lease_id)},
                    )
                    if rows:
                        return False
                raise TutorGroundingError(
                    "Tutor operation execution could not be reserved."
                ) from exc
            return True

    async def _default_operation_lease_renewer(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
        expires_at: datetime,
    ) -> bool:
        if operation.id is None:
            return False
        rows = await repo_query(
            """
            UPDATE $operation_lease
            SET expires_at = $expires_at
            WHERE lease_token = $lease_token
            RETURN AFTER;
            """,
            {
                "operation_lease": ensure_record_id(
                    self._operation_lease_record_id(operation.id)
                ),
                "lease_token": lease_token,
                "expires_at": expires_at,
            },
        )
        return bool(rows)

    async def _default_operation_lease_releaser(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
    ) -> None:
        if operation.id is None:
            return
        await repo_query(
            "DELETE $operation_lease WHERE lease_token = $lease_token;",
            {
                "operation_lease": ensure_record_id(
                    self._operation_lease_record_id(operation.id)
                ),
                "lease_token": lease_token,
            },
        )

    async def _acquire_operation_lease(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
    ) -> bool:
        now = datetime.now(timezone.utc)
        acquirer = (
            self.operation_lease_acquirer
            or self._default_operation_lease_acquirer
        )
        return await acquirer(
            operation,
            lease_token,
            now + _TUTOR_OPERATION_LEASE,
        )

    async def _renew_operation_lease(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
    ) -> bool:
        now = datetime.now(timezone.utc)
        renewer = (
            self.operation_lease_renewer
            or self._default_operation_lease_renewer
        )
        return await renewer(
            operation,
            lease_token,
            now + _TUTOR_OPERATION_LEASE,
        )

    async def _maintain_operation_lease(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
        owner_task: asyncio.Task[object],
        failures: list[BaseException],
    ) -> None:
        while True:
            await asyncio.sleep(_TUTOR_OPERATION_HEARTBEAT_SECONDS)
            try:
                renewed = await asyncio.wait_for(
                    self._renew_operation_lease(
                        operation,
                        lease_token,
                    ),
                    timeout=_TUTOR_OPERATION_RENEW_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                failures.append(exc)
                owner_task.cancel()
                return
            if not renewed:
                failures.append(
                    TutorGroundingError(
                        "Tutor operation execution lease was lost."
                    )
                )
                owner_task.cancel()
                return

    async def _release_operation_lease(
        self,
        operation: CourseTutorOperation,
        lease_token: str,
    ) -> None:
        releaser = (
            self.operation_lease_releaser
            or self._default_operation_lease_releaser
        )
        try:
            await asyncio.wait_for(
                releaser(operation, lease_token),
                timeout=_TUTOR_OPERATION_RELEASE_TIMEOUT_SECONDS,
            )
        except Exception:
            # The bounded lease remains safe after a database interruption and
            # can be reclaimed after expiration.
            return

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
                raise TutorGroundingError(
                    "Tutor turn history has invalid ownership."
                )

    @staticmethod
    def _replayed_turn(
        turns: tuple[CourseTutorTurn, ...],
        *,
        operation_identity: str,
        operation_key: str,
        user_content: str,
    ) -> CourseTutorTurn | None:
        identity_matches = tuple(
            turn
            for turn in turns
            if turn.operation_key == operation_identity
            or (
                turn.operation_key is not None
                and turn.operation_key.startswith(f"{operation_identity}-")
            )
        )
        if not identity_matches:
            return None
        if any(turn.operation_key != operation_key for turn in identity_matches):
            raise TutorGroundingError(
                "Tutor message identity already has different trusted request content."
            )
        matched = identity_matches
        if (
            len(matched) != 2
            or matched[0].role != "user"
            or matched[1].role != "assistant"
            or matched[0].content != user_content
        ):
            raise TutorGroundingError(
                "Tutor message identity already has different turn content."
            )
        return matched[1]

    @staticmethod
    def _public_response(
        session_id: str,
        assistant_turn: CourseTutorTurn,
    ) -> TutorResponse:
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
        evidence: tuple[TutorEvidence, ...],
        protected_exercises: tuple[CourseExercise, ...] = (),
    ) -> TutorModelArtifact:
        evidence_by_id = {item.anchor_id: item for item in evidence}
        evidence_ids = set(evidence_by_id)
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
        if intent != "reveal" and TutorService._contains_protected_answer(
            artifact,
            protected_exercises,
        ):
            raise TutorGroundingError(
                "A complete answer requires an explicit reveal request."
            )
        if artifact.insufficient_evidence:
            return artifact.model_copy(
                update={
                    "refusal_message": (
                        "The current chapter evidence is insufficient for a reliable answer."
                    )
                }
            )

        # Model prose is never delivered directly in non-reveal mode. The model
        # may select current evidence anchors, but the server renders only the
        # immutable source quotes. This structurally prevents paraphrased or
        # multilingual answer synonyms from bypassing the reveal gate.
        cited_ids = tuple(
            dict.fromkeys(
                anchor_id
                for claim in artifact.claims
                for anchor_id in claim.anchor_ids
            )
        )
        safe_artifact = artifact.model_copy(
            update={
                "claims": tuple(
                    TutorClaim(
                        content=evidence_by_id[anchor_id].quote,
                        anchor_ids=(anchor_id,),
                    )
                    for anchor_id in cited_ids
                ),
                "refusal_message": None,
                "answer_revealed": False,
            }
        )
        if TutorService._contains_protected_answer(
            safe_artifact,
            protected_exercises,
        ):
            raise TutorGroundingError(
                "Current evidence contains a protected answer; use explicit reveal."
            )
        return safe_artifact

    @staticmethod
    def _contains_protected_answer(
        artifact: TutorModelArtifact,
        exercises: tuple[CourseExercise, ...],
    ) -> bool:
        def scalar_values(value: object) -> tuple[str, ...]:
            if isinstance(value, dict):
                return tuple(
                    item
                    for nested in value.values()
                    for item in scalar_values(nested)
                )
            if isinstance(value, list | tuple):
                return tuple(
                    item for nested in value for item in scalar_values(nested)
                )
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                normalized = re.sub(r"\s+", " ", str(value)).strip().casefold()
                return (normalized,) if normalized else ()
            return ()

        protected = {
            value
            for exercise in exercises
            if exercise.grader.kind != "advisory"
            for value in scalar_values(
                AssessmentService.reveal_grader_answer(exercise.grader)
            )
        }
        if not protected:
            return False
        graders = []
        pending_graders = [
            exercise.grader
            for exercise in exercises
            if exercise.grader.kind != "advisory"
        ]
        while pending_graders:
            grader = pending_graders.pop()
            graders.append(grader)
            if grader.kind == "multipart":
                pending_graders.extend(grader.parts)
        numeric_aliases: set[str] = set()
        for grader in graders:
            raw_values: tuple[object, ...] = ()
            if grader.kind == "numeric":
                raw_values = (grader.expected,)
            elif grader.kind == "unit":
                raw_values = (grader.expected_value,)
            elif grader.kind == "vector":
                raw_values = tuple(grader.expected_components)
            for raw_value in raw_values:
                try:
                    numeric_value = AssessmentService._numeric_value(raw_value)
                except (ArithmeticError, TypeError, ValueError):
                    continue
                numeric_aliases.update(_numeric_word_aliases(numeric_value))

        protected_texts = [claim.content for claim in artifact.claims]
        if artifact.refusal_message is not None:
            protected_texts.append(artifact.refusal_message)
        for text in protected_texts:
            normalized = re.sub(r"\s+", " ", text).casefold()
            contexts = []
            for cue in _ANSWER_CUE.finditer(normalized):
                tail = normalized[cue.end() :]
                contexts.append(re.split(r"[.!?;。！？；\n]", tail, maxsplit=1)[0][:160])
            standalone = normalized.strip(" \t\r\n.!?;。！？；")
            if len(standalone) <= 80 and (
                _ANSWER_TOKEN.fullmatch(standalone)
                or _ANSWER_EXPRESSION.fullmatch(standalone)
            ):
                contexts.append(standalone)
            for context in contexts:
                for value in protected:
                    if re.search(
                        r"(?<!\w)" + re.escape(value) + r"(?!\w)", context
                    ):
                        return True
                for alias in numeric_aliases:
                    alias_pattern = re.escape(alias).replace(r"\ ", r"[\s-]+")
                    alias_pattern = alias_pattern.replace(r"\-", r"[\s-]+")
                    if re.search(
                        r"(?<!\w)" + alias_pattern + r"(?!\w)", context
                    ):
                        return True
                candidates = {
                    match.group(0).strip()
                    for pattern in (_ANSWER_EXPRESSION, _ANSWER_TOKEN)
                    for match in pattern.finditer(context)
                }
                if any(
                    AssessmentService.grade(exercise.blueprint, candidate).correct
                    is True
                    for exercise in exercises
                    for candidate in candidates
                ):
                    return True
        return False

    @staticmethod
    def _reveal_artifact(
        exercise: CourseExercise,
        *,
        evidence_ids: set[str],
        learner_content: str,
    ) -> TutorModelArtifact:
        anchor_ids = tuple(
            anchor
            for anchor in exercise.source_anchor_ids
            if anchor in evidence_ids
        )
        if not anchor_ids:
            raise TutorGroundingError(
                "Tutor answer reveal has no current evidence for the exercise."
            )
        answer = AssessmentService.reveal_grader_answer(exercise.grader)
        serialized = json.dumps(
            answer,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        prefix = "完整答案：" if re.search(r"[\u3400-\u9fff]", learner_content) else "Answer: "
        return TutorModelArtifact(
            response_kind="answer",
            claims=(
                TutorClaim(
                    content=f"{prefix}{serialized}",
                    anchor_ids=anchor_ids,
                ),
            ),
            insufficient_evidence=False,
            refusal_message=None,
            answer_revealed=True,
        )

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

    @classmethod
    def _message_operation_key(
        cls,
        *,
        scope: TutorScope,
        session_id: str,
        message_key: str,
        content: str,
        intent: TutorIntent,
        exercise: CourseExercise | None,
        concept_key: str | None,
        attempt_key: str | None,
    ) -> tuple[str, str, str]:
        """Bind one client identity to the complete trusted tutor request."""

        identity = cls._event_key(
            session_id,
            message_key,
            scope.chapter_key,
            "message",
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "scope": scope.model_dump(mode="json"),
                    "content": content,
                    "intent": intent,
                    "exercise_key": (
                        exercise.exercise_key if exercise is not None else None
                    ),
                    "concept_key": concept_key,
                    "attempt_key": attempt_key,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return identity, f"{identity}-{fingerprint[:32]}", fingerprint

    @staticmethod
    def _validate_attempt_scope(
        scope: TutorScope,
        exercise: CourseExercise,
        concept_key: str,
    ) -> None:
        if (
            exercise.course != scope.course_id
            or exercise.course_version != scope.course_version_id
            or exercise.chapter != scope.chapter_id
            or exercise.chapter_key != scope.chapter_key
            or concept_key not in set(exercise.blueprint.concept_keys)
        ):
            raise TutorGroundingError(
                "Tutor attempt requires a current exercise and concept."
            )

    async def _attempt_events(
        self,
        scope: TutorScope,
        concept_key: str,
    ) -> tuple[LearningEvent, ...]:
        learning = self.learning_service or LearningService()
        loader = self.event_list_loader or getattr(
            learning, "_load_event_records", None
        )
        if loader is None:
            return ()
        return await loader(
            scope.course_id,
            scope.course_version_id,
            scope.chapter_key,
            concept_key,
        )

    @staticmethod
    def _attempt_matches(
        event: LearningEvent,
        *,
        exercise_key: str,
        attempt_key: str,
    ) -> bool:
        return (
            event.exercise_key == exercise_key
            and getattr(event.payload, "attempt_key", None) == attempt_key
        )

    async def _authored_hint_artifact(
        self,
        *,
        scope: TutorScope,
        session_id: str,
        message_key: str,
        exercise: CourseExercise,
        concept_key: str,
        attempt_key: str,
        evidence: tuple[TutorEvidence, ...],
        learner_content: str,
    ) -> TutorModelArtifact:
        self._validate_attempt_scope(scope, exercise, concept_key)
        anchor_ids = tuple(
            anchor_id
            for anchor_id in exercise.source_anchor_ids
            if any(item.anchor_id == anchor_id for item in evidence)
        )
        if not anchor_ids:
            raise TutorGroundingError(
                "Tutor hint has no current evidence for the exercise."
            )
        hints = exercise.blueprint.hints
        event_id = self._event_key(
            session_id, message_key, exercise.exercise_key, "hint"
        )
        learning = self.learning_service or LearningService()
        loader = self.event_loader or getattr(learning, "_load_event_by_key", None)
        existing = (
            await loader(scope.course_id, event_id) if loader is not None else None
        )
        if existing is not None:
            if (
                existing.course_version_id != scope.course_version_id
                or existing.chapter_key != scope.chapter_key
                or existing.concept_key != concept_key
                or existing.exercise_key != exercise.exercise_key
                or existing.kind != "hint_viewed"
                or not isinstance(existing.payload, HintViewedPayload)
                or existing.payload.attempt_key != attempt_key
            ):
                raise TutorGroundingError(
                    "Tutor hint identity already has different event content."
                )
            hint_index = existing.payload.hint_index
            event = existing
        else:
            events = await self._attempt_events(scope, concept_key)
            used = [
                event.payload.hint_index
                for event in events
                if event.kind == "hint_viewed"
                and isinstance(event.payload, HintViewedPayload)
                and self._attempt_matches(
                    event,
                    exercise_key=exercise.exercise_key,
                    attempt_key=attempt_key,
                )
            ]
            hint_index = max(used, default=0) + 1
            if hint_index > len(hints):
                return TutorModelArtifact(
                    response_kind="refusal",
                    claims=(),
                    insufficient_evidence=True,
                    refusal_message=(
                        "本次尝试的全部分层提示已显示。"
                        if re.search(r"[\u3400-\u9fff]", learner_content)
                        else "All authored hints for this attempt have been shown."
                    ),
                    answer_revealed=False,
                )
            occurred_at = self.clock()
            if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                raise TutorGroundingError("Tutor clock must include a timezone.")
            event = LearningEvent(
                event_id=event_id,
                course_id=scope.course_id,
                course_version_id=scope.course_version_id,
                chapter_key=scope.chapter_key,
                concept_key=concept_key,
                exercise_key=exercise.exercise_key,
                kind="hint_viewed",
                payload=HintViewedPayload(
                    attempt_key=attempt_key,
                    hint_index=hint_index,
                ),
                occurred_at=occurred_at.astimezone(timezone.utc),
            )
        artifact = TutorModelArtifact(
            response_kind="hint",
            claims=(
                TutorClaim(
                    content=hints[hint_index - 1],
                    anchor_ids=anchor_ids,
                ),
            ),
            insufficient_evidence=False,
            refusal_message=None,
            answer_revealed=False,
        )
        if await asyncio.to_thread(
            self._contains_protected_answer,
            artifact,
            (exercise,),
        ):
            raise TutorGroundingError(
                "Authored hint contains a protected answer; use explicit reveal."
            )
        try:
            await learning.append_event(event)
        except InvalidInputError:
            concurrent = (
                await loader(scope.course_id, event_id)
                if loader is not None
                else None
            )
            if concurrent is None or concurrent != event:
                raise
            await learning.append_event(concurrent)
        return artifact

    async def _diagnosis_artifact(
        self,
        *,
        scope: TutorScope,
        exercise: CourseExercise,
        concept_key: str,
        attempt_key: str,
        evidence: tuple[TutorEvidence, ...],
        learner_content: str,
    ) -> TutorModelArtifact:
        self._validate_attempt_scope(scope, exercise, concept_key)
        events = await self._attempt_events(scope, concept_key)
        graded = tuple(
            event
            for event in events
            if event.kind
            in {"graded_correct", "graded_incorrect", "review_completed"}
            and self._attempt_matches(
                event,
                exercise_key=exercise.exercise_key,
                attempt_key=attempt_key,
            )
        )
        if not graded:
            return TutorModelArtifact(
                response_kind="refusal",
                claims=(),
                insufficient_evidence=True,
                refusal_message=(
                    "尚未找到该题本次尝试的评分记录，请先提交答案。"
                    if re.search(r"[\u3400-\u9fff]", learner_content)
                    else "No graded record exists for this attempt; submit it first."
                ),
                answer_revealed=False,
            )
        latest = max(graded, key=lambda event: event.occurred_at)
        correct = latest.kind == "graded_correct" or bool(
            getattr(latest.payload, "correct", False)
        )
        anchor_ids = tuple(
            anchor_id
            for anchor_id in exercise.source_anchor_ids
            if any(item.anchor_id == anchor_id for item in evidence)
        )
        if not anchor_ids:
            raise TutorGroundingError(
                "Tutor diagnosis has no current evidence for the exercise."
            )
        chinese = bool(re.search(r"[\u3400-\u9fff]", learner_content))
        content = (
            "该尝试已通过确定性评分。"
            if chinese and correct
            else "该尝试尚未通过确定性评分；请查看下一层分步提示。"
            if chinese
            else "This attempt passed deterministic grading."
            if correct
            else "This attempt did not pass deterministic grading; use the next authored hint."
        )
        return TutorModelArtifact(
            response_kind="diagnosis",
            claims=(TutorClaim(content=content, anchor_ids=anchor_ids),),
            insufficient_evidence=False,
            refusal_message=None,
            answer_revealed=False,
        )

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
        reveal_event_id = self._event_key(
            session_id, attempt_key, exercise.exercise_key, "reveal"
        )
        transfer_event_id = self._event_key(
            session_id, attempt_key, exercise.exercise_key, "transfer"
        )
        revealed_candidate = LearningEvent(
            event_id=reveal_event_id,
            course_id=scope.course_id,
            course_version_id=scope.course_version_id,
            chapter_key=scope.chapter_key,
            concept_key=concept_key,
            exercise_key=exercise.exercise_key,
            kind="answer_revealed",
            payload=payload,
            occurred_at=occurred_at,
        )
        required_candidate = LearningEvent(
            event_id=transfer_event_id,
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
        loader = self.event_loader or getattr(learning, "_load_event_by_key", None)

        async def persisted_pair() -> tuple[LearningEvent | None, LearningEvent | None]:
            if loader is None:
                return None, None
            return tuple(
                await asyncio.gather(
                    loader(scope.course_id, reveal_event_id),
                    loader(scope.course_id, transfer_event_id),
                )
            )  # type: ignore[return-value]

        def same_content(existing: LearningEvent, candidate: LearningEvent) -> bool:
            return (
                existing.event_id == candidate.event_id
                and existing.course_id == candidate.course_id
                and existing.course_version_id == candidate.course_version_id
                and existing.chapter_key == candidate.chapter_key
                and existing.concept_key == candidate.concept_key
                and existing.exercise_key == candidate.exercise_key
                and existing.kind == candidate.kind
                and existing.payload == candidate.payload
            )

        existing_revealed, existing_required = await persisted_pair()
        if (existing_revealed is None) != (existing_required is None):
            raise TutorGroundingError("Tutor reveal audit pair is incomplete.")
        if existing_revealed is not None and existing_required is not None:
            if (
                not same_content(existing_revealed, revealed_candidate)
                or not same_content(existing_required, required_candidate)
                or existing_revealed.occurred_at != existing_required.occurred_at
            ):
                raise TutorGroundingError(
                    "Tutor reveal identity already has different event content."
                )
            revealed, required = existing_revealed, existing_required
        else:
            revealed, required = revealed_candidate, required_candidate
        try:
            await learning.append_reveal_events(revealed, required)
        except InvalidInputError:
            concurrent_revealed, concurrent_required = await persisted_pair()
            if concurrent_revealed is None or concurrent_required is None:
                raise
            if (
                not same_content(concurrent_revealed, revealed_candidate)
                or not same_content(concurrent_required, required_candidate)
                or concurrent_revealed.occurred_at != concurrent_required.occurred_at
            ):
                raise
            await learning.append_reveal_events(
                concurrent_revealed, concurrent_required
            )

    async def _execute_reserved_operation(
        self,
        *,
        scope: TutorScope,
        session_id: str,
        message_key: str,
        content: str,
        intent: TutorIntent,
        evidence: tuple[TutorEvidence, ...],
        exercise: CourseExercise | None,
        protected_exercises: tuple[CourseExercise, ...],
        concept_key: str | None,
        attempt_key: str | None,
        session: CourseTutorSession,
        turns: tuple[CourseTutorTurn, ...],
        turn_loader: TurnLoader,
        operation_identity: str,
        operation_key: str,
    ) -> TutorResponse:
        validated_evidence = self._validated_evidence(scope, evidence)
        if intent in {"diagnose", "hint", "reveal"}:
            exercise_anchor_ids = set(
                cast(CourseExercise, exercise).source_anchor_ids
            )
            selected_evidence = tuple(
                item
                for item in validated_evidence
                if item.anchor_id in exercise_anchor_ids
            )[:_TUTOR_EVIDENCE_LIMIT]
        else:
            selected_evidence = self._select_evidence(content, validated_evidence)

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
        elif intent == "reveal":
            evidence_ids = {item.anchor_id for item in selected_evidence}
            artifact = self._reveal_artifact(
                cast(CourseExercise, exercise),
                evidence_ids=evidence_ids,
                learner_content=content,
            )
        elif intent == "hint":
            artifact = await self._authored_hint_artifact(
                scope=scope,
                session_id=session_id,
                message_key=message_key,
                exercise=cast(CourseExercise, exercise),
                concept_key=cast(str, concept_key),
                attempt_key=cast(str, attempt_key),
                evidence=selected_evidence,
                learner_content=content,
            )
        elif intent == "diagnose":
            artifact = await self._diagnosis_artifact(
                scope=scope,
                exercise=cast(CourseExercise, exercise),
                concept_key=cast(str, concept_key),
                attempt_key=cast(str, attempt_key),
                evidence=selected_evidence,
                learner_content=content,
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
            artifact = await asyncio.to_thread(
                self._grounded_output,
                generated,
                intent=intent,
                evidence=selected_evidence,
                protected_exercises=(
                    protected_exercises
                    or ((exercise,) if exercise is not None else ())
                ),
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
            operation_key=operation_key,
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
            operation_key=operation_key,
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
        try:
            await appender(user_turn, assistant_turn)
        except TutorGroundingError:
            latest = await turn_loader(session_id)
            self._validate_history(latest, session)
            replayed = self._replayed_turn(
                latest,
                operation_identity=operation_identity,
                operation_key=operation_key,
                user_content=content,
            )
            if replayed is None:
                raise
            return self._public_response(session_id, replayed)
        return self._public_response(session_id, assistant_turn)

    async def respond(
        self,
        *,
        scope: TutorScope,
        session_id: str,
        message_key: str,
        content: str,
        intent: TutorIntent,
        evidence: tuple[TutorEvidence, ...],
        exercise: CourseExercise | None = None,
        protected_exercises: tuple[CourseExercise, ...] = (),
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
        turn_loader = self.turn_loader or self._default_turn_loader
        turns = await turn_loader(session_id)
        self._validate_history(turns, session)
        if intent in {"diagnose", "hint", "reveal"} and (
            exercise is None or concept_key is None or attempt_key is None
        ):
            raise TutorGroundingError(
                "Tutor diagnose, hint, and reveal require an exercise and attempt identity."
            )
        operation_identity, operation_key, request_fingerprint = (
            self._message_operation_key(
                scope=scope,
                session_id=session_id,
                message_key=message_key,
                content=content,
                intent=intent,
                exercise=exercise,
                concept_key=concept_key,
                attempt_key=attempt_key,
            )
        )
        operation = CourseTutorOperation(
            id=self._operation_record_id(session_id, operation_identity),
            course=scope.course_id,
            course_version=scope.course_version_id,
            session=session_id,
            chapter_key=scope.chapter_key,
            operation_identity=operation_identity,
            operation_key=operation_key,
            request_fingerprint=request_fingerprint,
        )
        operation = await self._reserve_message_operation(operation)
        replayed = self._replayed_turn(
            turns,
            operation_identity=operation_identity,
            operation_key=operation_key,
            user_content=content,
        )
        if replayed is not None:
            return self._public_response(session_id, replayed)
        lease_token = secrets.token_hex(16)
        if not await self._acquire_operation_lease(operation, lease_token):
            raise TutorGroundingError(
                "Tutor request is already in progress; retry shortly."
            )
        owner_task = asyncio.current_task()
        if owner_task is None:
            await self._release_operation_lease(operation, lease_token)
            raise TutorGroundingError("Tutor request has no execution task.")
        lease_failures: list[BaseException] = []
        heartbeat = asyncio.create_task(
            self._maintain_operation_lease(
                operation,
                lease_token,
                cast(asyncio.Task[object], owner_task),
                lease_failures,
            )
        )
        try:
            # A previous owner may have committed the turn pair just before
            # releasing its lease. Re-read after acquiring execution rights.
            latest = await turn_loader(session_id)
            self._validate_history(latest, session)
            replayed = self._replayed_turn(
                latest,
                operation_identity=operation_identity,
                operation_key=operation_key,
                user_content=content,
            )
            if replayed is not None:
                return self._public_response(session_id, replayed)
            return await self._execute_reserved_operation(
                scope=scope,
                session_id=session_id,
                message_key=message_key,
                content=content,
                intent=intent,
                evidence=evidence,
                exercise=exercise,
                protected_exercises=protected_exercises,
                concept_key=concept_key,
                attempt_key=attempt_key,
                session=session,
                turns=latest,
                turn_loader=turn_loader,
                operation_identity=operation_identity,
                operation_key=operation_key,
            )
        except asyncio.CancelledError as exc:
            if lease_failures:
                raise TutorGroundingError(
                    "Tutor operation stopped because its execution lease was lost."
                ) from lease_failures[0]
            raise exc
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self._release_operation_lease(operation, lease_token)


__all__ = [
    "TutorEvidence",
    "TutorGroundingError",
    "TutorIntent",
    "TutorScope",
    "TutorService",
]
