"""Resume-pending recovery notes for an interrupted turn (kept out of the run.py facade)."""
from __future__ import annotations

from typing import Optional

def build_resume_recovery_note(
    reason: Optional[str], message: str = "", *, interactive: bool = True) -> str:
    """Build the resume-pending recovery system note for an interrupted turn (empty ``message`` = auto-resume).

    Interactive platforms report the restore and ask what next; non-interactive ones finish the work.

    On non-interactive event platforms (webhook, API server — adapters with ``interactive_resume = False``)
    nobody can answer; the resumed turn must instead complete the interrupted work, or the task is silently
    abandoned behind a "restored" acknowledgement that goes nowhere (#57056).
    """
    if reason in {"artifact_delivery_abandoned", "artifact_delivery_uncertain"}:
        delivery_state = ("may have been delivered, but no durable receipt was stored"
                          if reason == "artifact_delivery_uncertain" else "was not sent")
        return ("[System note: A document delivery was interrupted and " + delivery_state
                + ". Do not re-execute or auto-resume old tool calls. Tell the user to explicitly retry "
                "the document request if they still want it delivered, then address only their new message.]"
                + ("\n\n" + message if message else ""))
    reason_phrase = (
        "a gateway restart" if reason == "restart_timeout"
        else "a gateway shutdown" if reason == "shutdown_timeout" else "a gateway interruption")
    if message:
        resume_guidance = (
            "Address the user's NEW message below FIRST and focus on what the user is asking now.")
        tail_guidance = (
            "Do NOT re-execute old tool calls — skip any unfinished work from the conversation history."
        )
    elif interactive:
        resume_guidance = (
            "Report to the user that the session was restored "
            "successfully and ask what they would like to do next.")
        tail_guidance = (
            "Do NOT re-execute old tool calls — skip any unfinished work from the conversation history."
        )
    else:
        resume_guidance = (
            "No user is present on this non-interactive platform, "
            "so do NOT emit a 'session restored' acknowledgement "
            "or ask questions. Review the conversation history and "
            "CONTINUE the interrupted task to completion.")
        tail_guidance = (
            "Do NOT re-run tool calls whose results already "
            "appear in the history — resume from the first step that has no recorded result.")
    return (
        f"[System note: The previous turn was interrupted by "
        f"{reason_phrase}; the gateway is now back online. "
        f"Any restart/shutdown command in the history has already "
        f"run — do NOT re-execute or verify it. {resume_guidance} {tail_guidance}]"
        + (f"\n\n{message}" if message else ""))

