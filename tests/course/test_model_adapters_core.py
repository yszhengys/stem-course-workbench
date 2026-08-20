import asyncio
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

from open_notebook.course.contracts import GenerationRequest, ModelSelection
from open_notebook.course.model_adapters import (
    AdapterError,
    CodexCliAdapter,
    FakeCourseModelAdapter,
    OllamaModelAdapter,
    OpenNotebookModelAdapter,
    build_adapter,
)


class Output(BaseModel):
    answer: str


class MapOutput(BaseModel):
    values: dict[str, float]
    objects: list[dict[str, object]]


def _request(adapter: str = "codex_cli", model: str = "gpt-5.6-sol") -> GenerationRequest:
    return GenerationRequest(
        stage="outline",
        course_id="course:one",
        model=ModelSelection(
            adapter=adapter,  # type: ignore[arg-type]
            model=model,
            reasoning_effort="max" if adapter == "codex_cli" else None,
        ),
        anchor_ids=["anchor:one"],
        prompt_version="v1",
        schema_name="output",
    )


def test_codex_binary_discovery_order(monkeypatch, tmp_path: Path):
    explicit = tmp_path / "explicit"
    env_binary = tmp_path / "env"
    monkeypatch.setenv("CODEX_CLI_PATH", str(env_binary))
    monkeypatch.setattr(
        "shutil.which",
        lambda candidate: (
            str(env_binary)
            if candidate == str(env_binary)
            else "/usr/local/bin/codex"
        ),
    )

    assert CodexCliAdapter(binary=str(explicit)).binary == str(explicit)
    assert CodexCliAdapter().binary == str(env_binary)

    monkeypatch.delenv("CODEX_CLI_PATH")
    assert CodexCliAdapter().binary == "/usr/local/bin/codex"


def test_codex_uses_bundled_binary_only_when_it_exists(monkeypatch):
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(Path, "is_file", lambda path: str(path).endswith("/codex"))
    assert CodexCliAdapter().binary.endswith("/Applications/ChatGPT.app/Contents/Resources/codex")

    monkeypatch.setattr(Path, "is_file", lambda _: False)
    with pytest.raises(AdapterError, match="not found"):
        CodexCliAdapter()


def test_invalid_configured_codex_path_fails_closed_without_path_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/private/missing/codex")
    monkeypatch.setattr(
        "shutil.which",
        lambda candidate: "/usr/local/bin/codex" if candidate == "codex" else None,
    )

    with pytest.raises(AdapterError, match="not found"):
        CodexCliAdapter()
    assert CodexCliAdapter.is_available() is False


@pytest.mark.asyncio
async def test_codex_exact_safe_invocation_stdin_env_and_tempdir(monkeypatch):
    calls: dict[str, object] = {}

    class Process:
        returncode = 0
        pid = 123

        async def communicate(self, data):
            calls["stdin"] = data
            args = list(cast(tuple[str, ...], calls["args"]))
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"answer": "ok"}), encoding="utf-8")
            return b"", b""

        async def wait(self):
            return 0

    async def create(*args, **kwargs):
        calls.update(args=args, kwargs=kwargs)
        return Process()

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/tmp/test-home")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await CodexCliAdapter(binary="/usr/bin/codex").generate(
        _request(), Output, prompt="private full prompt"
    )

    assert result.answer == "ok"
    args = list(cast(tuple[str, ...], calls["args"]))
    assert args[:4] == ["/usr/bin/codex", "exec", "--model", "gpt-5.6-sol"]
    assert args[-1] == "-"
    for required in (
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--output-schema",
    ):
        assert required in args
    assert "private full prompt" not in args
    assert calls["stdin"] == b"private full prompt"
    kwargs = cast(dict[str, object], calls["kwargs"])
    assert kwargs["start_new_session"] is True
    assert Path(cast(str, kwargs["cwd"])).name.startswith("course-codex-")
    assert kwargs["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/test-home",
        "PYTHONIOENCODING": "utf-8",
    }


def test_codex_output_schema_requires_every_declared_property() -> None:
    from open_notebook.course.contracts import CourseOutlineArtifact

    schema = CodexCliAdapter.output_schema(CourseOutlineArtifact)
    pending: list[object] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            assert "default" not in value
            assert "maxProperties" not in value
            assert "minProperties" not in value
            assert "discriminator" not in value
            assert "oneOf" not in value
            properties = value.get("properties")
            if isinstance(properties, dict):
                assert value.get("required") == list(properties)
                assert value.get("additionalProperties") is False
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def test_codex_output_schema_round_trips_dynamic_json_maps() -> None:
    schema = CodexCliAdapter.output_schema(MapOutput)
    values = schema["properties"]["values"]
    objects = schema["properties"]["objects"]["items"]

    assert values["type"] == "array"
    assert objects["type"] == "array"
    assert values["items"]["required"] == ["key", "value_json"]
    assert values["items"]["additionalProperties"] is False

    encoded = {
        "values": [
            {"key": "x", "value_json": "2.5"},
            {"key": "y", "value_json": "-1"},
        ],
        "objects": [
            [
                {"key": "kind", "value_json": '"point"'},
                {"key": "coordinates", "value_json": "[1,2]"},
            ]
        ],
    }

    restored = CodexCliAdapter.restore_output_payload(encoded, MapOutput)
    assert MapOutput.model_validate(restored) == MapOutput(
        values={"x": 2.5, "y": -1.0},
        objects=[{"kind": "point", "coordinates": [1, 2]}],
    )

    duplicate = {
        **encoded,
        "values": [
            {"key": "x", "value_json": "1"},
            {"key": "x", "value_json": "2"},
        ],
    }
    with pytest.raises(AdapterError, match="duplicate dictionary key"):
        CodexCliAdapter.restore_output_payload(duplicate, MapOutput)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "message"),
    [
        (b"login required token=secret", "authentication"),
        (b"quota exceeded key=secret", "quota"),
        (b"invalid_json_schema key=secret", "output schema"),
        (b"internal detail key=secret", "exit code 2"),
    ],
)
async def test_codex_nonzero_errors_are_classified_and_sanitized(
    monkeypatch, stderr: bytes, message: str
):
    class Process:
        returncode = 2
        pid = 123

        async def communicate(self, data):
            return b"", stderr

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(AdapterError, match=message) as caught:
        await CodexCliAdapter(binary="codex").generate(
            _request(), Output, prompt="secret prompt"
        )

    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_codex_invalid_json_is_explicit(monkeypatch):
    class Process:
        returncode = 0
        pid = 123

        async def communicate(self, data):
            return b"not-json", b""

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(AdapterError, match="valid JSON"):
        await CodexCliAdapter(binary="codex").generate(
            _request(), Output, prompt="prompt"
        )


