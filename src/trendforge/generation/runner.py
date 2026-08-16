from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from trendforge.config import ROOT
from trendforge.db import get_session_factory
from trendforge.generation.agent import apply_result_file
from trendforge.generation.events import append_event, redact_text
from trendforge.generation.handoff import job_handoff_dir, read_result
from trendforge.generation.prompt import render_launch_prompt
from trendforge.models import GenerationJob, GenerationJobStatus, utcnow

REASON_CLI_MISSING = "Cursor Agent CLI not found"
REASON_PROCESS_FAILED = "agent process exited non-zero"
REASON_MCP_UNAVAILABLE = "Cursor Agent launched but comfy-cloud MCP was not available."
REASON_NO_RESULT = "agent exited without result contract"
REASON_CLI_AUTH = "Cursor Agent CLI is not authenticated. Run agent login once."

BUSY_STATUSES = {
    GenerationJobStatus.AGENT_RUNNING,
    GenerationJobStatus.COMFY_RUNNING,
    GenerationJobStatus.REVIEWING,
}

_IDE_CLI_NAMES = {"cursor", "cursor.exe", "cursor.cmd", "cursor.ps1"}
_AGENT_NAMES = (
    "agent.exe",
    "agent.cmd",
    "agent.ps1",
    "agent",
    "cursor-agent.exe",
    "cursor-agent.cmd",
    "cursor-agent.ps1",
    "cursor-agent",
)
_VERSION_DIR = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(-\d{2}-\d{2}-\d{2})?-[a-f0-9]+$")
_COMFY_CLOUD_HINTS = ("comfy-cloud", "comfyui-cloud", "user-comfyui-cloud")

_watchers: dict[int, threading.Thread] = {}
_watchers_lock = threading.Lock()


@dataclass(frozen=True)
class CliCapabilities:
    executable: Path
    version: str
    help_text: str
    print_flag: str | None
    has_force: bool
    has_trust: bool
    has_approve_mcps: bool
    has_workspace: bool
    has_output_format: bool


def workspace_root() -> Path:
    return ROOT


def _search_dirs() -> list[Path]:
    home = Path.home()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    dirs = [
        home / ".local" / "bin",
        home / ".cursor" / "bin",
        home / ".cursor" / "cli",
    ]
    if local:
        lp = Path(local)
        dirs.extend(
            [
                lp / "cursor-agent",
                lp / "cursor-agent" / "bin",
                lp / "Programs" / "cursor-agent",
            ]
        )
    return dirs


def discover_cursor_agent(extra_paths: Iterable[str | Path] | None = None) -> Path | None:
    override = os.environ.get("TRENDFORGE_CURSOR_AGENT", "").strip()
    if override:
        resolved = Path(override).expanduser()
        if resolved.name.lower() in _IDE_CLI_NAMES:
            return None
        if resolved.is_file():
            return resolved
        return None
    candidates: list[Path] = []
    for name in _AGENT_NAMES:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for directory in _search_dirs():
        for name in _AGENT_NAMES:
            candidates.append(directory / name)
    if extra_paths:
        candidates.extend(Path(p) for p in extra_paths)
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.expanduser()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.name.lower() in _IDE_CLI_NAMES:
            continue
        if resolved.is_file():
            return resolved
    return None


def resolve_agent_argv(executable: Path) -> list[str]:
    """Turn Windows wrapper scripts into a Popen argv (no shell string)."""
    suffix = executable.suffix.lower()
    name = executable.name.lower()
    if suffix in {".cmd", ".bat", ".ps1"} or name in {"agent", "cursor-agent"}:
        versions = executable.parent / "versions"
        if versions.is_dir():
            dirs = [p for p in versions.iterdir() if p.is_dir() and _VERSION_DIR.match(p.name)]
            dirs.sort(key=lambda p: p.name, reverse=True)
            for directory in dirs:
                node = directory / "node.exe"
                index = directory / "index.js"
                if node.is_file() and index.is_file():
                    return [str(node), str(index)]
        ps1 = executable.with_suffix(".ps1")
        if name.endswith(".cmd"):
            ps1 = executable.with_name(executable.stem + ".ps1")
        if ps1.is_file():
            root = os.environ.get("SystemRoot", r"C:\Windows")
            powershell = str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
    return [str(executable)]


def _run_cli(executable: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*resolve_agent_argv(executable), *args],
        cwd=str(workspace_root()),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )


