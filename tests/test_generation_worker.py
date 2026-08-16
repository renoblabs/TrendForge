from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.generation.events import redact_text
from trendforge.generation.fake_png import write_png
from trendforge.generation.handoff import job_handoff_dir, write_result
from trendforge.generation.prompt import AGENT_PROMPT_VERSION, render_launch_prompt
from trendforge.generation.runner import (
    REASON_CLI_AUTH,
    REASON_CLI_MISSING,
    REASON_MCP_UNAVAILABLE,
    REASON_NO_RESULT,
    REASON_PROCESS_FAILED,
    build_agent_command,
    discover_cursor_agent,
    handle_agent_exit,
    launch_generation_agent,
    preflight_agent_auth,
    preflight_comfy_cloud_mcp,
    probe_cli_capabilities,
    recover_generation_jobs,
    resolve_agent_argv,
)
from trendforge.generation.service import create_generation_job
from trendforge.ideation.generate import approve_all_ideas, generate_brainstorm_set
from trendforge.models import AnalysisStatus, ContentCandidate, GenerationJobStatus
import pytest


HELP_TEXT = """
Usage: agent [options] [prompt...]
  -p, --print              Print responses (non-interactive)
  --force                  Force allow commands
  --trust                  Trust the workspace without prompting
  --approve-mcps           Automatically approve all MCP servers
  --workspace <path>       Workspace directory to use
  --output-format <fmt>    text, json, or stream-json
"""


@pytest.fixture()
def db(tmp_path: Path, monkeypatch) -> Session:
    monkeypatch.setenv("TRENDFORGE_DB_PATH", str(tmp_path / "gen.db"))
    monkeypatch.setenv("TRENDFORGE_GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("TRENDFORGE_JOBS_DIR", str(tmp_path / "jobs"))
    path = tmp_path / "gen.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _idea(db: Session, family: str = "worker-family"):
    db.add(
        ContentCandidate(
            platform="youtube",
            url=f"https://www.youtube.com/shorts/{family}-w",
            title="live",
            analysis_status=AnalysisStatus.ANALYZED,
            data_origin="live",
            is_short=True,
            discovery_score=40,
            analysis_json={
                "format_family": family,
                "format_key": family,
                "format_name": family,
                "primary_mechanic": "IMPOSSIBLE_POV",
                "format_hypothesis": "Impossible POV plus payoff.",
            },
        )
    )
    db.commit()
    bset = generate_brainstorm_set(db, family, count=5, force_stub=True)
    return approve_all_ideas(db, bset)


def _job(db: Session, family: str = "worker-family"):
    bset = _idea(db, family)
    return create_generation_job(
        db,
        format_family=family,
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )


def _ok_result(job_id: int, png: Path) -> dict:
    return {
        "status": "completed",
        "job_id": job_id,
        "success": True,
        "execution_target": "comfy_cloud",
        "local_comfy_used": False,
        "cloud_mcp_used": True,
        "cloud_job_id": f"cloud-{job_id}",
        "workflow_or_template": "image_z_image_turbo",
        "model": "Z-Image-Turbo",
        "output_files": [str(png)],
        "output_dimensions": "72x128",
        "output_duration_seconds": None,
        "qa_result": "passed",
        "agent": "cursor",
        "completed_at": "2026-08-15T00:00:00+00:00",
        "error": None,
    }


def _caps(exe: Path):
    return probe_cli_capabilities(
        exe,
        runner=lambda executable, args, timeout=30: CompletedProcess(
            args, 0, stdout=HELP_TEXT if args[:1] == ["--help"] else "agent 1.0.0", stderr=""
        ),
    )


def test_prompt_version_is_v3():
    assert AGENT_PROMPT_VERSION == "production-agent-v3"
    text = render_launch_prompt(42)
    assert "Generation Job #42" in text
    assert "comfy-cloud" in text
    assert "result.json" in text
    assert "sk-" not in text
    assert "api_key" not in text.lower()


def test_discover_cursor_agent_override_and_rejects_ide(tmp_path: Path, monkeypatch):
    fake = tmp_path / "agent.exe"
    fake.write_bytes(b"")
    ide = tmp_path / "cursor.cmd"
    ide.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.delenv("TRENDFORGE_CURSOR_AGENT", raising=False)
    monkeypatch.setenv("TRENDFORGE_CURSOR_AGENT", str(ide))
    assert discover_cursor_agent() is None
    monkeypatch.setenv("TRENDFORGE_CURSOR_AGENT", str(fake))
    assert discover_cursor_agent() == fake


def test_resolve_cmd_wrapper_to_node(tmp_path: Path):
    root = tmp_path / "cursor-agent"
    ver = root / "versions" / "2026.08.11-e8db854"
    ver.mkdir(parents=True)
    (ver / "node.exe").write_bytes(b"")
    (ver / "index.js").write_text("console.log('ok')", encoding="utf-8")
    cmd = root / "agent.cmd"
    cmd.write_text("rem wrapper\n", encoding="utf-8")
    argv = resolve_agent_argv(cmd)
    assert argv[0] == str(ver / "node.exe")
    assert argv[1] == str(ver / "index.js")


def test_command_construction(tmp_path: Path):
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"")
    caps = _caps(exe)
    cmd = build_agent_command(9, executable=exe, caps=caps, workspace=Path(r"C:\Users\19057\TrendFactory"))
    assert cmd[0] == str(exe)
    assert "--print" in cmd
    assert "--force" in cmd
    assert "--trust" in cmd
    assert "--approve-mcps" in cmd
    assert "--workspace" in cmd
    assert r"C:\Users\19057\TrendFactory" in cmd
    assert "--api-key" not in cmd
    joined = " ".join(cmd)
    assert "sk-" not in joined
    assert "COMFY" not in joined


