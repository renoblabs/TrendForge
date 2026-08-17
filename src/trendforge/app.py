from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from trendforge.acquisition.errors import AcquisitionError, MissingTokenError
from trendforge.acquisition.sampling import compare_profile_rows
from trendforge.acquisition.service import run_acquisition
from trendforge.analysis.client import MissingAPIKeyError
from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.config import get_settings, load_data_sources_config, load_generation_config
from trendforge.db import get_db, get_session_factory, init_db
from trendforge.discovery.gather import run_gathering_jobs
from trendforge.discovery.pipeline import promote_top_candidates
from trendforge.discovery.provider import DiscoveryError
from trendforge.discovery.schedule import gathering_status
from trendforge.ideation.generate import (
    UnknownFamilyError,
    UnknownBrainstormIdeaError,
    generate_brainstorm_set,
    list_idea_review,
    list_ideation_families,
    load_brainstorm_sets,
    set_idea_pool_status,
)
from trendforge.ideation.prompt import ALLOWED_COUNTS, DEFAULT_COUNT, EMPHASIS_PRESETS
from trendforge.generation.runner import agent_log_tail, cli_auth_status, recover_generation_jobs
from trendforge.generation.service import (
    GenerationError,
    create_generation_job,
    list_cloud_models,
    list_generation_options,
)
from trendforge.models import (
    AcquisitionRun,
    AnalysisStatus,
    ContentAsset,
    ContentCandidate,
    DiscoveryRun,
    Format,
    FormatBrainstormSet,
    FormatStatus,
    GenerationJob,
    ProductionSpec,
    utcnow,
)
from trendforge.production import list_production_methods
from trendforge.production.spec_export import spec_to_markdown
from trendforge.production.spec_generate import (
    UnknownIdeaError,
    generate_production_spec,
    specs_by_brainstorm,
)
from trendforge.production.spec_prompt import DEFAULT_DURATION_SECONDS
from trendforge.services import analyze_candidate, analyze_pending, ingest_text

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

app = FastAPI(title="TrendForge", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    static_dir = PACKAGE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    db = get_session_factory()()
    try:
        recover_generation_jobs(db)
    finally:
        db.close()


if (PACKAGE_DIR / "static").exists() or True:
    static_path = PACKAGE_DIR / "static"
    static_path.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


def _nav_context(request: Request) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "has_openrouter": settings.has_openrouter,
        "has_youtube": settings.has_youtube,
        "has_apify": settings.has_apify,
        "app_name": "TrendForge",
    }


@app.get("/", response_class=HTMLResponse)
def opportunity_queue(request: Request, db: Session = Depends(get_db)):
    formats = (
        db.query(Format)
        .order_by(Format.overall_score.desc(), Format.id.asc())
        .all()
    )
    return templates.TemplateResponse(
        "queue.html",
        {**_nav_context(request), "formats": formats, "page": "queue"},
    )