def probe_cli_capabilities(executable: Path, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> CliCapabilities:
    run = runner or _run_cli
    help_text = ""
    version = ""
    try:
        help_proc = run(executable, ["--help"], timeout=25)
        help_text = f"{help_proc.stdout or ''}\n{help_proc.stderr or ''}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        help_text = str(exc)
    try:
        ver_proc = run(executable, ["--version"], timeout=20)
        version = (ver_proc.stdout or ver_proc.stderr or "").strip().splitlines()[0] if (ver_proc.stdout or ver_proc.stderr) else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    lowered = help_text.lower()
    print_flag = None
    if re.search(r"--print\b", lowered) or re.search(r"\s-p,\s*--print", lowered):
        print_flag = "--print"
    elif re.search(r"\s-p\b", lowered):
        print_flag = "-p"
    return CliCapabilities(
        executable=executable,
        version=version,
        help_text=help_text,
        print_flag=print_flag,
        has_force="--force" in lowered,
        has_trust="--trust" in lowered,
        has_approve_mcps="--approve-mcps" in lowered,
        has_workspace="--workspace" in lowered,
        has_output_format="--output-format" in lowered,
    )


def build_agent_command(
    job_id: int,
    *,
    executable: Path,
    caps: CliCapabilities | None = None,
    workspace: Path | None = None,
) -> list[str]:
    caps = caps or probe_cli_capabilities(executable)
    ws = str(workspace or workspace_root())
    cmd = resolve_agent_argv(executable)
    if caps.print_flag:
        cmd.append(caps.print_flag)
    if caps.has_force:
        cmd.append("--force")
    if caps.has_trust:
        cmd.append("--trust")
    if caps.has_approve_mcps:
        cmd.append("--approve-mcps")
    if caps.has_workspace:
        cmd.extend(["--workspace", ws])
    if caps.has_output_format:
        cmd.extend(["--output-format", "text"])
    cmd.append(render_launch_prompt(job_id))
    return cmd


def preflight_comfy_cloud_mcp(
    executable: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, str]:
    run = runner or _run_cli
    try:
        proc = run(executable, ["mcp", "list"], timeout=40)
    except subprocess.TimeoutExpired:
        return False, REASON_MCP_UNAVAILABLE
    except OSError:
        return False, REASON_MCP_UNAVAILABLE
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    if proc.returncode != 0 and "mcp" not in blob:
        return False, REASON_MCP_UNAVAILABLE
    if any(hint in blob for hint in _COMFY_CLOUD_HINTS):
        return True, "comfy-cloud MCP listed"
    return False, REASON_MCP_UNAVAILABLE


def preflight_agent_auth(
    executable: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, str]:
    run = runner or _run_cli
    try:
        proc = run(executable, ["status"], timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return False, REASON_CLI_AUTH
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    if "not logged in" in blob or "please run 'agent login'" in blob:
        return False, REASON_CLI_AUTH
    return True, "cursor agent authenticated"


_auth_cache: dict[str, Any] = {"at": 0.0, "value": None}


def cli_auth_status(*, ttl_seconds: float = 20.0) -> dict[str, Any]:
    import time

    now = time.time()
    cached = _auth_cache.get("value")
    if cached and now - float(_auth_cache.get("at") or 0) < ttl_seconds:
        return cached
    exe = discover_cursor_agent()
    if exe is None:
        value = {
            "ok": False,
            "authenticated": False,
            "message": REASON_CLI_MISSING,
            "hint": "Install Cursor Agent CLI, then run: agent login",
        }
        _auth_cache["at"] = now
        _auth_cache["value"] = value
        return value
    ok, detail = preflight_agent_auth(exe)
    value = {
        "ok": ok,
        "authenticated": ok,
        "message": detail,
        "hint": None if ok else "Open PowerShell and run: agent login",
        "cli": str(exe),
    }
    _auth_cache["at"] = now
    _auth_cache["value"] = value
    return value


def pid_is_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _creationflags() -> int:
    flags = 0
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags


def _update_meta(job: GenerationJob, **fields: Any) -> None:
    meta = dict(job.job_metadata_json or {})
    meta.update(fields)
    job.job_metadata_json = meta
    job.updated_at = utcnow()


def fail_job(db, job: GenerationJob, reason: str) -> GenerationJob:
    job.status = GenerationJobStatus.FAILED
    job.error_message = reason[:400]
    _update_meta(job, agent_finished=utcnow().isoformat())
    append_event(job, "job failed", reason)
    db.commit()
    return job


def _already_running(job: GenerationJob) -> bool:
    if job.status not in BUSY_STATUSES:
        return False
    pid = (job.job_metadata_json or {}).get("agent_pid")
    if not pid:
        return False
    return pid_is_alive(pid)


def launch_generation_agent(
    db,
    job: GenerationJob,
    *,
    popen: Callable[..., subprocess.Popen[Any]] | None = None,
    discover: Callable[[], Path | None] | None = None,
    probe: Callable[[Path], CliCapabilities] | None = None,
    preflight: Callable[[Path], tuple[bool, str]] | None = None,
    watch: bool = True,
) -> GenerationJob:
    if _already_running(job):
        append_event(job, "duplicate launch skipped", f"pid {(job.job_metadata_json or {}).get('agent_pid')}")
        db.commit()
        return job

    exe = (discover or discover_cursor_agent)()
    if exe is None:
        return fail_job(db, job, REASON_CLI_MISSING)

    caps = (probe or probe_cli_capabilities)(exe)
    if preflight is None:
        auth_ok, auth_detail = preflight_agent_auth(exe)
        if not auth_ok:
            return fail_job(db, job, REASON_CLI_AUTH)
        ok, detail = preflight_comfy_cloud_mcp(exe)
        detail = f"{auth_detail}; {detail}"
    else:
        ok, detail = preflight(exe)
    if not ok:
        return fail_job(db, job, detail or REASON_MCP_UNAVAILABLE)

    folder = job_handoff_dir(job.id)
    log_path = folder / "agent.log"
    cmd = build_agent_command(job.id, executable=exe, caps=caps, workspace=workspace_root())
    if any(token in " ".join(cmd).lower() for token in ("--api-key", "sk-", "comfyui-")):
        return fail_job(db, job, "refusing to launch agent with credential-like arguments")

    spawn = popen or subprocess.Popen
    log_handle = log_path.open("ab")
    env = os.environ.copy()
    env.setdefault("CURSOR_INVOKED_AS", "agent")
    try:
        proc = spawn(
            cmd,
            cwd=str(workspace_root()),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            shell=False,
            env=env,
            creationflags=_creationflags(),
        )
    except OSError:
        log_handle.close()
        return fail_job(db, job, REASON_CLI_MISSING)

    started = utcnow().isoformat()
    _update_meta(
        job,
        agent_pid=proc.pid,
        agent_started=started,
        agent_finished=None,
        cli_path=str(exe),
        cli_version=caps.version,
        execution_target="Comfy Cloud",
        cloud_mcp_used=True,
        local_comfy_used=False,
        preflight=detail,
    )
    job.status = GenerationJobStatus.AGENT_RUNNING
    append_event(job, "cursor agent launched", f"pid {proc.pid}")
    db.commit()
    if watch:
        _start_watcher(job.id, proc, log_handle)
    else:
        try:
            log_handle.close()
        except OSError:
            pass
    return job


def _start_watcher(job_id: int, proc: subprocess.Popen[Any], log_handle: Any) -> None:
    def _run() -> None:
        try:
            code = proc.wait()
        finally:
            try:
                log_handle.close()
            except OSError:
                pass
            _redact_log_file(job_handoff_dir(job_id) / "agent.log")
        db = get_session_factory()()
        try:
            handle_agent_exit(db, job_id, code)
        finally:
            db.close()

    thread = threading.Thread(target=_run, name=f"tf-agent-{job_id}", daemon=True)
    with _watchers_lock:
        _watchers[job_id] = thread
    thread.start()


def _redact_log_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(redact_text(text), encoding="utf-8")
    except OSError:
        return


def handle_agent_exit(db, job_id: int, exit_code: int | None) -> GenerationJob | None:
    job = db.get(GenerationJob, job_id)
    if job is None:
        return None
    _update_meta(
        job,
        agent_finished=utcnow().isoformat(),
        agent_exit_code=exit_code,
    )
    append_event(job, "cursor agent exited", f"code {exit_code}")
    db.commit()
    if job.status in {GenerationJobStatus.COMPLETED, GenerationJobStatus.FAILED}:
        return job
    payload = read_result(job.id)
    if payload:
        return apply_result_file(db, job)
    log = agent_log_tail(job.id, 40).lower()
    if "not logged in" in log or "agent login" in log:
        return fail_job(db, job, REASON_CLI_AUTH)
    if exit_code not in (0, None):
        return fail_job(db, job, REASON_PROCESS_FAILED)
    return fail_job(db, job, REASON_NO_RESULT)


def recover_generation_jobs(db) -> list[int]:
    touched: list[int] = []
    rows = (
        db.query(GenerationJob)
        .filter(GenerationJob.status.in_(tuple(BUSY_STATUSES)))
        .all()
    )
    for job in rows:
        payload = read_result(job.id)
        if payload and job.status != GenerationJobStatus.COMPLETED:
            apply_result_file(db, job)
            touched.append(job.id)
            continue
        meta = job.job_metadata_json or {}
        pid = meta.get("agent_pid")
        if pid_is_alive(pid):
            continue
        if pid:
            fail_job(db, job, REASON_NO_RESULT if not payload else REASON_PROCESS_FAILED)
            touched.append(job.id)
    return touched


def agent_log_tail(job_id: int, lines: int = 80) -> str:
    path = job_handoff_dir(job_id) / "agent.log"
    if not path.is_file():
        return ""
    text = redact_text(path.read_text(encoding="utf-8", errors="replace"))
    parts = text.splitlines()
    return "\n".join(parts[-lines:])
