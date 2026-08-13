from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.config import get_settings
from trendforge.db import get_db, init_db
from trendforge.models import ContentCandidate, Format, FormatStatus
from trendforge.production import list_production_methods
from trendforge.services import analyze_candidate, analyze_pending, ingest_text

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

app = FastAPI(title="TrendForge", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    static_dir = PACKAGE_DIR / "static"
    static_dir.mkdir(exist_ok=True)


if (PACKAGE_DIR / "static").exists() or True:
    static_path = PACKAGE_DIR / "static"
    static_path.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


def _nav_context(request: Request) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "has_openrouter": settings.has_openrouter,
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


@app.get("/api/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "has_openrouter": settings.has_openrouter,
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
