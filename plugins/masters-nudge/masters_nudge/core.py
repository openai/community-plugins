"""One synchronous judgment immediately after a completed patch."""
from dataclasses import asdict
from . import providers
from .contracts import (
    MATERIAL_MAX_CHARS, SessionRef, ToolCompleted, ToolFault, patch_operations,
)
from .evidence import build_packet
from .prompting import load_system_prompt
from .provider_contract import parse_feedback
from .runtime import RuntimeSettings, PROVIDER_TIMEOUT_SEC
from .storage import Journal


class ReviewCancellation:
    def __init__(self, journal, attempt, event):
        self.journal, self.attempt, self.event = journal, attempt, event

    def is_set(self):
        return bool(self.event is not None and self.event.is_set()) or self.journal.cancelled(self.attempt)


class NudgeCore:
    def __init__(self, settings: RuntimeSettings, *, dispatch=None, log_error=None):
        self.settings = settings
        self.dispatch = dispatch or providers.call_codex_result
        self.log_error = log_error or (lambda message: None)
        self.journal = Journal(settings.paths.data_dir, model=settings.model)

    def start_round(self, session: SessionRef, request: str, goal: str = ""):
        self.journal.start_round(session, request, goal)

    def process_event(self, session: SessionRef, event: ToolCompleted, *, cancel_event=None):
        events = (event,)
        response = event.tool_response
        succeeded = (isinstance(response, str) and
                     (response.startswith("Success") or
                      response.startswith("Exit code: 0") and
                      any(line.startswith("Success.") for line in response.splitlines()))
                     or isinstance(response, dict) and
                     (response.get("success") is True
                      or type(response.get("exit_code")) is int and response["exit_code"] == 0
                      or isinstance(response.get("output"), str)
                      and response["output"].startswith("Success")))
        operations = patch_operations(event.modification, session.cwd) if succeeded else None
        test_only = bool(operations) and all(is_test for _, _, is_test in operations)
        reserved = self.journal.begin(session, skip_provider=test_only, cancel_event=cancel_event)
        if reserved is None:
            return None
        attempt, task = reserved
        cancel_event = ReviewCancellation(self.journal, attempt, cancel_event)
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise ToolFault("interrupted", "Provider review cancelled")
            if self.settings.configuration_error or self.settings.provider not in ("openai", "codex"):
                raise ToolFault("configuration", self.settings.configuration_error or "僅支援 OpenAI／Codex")
            packet = build_packet(session, task, events)
            run = self.dispatch(
                system_prompt=load_system_prompt(prompt_file=self.settings.paths.runtime_dir / "buddy-prompt.txt"),
                nudge_input=packet.render(), model=self.settings.model,
                reasoning_effort=self.settings.reasoning_effort,
                schema_path=self.settings.paths.runtime_dir / "nudge-schema.json",
                timeout_sec=task.get("provider_timeout_sec", PROVIDER_TIMEOUT_SEC),
                workspace_root=packet.workspace,
                remaining_chars=max(0, MATERIAL_MAX_CHARS - packet.material_chars),
                log_error=self.log_error,
                cancel_event=cancel_event,
            )
            if cancel_event is not None and cancel_event.is_set():
                raise ToolFault("interrupted", "Provider review cancelled")
            feedback = parse_feedback(run.raw_output)
            current = self.journal.finish(
                session, attempt, task, "feedback" if feedback else "silence",
                feedback=asdict(feedback) if feedback else None,
            )
            if current and feedback:
                return attempt, feedback
            return None
        except (KeyboardInterrupt, SystemExit):
            self.journal.finish(session, attempt, task, "fault", reason="interrupted")
            raise
        except Exception as exc:
            fault = exc if isinstance(exc, ToolFault) else ToolFault("internal", str(exc))
            self.journal.finish(session, attempt, task, "fault", reason=fault.kind)
            if not self.journal.is_current_round(session, task["round_id"]):
                return None
            raise fault
