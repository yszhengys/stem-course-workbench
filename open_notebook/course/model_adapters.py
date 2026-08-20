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

import httpx
from pydantic import BaseModel

from open_notebook.ai.models import Model
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


async def ensure_course_models_selectable(
    selections: list[ModelSelection],
) -> None:
    """Fail closed unless every exact selection is currently selectable."""

    if os.getenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS") != "1":
        raise AdapterError("Real Course model adapters are disabled.")
    required: set[tuple[str, str]] = {
        (selection.adapter, selection.model) for selection in selections
    }
    selectable: set[tuple[str, str]] = set()
    if any(adapter == "codex_cli" for adapter, _ in required):
        if CodexCliAdapter.is_available():
            selectable.update(
                ("codex_cli", model)
                for model in ("gpt-5.6-sol", "gpt-5.6-luna")
            )
    if any(adapter == "open_notebook" for adapter, _ in required):
        configured = await Model.get_models_by_type("language")
        selectable.update(
            ("open_notebook", str(model.id))
            for model in configured
            if model.id is not None
        )
    if any(adapter == "ollama" for adapter, _ in required):
        host = os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(f"{host.rstrip('/')}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError):
            payload = {}
        models = payload.get("models", []) if isinstance(payload, dict) else []
        selectable.update(
            ("ollama", name)
            for item in models
            if isinstance(item, dict)
            for name in (item.get("name") or item.get("model"),)
            if isinstance(name, str)
        )
    unavailable = sorted(required - selectable)
    if unavailable:
        rendered = ", ".join(f"{adapter}/{model}" for adapter, model in unavailable)
        raise AdapterError(f"Selected Course model is unavailable: {rendered}.")


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
            discovered = shutil.which(configured)
            if discovered:
                return discovered
            raise AdapterError(
                "Codex CLI was not found. Configure CODEX_CLI_PATH or install Codex."
            )
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        if cls.BUNDLED_MACOS_BINARY.is_file():
            return str(cls.BUNDLED_MACOS_BINARY)
        raise AdapterError(
            "Codex CLI was not found. Configure CODEX_CLI_PATH or install Codex."
        )

    @classmethod
    def is_available(cls) -> bool:
        """Probe discovery without exposing the resolved local filesystem path."""

        try:
            cls.discover_binary()
        except AdapterError:
            return False
        return True

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
        if "invalid_json_schema" in summary or "invalid schema for response_format" in summary:
            return AdapterError("Codex CLI rejected the requested output schema.")
        return AdapterError(f"Codex CLI failed with exit code {returncode}.")

    @staticmethod
    def output_schema(output_model: type[BaseModel]) -> dict[str, Any]:
        """Convert Pydantic defaults into Codex strict structured-output fields."""

        def strict(value: Any) -> Any:
            if isinstance(value, list):
                return [strict(item) for item in value]
            if not isinstance(value, dict):
                return value
            if (
                value.get("type") == "object"
                and "properties" not in value
                and "additionalProperties" in value
            ):
                encoded: dict[str, Any] = {
                    "type": "array",
                    "description": (
                        "Dictionary entries encoded as key plus value_json, where "
                        "value_json is one valid compact JSON value string."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "minLength": 1},
                            "value_json": {"type": "string", "minLength": 1},
                        },
                        "required": ["key", "value_json"],
                        "additionalProperties": False,
                    },
                }
                if isinstance(value.get("minProperties"), int):
                    encoded["minItems"] = value["minProperties"]
                if isinstance(value.get("maxProperties"), int):
                    encoded["maxItems"] = value["maxProperties"]
                if isinstance(value.get("title"), str):
                    encoded["title"] = value["title"]
                return encoded
            result = {
                key: strict(item)
                for key, item in value.items()
                if key
                not in {
                    "default",
                    "discriminator",
                    "exclusiveMaximum",
                    "exclusiveMinimum",
                    "maxProperties",
                    "minProperties",
                }
            }
            if "oneOf" in result:
                result["anyOf"] = result.pop("oneOf")
            if "const" in result:
                result["enum"] = [result.pop("const")]
            prefix_items = result.pop("prefixItems", None)
            if isinstance(prefix_items, list) and prefix_items:
                result["items"] = (
                    prefix_items[0]
                    if all(item == prefix_items[0] for item in prefix_items)
                    else {"anyOf": prefix_items}
                )
            properties = result.get("properties")
            if isinstance(properties, dict):
                result["required"] = list(properties)
                result["additionalProperties"] = False
            return result

        return strict(output_model.model_json_schema())

    @staticmethod
    def _has_dynamic_maps(output_model: type[BaseModel]) -> bool:
        pending: list[Any] = [output_model.model_json_schema()]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if (
                    value.get("type") == "object"
                    and "properties" not in value
                    and "additionalProperties" in value
                ):
                    return True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return False

    @staticmethod
    def restore_output_payload(
        payload: dict[str, Any], output_model: type[BaseModel]
    ) -> dict[str, Any]:
        """Restore strict-schema dictionary entries before Pydantic validation."""

        root_schema = output_model.model_json_schema()

        def resolve(schema: Any) -> Any:
            if not isinstance(schema, dict):
                return schema
            reference = schema.get("$ref")
            if not isinstance(reference, str) or not reference.startswith("#/"):
                return schema
            target: Any = root_schema
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return target

        def branch_for(value: Any, schemas: list[Any]) -> Any:
            candidates = [resolve(schema) for schema in schemas]
            if value is None:
                return next(
                    (
                        schema
                        for schema in candidates
                        if isinstance(schema, dict) and schema.get("type") == "null"
                    ),
                    {},
                )
            if isinstance(value, dict) and isinstance(value.get("kind"), str):
                for schema in candidates:
                    if not isinstance(schema, dict):
                        continue
                    kind = schema.get("properties", {}).get("kind", {})
                    expected = kind.get("const")
                    if expected is None:
                        enum = kind.get("enum")
                        expected = enum[0] if isinstance(enum, list) and enum else None
                    if expected == value["kind"]:
                        return schema
            expected_type = (
                "object"
                if isinstance(value, dict)
                else "array"
                if isinstance(value, list)
                else "boolean"
                if isinstance(value, bool)
                else "number"
                if isinstance(value, (int, float))
                else "string"
                if isinstance(value, str)
                else None
            )
            return next(
                (
                    schema
                    for schema in candidates
                    if isinstance(schema, dict)
                    and schema.get("type") == expected_type
                ),
                candidates[0] if candidates else {},
            )

        def restore(value: Any, schema: Any) -> Any:
            schema = resolve(schema)
            if not isinstance(schema, dict):
                return value
            union = schema.get("anyOf") or schema.get("oneOf")
            if isinstance(union, list):
                return restore(value, branch_for(value, union))
            if (
                schema.get("type") == "object"
                and "properties" not in schema
                and "additionalProperties" in schema
            ):
                if not isinstance(value, list):
                    return value
                restored: dict[str, Any] = {}
                child_schema = schema.get("additionalProperties")
                for entry in value:
                    if not isinstance(entry, dict):
                        raise AdapterError(
                            "Codex CLI returned an invalid encoded dictionary."
                        )
                    key = entry.get("key")
                    encoded = entry.get("value_json")
                    if not isinstance(key, str) or not isinstance(encoded, str):
                        raise AdapterError(
                            "Codex CLI returned an invalid encoded dictionary."
                        )
                    if key in restored:
                        raise AdapterError(
                            "Codex CLI returned a duplicate dictionary key."
                        )
                    try:
                        decoded = json.loads(encoded)
                    except json.JSONDecodeError as exc:
                        raise AdapterError(
                            "Codex CLI returned an invalid encoded dictionary value."
                        ) from exc
                    restored[key] = restore(
                        decoded, child_schema if isinstance(child_schema, dict) else {}
                    )
                return restored
            properties = schema.get("properties")
            if isinstance(properties, dict) and isinstance(value, dict):
                return {
                    key: restore(item, properties.get(key, {}))
                    for key, item in value.items()
                }
            if schema.get("type") == "array" and isinstance(value, list):
                prefix_items = schema.get("prefixItems")
                item_schema = schema.get("items", {})
                return [
                    restore(
                        item,
                        prefix_items[index]
                        if isinstance(prefix_items, list) and index < len(prefix_items)
                        else item_schema,
                    )
                    for index, item in enumerate(value)
                ]
            return value

        restored = restore(payload, root_schema)
        if not isinstance(restored, dict):
            raise AdapterError("Codex CLI output was not a JSON object.")
        return restored

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
                json.dumps(self.output_schema(output_model), ensure_ascii=False),
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
            codex_prompt = prompt
            if self._has_dynamic_maps(output_model):
                codex_prompt += (
                    "\n\nCodex schema compatibility: every dictionary/map field is "
                    "represented by the output schema as an array of objects with "
                    "exact keys key and value_json. Encode each original dictionary "
                    "value as one valid compact JSON string in value_json."
                )
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
                    process.communicate(codex_prompt.encode("utf-8")),
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
            payload = self.restore_output_payload(payload, output_model)
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
