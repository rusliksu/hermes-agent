"""Regression test for the "@" context-reference-expansion block in
``GatewayRunner._prepare_inbound_message_text``.

Bug: the block read ``self._model`` / ``self._base_url`` to resolve the
model/base_url for ``get_model_context_length_async``. ``GatewayRunner``
never assigns either attribute (that pattern was copy-pasted from
``HermesCLI``, which does carry ``self.model``/``self.base_url`` — see
commit da44c196b). Every message containing "@" raised ``AttributeError``
inside the ``try`` block, which the surrounding ``except Exception`` silently
swallowed at debug level, so ``preprocess_context_references_async`` never
ran in the gateway and @-references (``@file:``, ``@folder:``, ``@diff``,
etc.) passed through to the model completely unexpanded.

These tests pin the fix: the block must resolve model/provider/base_url via
``self._resolve_session_agent_runtime`` (the same session-aware resolution
the hygiene-compression block already uses) and must actually reach
``preprocess_context_references_async`` with a real context length.
"""
import logging
import threading
from contextlib import contextmanager

import pytest

import gateway.run as gateway_run
from agent.context_references import ContextReferenceResult
from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
    )
    runner.adapters = {}
    # Attrs touched by _resolve_session_agent_runtime on a bare test runner
    # (mirrors tests/gateway/test_empty_model_recovery.py).
    runner._session_model_overrides = {}
    runner._last_resolved_model = {}
    runner._service_tier = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    return runner


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_name="DM",
        chat_type="private",
        user_name="Alice",
    )


def _typed_source(profile_id: str = "family-alpha") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_name="DM",
        chat_type="private",
        user_name="Alice",
        profile=profile_id,
        resolved_access_context=ResolvedAccessContext(
            principal_id="principal-alpha",
            role_id="family_standard",
            profile_id=profile_id,
            conversation_scope=f"dm:{profile_id}",
            capabilities=frozenset({"chat"}),
            delivery_target=DeliveryTarget(
                platform="telegram",
                account="family-bot",
                peer_kind="dm",
                chat_id="123",
            ),
        ),
    )


def _write_config(home, config_text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(config_text, encoding="utf-8")


def _patch_runtime_resolution(monkeypatch) -> None:
    """Stub the module-level runtime resolution so the test never hits the
    network — mirrors _patch_resolution() in test_empty_model_recovery.py."""
    monkeypatch.setattr(
        gateway_run, "_resolve_gateway_model", lambda cfg=None: "openai/gpt-4.1-mini"
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "api_mode": "chat_completions",
        },
    )
    # config_context_length is int > 0, so get_model_context_length_async's
    # config-override short-circuit (agent/model_metadata.py) fires and
    # returns it directly — no network probe needed.
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"model": {"default": "openai/gpt-4.1-mini", "context_length": 128000}},
    )


def _patch_context_ref_runtime(monkeypatch, runner: GatewayRunner) -> None:
    monkeypatch.setattr(runner, "_resolve_session_agent_runtime", lambda **_kwargs: (
        "openai/gpt-4.1-mini",
        {"provider": "openai", "api_key": "test", "base_url": "https://api.openai.com/v1"},
    ))

    import agent.model_metadata as model_meta_mod

    async def _fake_get_context(*_args, **_kwargs):
        return 128000

    monkeypatch.setattr(model_meta_mod, "get_model_context_length_async", _fake_get_context)


