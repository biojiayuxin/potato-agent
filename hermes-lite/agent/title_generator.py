"""Auto-generate short session titles from the first user/assistant exchange.

Runs asynchronously after the first response is delivered so it never
adds latency to the user-facing reply.
"""

import contextvars
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

# Legacy callback signature retained for callers outside the Lite Gateway.
# The Gateway uses outcome_callback so title failures stay out of chat content.
FailureCallback = Callable[[str, BaseException], None]
TitleCallback = Callable[[str], None]


@dataclass(frozen=True)
class AutoTitleOutcome:
    status: str
    title: Optional[str] = None
    reason: str = ""


OutcomeCallback = Callable[[AutoTitleOutcome], None]

_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
    "following exchange. The title should capture the main topic or intent. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


def generate_title(
    user_message: str,
    assistant_response: str,
    timeout: float = 30.0,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
) -> Optional[str]:
    """Generate a session title from the first exchange.

    Uses the main runtime's model when available, falling back to the
    auxiliary LLM client (cheapest/fastest available model).
    Returns the title string or None on failure.

    ``failure_callback`` is invoked with ``(task, exception)`` when the
    auxiliary call raises. Lite Gateway callers intentionally leave it unset.
    """
    # Truncate long messages to keep the request small
    user_snippet = user_message[:500] if user_message else ""
    assistant_snippet = assistant_response[:500] if assistant_response else ""

    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"},
    ]

    try:
        response = call_llm(
            task="title_generation",
            messages=messages,
            max_tokens=500,
            temperature=0.3,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        title = (response.choices[0].message.content or "").strip()
        # Clean up: remove quotes, trailing punctuation, prefixes like "Title: "
        title = title.strip('"\'')
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        # Enforce reasonable length
        if len(title) > 80:
            title = title[:77] + "..."
        return title if title else None
    except Exception as e:
        # Log at WARNING so this shows up in agent.log without debug mode.
        # Full detail at debug level for operators who need the stack.
        logger.debug("Title generation failed: %s", e, exc_info=True)
        if failure_callback is not None:
            try:
                failure_callback("title generation", e)
            except Exception:
                logger.debug("Title generation failure_callback raised", exc_info=True)
        return None


def auto_title_session(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
    title_callback: Optional[TitleCallback] = None,
    outcome_callback: Optional[OutcomeCallback] = None,
) -> AutoTitleOutcome:
    """Generate and set a session title if one doesn't already exist.

    Called in a background thread after the first exchange completes.
    Produces one terminal outcome and skips generation if:
    - session_db is None
    - session already has a title (user-set or previously auto-generated)
    - title generation fails
    """
    outcome = AutoTitleOutcome("skipped", reason="missing_session")
    try:
        if not session_db or not session_id:
            return outcome

        existing = session_db.get_session_title_in_lineage(session_id)
        if existing:
            outcome = AutoTitleOutcome("skipped", title=existing, reason="title_exists")
            return outcome
        if session_db.get_session(session_id) is None:
            outcome = AutoTitleOutcome("skipped", reason="session_not_found")
            return outcome

        generation_errors: list[BaseException] = []

        def _generation_failed(task: str, exc: BaseException) -> None:
            generation_errors.append(exc)
            if failure_callback is not None:
                failure_callback(task, exc)

        title = generate_title(
            user_message,
            assistant_response,
            failure_callback=_generation_failed,
            main_runtime=main_runtime,
        )
        if not title:
            reason = str(generation_errors[-1]) if generation_errors else "empty_title"
            logger.warning("Auto-title failed for session %s: %s", session_id, reason)
            outcome = AutoTitleOutcome("failed", reason=reason)
            return outcome

        claimed = session_db.claim_auto_session_title(session_id, title)
        if claimed is None:
            outcome = AutoTitleOutcome("skipped", reason="title_claim_lost")
            return outcome

        committed_session_id, committed_title = claimed
        logger.debug(
            "Auto-generated session title for %s: %s",
            committed_session_id,
            committed_title,
        )
        if title_callback is not None:
            try:
                title_callback(committed_title)
            except Exception:
                logger.debug("Auto-title callback failed", exc_info=True)
        outcome = AutoTitleOutcome("updated", title=committed_title)
        return outcome
    except Exception as exc:
        logger.warning("Auto-title failed for session %s: %s", session_id, exc)
        logger.debug("Auto-title worker traceback", exc_info=True)
        outcome = AutoTitleOutcome("failed", reason=str(exc) or type(exc).__name__)
        return outcome
    finally:
        if outcome_callback is not None:
            try:
                outcome_callback(outcome)
            except Exception:
                logger.debug("Auto-title outcome callback failed", exc_info=True)


def maybe_auto_title(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
    title_callback: Optional[TitleCallback] = None,
    outcome_callback: Optional[OutcomeCallback] = None,
) -> bool:
    """Fire-and-forget title generation after the first exchange.

    Only generates a title when:
    - This appears to be the first user→assistant exchange
    - No title is already set
    """
    if not session_db or not session_id or not user_message or not assistant_response:
        return False

    # Count user messages in history to detect first exchange.
    # conversation_history includes the exchange that just happened,
    # so for a first exchange we expect exactly 1 user message
    # (or 2 counting system). Be generous: generate on first 2 exchanges.
    user_msg_count = sum(1 for m in (conversation_history or []) if m.get("role") == "user")
    if user_msg_count > 2:
        return False

    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(
            auto_title_session,
            session_db,
            session_id,
            user_message,
            assistant_response,
        ),
        kwargs={
            "failure_callback": failure_callback,
            "main_runtime": main_runtime,
            "title_callback": title_callback,
            "outcome_callback": outcome_callback,
        },
        daemon=True,
        name="auto-title",
    )
    thread.start()
    return True
