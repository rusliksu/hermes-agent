import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from hermes_state import SessionDB
from webapi.deps import get_session_db_dependency, get_session_or_404, get_runtime_model
from webapi.models.chat import ChatRequest, ChatResponse
from webapi.sse import SSEEmitter, SSEStream


router = APIRouter(prefix="/api/sessions", tags=["chat"])


def _read_attachment_field(attachment: Any, *keys: str) -> str:
    if not isinstance(attachment, dict):
        return ""
    for key in keys:
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_user_content(payload: ChatRequest) -> tuple[str | list[dict[str, Any]], str]:
    text = payload.message or ""
    attachments = payload.attachments or []
    image_parts: list[dict[str, Any]] = []

    for attachment in attachments:
        if hasattr(attachment, "model_dump"):
            raw = attachment.model_dump(exclude_none=True)
        elif isinstance(attachment, dict):
            raw = dict(attachment)
        else:
            continue

        mime = _read_attachment_field(raw, "contentType", "mimeType", "mediaType")
        if not mime.startswith("image/"):
            continue

        content = _read_attachment_field(raw, "content", "base64", "data")
        if not content:
            continue

        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{content}"},
            }
        )

    if not image_parts:
        return text, payload.persist_user_message or text

    content_parts: list[dict[str, Any]] = []
    if text.strip():
        content_parts.append({"type": "text", "text": text})
    content_parts.extend(image_parts)
    if not content_parts:
        content_parts.append({"type": "text", "text": ""})

    persist_text = payload.persist_user_message or text
    return content_parts, persist_text


