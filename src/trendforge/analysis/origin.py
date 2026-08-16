from __future__ import annotations

from trendforge.models import ContentCandidate

SEED_ORIGIN = "seed"
LIVE_ORIGIN = "live"


def is_seed_record(candidate: ContentCandidate) -> bool:
    origin = str(getattr(candidate, "data_origin", None) or "").strip().lower()
    if origin == SEED_ORIGIN:
        return True
    url = (candidate.url or "").lower()
    if "seed-" in url or "/@demo/" in url:
        return True
    title = (candidate.title or "").lower()
    if "(demo)" in title:
        return True
    return False