@pytest.mark.asyncio
async def test_at_reference_reaches_preprocessor_with_real_context_length(
    monkeypatch, caplog
):
    """A message containing "@" must reach preprocess_context_references_async
    with a real (int > 0) context_length, and the except branch must not
    fire. This fails on unfixed code with AttributeError:
    'GatewayRunner' object has no attribute '_model' (swallowed as a debug
    log, so pre-fix this assertion sees no expansion and no captured call)."""
    runner = _make_runner()
    source = _source()
    _patch_runtime_resolution(monkeypatch)

    captured: dict = {}

    async def _fake_preprocess(message, *, cwd, context_length, url_fetcher=None, allowed_root=None):
        captured["message"] = message
        captured["cwd"] = cwd
        captured["context_length"] = context_length
        captured["allowed_root"] = allowed_root
        return ContextReferenceResult(
            message="[expanded body]",
            original_message=message,
            expanded=True,
        )

    import agent.context_references as ctx_mod
    import agent.model_metadata as model_meta_mod

    async def _fake_get_ctx_len(
        model,
        base_url="",
        api_key="",
        config_context_length=None,
        provider="",
        custom_providers=None,
    ):
        return config_context_length or 128000

    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _fake_preprocess)
    monkeypatch.setattr(
        model_meta_mod,
        "get_model_context_length_async",
        _fake_get_ctx_len,
    )

    caplog.set_level(logging.DEBUG, logger="gateway.run")

    event = MessageEvent(text="please look at @file:notes.txt", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    # The except branch (AttributeError on self._model/self._base_url,
    # pre-fix) must not have fired.
    assert not any(
        "@ context reference expansion failed" in record.getMessage()
        for record in caplog.records
    ), "the except branch swallowed an exception instead of reaching the preprocessor"

    # preprocess_context_references_async must actually have been called,
    # with a real positive context length (not skipped by the AttributeError).
    assert captured, "preprocess_context_references_async was never called"
    assert isinstance(captured["context_length"], int)
    assert captured["context_length"] > 0
    assert captured["context_length"] == 128000

    # The expanded result from the (stubbed) preprocessor must have been
    # adopted as the final message text.
    assert result == "[expanded body]"


@pytest.mark.asyncio
async def test_at_reference_resolves_model_via_session_runtime(monkeypatch):
    """The block must source model/provider/base_url from
    self._resolve_session_agent_runtime (session-aware), not from
    nonexistent self._model/self._base_url attributes."""
    runner = _make_runner()
    source = _source()
    _patch_runtime_resolution(monkeypatch)

    captured_runtime_call = {}

    async def _fake_get_ctx_len(model, base_url="", api_key="", config_context_length=None, provider="", custom_providers=None):
        captured_runtime_call["model"] = model
        captured_runtime_call["base_url"] = base_url
        captured_runtime_call["provider"] = provider
        captured_runtime_call["config_context_length"] = config_context_length
        return config_context_length or 128000

    import agent.model_metadata as model_meta_mod

    monkeypatch.setattr(
        model_meta_mod, "get_model_context_length_async", _fake_get_ctx_len
    )

    import agent.context_references as ctx_mod

    async def _passthrough_preprocess(message, *, cwd, context_length, url_fetcher=None, allowed_root=None):
        return ContextReferenceResult(message=message, original_message=message)

    monkeypatch.setattr(
        ctx_mod, "preprocess_context_references_async", _passthrough_preprocess
    )

    event = MessageEvent(text="hi @diff", source=source)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    assert captured_runtime_call.get("model") == "openai/gpt-4.1-mini"
    assert captured_runtime_call.get("base_url") == "https://api.openai.com/v1"
    assert captured_runtime_call.get("provider") == "openai"


@pytest.mark.asyncio
async def test_at_reference_uses_routed_profile_scope_when_multiplexed(monkeypatch, tmp_path):
    """Secondary-profile preprocessing must resolve inside that profile scope."""
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    source = _source()
    source.profile = "secondary"
    profile_home = tmp_path / "profiles" / "secondary"
    seen = []

    @contextmanager
    def _scope(home):
        seen.append(("enter", home))
        try:
            yield
        finally:
            seen.append(("exit", home))

    async def _prepared(**kwargs):
        seen.append(("prepared", kwargs["source"].profile))
        return "expanded"

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", _scope)
    monkeypatch.setattr(runner, "_resolve_profile_home_for_source", lambda _source: profile_home)
    monkeypatch.setattr(runner, "_prepare_inbound_message_text", _prepared)

    result = await runner._prepare_profile_scoped_inbound_message_text(
        event=MessageEvent(text="@file:note", source=source),
        source=source,
        history=[],
        session_key="agent:secondary:telegram:dm:123",
    )

    assert result == "expanded"
    assert seen == [
        ("enter", profile_home),
        ("prepared", "secondary"),
        ("exit", profile_home),
    ]


@pytest.mark.asyncio
async def test_at_reference_passes_compatible_custom_provider_context(monkeypatch):
    """Per-model custom-provider limits must bound context-reference injection."""
    runner = _make_runner()
    source = _source()
    captured = {}
    custom_providers = [{
        "name": "private",
        "base_url": "https://private.example/v1",
        "models": {"private/model": {"context_length": 32768}},
    }]

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"model": {"default": "private/model"}, "custom_providers": custom_providers},
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_gateway_model",
        lambda _cfg=None: "private/model",
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "custom:private", "api_key": "test", "base_url": "https://private.example/v1"},
    )

    import hermes_cli.config as config_mod
    import agent.model_metadata as model_meta_mod
    import agent.context_references as ctx_mod

    monkeypatch.setattr(config_mod, "get_compatible_custom_providers", lambda _cfg: custom_providers)

    async def _fake_get_context(_model, **kwargs):
        captured["custom_providers"] = kwargs["custom_providers"]
        return 32768

    async def _passthrough(message, **_kwargs):
        return ContextReferenceResult(message=message, original_message=message)

    monkeypatch.setattr(model_meta_mod, "get_model_context_length_async", _fake_get_context)
    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _passthrough)

    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="@file:note", source=source), source=source, history=[]
    )
    assert captured["custom_providers"] == custom_providers


