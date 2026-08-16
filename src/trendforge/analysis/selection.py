from __future__ import annotations

from typing import Any

from trendforge.analysis.prompt import PROMPT_VERSION
from trendforge.models import AnalysisStatus, ContentCandidate


def analysis_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("analysis") or {})


def is_high_signal(candidate: ContentCandidate, cfg: dict[str, Any] | None = None) -> bool:
    """Quantitative gate. LLM never decides this."""
    conf = analysis_config(cfg)
    labels = {str(x).upper() for x in (candidate.discovery_labels or [])}
    if conf.get("promote_accelerating", True) and "ACCELERATING" in labels:
        return True
    if conf.get("promote_emerging", True) and "EMERGING" in labels:
        min_score = conf.get("emerging_min_discovery_score", 20)
        score = candidate.discovery_score
        if min_score is None:
            return True
        return score is not None and float(score) >= float(min_score)
    return False


def _analysis_payload(candidate: ContentCandidate) -> dict[str, Any]:
    data = candidate.analysis_json
    return data if isinstance(data, dict) else {}


def is_stub_analysis(candidate: ContentCandidate) -> bool:
    data = _analysis_payload(candidate)
    analyzer = str(data.get("analyzer") or "")
    model = str(data.get("model") or "").lower()
    return analyzer == "StubAnalyzer" or model == "stub"


def recorded_prompt_version(candidate: ContentCandidate) -> str | None:
    raw = _analysis_payload(candidate).get("prompt_version")
    if raw is None or raw == "":
        return None
    return str(raw)


def needs_live_reanalysis(
    candidate: ContentCandidate,
    *,
    prompt_version: str | None = None,
) -> bool:
    """True when a live LLM pass should run. Existing ANALYZED rows are eligible if stub or stale prompt."""
    want = prompt_version or PROMPT_VERSION
    status = candidate.analysis_status
    if status in {
        AnalysisStatus.PENDING,
        AnalysisStatus.FAILED,
        AnalysisStatus.SKIPPED,
        AnalysisStatus.ANALYZING,
    }:
        return True
    if status != AnalysisStatus.ANALYZED:
        return True
    if is_stub_analysis(candidate):
        return True
    return recorded_prompt_version(candidate) != want


def rank_for_analysis(rows: list[ContentCandidate]) -> list[ContentCandidate]:
    """Existing discovery ranking: score desc, then id asc. Not a new ranker."""
    return sorted(
        rows,
        key=lambda row: (
            -(float(row.discovery_score) if row.discovery_score is not None else -1.0),
            int(row.id or 0),
        ),
    )