def test_missing_executable_fails_job(db: Session):
    job = _job(db, "missing-cli")
    launch_generation_agent(db, job, discover=lambda: None, watch=False)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert job.error_message == REASON_CLI_MISSING


def test_mcp_unavailable_fails_job(db: Session, tmp_path: Path):
    job = _job(db, "no-mcp")
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"")
    launch_generation_agent(
        db,
        job,
        discover=lambda: exe,
        probe=lambda path: _caps(path),
        preflight=lambda path: (False, REASON_MCP_UNAVAILABLE),
        watch=False,
    )
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert job.error_message == REASON_MCP_UNAVAILABLE


def test_preflight_detects_comfy_cloud_name():
    exe = Path("agent")
    proc = CompletedProcess(["mcp", "list"], 0, stdout="comfyui-cloud  enabled\n", stderr="")
    ok, msg = preflight_comfy_cloud_mcp(exe, runner=lambda executable, args, timeout=30: proc)
    assert ok is True
    bad = CompletedProcess(["mcp", "list"], 0, stdout="other-server\n", stderr="")
    ok2, msg2 = preflight_comfy_cloud_mcp(exe, runner=lambda executable, args, timeout=30: bad)
    assert ok2 is False
    assert msg2 == REASON_MCP_UNAVAILABLE


def test_job_launch_records_pid(db: Session, tmp_path: Path):
    job = _job(db, "launch-ok")
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"")
    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["cwd"] = kwargs.get("cwd")
        spawned["shell"] = kwargs.get("shell")
        return SimpleNamespace(pid=4242, wait=lambda: 0)

    launch_generation_agent(
        db,
        job,
        discover=lambda: exe,
        probe=lambda path: _caps(path),
        preflight=lambda path: (True, "ok"),
        popen=fake_popen,
        watch=False,
    )
    db.refresh(job)
    assert job.status == GenerationJobStatus.AGENT_RUNNING
    assert (job.job_metadata_json or {}).get("agent_pid") == 4242
    assert spawned["shell"] is False
    assert isinstance(spawned["cmd"], list)
    assert "--api-key" not in spawned["cmd"]
    events = [e["event"] for e in (job.job_metadata_json or {}).get("events") or []]
    assert "cursor agent launched" in events