@pytest.mark.asyncio
async def test_at_reference_applies_custom_runtime_budget_to_preprocessor(monkeypatch):
    """The custom runtime's real budget must reach reference expansion."""
    runner = _make_runner()
    source = _source()
    captured = {}
    custom_providers = [{
        "name": "private",
        "base_url": "https://private.example/v1",
        "models": {"session/model": {"context_length": 32768}},
    }]
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"model": {"default": "global/model", "context_length": 128000}, "custom_providers": custom_providers},
    )
    monkeypatch.setattr(runner, "_resolve_session_agent_runtime", lambda **_kwargs: (
        "session/model",
        {"provider": "custom:private", "api_key": "test", "base_url": "https://private.example/v1"},
    ))

    import hermes_cli.config as config_mod
    import agent.model_metadata as model_meta_mod
    import agent.context_references as ctx_mod

    monkeypatch.setattr(config_mod, "get_compatible_custom_providers", lambda _cfg: custom_providers)
    monkeypatch.setattr(config_mod, "get_custom_provider_context_length", lambda **_kwargs: 32768)

    async def _fake_get_context(_model, **kwargs):
        captured["config_context_length"] = kwargs["config_context_length"]
        return kwargs["config_context_length"]

    async def _preprocess(message, *, context_length, **_kwargs):
        captured["preprocessor_budget"] = context_length
        return ContextReferenceResult(message="expanded", original_message=message, expanded=True)

    monkeypatch.setattr(model_meta_mod, "get_model_context_length_async", _fake_get_context)
    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _preprocess)

    result = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="@file:note", source=source), source=source, history=[]
    )
    assert result == "expanded"
    assert captured == {"config_context_length": 32768, "preprocessor_budget": 32768}


@pytest.mark.asyncio
async def test_at_reference_ignores_global_context_for_session_model_override(monkeypatch):
    """A session model override must not inherit another model's global limit."""
    runner = _make_runner()
    source = _source()
    captured = {}

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"model": {"default": "global/model", "context_length": 128000}},
    )
    monkeypatch.setattr(runner, "_resolve_session_agent_runtime", lambda **_kwargs: (
        "session/model",
        {"provider": "openai", "api_key": "test", "base_url": "https://api.openai.com/v1"},
    ))

    import agent.model_metadata as model_meta_mod
    import agent.context_references as ctx_mod

    async def _fake_get_context(_model, **kwargs):
        captured["config_context_length"] = kwargs["config_context_length"]
        return 32768

    async def _passthrough(message, **_kwargs):
        return ContextReferenceResult(message=message, original_message=message)

    monkeypatch.setattr(model_meta_mod, "get_model_context_length_async", _fake_get_context)
    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _passthrough)

    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="@file:note", source=source), source=source, history=[]
    )
    assert captured["config_context_length"] is None


@pytest.mark.asyncio
async def test_typed_at_file_uses_profile_configured_cwd_not_owner_env(monkeypatch, tmp_path):
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    _patch_context_ref_runtime(monkeypatch, runner)
    source = _typed_source()
    family_home = tmp_path / "profiles" / "family-alpha"
    family_workspace = tmp_path / "family-workspace"
    owner_workspace = tmp_path / "owner-workspace"
    family_workspace.mkdir()
    owner_workspace.mkdir()
    (family_workspace / "sentinel.txt").write_text(
        "FAMILY_SENTINEL_OK",
        encoding="utf-8",
    )
    (owner_workspace / "sentinel.txt").write_text(
        "OWNER_SENTINEL_SHOULD_NOT_LEAK",
        encoding="utf-8",
    )
    _write_config(
        family_home,
        f"""
model:
  default: openai/gpt-4.1-mini
  context_length: 128000
terminal:
  cwd: {family_workspace}
""",
    )
    monkeypatch.setenv("TERMINAL_CWD", str(owner_workspace))
    monkeypatch.setattr(runner, "_resolve_profile_home_for_source", lambda _source: family_home)

    result = await runner._prepare_profile_scoped_inbound_message_text(
        event=MessageEvent(text="read @file:sentinel.txt", source=source),
        source=source,
        history=[],
        session_key="agent:family-alpha:telegram:dm:123",
    )

    assert "FAMILY_SENTINEL_OK" in result
    assert "OWNER_SENTINEL_SHOULD_NOT_LEAK" not in result