@pytest.mark.asyncio
async def test_codex_timeout_terminates_process_group(monkeypatch):
    killed: list[tuple[int, int]] = []

    class Process:
        returncode = None
        pid = 456

        async def communicate(self, data):
            await asyncio.sleep(10)

        async def wait(self):
            self.returncode = -15
            return self.returncode

        def kill(self):
            self.returncode = -9

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("os.killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(AdapterError, match="timed out"):
        await CodexCliAdapter(binary="codex", timeout_seconds=0.001).generate(
            _request(), Output, prompt="prompt"
        )

    assert killed and killed[0][0] == 456


@pytest.mark.asyncio
async def test_codex_cancel_terminates_process_group(monkeypatch):
    killed: list[int] = []

    class Process:
        returncode = None
        pid = 789

        async def communicate(self, data):
            raise asyncio.CancelledError

        async def wait(self):
            self.returncode = -15
            return self.returncode

        def kill(self):
            self.returncode = -9

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("os.killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(asyncio.CancelledError):
        await CodexCliAdapter(binary="codex").generate(
            _request(), Output, prompt="prompt"
        )

    assert killed == [789]


@pytest.mark.asyncio
async def test_codex_forced_termination_sends_sigkill_to_process_group(monkeypatch):
    signals: list[tuple[int, int]] = []

    class Process:
        returncode = None
        pid = 987

        async def wait(self):
            return None

        def kill(self):
            raise AssertionError("must not kill only the child PID")

    async def timeout(coroutine, **kwargs):
        coroutine.close()
        raise TimeoutError

    monkeypatch.setattr("os.killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(asyncio, "wait_for", timeout)

    await CodexCliAdapter._terminate_process(Process())  # type: ignore[arg-type]

    assert signals == [
        (987, signal.SIGTERM),
        (987, signal.SIGKILL),
    ]


@pytest.mark.asyncio
async def test_open_notebook_passes_exact_selected_model_without_fallback(monkeypatch):
    calls: dict[str, object] = {}
    long_prompt = "grounded evidence " * 120_000

    class Model:
        async def ainvoke(self, prompt):
            calls["invoked_prompt"] = prompt
            return SimpleNamespace(content=json.dumps({"answer": "ok"}))

    async def provision(content, model_id, default_type):
        calls.update(
            provisioning_content=content,
            model_id=model_id,
            default_type=default_type,
        )
        return Model()

    monkeypatch.setattr(
        "open_notebook.course.model_adapters.provision_langchain_model", provision
    )
    result = await OpenNotebookModelAdapter().generate(
        _request("open_notebook", "model:deepseek-v4-pro"),
        Output,
        prompt=long_prompt,
    )

    assert result.answer == "ok"
    assert calls == {
        "provisioning_content": "",
        "model_id": "model:deepseek-v4-pro",
        "default_type": "language",
        "invoked_prompt": long_prompt,
    }


@pytest.mark.asyncio
async def test_open_notebook_rejects_display_name_before_provision(monkeypatch):
    async def provision(*_args, **_kwargs):
        raise AssertionError("display names must not reach provisioning")

    monkeypatch.setattr(
        "open_notebook.course.model_adapters.provision_langchain_model", provision
    )

    with pytest.raises(AdapterError, match="registered model ID"):
        await OpenNotebookModelAdapter().generate(
            _request("open_notebook", "deepseek-v4-pro"),
            Output,
            prompt="prompt",
        )


@pytest.mark.asyncio
async def test_ollama_passes_exact_model_and_structured_format(monkeypatch):
    calls: dict[str, object] = {}

    class Client:
        def __init__(self, host=None):
            calls["host"] = host

        async def chat(self, **kwargs):
            calls.update(kwargs)
            return {"message": {"content": json.dumps({"answer": "ok"})}}

    monkeypatch.setattr("ollama.AsyncClient", Client)
    result = await OllamaModelAdapter(host="http://ollama").generate(
        _request("ollama", "qwen3.5:9b"), Output, prompt="prompt"
    )

    assert result.answer == "ok"
    assert calls["model"] == "qwen3.5:9b"
    assert calls["format"] == Output.model_json_schema()


@pytest.mark.asyncio
async def test_fake_adapter_is_deterministic_and_real_adapter_requires_switch(monkeypatch):
    fake = FakeCourseModelAdapter({"answer": "ok"})
    result = await fake.generate(_request(), Output, prompt="prompt")
    assert result.answer == "ok"
    assert fake.calls[0].prompt == "prompt"

    monkeypatch.delenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", raising=False)
    with pytest.raises(AdapterError, match="disabled"):
        build_adapter(_request().model, binary="codex")
    monkeypatch.setenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", "1")
    assert isinstance(build_adapter(_request().model, binary="codex"), CodexCliAdapter)
