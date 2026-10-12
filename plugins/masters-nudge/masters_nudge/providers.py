"""Only the Codex Provider transport; subprocess failure is always explicit."""
from __future__ import annotations
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from .contracts import MaterialLine, ProviderRun, ToolFault, json_text
from .read_only_repo_mcp import read_audit
from .runtime import PROVIDER_REASONING_EFFORT, provider_environment

Logger = Callable[[str], None]

def _noop(_message: str) -> None:
    return None


def _provider_process_kwargs() -> dict[str, int]:
    """Keep Provider CLIs from opening a transient console on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _terminate_process_tree(
    process: subprocess.Popen,
    *,
    log_error: Logger = _noop,
) -> tuple[str, str]:
    """Terminate a timed-out Provider and every descendant it started."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=3,
                **_provider_process_kwargs(),
            )
        else:
            # `_run_cli_process` makes the Provider PID its process-group ID.
            # The group can outlive its leader, so do not look the PID up first.
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError) as exc:
        log_error(f"Provider process-tree cleanup failed: {exc}")
        try:
            process.kill()
        except OSError:
            pass
    try:
        stdout, stderr = process.communicate(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            return "", ""
    return str(stdout or ""), str(stderr or "")


def _run_cli_process(
    command: list[str] | str,
    *,
    input_text: str | None = None,
    cwd: str | None = None,
    environment: dict[str, str],
    timeout_sec: int,
    shell: bool = False,
    log_error: Logger = _noop,
) -> subprocess.CompletedProcess:
    kwargs = _provider_process_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    # Windows' communicate(input=...) writes to the pipe synchronously before
    # waiting with a deadline. A child that stops reading can therefore bypass
    # the timeout. A file-backed stdin makes the deadline own the whole wait.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") if input_text is not None else _null_stdin() as input_stream, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_stream, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_stream:
        if input_text is not None:
            input_stream.write(input_text)
            input_stream.seek(0)
        process = subprocess.Popen(
            command,
            stdin=input_stream if input_text is not None else None,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
            encoding="utf-8",
            env=environment,
            cwd=cwd,
            shell=shell,
            **kwargs,
        )
        try:
            process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process, log_error=log_error)
            stdout_stream.seek(0)
            stderr_stream.seek(0)
            exc.output = stdout_stream.read()
            exc.stderr = stderr_stream.read()
            raise
        stdout_stream.seek(0)
        stderr_stream.seek(0)
        stdout = stdout_stream.read()
        stderr = stderr_stream.read()
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )


class _null_stdin:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False



def resolve_codex_bin() -> str | None:
    if os.name == "nt":
        native = shutil.which("codex.exe")
        if native:
            return native
    return shutil.which("codex")


def _failure_message(stdout: str, stderr: str) -> str:
    """Prefer the terminal failure event over incidental process warnings."""
    last_error = ""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
        if event.get("type") == "error" and not last_error:
            message = event.get("message")
            if isinstance(message, str):
                last_error = message.strip()
    return (last_error or stderr.strip() or "未提供錯誤訊息")[:500]


def _call_codex_in_directory(
    system_prompt: str, nudge_input: str, model: str, *, schema_path: Path,
    timeout_sec: int, workspace_root: str, remaining_chars: int,
    run_directory: Path,
    log_error: Logger = _noop, codex_bin_resolver=resolve_codex_bin,
) -> ProviderRun:
    binary = codex_bin_resolver()
    if not binary:
        raise ToolFault("configuration", "找不到 Codex Provider 執行檔")
    if not schema_path.is_file():
        raise ToolFault("configuration", "找不到反饋輸出契約")
    root = Path(run_directory)
    audit = root / "reads.jsonl"
    output = root / "output.json"
    script = Path(__file__).with_name("read_only_repo_mcp.py")
    args = [script.as_posix(), "--root", Path(workspace_root).as_posix(),
            "--budget", str(remaining_chars), "--audit", audit.as_posix()]
    command = [
        binary, "-c", "features.shell_tool=false",
        "-c", "features.view_image=false",
        "-c", "features.plugins=false",
        "-c", "features.multi_agent=false",
        "-c", f'model_reasoning_effort="{PROVIDER_REASONING_EFFORT}"',
        "-c", 'web_search="disabled"',
        "-c", "project_doc_max_bytes=0",
        "-c", f"mcp_servers.readrepo.command={json_text(Path(sys.executable).as_posix())}",
        "-c", f"mcp_servers.readrepo.args={json_text(args)}",
        "-c", 'mcp_servers.readrepo.env={PYTHONIOENCODING="utf-8"}',
        "-c", "mcp_servers.readrepo.required=true",
        "-c", 'mcp_servers.readrepo.enabled_tools=["search_repo","read_file"]',
        "-c", 'mcp_servers.readrepo.default_tools_approval_mode="approve"',
        "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
        "--json", "-s", "read-only", "-m", model, "--output-schema", str(schema_path),
        "-o", str(output), "-",
    ]
    shell = binary.lower().endswith((".cmd", ".bat"))
    try:
        result = _run_cli_process(
            subprocess.list2cmdline(command) if shell else command,
            input_text=f"{system_prompt}\n\n{nudge_input}", cwd=str(root),
            environment=provider_environment(), timeout_sec=timeout_sec, shell=shell, log_error=log_error,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFault("timeout", "Provider 未在時限內完成", evidence={
            "trace": read_audit(audit), "raw_output": output.read_text(encoding="utf-8") if output.exists() else "",
            "stdout": str(exc.output or ""), "stderr": str(exc.stderr or "")}) from exc
    except OSError as exc:
        raise ToolFault("provider", str(exc)) from exc
    if result.returncode:
        raise ToolFault("provider", f"Codex 結束碼 {result.returncode}：{_failure_message(result.stdout, result.stderr)}",
                        evidence={"trace": read_audit(audit), "stdout": result.stdout, "stderr": result.stderr})
    trace = read_audit(audit)
    if not any(entry["name"] == "initialize" for entry in trace):
        raise ToolFault("mcp_startup", "Provider 的唯讀工具未成功初始化",
                        evidence={"stdout": result.stdout, "stderr": result.stderr})
    if sum(len(entry["text"]) for entry in trace) > remaining_chars:
        raise ToolFault("mcp_budget", "讀取材料超過共同上限")
    materials = []
    for entry in trace:
        if entry["text"]:
            for segment in json.loads(entry["text"]).get("segments", []):
                materials.extend(
                    MaterialLine("current_structure", segment["path"], segment["start"] + offset, line)
                    for offset, line in enumerate(segment["lines"])
                )
    try:
        raw = output.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolFault("output", "Provider 沒有輸出檔案") from exc
    usage = {}
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
    return ProviderRun(raw, tuple(materials), usage, tuple(trace))


def call_codex_result(
    system_prompt: str, nudge_input: str, model: str, *, schema_path: Path,
    timeout_sec: int, workspace_root: str, remaining_chars: int,
    log_error: Logger = _noop, codex_bin_resolver=resolve_codex_bin,
) -> ProviderRun:
    """Run one Provider call and keep its temporary evidence within that call."""
    with tempfile.TemporaryDirectory(prefix="masters-nudge-provider-") as directory:
        return _call_codex_in_directory(
            system_prompt, nudge_input, model, schema_path=schema_path,
            timeout_sec=timeout_sec, workspace_root=workspace_root,
            remaining_chars=remaining_chars, run_directory=Path(directory),
            log_error=log_error, codex_bin_resolver=codex_bin_resolver,
        )