@app.get("/candidates", response_class=HTMLResponse)
def candidate_feed(request: Request, db: Session = Depends(get_db)):
    candidates = (
        db.query(ContentCandidate)
        .options(joinedload(ContentCandidate.format))
        .order_by(ContentCandidate.discovered_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "candidates.html",
        {**_nav_context(request), "candidates": candidates, "page": "candidates"},
    )


@app.get("/formats", response_class=HTMLResponse)
def format_explorer(request: Request, db: Session = Depends(get_db)):
    formats = (
        db.query(Format)
        .order_by(Format.overall_score.desc(), Format.name.asc())
        .all()
    )
    return templates.TemplateResponse(
        "formats.html",
        {**_nav_context(request), "formats": formats, "page": "formats"},
    )


@app.get("/formats/{format_id}", response_class=HTMLResponse)
def format_detail(format_id: int, request: Request, db: Session = Depends(get_db)):
    fmt = (
        db.query(Format)
        .options(joinedload(Format.candidates), joinedload(Format.variations))
        .filter(Format.id == format_id)
        .one_or_none()
    )
    if not fmt:
        raise HTTPException(status_code=404, detail="Format not found")
    return templates.TemplateResponse(
        "format_detail.html",
        {
            **_nav_context(request),
            "fmt": fmt,
            "page": "formats",
            "production_methods": list_production_methods(),
        },
    )


@app.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(
        "ingest.html",
        {
            **_nav_context(request),
            "page": "ingest",
            "message": message,
            "error": error,
        },
    )


@app.post("/ingest")
def ingest_submit(
    text: str = Form(...),
    fetch_oembed: bool = Form(False),
    db: Session = Depends(get_db),
):
    created = ingest_text(db, text, fetch_oembed=fetch_oembed)
    msg = f"Ingested {len(created)} new candidate(s)."
    return RedirectResponse(url=f"/ingest?message={msg}", status_code=303)


@app.post("/analyze")
def analyze_all(
    force_stub: bool = Form(False),
    require_live: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        results = analyze_pending(
            db, force_stub=force_stub, require_live=require_live and not force_stub
        )
        msg = f"Analyzed {len(results)} candidate(s)."
        return RedirectResponse(url=f"/ingest?message={msg}", status_code=303)
    except MissingAPIKeyError as exc:
        return RedirectResponse(url=f"/ingest?error={exc}", status_code=303)


@app.post("/candidates/{candidate_id}/analyze")
def analyze_one(
    candidate_id: int,
    force_stub: bool = Form(False),
    db: Session = Depends(get_db),
):
    row = db.get(ContentCandidate, candidate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        analyze_candidate(db, row, force_stub=force_stub)
        return RedirectResponse(url="/candidates", status_code=303)
    except MissingAPIKeyError as exc:
        return RedirectResponse(url=f"/ingest?error={exc}", status_code=303)


@app.post("/formats/{format_id}/status")
def set_format_status(
    format_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    fmt = db.get(Format, format_id)
    if not fmt:
        raise HTTPException(status_code=404, detail="Format not found")
    try:
        fmt.status = FormatStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/formats/{format_id}", status_code=303)


def _platform_candidates(db: Session, platform: str):
    return (
        db.query(ContentCandidate)
        .filter(ContentCandidate.platform == platform)
        .order_by(ContentCandidate.discovery_score.desc(), ContentCandidate.id.asc())
    )


def _labeled_groups(rows):
    def has_label(row: ContentCandidate, label: str) -> bool:
        return label in (row.discovery_labels or [])

    return {
        "emerging": [r for r in rows if has_label(r, "EMERGING")][:25],
        "accelerating": [r for r in rows if has_label(r, "ACCELERATING")][:25],
        "popular": [r for r in rows if has_label(r, "POPULAR")][:15],
        "promoted": [r for r in rows if r.promoted_at is not None][:25],
    }


@app.get("/discovery", response_class=HTMLResponse)
def discovery_home(
    request: Request,
    db: Session = Depends(get_db),
    message: str | None = None,
    error: str | None = None,
):
    yt_rows = _platform_candidates(db, "youtube").all()
    tt_rows = _platform_candidates(db, "tiktok").all()
    ig_rows = _platform_candidates(db, "instagram").all()
    youtube = _labeled_groups(yt_rows)
    tiktok = _labeled_groups(tt_rows)
    instagram = _labeled_groups(ig_rows)
    runs = (
        db.query(DiscoveryRun)
        .order_by(DiscoveryRun.started_at.desc())
        .limit(12)
        .all()
    )
    return templates.TemplateResponse(
        "discovery.html",
        {
            **_nav_context(request),
            "page": "discovery",
            "emerging": youtube["emerging"],
            "accelerating": youtube["accelerating"],
            "popular": youtube["popular"],
            "promoted": youtube["promoted"],
            "tiktok_emerging": tiktok["emerging"],
            "tiktok_accelerating": tiktok["accelerating"],
            "tiktok_popular": tiktok["popular"],
            "tiktok_promoted": tiktok["promoted"],
            "instagram_emerging": instagram["emerging"],
            "instagram_accelerating": instagram["accelerating"],
            "instagram_popular": instagram["popular"],
            "instagram_promoted": instagram["promoted"],
            "runs": runs,
            "total": len(yt_rows),
            "tiktok_total": len(tt_rows),
            "instagram_total": len(ig_rows),
            "gathering": gathering_status(db),
            "message": message,
            "error": error,
        },
    )


@app.post("/discovery/gather")
def discovery_gather(
    job: str = Form("both"),
    db: Session = Depends(get_db),
):
    mapping = {
        "discover": ["discover"],
        "observe": ["observe"],
        "both": ["discover", "observe"],
        "discover_tiktok": ["discover_tiktok"],
        "observe_tiktok": ["observe_tiktok"],
        "tiktok": ["discover_tiktok", "observe_tiktok"],
        "discover_instagram": ["discover_instagram"],
        "observe_instagram": ["observe_instagram"],
        "instagram": ["discover_instagram", "observe_instagram"],
    }
    selected = mapping.get(job)
    if not selected:
        return RedirectResponse(url="/discovery?error=unknown+gather+job", status_code=303)
    try:
        ran = run_gathering_jobs(db, jobs=selected, force=True)
    except DiscoveryError as exc:
        return RedirectResponse(url=f"/discovery?error={exc}", status_code=303)
    names = "+".join(name for name, _ in ran) or "none"
    return RedirectResponse(url=f"/discovery?message=ran+{names}", status_code=303)


def _source_actor(cfg: dict, source: str) -> str | None:
    sources = cfg.get("sources") if isinstance(cfg.get("sources"), dict) else {}
    source_cfg = sources.get(source) if isinstance(sources.get(source), dict) else {}
    actor_key = source_cfg.get("actor_key")
    actors = (cfg.get("apify") or {}).get("actors") if isinstance(cfg.get("apify"), dict) else {}
    actor_id = actors.get(actor_key) if actor_key else None
    return str(actor_id) if actor_id else None


@app.get("/acquisition", response_class=HTMLResponse)
def acquisition_home(
    request: Request,
    db: Session = Depends(get_db),
    message: str | None = None,
    error: str | None = None,
):
    cfg = load_data_sources_config()
    sources = cfg.get("sources") if isinstance(cfg.get("sources"), dict) else {}
    tiktok_cfg = sources.get("tiktok") if isinstance(sources.get("tiktok"), dict) else {}
    ig_cfg = sources.get("instagram") if isinstance(sources.get("instagram"), dict) else {}
    experiments = cfg.get("experiments") if isinstance(cfg.get("experiments"), dict) else {}
    emerging = experiments.get("tiktok_emerging_breakout") if isinstance(experiments, dict) else {}
    emerging_actor = None
    actors = (cfg.get("apify") or {}).get("actors") if isinstance(cfg.get("apify"), dict) else {}
    if isinstance(emerging, dict) and emerging.get("actor_key"):
        emerging_actor = actors.get(emerging.get("actor_key"))
    profiles_cfg = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    profile_actors = {}
    for name, row in profiles_cfg.items():
        if not isinstance(row, dict):
            continue
        key = row.get("actor_key")
        actor = actors.get(key) if key else None
        if isinstance(actor, dict):
            actor = actor.get("actor_id")
        profile_actors[name] = actor
    runs = (
        db.query(AcquisitionRun)
        .order_by(AcquisitionRun.started_at.desc())
        .limit(50)
        .all()
    )
    comparison = compare_profile_rows(runs)
    return templates.TemplateResponse(
        "acquisition.html",
        {
            **_nav_context(request),
            "page": "acquisition",
            "runs": runs[:25],
            "tiktok_actor": _source_actor(cfg, "tiktok"),
            "instagram_actor": _source_actor(cfg, "instagram"),
            "tiktok_enabled": bool(tiktok_cfg.get("enabled", True)),
            "instagram_enabled": bool(ig_cfg.get("enabled", True)),
            "emerging_enabled": bool((emerging or {}).get("enabled")),
            "emerging_actor": emerging_actor,
            "emerging_queries": (emerging or {}).get("first_run_queries") or (emerging or {}).get("queries") or [],
            "emerging_window": (emerging or {}).get("window"),
            "emerging_regions": (emerging or {}).get("regions") or [],
            "profiles": profiles_cfg,
            "profile_actors": profile_actors,
            "comparison": comparison,
            "trending_actor": profile_actors.get("tiktok_trending"),
            "fresh_actor": profile_actors.get("tiktok_fresh_search"),
            "creator_reels_actor": profile_actors.get("instagram_creator_reels"),
            "creator_list": (profiles_cfg.get("instagram_creator_reels") or {}).get("creators") or [],
            "fresh_queries": (profiles_cfg.get("tiktok_fresh_search") or {}).get("queries") or [],
            "message": message,
            "error": error,
        },
    )


@app.post("/acquisition/run")
def acquisition_run_create(
    source: str = Form(...),
    limit: int = Form(25),
    mode: str = Form("default"),
    profile: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        run = run_acquisition(
            db,
            source,
            limit=limit,
            mode=mode,
            profile=profile.strip() or None,
        )
    except MissingTokenError as exc:
        return RedirectResponse(url=f"/acquisition?error={quote(str(exc))}", status_code=303)
    except AcquisitionError as exc:
        return RedirectResponse(url=f"/acquisition?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url=f"/acquisition/runs/{run.id}", status_code=303)


@app.get("/acquisition/runs/{run_id}", response_class=HTMLResponse)
def acquisition_run_detail(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(AcquisitionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Acquisition run not found")
    candidates = (
        db.query(ContentCandidate)
        .filter(ContentCandidate.acquisition_run_id == run.id)
        .order_by(ContentCandidate.views.desc(), ContentCandidate.id.asc())
        .limit(40)
        .all()
    )
    return templates.TemplateResponse(
        "acquisition_run.html",
        {
            **_nav_context(request),
            "page": "acquisition",
            "run": run,
            "candidates": candidates,
        },
    )


@app.get("/discovery/opportunities", response_class=HTMLResponse)
def discovery_opportunities(
    request: Request,
    sort: str = "score",
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    order = "recent" if sort == "recent" else "score"
    clusters = aggregate_format_opportunities(db, sort=order)
    brainstorms = load_brainstorm_sets(db)
    spec_rows = specs_by_brainstorm(db)
    spec_index = {
        f"{set_id}:{idea_id}": rows for (set_id, idea_id), rows in spec_rows.items()
    }
    return templates.TemplateResponse(
        "discovery_opportunities.html",
        {
            **_nav_context(request),
            "page": "opportunities",
            "clusters": clusters,
            "sort": order,
            "brainstorms": brainstorms,
            "spec_index": spec_index,
            "ideation_counts": ALLOWED_COUNTS,
            "ideation_default_count": DEFAULT_COUNT,
            "ideation_presets": EMPHASIS_PRESETS,
            "ideation_message": message,
            "ideation_error": error,
            "default_spec_duration": DEFAULT_DURATION_SECONDS,
        },
    )


@app.post("/discovery/opportunities/{family_key}/brainstorm")
def discovery_brainstorm(
    family_key: str,
    count: int = Form(DEFAULT_COUNT),
    emphasis: str = Form(""),
    sort: str = Form("score"),
    force_stub: bool = Form(False),
    db: Session = Depends(get_db),
):
    order = "recent" if sort == "recent" else "score"
    try:
        generate_brainstorm_set(
            db,
            family_key,
            count=count,
            emphasis=emphasis,
            force_stub=force_stub,
        )
    except UnknownFamilyError:
        raise HTTPException(status_code=404, detail="Format family not found")
    except MissingAPIKeyError as exc:
        return RedirectResponse(
            url=f"/discovery/opportunities?sort={order}&error={exc}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/discovery/opportunities?sort={order}&error={exc}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/discovery/opportunities?sort={order}&message=ideas#family-{family_key}",
        status_code=303,
    )


@app.post("/discovery/opportunities/brainstorm/{set_id}/ideas/{idea_index}/spec")
def create_production_spec(
    set_id: int,
    idea_index: int,
    sort: str = Form("score"),
    duration_seconds: float = Form(DEFAULT_DURATION_SECONDS),
    force_stub: bool = Form(False),
    db: Session = Depends(get_db),
):
    order = "recent" if sort == "recent" else "score"
    try:
        record = generate_production_spec(
            db,
            brainstorm_set_id=set_id,
            idea_index=idea_index,
            duration_seconds=duration_seconds,
            force_stub=force_stub,
        )
    except UnknownIdeaError:
        raise HTTPException(status_code=404, detail="Brainstorm idea not found")
    except MissingAPIKeyError as exc:
        return RedirectResponse(
            url=f"/discovery/opportunities?sort={order}&error={exc}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/discovery/opportunities?sort={order}&error={exc}",
            status_code=303,
        )
    return RedirectResponse(url=f"/production/specs/{record.id}", status_code=303)


@app.get("/production/specs/{spec_id}", response_class=HTMLResponse)
def production_spec_detail(
    spec_id: int, request: Request, db: Session = Depends(get_db)
):
    row = db.get(ProductionSpec, spec_id)
    if not row:
        raise HTTPException(status_code=404, detail="Production spec not found")
    idea = {}
    if row.source_brainstorm_set_id:
        bset = db.get(FormatBrainstormSet, row.source_brainstorm_set_id)
        ideas = (bset.ideas_json if bset and isinstance(bset.ideas_json, list) else [])
        try:
            idx = int(row.source_idea_identifier)
            if 0 <= idx < len(ideas) and isinstance(ideas[idx], dict):
                idea = ideas[idx]
        except (TypeError, ValueError):
            idea = {}
    spec = row.spec_json or {}
    return templates.TemplateResponse(
        "production_spec.html",
        {
            **_nav_context(request),
            "page": "opportunities",
            "row": row,
            "spec": spec,
            "idea": idea,
            "export_text": spec_to_markdown(spec),
        },
    )


@app.get("/production/specs/{spec_id}/export.md")
def production_spec_export(spec_id: int, db: Session = Depends(get_db)):
    row = db.get(ProductionSpec, spec_id)
    if not row:
        raise HTTPException(status_code=404, detail="Production spec not found")
    text = spec_to_markdown(row.spec_json or {})
    headers = {
        "Content-Disposition": f'attachment; filename="production_spec_{spec_id}.md"'
    }
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/generation", response_class=HTMLResponse)
def generation_home(
    request: Request,
    error: str | None = None,
    message: str | None = None,
    db: Session = Depends(get_db),
):
    recover_generation_jobs(db)
    jobs = (
        db.query(GenerationJob)
        .order_by(GenerationJob.created_at.desc())
        .limit(40)
        .all()
    )
    assets = {
        asset.generation_job_id: asset
        for asset in db.query(ContentAsset).filter(ContentAsset.generation_job_id.isnot(None)).all()
        if asset.generation_job_id is not None
    }
    review = list_idea_review(db)
    gen_cfg = load_generation_config()
    return templates.TemplateResponse(
        "generation.html",
        {
            **_nav_context(request),
            "page": "generation",
            "options": list_generation_options(db),
            "cloud_models": list_cloud_models(),
            "cli_auth": cli_auth_status(),
            "jobs": jobs,
            "assets": assets,
            "error": error,
            "message": message,
            "pending_ideas": review["pending"],
            "approved_ideas": review["approved"],
            "denied_ideas": review["denied"],
            "ideation_families": list_ideation_families(db),
            "ideation_counts": ALLOWED_COUNTS,
            "ideation_default_count": DEFAULT_COUNT,
            "ideation_presets": EMPHASIS_PRESETS,
            "allowed_durations": [int(x) for x in gen_cfg.get("allowed_durations") or [5, 8, 15, 20, 30]],
            "default_duration": int(gen_cfg.get("default_duration_seconds") or 5),
        },
    )


@app.post("/generation")
def generation_submit(
    idea_ref: str = Form(...),
    duration_seconds: int = Form(5),
    variant_count: int = Form(1),
    cloud_model: str = Form(""),
    style: str = Form(""),
    voice: str = Form(""),
    audio_preferences: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    try:
        family, set_id_s, idx_s = idea_ref.split("|", 2)
        set_id = int(set_id_s)
        idea_index = int(idx_s)
    except (ValueError, AttributeError):
        return RedirectResponse(url="/generation?error=Invalid+idea+selection", status_code=303)
    try:
        job = create_generation_job(
            db,
            format_family=family,
            brainstorm_set_id=set_id,
            idea_index=idea_index,
            duration_seconds=duration_seconds,
            variant_count=variant_count,
            cloud_model=cloud_model or None,
            style=style,
            voice=voice,
            audio_preferences=audio_preferences,
            force_stub_spec=not settings.has_openrouter,
        )
    except GenerationError as exc:
        return RedirectResponse(url=f"/generation?error={exc}", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/generation?error={exc}", status_code=303)
    return RedirectResponse(url=f"/generation/jobs/{job.id}", status_code=303)


@app.post("/generation/ideas/brainstorm")
def generation_brainstorm(
    family_key: str = Form(...),
    count: int = Form(DEFAULT_COUNT),
    emphasis: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    try:
        generate_brainstorm_set(
            db,
            family_key,
            count=count,
            emphasis=emphasis,
            force_stub=not settings.has_openrouter,
        )
    except UnknownFamilyError:
        return RedirectResponse(url="/generation?error=Format+family+not+found", status_code=303)
    except MissingAPIKeyError as exc:
        return RedirectResponse(url=f"/generation?error={exc}", status_code=303)
    except ValueError as exc:
        return RedirectResponse(url=f"/generation?error={exc}", status_code=303)
    return RedirectResponse(url="/generation?message=ideas#review", status_code=303)


@app.post("/generation/ideas/{set_id}/{idea_index}/{action}")
def generation_idea_review(
    set_id: int,
    idea_index: int,
    action: str,
    next: str = Form("/generation?message=reviewed#review"),
    db: Session = Depends(get_db),
):
    status = {"approve": "approved", "deny": "denied", "pending": "pending"}.get(action)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown review action")
    try:
        set_idea_pool_status(db, set_id, idea_index, status)
    except UnknownBrainstormIdeaError:
        raise HTTPException(status_code=404, detail="Idea not found")
    except ValueError as exc:
        return RedirectResponse(url=f"/generation?error={exc}", status_code=303)
    target = next if str(next).startswith("/") else "/generation?message=reviewed#review"
    return RedirectResponse(url=target, status_code=303)


@app.get("/generation/jobs/{job_id}", response_class=HTMLResponse)
def generation_job_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    recover_generation_jobs(db)
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    spec = db.get(ProductionSpec, job.production_spec_id) if job.production_spec_id else None
    idea = {}
    if job.source_brainstorm_set_id:
        bset = db.get(FormatBrainstormSet, job.source_brainstorm_set_id)
        ideas = bset.ideas_json if bset and isinstance(bset.ideas_json, list) else []
        try:
            idx = int(job.source_idea_identifier)
            if 0 <= idx < len(ideas) and isinstance(ideas[idx], dict):
                idea = ideas[idx]
        except (TypeError, ValueError):
            idea = {}
    assets = (
        db.query(ContentAsset).filter(ContentAsset.generation_job_id == job.id).all()
    )
    events = list((job.job_metadata_json or {}).get("events") or [])
    meta = job.job_metadata_json or {}
    return templates.TemplateResponse(
        "generation_job.html",
        {
            **_nav_context(request),
            "page": "generation",
            "job": job,
            "spec": spec,
            "idea": idea,
            "assets": assets,
            "events": events,
            "workflow": meta.get("workflow") or {},
            "agent_log": agent_log_tail(job.id),
            "meta": meta,
        },
    )


@app.get("/generation/assets/{asset_id}/file")
def generation_asset_file(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(ContentAsset, asset_id)
    if not asset or not asset.file_reference:
        raise HTTPException(status_code=404, detail="Asset not found")
    from trendforge.generation.adapters import generation_output_dir

    path = Path(asset.file_reference)
    root = generation_output_dir().resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except Exception:
        raise HTTPException(status_code=404, detail="Asset path not allowed")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Asset file missing")
    return FileResponse(resolved, media_type=asset.mime_type or "application/octet-stream")


@app.get("/production/jobs/{job_id}")
def production_job_alias(job_id: int):
    return RedirectResponse(url=f"/generation/jobs/{job_id}", status_code=307)


@app.post("/discovery/analyze-high-signal")
def discovery_analyze_high_signal(
    force_stub: bool = Form(True),
    db: Session = Depends(get_db),
):
    promote_top_candidates(db, force_stub_analysis=force_stub, analyze=True)
    return RedirectResponse(url="/discovery/opportunities", status_code=303)


@app.get("/discovery/candidates/{candidate_id}", response_class=HTMLResponse)
def discovery_candidate(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    row = (
        db.query(ContentCandidate)
        .options(joinedload(ContentCandidate.format), joinedload(ContentCandidate.observations))
        .filter(ContentCandidate.id == candidate_id)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    observations = list(row.observations or [])
    chart = _observation_chart(observations)
    return templates.TemplateResponse(
        "discovery_candidate.html",
        {
            **_nav_context(request),
            "page": "discovery",
            "c": row,
            "observations": observations,
            "chart": chart,
            "analysis": row.analysis_json or {},
        },
    )


@app.post("/discovery/candidates/{candidate_id}/promote")
def discovery_promote_one(
    candidate_id: int,
    force_stub: bool = Form(True),
    db: Session = Depends(get_db),
):
    row = db.get(ContentCandidate, candidate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if row.promoted_at is None:
        row.promoted_at = utcnow()
    if row.analysis_status == AnalysisStatus.SKIPPED:
        row.analysis_status = AnalysisStatus.PENDING
    db.commit()
    try:
        analyze_candidate(db, row, force_stub=force_stub)
    except MissingAPIKeyError as exc:
        return RedirectResponse(url=f"/ingest?error={exc}", status_code=303)
    except Exception:
        pass
    return RedirectResponse(url=f"/discovery/candidates/{candidate_id}", status_code=303)


def _observation_chart(observations) -> dict:
    points = [
        {"t": o.observed_at, "v": o.view_count}
        for o in observations
        if o.view_count is not None and o.observed_at is not None
    ]
    if not points:
        return {"polylines": "", "dots": [], "labels": [], "max_v": 0}
    max_v = max(p["v"] for p in points) or 1
    times = [p["t"] for p in points]
    t0, t1 = min(times), max(times)
    span = max((t1 - t0).total_seconds(), 1.0)
    width, height, pad = 640, 180, 28
    coords = []
    for p in points:
        x = pad + ((p["t"] - t0).total_seconds() / span) * (width - 2 * pad)
        y = height - pad - (p["v"] / max_v) * (height - 2 * pad)
        coords.append((round(x, 1), round(y, 1), p["v"], p["t"]))
    polyline = " ".join(f"{x},{y}" for x, y, _, _ in coords)
    return {
        "width": width,
        "height": height,
        "pad": pad,
        "polyline": polyline,
        "dots": coords,
        "max_v": max_v,
        "t0": t0,
        "t1": t1,
    }


@app.get("/api/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "has_openrouter": settings.has_openrouter,
        "has_youtube": settings.has_youtube,
        "has_apify": settings.has_apify,
        "db": str(settings.db_path),
    }


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "trendforge.app:app",
        host=settings.trendforge_host,
        port=settings.trendforge_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
