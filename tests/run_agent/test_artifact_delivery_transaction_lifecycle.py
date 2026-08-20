"""Focused lifecycle coverage for durable bound-artifact transactions."""

from __future__ import annotations

import json
import re
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.tool_guardrails import ToolGuardrailDecision
from hermes_state import SessionDB
from run_agent import AIAgent


def _tool_defs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "deliver_artifact",
                "description": "test artifact tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def _tool_call(call_id: str = "artifact-call") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name="deliver_artifact",
            arguments=json.dumps({"path": "/trusted/report.xlsx"}),
        ),
    )


def _response(
    content: str = "",
    *,
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        model="test/model",
        usage=None,
    )


def _make_agent(
    tmp_path: Path,
    *,
    max_iterations: int,
) -> tuple[AIAgent, SessionDB, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = SessionDB(tmp_path / "state.db")
    session_id = "raw-session-user-chat-identity"
    db.create_session(session_id, "telegram")
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_logging.setup_logging"),
    ):
        agent = AIAgent(
            session_id=session_id,
            session_db=db,
            api_key="test-key",
            base_url="https://example.invalid/v1",
            provider="openai-compat",
            model="test/model",
            max_iterations=max_iterations,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent._cached_system_prompt = "stable test prompt"
    agent._disable_streaming = True
    agent._session_json_enabled = False
    agent.save_trajectories = False
    agent.compression_enabled = False
    agent._cleanup_task_resources = lambda *_args, **_kwargs: None
    agent._save_trajectory = lambda *_args, **_kwargs: None
    agent._persist_session = lambda *_args, **_kwargs: None
    return agent, db, session_id


def _transaction(db: SessionDB, session_id: str) -> dict | None:
    row = db.get_session(session_id)
    assert row is not None
    raw = row["artifact_delivery_json"]
    return json.loads(raw) if raw is not None else None


def _run(
    agent: AIAgent,
    *,
    responses: list[SimpleNamespace | BaseException],
    tool_effect=None,
):
    sequence = iter(responses)

    def model_call(_kwargs):
        value = next(sequence)
        if isinstance(value, BaseException):
            raise value
        return value

    def execute_tool(*_args, **_kwargs):
        if tool_effect is not None:
            tool_effect()
        return json.dumps(
            {
                "success": True,
                "status": "ready_for_delivery",
                "media_tag": "MEDIA:/trusted/report.xlsx",
            }
        )

    agent._interruptible_api_call = model_call
    with (
        patch("run_agent.handle_function_call", side_effect=execute_tool) as called,
        patch(
            "agent.artifact_delivery_stop.bound_artifact_tool_batch_relevant",
            return_value=True,
        ),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("make and deliver a report")
    return result, called


@pytest.mark.parametrize(
    "exit_kind",
    ["interrupt", "guardrail", "provider_failure", "budget_exhaustion"],
)
def test_non_delivery_exits_abandon_live_transaction_and_clear_confirmation(
    tmp_path,
    exit_kind,
):
    max_iterations = 1 if exit_kind == "budget_exhaustion" else 2
    agent, db, session_id = _make_agent(
        tmp_path / exit_kind,
        max_iterations=max_iterations,
    )

    def effect():
        if exit_kind == "interrupt":
            agent._interrupt_requested = True
        elif exit_kind == "guardrail":
            agent._tool_guardrail_halt_decision = ToolGuardrailDecision(
                action="halt",
                code="synthetic_halt",
                message="stop",
                tool_name="deliver_artifact",
                count=1,
            )

    responses: list[SimpleNamespace | BaseException] = [
        _response(tool_calls=[_tool_call()])
    ]
    if exit_kind == "provider_failure":
        responses.append(RuntimeError("synthetic provider failure"))
    elif max_iterations > 1:
        responses.append(_response("must not be released"))

    result, _ = _run(agent, responses=responses, tool_effect=effect)

    assert _transaction(db, session_id)["state"] == "abandoned"
    assert agent._artifact_delivery_confirmation is None
    assert result.get("artifact_delivery_confirmation") is None
    db.close()


@pytest.mark.parametrize("gate", ["verification", "pre_verify", "kanban"])
def test_confirmed_candidate_is_not_made_ready_before_continuation_gates(
    tmp_path,
    monkeypatch,
    gate,
):
    agent, db, session_id = _make_agent(tmp_path / gate, max_iterations=3)
    transition_calls: list[tuple[str, str]] = []
    original_transition = db.transition_artifact_delivery

    def transition(*args, **kwargs):
        transition_calls.append((args[2], args[3]))
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(db, "transition_artifact_delivery", transition)

    def stop_action(events, *, attempts):
        if not events:
            return "none", None, None
        return (
            "confirmed",
            None,
            {
                "tool_call_id": "artifact-call",
                "path": "/trusted/report.xlsx",
                "media_tag": "MEDIA:/trusted/report.xlsx",
            },
        )

    contexts = [
        patch(
            "agent.artifact_delivery_stop.bound_artifact_stop_action",
            side_effect=stop_action,
        )
    ]
    if gate == "verification":
        monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "1")
        contexts.append(
            patch(
                "agent.verification_stop.build_verify_on_stop_nudge",
                side_effect=["verify first", None],
            )
        )
    elif gate == "pre_verify":
        monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")
        contexts.extend(
            [
                patch(
                    "hermes_cli.plugins.has_hook",
                    side_effect=lambda name: name == "pre_verify",
                ),
                patch(
                    "hermes_cli.plugins.get_pre_verify_continue_message",
                    side_effect=["run checks", None],
                ),
                patch("agent.verify_hooks.max_verify_nudges", return_value=2),
            ]
        )
    else:
        monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")
        contexts.append(
            patch(
                "agent.kanban_stop.build_kanban_stop_nudge",
                side_effect=["finish the board protocol", None],
            )
        )

    def tool_effect():
        if gate == "pre_verify":
            agent._turn_file_mutation_paths = {"report.xlsx"}

    with ExitStack() as stack:
        for context in contexts:
            stack.enter_context(context)
        result, _ = _run(
            agent,
            responses=[
                _response(tool_calls=[_tool_call()]),
                _response("premature success MEDIA:/trusted/report.xlsx"),
                _response("stale re-evaluation must not pass"),
            ],
            tool_effect=tool_effect,
        )

    assert ("pending", "ready") not in transition_calls
    assert _transaction(db, session_id)["state"] == "abandoned"
    assert result["artifact_delivery_confirmation"] is None
    assert "stale re-evaluation must not pass" not in result["final_response"]
    db.close()


def test_batch_classification_exception_blocks_tools_and_ordinary_release(tmp_path):
    agent, db, session_id = _make_agent(tmp_path, max_iterations=2)
    agent._interruptible_api_call = MagicMock(
        side_effect=[
            _response(tool_calls=[_tool_call()]),
            _response("UNSAFE_SUCCESS MEDIA:/trusted/report.xlsx"),
        ]
    )
    with (
        patch("run_agent.handle_function_call", return_value="SHOULD_NOT_EXECUTE") as called,
        patch(
            "agent.artifact_delivery_stop.bound_artifact_tool_batch_relevant",
            side_effect=RuntimeError("classification context failure"),
        ),
        patch(
            "agent.artifact_delivery_stop.bound_artifact_stop_action",
            side_effect=RuntimeError("stop context failure"),
        ),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("make and deliver a report")

    called.assert_not_called()
    assert "UNSAFE_SUCCESS" not in result["final_response"]
    assert result["artifact_delivery_confirmation"] is None
    assert _transaction(db, session_id) is None
    db.close()


def test_stop_scanner_exception_abandons_and_suppresses_ordinary_media(tmp_path):
    agent, db, session_id = _make_agent(tmp_path, max_iterations=2)
    with patch(
        "agent.artifact_delivery_stop.bound_artifact_stop_action",
        side_effect=RuntimeError("stop scanner context failure"),
    ):
        result, called = _run(
            agent,
            responses=[
                _response(tool_calls=[_tool_call()]),
                _response("UNSAFE_SUCCESS MEDIA:/trusted/report.xlsx"),
            ],
        )

    called.assert_called_once()
    assert _transaction(db, session_id)["state"] == "abandoned"
    assert "UNSAFE_SUCCESS" not in result["final_response"]
    assert result["artifact_delivery_confirmation"] is None
    db.close()


def test_corrective_bound_delivery_reaches_ready_without_mutation_metadata(
    tmp_path,
    monkeypatch,
):
    agent, db, session_id = _make_agent(tmp_path, max_iterations=4)
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch(
            "agent.artifact_delivery_stop.bound_document_context_active",
            return_value=True,
        ),
        patch(
            "tools.artifact_delivery_tool.validate_bound_artifact_output",
            return_value=None,
        ),
    ):
        result, called = _run(
            agent,
            responses=[
                _response(tool_calls=[_tool_call("first-delivery")]),
                _response("first attempt"),
                _response(tool_calls=[_tool_call("corrective-delivery")]),
                _response("corrected document ready"),
            ],
        )

    assert called.call_count == 2
    assert result["final_response"] == "corrected document ready"
    assert result["artifact_delivery_confirmation"] is not None
    assert result["artifact_delivery_confirmation"]["tool_call_id"] == (
        "corrective-delivery"
    )
    assert _transaction(db, session_id)["state"] == "ready"
    db.close()


def test_new_artifact_logs_exclude_raw_session_and_turn_identity(
    tmp_path,
    monkeypatch,
    caplog,
):
    agent, db, raw_session = _make_agent(tmp_path, max_iterations=1)

    def fail_begin(_session_id):
        raise RuntimeError("synthetic begin failure")

    monkeypatch.setattr(db, "begin_artifact_delivery", fail_begin)
    caplog.set_level("WARNING", logger="agent.conversation_loop")
    result, called = _run(
        agent,
        responses=[_response(tool_calls=[_tool_call()])],
    )

    called.assert_not_called()
    artifact_logs = [
        record.getMessage()
        for record in caplog.records
        if "artifact" in record.getMessage().lower()
    ]
    assert artifact_logs
    assert all(raw_session not in message for message in artifact_logs)
    assert all(agent._current_turn_id not in message for message in artifact_logs)
    assert result["artifact_delivery_confirmation"] is None
    db.close()


def test_artifact_stop_scan_diagnostics_exclude_paths_and_call_ids(
    tmp_path,
    monkeypatch,
    caplog,
):
    agent, db, raw_session = _make_agent(tmp_path, max_iterations=4)
    raw_path = "/trusted/sensitive-report-name.xlsx"
    raw_call_id = "sensitive-artifact-call-id"
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")
    caplog.set_level("INFO", logger="agent.artifact_delivery_stop")

    with (
        patch(
            "agent.artifact_delivery_stop.bound_document_context_active",
            return_value=True,
        ),
        patch(
            "tools.artifact_delivery_tool.validate_bound_artifact_output",
            return_value=None,
        ),
    ):
        result, _ = _run(
            agent,
            responses=[
                _response(tool_calls=[_tool_call(raw_call_id)]),
                _response("first document attempt"),
                _response(tool_calls=[_tool_call(f"{raw_call_id}-corrective")]),
                _response("document ready"),
            ],
        )

    diagnostics = [
        record.getMessage()
        for record in caplog.records
        if "bound artifact stop scan" in record.getMessage()
    ]
    assert diagnostics
    assert all(raw_session not in message for message in diagnostics)
    assert all(raw_path not in message for message in diagnostics)
    assert all(raw_call_id not in message for message in diagnostics)
    assert result["artifact_delivery_confirmation"] is not None
    db.close()


def test_transaction_id_is_opaque_random_and_reused_while_live(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    raw_session = "session-task-user-chat-raw-identity"
    db.create_session(raw_session, "telegram")

    transaction_id = db.begin_artifact_delivery(raw_session)
    reused = db.begin_artifact_delivery(raw_session)
    transaction = _transaction(db, raw_session)

    assert reused == transaction_id
    assert isinstance(transaction_id, str)
    assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", transaction_id)
    assert transaction == {"state": "pending", "transaction_id": transaction_id}
    assert raw_session not in transaction_id
    assert raw_session not in json.dumps(transaction)
    db.close()


class _RotatingCompressor:
    def __init__(self) -> None:
        self.last_prompt_tokens = 100_000
        self.last_completion_tokens = 0
        self.compression_count = 0
        self._last_summary_error = None
        self._last_compress_aborted = False
        self._last_aux_model_failure_model = None
        self._last_aux_model_failure_error = None
        self._should_compress = True
        self.protect_first_n = 10
        self.protect_last_n = 10
        self.threshold_tokens = 1_000_000

    def should_compress(self, _tokens) -> bool:
        value = self._should_compress
        self._should_compress = False
        return value

    def compress(self, _messages, **_kwargs):
        self.compression_count += 1
        return [{"role": "user", "content": "retained compressed turn"}]

    def update_from_response(self, _response) -> None:
        return None

    def on_session_start(self, *_args, **_kwargs) -> None:
        return None


def test_real_session_rotation_retains_transaction_handoff_end_to_end(
    tmp_path,
    monkeypatch,
):
    agent, db, parent_session_id = _make_agent(tmp_path, max_iterations=2)
    agent.compression_enabled = True
    agent.compression_in_place = False
    agent._compression_feasibility_checked = True
    agent.context_compressor = _RotatingCompressor()
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    confirmation = {
        "tool_call_id": "artifact-call",
        "path": "/trusted/report.xlsx",
        "media_tag": "MEDIA:/trusted/report.xlsx",
    }

    def stop_action(events, *, attempts):
        return ("confirmed", None, confirmation) if events else ("none", None, None)

    with patch(
        "agent.artifact_delivery_stop.bound_artifact_stop_action",
        side_effect=stop_action,
    ):
        result, _ = _run(
            agent,
            responses=[
                _response(tool_calls=[_tool_call()]),
                _response("confirmed after actual rotation"),
            ],
        )

    assert agent.session_id != parent_session_id
    child = db.get_session(agent.session_id)
    assert child is not None
    assert child["parent_session_id"] == parent_session_id
    parent_transaction = _transaction(db, parent_session_id)
    assert parent_transaction["state"] == "ready"
    assert _transaction(db, agent.session_id) is None
    assert result["artifact_delivery_confirmation"] == {
        **confirmation,
        "transaction_session_id": parent_session_id,
        "transaction_id": parent_transaction["transaction_id"],
    }
    db.close()
