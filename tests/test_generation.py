from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.generation.agent import apply_result_file
from trendforge.generation.events import redact
from trendforge.generation.fake_png import write_png
from trendforge.generation.handoff import job_handoff_dir, write_result
from trendforge.generation.qa import review_output
from trendforge.generation.prompt import render_video_prompt
from trendforge.generation.service import (
    GenerationError,
    create_generation_job,
    list_cloud_models,
    list_generation_options,
)
from trendforge.ideation.generate import approve_all_ideas, generate_brainstorm_set, set_idea_pool_status
from trendforge.models import (
    AnalysisStatus,
    ContentAsset,
    ContentCandidate,
    GenerationJobStatus,
    ProductionSpec,
)


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


def _idea_family(db: Session, family: str = "impossible-pov-micro-story"):
    db.add(
        ContentCandidate(
            platform="youtube",
            url=f"https://www.youtube.com/shorts/{family}-g",
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
        "output_duration": None,
        "qa_result": "passed",
        "agent": "cursor-agent",
        "completed_at": "2026-08-15T00:00:00+00:00",
        "error": None,
    }


def test_redact_secrets_from_logs():
    payload = redact(
        {
            "openrouter_api_key": "sk-secret",
            "ok": "yes",
            "nested": {"token": "abc"},
            "cloud": "comfyui-" + ("a" * 40),
        }
    )
    assert payload["openrouter_api_key"] == "[redacted]"
    assert payload["nested"]["token"] == "[redacted]"
    assert payload["ok"] == "yes"
    assert payload["cloud"] == "[redacted]"


def test_create_job_defaults_to_short_test_duration(db: Session, tmp_path: Path):
    bset = _idea_family(db, family="short-test-family")
    job = create_generation_job(
        db,
        format_family="short-test-family",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )
    assert job.requested_duration_seconds == 5
    prompt = (job_handoff_dir(job.id) / "video_prompt.txt").read_text(encoding="utf-8")
    assert "5 seconds" in prompt
    assert "test clip" in prompt.lower()


def test_video_prompt_skips_beats_past_duration():
    prompt = render_video_prompt(
        {"requested_duration": 5, "cloud_model": {"max_duration_seconds": 15}},
        {
            "title": "smoke detector",
            "hook": "beep",
            "premise": "kitchen at 2am",
            "shots": [
                {"start_seconds": 0, "end_seconds": 3, "action": "first beep"},
                {"start_seconds": 12, "end_seconds": 15, "action": "should not appear"},
            ],
        },
    )
    assert "first beep" in prompt
    assert "should not appear" not in prompt
    assert "Compress the hook and payoff into 5 seconds" in prompt


def test_create_job_writes_contract_and_waits_for_agent(db: Session, tmp_path: Path):
    bset = _idea_family(db)
    job = create_generation_job(
        db,
        format_family="impossible-pov-micro-story",
        brainstorm_set_id=bset.id,
        idea_index=0,
        duration_seconds=20,
        force_stub_spec=True,
        launch_agent=False,
    )
    assert job.requested_duration_seconds == 20
    assert job.status == GenerationJobStatus.AGENT_RUNNING
    assert job.production_spec_id
    folder = job_handoff_dir(job.id)
    assert (folder / "job.json").is_file()
    assert (folder / "production_spec.json").is_file()
    assert (folder / "AGENT.md").is_file()
    assert (folder / "video_prompt.txt").is_file()
    import json

    payload = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert payload["cloud_model"]["id"] == "seedance-2.0"
    assert payload["output_kind"] == "video"
    prompt = (folder / "video_prompt.txt").read_text(encoding="utf-8")
    assert "9:16" in prompt
    assert "Vertical" in prompt
    assert "Seedance" in (folder / "AGENT.md").read_text(encoding="utf-8")
    assert "video_prompt.txt" in (folder / "AGENT.md").read_text(encoding="utf-8")
    events = [e["event"] for e in (job.job_metadata_json or {}).get("events") or []]
    assert "waiting for agent" in events
    specs = db.query(ProductionSpec).count()
    job2 = create_generation_job(
        db,
        format_family="impossible-pov-micro-story",
        brainstorm_set_id=bset.id,
        idea_index=0,
        duration_seconds=20,
        force_stub_spec=True,
        launch_agent=False,
    )
    assert db.query(ProductionSpec).count() == specs
    assert job2.production_spec_id == job.production_spec_id


def test_apply_result_registers_asset(db: Session, tmp_path: Path):
    bset = _idea_family(db, family="apply-family")
    job = create_generation_job(
        db,
        format_family="apply-family",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )
    png = tmp_path / "generated" / "ok.png"
    write_png(png, 72, 128)
    write_result(job.id, _ok_result(job.id, png))
    apply_result_file(db, job)
    db.refresh(job)
    assert job.status == GenerationJobStatus.COMPLETED
    assert job.output_asset_ids
    asset = db.get(ContentAsset, job.output_asset_ids[0])
    assert asset is not None
    assert Path(asset.file_reference).exists()
    meta = job.job_metadata_json or {}
    assert meta.get("execution_target") == "Comfy Cloud"
    assert meta.get("local_comfy_used") is False
    assert meta.get("cloud_mcp_used") is True
    assert meta.get("cloud_job_id") == f"cloud-{job.id}"


def test_malformed_result_fails(db: Session, tmp_path: Path):
    bset = _idea_family(db, family="bad-result")
    job = create_generation_job(
        db,
        format_family="bad-result",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )
    apply_result_file(db, job)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert "missing" in (job.error_message or "")


def test_local_comfy_result_rejected(db: Session, tmp_path: Path):
    bset = _idea_family(db, family="local-reject")
    job = create_generation_job(
        db,
        format_family="local-reject",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )
    png = tmp_path / "generated" / "local.png"
    write_png(png, 72, 128)
    payload = _ok_result(job.id, png)
    payload["local_comfy_used"] = True
    write_result(job.id, payload)
    apply_result_file(db, job)
    db.refresh(job)
    assert job.status == GenerationJobStatus.FAILED
    assert "local Comfy" in (job.error_message or "")


def test_unknown_cloud_model_rejected(db: Session):
    bset = _idea_family(db, family="bad-model")
    with pytest.raises(GenerationError):
        create_generation_job(
            db,
            format_family="bad-model",
            brainstorm_set_id=bset.id,
            idea_index=0,
            force_stub_spec=True,
            launch_agent=False,
            cloud_model="not-a-real-model",
        )


def test_selected_video_model_is_stored(db: Session):
    bset = _idea_family(db, family="kling-pick")
    job = create_generation_job(
        db,
        format_family="kling-pick",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
        cloud_model="kling-v3",
    )
    meta = job.job_metadata_json or {}
    assert meta["cloud_model"]["partner_model"] == "kling/kling-v3-t2v"
    ids = [row["id"] for row in list_cloud_models()]
    assert "seedance-2.0" in ids
    assert "veo-3.1" in ids


def test_qa_accepts_mp4(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40)
    review = review_output(path)
    assert review["ok"] is True
    assert review["mime_type"] == "video/mp4"


def test_only_approved_ideas_enter_generation_pool(db: Session):
    db.add(
        ContentCandidate(
            platform="youtube",
            url="https://www.youtube.com/shorts/pool-g",
            title="live",
            analysis_status=AnalysisStatus.ANALYZED,
            data_origin="live",
            is_short=True,
            discovery_score=40,
            analysis_json={
                "format_family": "pool-family",
                "format_key": "pool-family",
                "format_name": "pool-family",
                "primary_mechanic": "IMPOSSIBLE_POV",
                "format_hypothesis": "Impossible POV plus payoff.",
            },
        )
    )
    db.commit()
    bset = generate_brainstorm_set(db, "pool-family", count=5, force_stub=True)
    assert list_generation_options(db) == []
    with pytest.raises(GenerationError, match="generation pool"):
        create_generation_job(
            db,
            format_family="pool-family",
            brainstorm_set_id=bset.id,
            idea_index=0,
            force_stub_spec=True,
            launch_agent=False,
        )
    set_idea_pool_status(db, bset.id, 0, "approved")
    set_idea_pool_status(db, bset.id, 1, "denied")
    options = list_generation_options(db)
    assert len(options) == 1
    assert len(options[0]["ideas"]) == 1
    assert options[0]["ideas"][0]["idea_index"] == 0
    job = create_generation_job(
        db,
        format_family="pool-family",
        brainstorm_set_id=bset.id,
        idea_index=0,
        force_stub_spec=True,
        launch_agent=False,
    )
    assert job.status == GenerationJobStatus.AGENT_RUNNING


def test_generation_routes(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from trendforge.app import app
    from trendforge.db import get_db, get_session_factory, init_db

    monkeypatch.setenv("TRENDFORGE_GENERATED_DIR", str(tmp_path / "generated"))
    monkeypatch.setattr(
        "trendforge.app.cli_auth_status",
        lambda: {
            "ok": False,
            "authenticated": False,
            "message": "Cursor Agent CLI is not authenticated. Run agent login once.",
            "hint": "Open PowerShell and run: agent login",
        },
    )
    db_path = tmp_path / "routes.db"
    init_db(db_path)
    SessionLocal = get_session_factory(db_path)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as client:
            home = client.get("/generation")
            assert home.status_code == 200
            assert "Generation" in home.text
            assert "Generate" in home.text
            assert "Comfy Cloud" in home.text
            assert "9:16 video" in home.text
            assert "5s" in home.text
            assert "Start at 5" in home.text
            assert "Review" in home.text
            assert "Brainstorm ideas" in home.text
            missing = client.get("/generation/jobs/9999")
            assert missing.status_code == 404
            alias = client.get("/production/jobs/1", follow_redirects=False)
            assert alias.status_code in (307, 303)
            assert "/generation/jobs/1" in alias.headers.get("location", "")
    finally:
        app.dependency_overrides.clear()
