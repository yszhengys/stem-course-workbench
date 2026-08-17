"""Explicit, structured model adapters for Course generation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from open_notebook.ai.provision import provision_langchain_model

from .contracts import GenerationRequest, ModelSelection
from .locking import course_job_lock

OutputT = TypeVar("OutputT", bound=BaseModel)


class AdapterError(RuntimeError):
    """A sanitized model discovery, invocation, or structured-output failure."""


def _parse_json_payload(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError("Model output was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise AdapterError("Model output must be a JSON object.")
    return payload


class CourseModelAdapter:
    """One interface for every explicitly selected Course model backend."""

    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[OutputT],
        *,
        prompt: str,
    ) -> OutputT:
        raise NotImplementedError


class CodexCliAdapter(CourseModelAdapter):
    BUNDLED_MACOS_BINARY = Path(
        "/Applications/ChatGPT.app/Contents/Resources/codex"
    )

    def __init__(
        self, binary: str | None = None, timeout_seconds: float = 30 * 60
    ) -> None:
        self.binary = self.discover_binary(binary)
        self.timeout_seconds = timeout_seconds

    @classmethod
    def discover_binary(cls, explicit: str | None = None) -> str:
        if explicit:
            return explicit
        configured = os.getenv("CODEX_CLI_PATH")
        if configured:
            return configured
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        if cls.BUNDLED_MACOS_BINARY.is_file():
            return str(cls.BUNDLED_MACOS_BINARY)
        raise AdapterError(
            "Codex CLI was not found. Configure CODEX_CLI_PATH or install Codex."
        )

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME")
            if key in os.environ
        }
        environment["PYTHONIOENCODING"] = "utf-8"
        return environment

    @staticmethod
    def _classify_failure(returncode: int, stderr: bytes) -> AdapterError:
        summary = stderr.decode("utf-8", errors="ignore").lower()
        if any(token in summary for token in ("login", "authentication", "unauthorized")):
            return AdapterError(
                "Codex CLI authentication is required; sign in and retry."
            )
        if any(token in summary for token in ("quota", "rate limit", "usage limit")):
            return AdapterError(
                "Codex CLI quota was exceeded; review usage limits and retry later."
            )
        return AdapterError(f"Codex CLI failed with exit code {returncode}.")

    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[OutputT],
        *,
        prompt: str,
    ) -> OutputT:
        with tempfile.TemporaryDirectory(prefix="course-codex-") as temp_dir:
            root = Path(temp_dir)
            schema_path = root / "schema.json"
            output_path = root / "last-message.json"
            schema_path.write_text(
                json.dumps(output_model.model_json_schema(), ensure_ascii=False),
                encoding="utf-8",
            )
            arguments = [
                self.binary,
                "exec",
                "--model",
                request.model.model,
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if request.model.reasoning_effort is not None:
                arguments.extend(
                    [
                        "--config",
                        f"model_reasoning_effort={request.model.reasoning_effort}",
                    ]
                )
            arguments.append("-")
            process: asyncio.subprocess.Process | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=temp_dir,
                    env=self._environment(),
                    start_new_session=True,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                await self._terminate_process(process)
                raise AdapterError(
                    f"Codex CLI timed out after {self.timeout_seconds:g} seconds."
                ) from exc
            except asyncio.CancelledError:
                await self._terminate_process(process)
                raise
            except OSError as exc:
                raise AdapterError("Unable to start the configured Codex CLI.") from exc

            returncode = process.returncode
            if returncode is None or returncode != 0:
                raise self._classify_failure(returncode or -1, stderr)
            raw = (
                output_path.read_text(encoding="utf-8")
                if output_path.is_file()
                else stdout.decode("utf-8", errors="replace")
            )
            payload = _parse_json_payload(raw)
            try:
                return output_model.model_validate(payload)
            except Exception as exc:
                raise AdapterError(
                    "Codex CLI returned JSON that did not match the requested schema."
                ) from exc

    @staticmethod
    async def _terminate_process(
        process: asyncio.subprocess.Process | None,
    ) -> None:
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (PermissionError, ProcessLookupError):
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (PermissionError, ProcessLookupError):
                    process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (TimeoutError, ProcessLookupError):
                    pass


class OpenNotebookModelAdapter(CourseModelAdapter):
    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[OutputT],
        *,
        prompt: str,
    ) -> OutputT:
        if (
            request.model.adapter != "open_notebook"
            or not request.model.model.startswith("model:")
        ):
            raise AdapterError(
                "Open Notebook Course generation requires a registered model ID."
            )
        try:
            model = await provision_langchain_model(
                "", request.model.model, default_type="language"
            )
            response = await model.ainvoke(prompt)
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return output_model.model_validate(_parse_json_payload(str(content)))
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                "The selected Open Notebook model failed or returned invalid output."
            ) from exc


class OllamaModelAdapter(CourseModelAdapter):
    def __init__(self, host: str | None = None) -> None:
        self.host = host or os.getenv("OLLAMA_HOST")

    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[OutputT],
        *,
        prompt: str,
    ) -> OutputT:
        try:
            from ollama import AsyncClient

            client = AsyncClient(host=self.host) if self.host else AsyncClient()
            async with course_job_lock():
                response = await client.chat(
                    model=request.model.model,
                    messages=[{"role": "user", "content": prompt}],
                    format=output_model.model_json_schema(),
                    options={"temperature": 0},
                )
            message = response.get("message", {})
            return output_model.model_validate(
                _parse_json_payload(str(message.get("content", "")))
            )
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                "The selected Ollama model failed or returned invalid output."
            ) from exc


@dataclass(frozen=True)
class FakeAdapterCall:
    request: GenerationRequest
    output_model: type[BaseModel]
    prompt: str


class FakeCourseModelAdapter(CourseModelAdapter):
    """Deterministic in-memory adapter; never starts a real model process."""

    def __init__(self, output: BaseModel | dict[str, Any]) -> None:
        self.output = output
        self.calls: list[FakeAdapterCall] = []

    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[OutputT],
        *,
        prompt: str,
    ) -> OutputT:
        self.calls.append(FakeAdapterCall(request, output_model, prompt))
        if isinstance(self.output, output_model):
            return self.output
        return output_model.model_validate(self.output)


def build_adapter(
    selection: ModelSelection,
    *,
    binary: str | None = None,
    allow_real: bool | None = None,
) -> CourseModelAdapter:
    enabled = (
        os.getenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS") == "1"
        if allow_real is None
        else allow_real
    )
    if not enabled:
        raise AdapterError(
            "Real Course model adapters are disabled; explicitly enable them to run."
        )
    if selection.adapter == "codex_cli":
        return CodexCliAdapter(binary=binary)
    if selection.adapter == "open_notebook":
        return OpenNotebookModelAdapter()
    if selection.adapter == "ollama":
        return OllamaModelAdapter()
    raise AdapterError("Unknown Course model adapter.")