@pytest.mark.parametrize("message", ["summarize @diff", "summarize @git:1"])
@pytest.mark.asyncio
async def test_typed_git_references_use_profile_configured_cwd_not_owner_env(
    monkeypatch, tmp_path, message
):
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    _patch_context_ref_runtime(monkeypatch, runner)
    source = _typed_source()
    family_home = tmp_path / "profiles" / "family-alpha"
    family_workspace = tmp_path / "family-workspace"
    owner_workspace = tmp_path / "owner-workspace"
    family_workspace.mkdir()
    owner_workspace.mkdir()
    _write_config(
        family_home,
        f"""
model:
  default: openai/gpt-4.1-mini
  context_length: 128000
terminal:
  cwd: {family_workspace}
""",
    )
    monkeypatch.setenv("TERMINAL_CWD", str(owner_workspace))
    monkeypatch.setattr(runner, "_resolve_profile_home_for_source", lambda _source: family_home)

    captured = {}

    import agent.context_references as ctx_mod

    async def _fake_preprocess(message, *, cwd, allowed_root=None, **_kwargs):
        captured.update({"message": message, "cwd": cwd, "allowed_root": allowed_root})
        return ContextReferenceResult(message="expanded diff", original_message=message, expanded=True)

    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _fake_preprocess)

    result = await runner._prepare_profile_scoped_inbound_message_text(
        event=MessageEvent(text=message, source=source),
        source=source,
        history=[],
        session_key="agent:family-alpha:telegram:dm:123",
    )

    assert result == "expanded diff"
    assert captured["cwd"] == str(family_workspace)
    assert captured["allowed_root"] == str(family_workspace)
    assert captured["cwd"] != str(owner_workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_cwd",
    [
        None,
        "",
        ".",
        "auto",
        "cwd",
        "relative/workspace",
        "/does/not/exist",
        '"bad\\0cwd"',
        "__REGULAR_FILE__",
    ],
)
async def test_typed_invalid_profile_cwd_falls_closed_to_profile_home_not_env(
    monkeypatch,
    tmp_path,
    configured_cwd,
):
    runner = _make_runner()
    runner.config.multiplex_profiles = True
    _patch_context_ref_runtime(monkeypatch, runner)
    source = _typed_source()
    family_home = tmp_path / "profiles" / "family-alpha"
    owner_workspace = tmp_path / "owner-workspace"
    owner_workspace.mkdir()
    regular_file = tmp_path / "regular-file"
    regular_file.write_text("not a directory", encoding="utf-8")
    config_cwd = str(regular_file) if configured_cwd == "__REGULAR_FILE__" else configured_cwd
    terminal_block = "" if config_cwd is None else f"terminal:\n  cwd: {config_cwd}\n"
    _write_config(
        family_home,
        f"""
model:
  default: openai/gpt-4.1-mini
  context_length: 128000
{terminal_block}
""",
    )
    monkeypatch.setenv("TERMINAL_CWD", str(owner_workspace))
    monkeypatch.setattr(runner, "_resolve_profile_home_for_source", lambda _source: family_home)

    captured = {}

    import agent.context_references as ctx_mod

    async def _fake_preprocess(message, *, cwd, allowed_root=None, **_kwargs):
        captured.update({"cwd": cwd, "allowed_root": allowed_root})
        return ContextReferenceResult(message="expanded", original_message=message, expanded=True)

    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _fake_preprocess)

    await runner._prepare_profile_scoped_inbound_message_text(
        event=MessageEvent(text="read @file:notes.txt", source=source),
        source=source,
        history=[],
        session_key="agent:family-alpha:telegram:dm:123",
    )

    assert captured["cwd"] == str(family_home)
    assert captured["allowed_root"] == str(family_home)
    assert captured["cwd"] != str(owner_workspace)


@pytest.mark.asyncio
async def test_legacy_at_reference_preserves_terminal_cwd_env(monkeypatch, tmp_path):
    runner = _make_runner()
    _patch_context_ref_runtime(monkeypatch, runner)
    source = _source()
    legacy_workspace = tmp_path / "legacy-workspace"
    legacy_workspace.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(legacy_workspace))
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"model": {"default": "openai/gpt-4.1-mini", "context_length": 128000}},
    )

    captured = {}

    import agent.context_references as ctx_mod

    async def _fake_preprocess(message, *, cwd, allowed_root=None, **_kwargs):
        captured.update({"cwd": cwd, "allowed_root": allowed_root})
        return ContextReferenceResult(message="expanded", original_message=message, expanded=True)

    monkeypatch.setattr(ctx_mod, "preprocess_context_references_async", _fake_preprocess)

    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="read @file:notes.txt", source=source),
        source=source,
        history=[],
        session_key="agent:legacy:telegram:dm:123",
    )

    assert captured == {"cwd": str(legacy_workspace), "allowed_root": str(legacy_workspace)}