def test_duplicate_launch_prevented(db: Session, tmp_path: Path, monkeypatch):
    job = _job(db, "dup-launch")
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"")
    calls = {"n": 0}

    def fake_popen(cmd, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(pid=99, wait=lambda: 0)

    monkeypatch.setattr("trendforge.generation.runner.pid_is_alive", lambda pid: int(pid or 0) == 99)
    launch_generation_agent(
        db,
        job,
        discover=lambda: exe,
        probe=lambda path: _caps(path),
        preflight=lambda path: (True, "ok"),
        popen=fake_popen,
        watch=False,
    )
    launch_generation_agent(
        db,
        job,
        discover=lambda: exe,
        probe=lambda path: _caps(path),
        preflight=lambda path: (True, "ok"),
        popen=fake_popen,
        watch=False,
    )
    assert calls["n"] == 1
    events = [e["event"] for e in (job.job_metadata_json or {}).get("events") or []]
    assert "duplicate launch skipped" in events


def test_process_failure(db: Session):
    job = _job(db, "proc-fail")
    handle_agent_exit(db, job.id, 1)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert job.error_message == REASON_PROCESS_FAILED


def test_missing_result_json(db: Session):
    job = _job(db, "no-result")
    handle_agent_exit(db, job.id, 0)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert job.error_message == REASON_NO_RESULT


def test_result_application_on_exit(db: Session, tmp_path: Path):
    job = _job(db, "apply-exit")
    png = tmp_path / "generated" / "ok.png"
    write_png(png, 72, 128)
    write_result(job.id, _ok_result(job.id, png))
    handle_agent_exit(db, job.id, 0)
    db.refresh(job)
    assert job.status == GenerationJobStatus.COMPLETED
    assert (job.job_metadata_json or {}).get("cloud_job_id") == f"cloud-{job.id}"


def test_restart_recovery(db: Session, tmp_path: Path, monkeypatch):
    dead = _job(db, "recover-dead")
    dead.job_metadata_json = {**(dead.job_metadata_json or {}), "agent_pid": 2147483646}
    db.commit()
    live = _job(db, "recover-live")
    live.job_metadata_json = {**(live.job_metadata_json or {}), "agent_pid": 7}
    db.commit()
    ready = _job(db, "recover-ready")
    png = tmp_path / "generated" / "ready.png"
    write_png(png, 72, 128)
    write_result(ready.id, _ok_result(ready.id, png))
    ready.job_metadata_json = {**(ready.job_metadata_json or {}), "agent_pid": 8}
    db.commit()

    def fake_alive(pid):
        return int(pid or 0) == 7

    monkeypatch.setattr("trendforge.generation.runner.pid_is_alive", fake_alive)
    recover_generation_jobs(db)
    db.refresh(dead)
    db.refresh(live)
    db.refresh(ready)
    assert dead.status == GenerationJobStatus.FAILED
    assert live.status == GenerationJobStatus.AGENT_RUNNING
    assert ready.status == GenerationJobStatus.COMPLETED


def test_no_secret_logging():
    text = redact_text("Authorization Bearer secret-token COMFY_API_KEY=abc123 sk-abcdefghijklmnop")
    assert "sk-abcdefghijklmnop" not in text
    assert "secret-token" not in text
    assert "abc123" not in text
    assert "[redacted]" in text


def test_cli_auth_preflight_fails_job(db: Session, tmp_path: Path):
    job = _job(db, "no-auth")
    exe = tmp_path / "agent.exe"
    exe.write_bytes(b"")
    proc = CompletedProcess(["status"], 0, stdout="Not logged in\n", stderr="")
    ok, msg = preflight_agent_auth(exe, runner=lambda executable, args, timeout=30: proc)
    assert ok is False
    assert msg == REASON_CLI_AUTH
    launch_generation_agent(
        db,
        job,
        discover=lambda: exe,
        probe=lambda path: _caps(path),
        preflight=lambda path: (False, REASON_CLI_AUTH),
        watch=False,
    )
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED


def test_auth_error_from_agent_log(db: Session):
    job = _job(db, "auth-log")
    log = job_handoff_dir(job.id) / "agent.log"
    log.write_text("Error: Authentication required. Please run 'agent login' first.\n", encoding="utf-8")
    handle_agent_exit(db, job.id, 1)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert job.error_message == REASON_CLI_AUTH


def test_create_job_launches_when_requested(db: Session, tmp_path: Path, monkeypatch):
    bset = _idea(db, "auto-launch")
    called = {"job_id": None}

    def fake_launch(db, job):
        called["job_id"] = job.id
        return job

    monkeypatch.setattr("trendforge.generation.service.launch_generation_agent", fake_launch)
    job = create_generation_job(
        db,
        format_family="auto-launch",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=True,
    )
    assert called["job_id"] == job.id
    assert job.status == GenerationJobStatus.AGENT_RUNNING