def _tool_map(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            tool_id = tool_call.get("id")
            if not tool_id:
                continue
            fn = tool_call.get("function") or {}
            raw_args = fn.get("arguments")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
            except json.JSONDecodeError:
                parsed_args = raw_args
            mapping[tool_id] = {
                "tool_name": fn.get("name") or message.get("tool_name") or f"tool_{index + 1}",
                "args": parsed_args,
            }
    return mapping


def _result_preview(content: Any, limit: int = 400) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return text[:limit] + ("..." if len(text) > limit else "")


def _emit_post_run_events(emitter: SSEEmitter, stream: SSEStream, result: dict[str, Any], assistant_message_id: str) -> None:
    messages = result.get("messages") or []
    tools = _tool_map(messages)

    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_id = message.get("tool_call_id")
        tool_meta = tools.get(tool_id, {})
        tool_name = tool_meta.get("tool_name") or message.get("tool_name") or "unknown"
        payload = {
            "tool_call_id": tool_id,
            "tool_name": tool_name,
            "args": tool_meta.get("args"),
            "result_preview": _result_preview(message.get("content")),
        }
        content = message.get("content") or ""
        lower = content.lower() if isinstance(content, str) else ""
        failed = "error" in lower or "failed" in lower
        stream.put(emitter.event("tool.failed" if failed else "tool.completed", **payload))

        if not failed and tool_name == "memory":
            try:
                parsed = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            stream.put(
                emitter.event(
                    "memory.updated",
                    tool_name=tool_name,
                    target=parsed.get("target"),
                    entry_count=parsed.get("entry_count"),
                    message=parsed.get("message"),
                )
            )

        if not failed and "skill" in tool_name:
            stream.put(
                emitter.event(
                    "skill.loaded",
                    tool_name=tool_name,
                    name=(tool_meta.get("args") or {}).get("name"),
                )
            )

        if not failed:
            artifact_paths: list[str] = []
            parsed = None
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    parsed = None
            if isinstance(parsed, dict):
                for key in ("path", "file_path", "output_path"):
                    value = parsed.get(key)
                    if isinstance(value, str):
                        artifact_paths.append(value)
                files = parsed.get("files")
                if isinstance(files, list):
                    artifact_paths.extend(str(item) for item in files if isinstance(item, (str, int, float)))
            for path in artifact_paths:
                stream.put(
                    emitter.event(
                        "artifact.created",
                        tool_name=tool_name,
                        path=path,
                    )
                )

    stream.put(
        emitter.event(
            "assistant.completed",
            message_id=assistant_message_id,
            content=result.get("final_response") or "",
            completed=result.get("completed", False),
            partial=result.get("partial", False),
            interrupted=result.get("interrupted", False),
        )
    )
    stream.put(
        emitter.event(
            "run.completed",
            message_id=assistant_message_id,
            completed=result.get("completed", False),
            partial=result.get("partial", False),
            interrupted=result.get("interrupted", False),
            api_calls=result.get("api_calls"),
        )
    )


def _run_chat(
    *,
    session_id: str,
    payload: ChatRequest,
    session_db: SessionDB,
) -> dict[str, Any]:
    get_session_or_404(session_id, session_db)
    _user_content, persist_text = _build_user_content(payload)
    if persist_text:
        session_db.append_message(
            session_id=session_id,
            role="user",
            content=persist_text,
        )
    final_response = (
        "Hermes webapi compatibility shim: chat execution is disabled in this "
        "server-side compatibility route to avoid external LLM calls."
    )
    session_db.append_message(
        session_id=session_id,
        role="assistant",
        content=final_response,
        finish_reason="mock_boundary",
    )
    return {
        "final_response": final_response,
        "completed": False,
        "partial": True,
        "interrupted": True,
        "api_calls": 0,
        "messages": [
            {"role": "user", "content": persist_text},
            {
                "role": "assistant",
                "content": final_response,
                "finish_reason": "mock_boundary",
            },
        ],
        "response_previewed": False,
    }


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def chat(
    session_id: str,
    payload: ChatRequest,
    session_db: Annotated[SessionDB, Depends(get_session_db_dependency)],
) -> ChatResponse:
    result = _run_chat(session_id=session_id, payload=payload, session_db=session_db)
    if result.get("error") and not result.get("final_response"):
        raise HTTPException(status_code=500, detail=result["error"])
    return ChatResponse(
        session_id=session_id,
        run_id=f"run_{uuid.uuid4().hex}",
        model=payload.model or get_runtime_model(),
        final_response=result.get("final_response"),
        completed=result.get("completed", False),
        partial=result.get("partial", False),
        interrupted=result.get("interrupted", False),
        api_calls=result.get("api_calls", 0),
        messages=result.get("messages", []),
        last_reasoning=result.get("last_reasoning"),
        response_previewed=result.get("response_previewed", False),
    )


@router.post("/{session_id}/chat/stream")
async def chat_stream(
    session_id: str,
    payload: ChatRequest,
    session_db: Annotated[SessionDB, Depends(get_session_db_dependency)],
) -> StreamingResponse:
    session = get_session_or_404(session_id, session_db)
    user_content, persist_text = _build_user_content(payload)
    run_id = f"run_{uuid.uuid4().hex}"
    assistant_message_id = f"msg_asst_{uuid.uuid4().hex}"
    stream = SSEStream()
    emitter = SSEEmitter(session_id=session_id, run_id=run_id)

    stream.put(
        emitter.event(
            "session.created",
            title=session.get("title") or "New Chat",
            cwd=None,
            model=payload.model or session.get("model") or get_runtime_model(),
        )
    )
    stream.put(
        emitter.event(
            "run.started",
            user_message={
                "id": f"msg_user_{uuid.uuid4().hex}",
                "role": "user",
                "content": persist_text,
            },
        )
    )
    stream.put(
        emitter.event(
            "message.started",
            message={"id": assistant_message_id, "role": "assistant"},
        )
    )

    try:
        if persist_text:
            session_db.append_message(
                session_id=session_id,
                role="user",
                content=persist_text,
            )
        final_response = (
            "Hermes webapi compatibility shim: chat execution is disabled in this "
            "server-side compatibility route to avoid external LLM calls."
        )
        session_db.append_message(
            session_id=session_id,
            role="assistant",
            content=final_response,
            finish_reason="mock_boundary",
        )
        stream.put(
            emitter.event(
                "assistant.delta",
                message_id=assistant_message_id,
                delta=final_response,
            )
        )
        _emit_post_run_events(
            emitter,
            stream,
            {
                "messages": [
                    {"role": "user", "content": persist_text},
                    {
                        "role": "assistant",
                        "content": final_response,
                        "finish_reason": "mock_boundary",
                    },
                ],
                "final_response": final_response,
                "completed": False,
                "partial": True,
                "interrupted": True,
                "api_calls": 0,
            },
            assistant_message_id,
        )
    except Exception as exc:
        stream.put(emitter.event("error", message=str(exc)))
    finally:
        stream.put(emitter.event("done"))
        stream.close()
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
