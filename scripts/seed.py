from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.analysis.schema import FormatAnalysis, VariationIdea
from trendforge.db import get_session_factory, init_db
from trendforge.models import AnalysisStatus, ContentCandidate, FormatStatus
from trendforge.services import apply_analysis_to_format


SEED_FORMATS: list[dict] = [
    {
        "analysis": FormatAnalysis(
            what_happens="An unexpected performer delivers a familiar performance structure.",
            first_second_hook="Kid/grandma/dog appears where an adult performer is expected.",
            why_stop_scrolling="Role incongruity creates instant curiosity.",
            attention_mechanic="Unexpected character + preserved performance",
            format_name="Unexpected Character + Performance",
            format_key="unexpected-character-performance",
            format_category="performance-transfer",
            format_description=(
                "Preserve a performance scaffold (timing, cadence, staging) while replacing "
                "the expected performer with an incongruous character. Generate original scripts."
            ),
            hook_pattern="Incongruous character mid-performance within 1s",
            why_it_works="Familiar structure + surprise casting = scroll-stop + watch-through.",
            variables=["character", "performance type", "setting", "costume", "original script"],
            estimated_variation_count=50,
            novelty_signal=92.0,
            replicability_signal=96.0,
            saturation_signal=20.0,
            trend_velocity_signal=96.0,
            cross_platform_signal=80.0,
            hook_strength_signal=93.0,
            production_complexity_signal=25.0,
            comfy_feasibility_signal=94.0,
            ip_risk_signal=25.0,
            ip_notes="Do not copy protected comedy/music routines; invent original performances.",
            recommended_production_method="performance_transfer",
            production_notes="Character replacement + motion/performance transfer with original audio/script.",
            estimated_generation_cost_usd=3.5,
            original_variations=[
                VariationIdea(
                    concept="A raccoon CEO delivers an original quarterly earnings rant",
                    character="raccoon in tiny suit",
                    scenario="boardroom",
                    hook="Raccoon bangs gavel, then starts the pitch",
                    production_method="character_replacement",
                ),
                VariationIdea(
                    concept="A medieval knight gives an original motivational cold open",
                    character="knight",
                    scenario="foggy battlefield",
                    hook="Visor lifts mid-sentence",
                    production_method="performance_transfer",
                ),
                VariationIdea(
                    concept="A grandma drops an original tech-support freestyle",
                    character="grandma",
                    scenario="living room",
                    hook="She starts rapping about Wi-Fi passwords",
                    production_method="lip_sync_avatar",
                ),
            ],
        ),
        "candidates": [
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-ucp-001",
                "creator": "@kidcomedy_demo",
                "title": "Kid nails the office rant (demo)",
                "views": 2_400_000,
                "likes": 310_000,
                "comments": 12_400,
                "shares": 48_000,
                "duration": 18,
            },
            {
                "platform": "instagram",
                "url": "https://www.instagram.com/reel/seed-ucp-002",
                "creator": "@nana_beats_demo",
                "title": "Grandma vs the beat (demo)",
                "views": 980_000,
                "likes": 142_000,
                "comments": 6_100,
                "shares": 21_000,
                "duration": 15,
            },
            {
                "platform": "youtube",
                "url": "https://www.youtube.com/shorts/seed-ucp-003",
                "creator": "PetStage Demo",
                "title": "Dog does the dance break (demo)",
                "views": 1_100_000,
                "likes": 88_000,
                "comments": 3_200,
                "shares": 9_400,
                "duration": 12,
            },
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-ucp-004",
                "creator": "@armor_monologue",
                "title": "Knight motivational cold open (demo)",
                "views": 640_000,
                "likes": 71_000,
                "comments": 2_100,
                "shares": 11_000,
                "duration": 22,
            },
        ],
    },
    {
        "analysis": FormatAnalysis(
            what_happens="Ultra-short impossible POV stories with a twist ending.",
            first_second_hook="Camera is already inside an impossible situation.",
            why_stop_scrolling="POV immersion + unanswered question in frame 1.",
            attention_mechanic="Impossible POV micro-story",
            format_name="Impossible POV Micro-Stories",
            format_key="impossible-pov-micro-stories",
            format_category="narrative-pov",
            format_description=(
                "First-person micro-narratives from physically or socially impossible viewpoints, "
                "resolved in under 20 seconds."
            ),
            hook_pattern="Immediate POV caption + impossible visual premise",
            why_it_works="Immersion + curiosity gap + quick payoff.",
            variables=["POV entity", "premise", "twist", "setting", "caption voice"],
            estimated_variation_count=80,
            novelty_signal=90.0,
            replicability_signal=92.0,
            saturation_signal=18.0,
            trend_velocity_signal=93.0,
            cross_platform_signal=85.0,
            hook_strength_signal=92.0,
            production_complexity_signal=28.0,
            comfy_feasibility_signal=90.0,
            ip_risk_signal=15.0,
            ip_notes="Low IP risk if premises and scripts are original.",
            recommended_production_method="image_to_video",
            production_notes="Generate POV stills then I2V; voiceover optional.",
            estimated_generation_cost_usd=2.0,
            original_variations=[
                VariationIdea(
                    concept="POV: you are a lost AirPod under a couch during a breakup",
                    character="airpod",
                    scenario="apartment living room",
                    hook="Dust and muffled arguing",
                    production_method="image_to_video",
                ),
                VariationIdea(
                    concept="POV: you are the last slice in a group chat pizza order",
                    character="pizza slice",
                    scenario="office kitchen",
                    hook="Lid opens. Silence.",
                    production_method="reference_to_video",
                ),
            ],
        ),
        "candidates": [
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-pov-001",
                "creator": "@impossible_pov",
                "title": "POV: traffic cone during rush hour (demo)",
                "views": 3_100_000,
                "likes": 401_000,
                "comments": 18_000,
                "shares": 62_000,
                "duration": 11,
            },
            {
                "platform": "youtube",
                "url": "https://www.youtube.com/shorts/seed-pov-002",
                "creator": "MicroPOV Demo",
                "title": "POV: elevator button that nobody presses (demo)",
                "views": 820_000,
                "likes": 54_000,
                "comments": 1_900,
                "shares": 7_200,
                "duration": 9,
            },
            {
                "platform": "instagram",
                "url": "https://www.instagram.com/reel/seed-pov-003",
                "creator": "@tiny_cameras",
                "title": "POV: fridge light at 2am (demo)",
                "views": 1_450_000,
                "likes": 190_000,
                "comments": 8_800,
                "shares": 33_000,
                "duration": 10,
            },
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-pov-004",
                "creator": "@impossible_pov",
                "title": "POV: unread email in a group thread (demo)",
                "views": 510_000,
                "likes": 66_000,
                "comments": 4_400,
                "shares": 9_100,
                "duration": 14,
            },
        ],
    },
    {
        "analysis": FormatAnalysis(
            what_happens="A recurring absurd character returns across unrelated scenarios.",
            first_second_hook="Recognizable absurd character reappears instantly.",
            why_stop_scrolling="Character recognition + 'what will they do this time?'",
            attention_mechanic="Recurring absurd character serial",
            format_name="Recurring Absurd Character",
            format_key="recurring-absurd-character",
            format_category="character-ip",
            format_description=(
                "Build a distinctive absurd character and drop them into new scenarios "
                "with consistent personality rules."
            ),
            hook_pattern="Character face/costume recognition in first frame",
            why_it_works="Series compounding + anticipation + memeability.",
            variables=["character rules", "scenario", "prop", "catchphrase", "escalation"],
            estimated_variation_count=120,
            novelty_signal=76.0,
            replicability_signal=92.0,
            saturation_signal=28.0,
            trend_velocity_signal=81.0,
            cross_platform_signal=65.0,
            hook_strength_signal=84.0,
            production_complexity_signal=48.0,
            comfy_feasibility_signal=90.0,
            ip_risk_signal=22.0,
            ip_notes="Create original characters; avoid lookalikes of protected IP.",
            recommended_production_method="character_consistency",
            production_notes="Lock identity LoRA/reference pack; generate scenario variations.",
            estimated_generation_cost_usd=2.8,
            original_variations=[
                VariationIdea(
                    concept="The overly polite raccoon returns as a hotel concierge",
                    character="polite raccoon",
                    scenario="boutique hotel lobby",
                    hook="He bows before handing over the keycard",
                    production_method="character_consistency",
                ),
                VariationIdea(
                    concept="Same raccoon as a TSA agent confiscating snacks",
                    character="polite raccoon",
                    scenario="airport security",
                    hook="Glove snap, then 'sir, the grapes'",
                    production_method="character_consistency",
                ),
            ],
        ),
        "candidates": [
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-rac-001",
                "creator": "@absurd_cast",
                "title": "Polite raccoon barista (demo)",
                "views": 780_000,
                "likes": 99_000,
                "comments": 5_500,
                "shares": 14_000,
                "duration": 13,
            },
            {
                "platform": "tiktok",
                "url": "https://www.tiktok.com/@demo/video/seed-rac-002",
                "creator": "@absurd_cast",
                "title": "Polite raccoon dentist (demo)",
                "views": 920_000,
                "likes": 120_000,
                "comments": 7_100,
                "shares": 18_500,
                "duration": 16,
            },
            {
                "platform": "youtube",
                "url": "https://www.youtube.com/shorts/seed-rac-003",
                "creator": "Absurd Cast Demo",
                "title": "Polite raccoon Uber driver (demo)",
                "views": 430_000,
                "likes": 31_000,
                "comments": 1_200,
                "shares": 4_800,
                "duration": 19,
            },
            {
                "platform": "instagram",
                "url": "https://www.instagram.com/reel/seed-rac-004",
                "creator": "@absurd_cast",
                "title": "Polite raccoon gym trainer (demo)",
                "views": 610_000,
                "likes": 84_000,
                "comments": 3_900,
                "shares": 12_200,
                "duration": 12,
            },
        ],
    },
]


def seed(db_path: Path | None = None, reset: bool = False) -> None:
    from trendforge.config import get_settings
    from trendforge.db import get_engine
    from trendforge.models import Base

    path = db_path or get_settings().db_path
    if reset and path.exists():
        path.unlink()

    init_db(path)
    SessionLocal = get_session_factory(path)
    db = SessionLocal()
    try:
        existing = db.query(ContentCandidate).count()
        if existing and not reset:
            print(f"DB already has {existing} candidates; skipping seed (use --reset).")
            return

        if reset:
            engine = get_engine(path)
            Base.metadata.drop_all(bind=engine)
            Base.metadata.create_all(bind=engine)

        for block in SEED_FORMATS:
            analysis: FormatAnalysis = block["analysis"]
            fmt = apply_analysis_to_format(db, analysis)
            db.flush()
            for c in block["candidates"]:
                row = ContentCandidate(
                    platform=c["platform"],
                    url=c["url"],
                    creator=c.get("creator"),
                    title=c.get("title"),
                    views=c.get("views"),
                    likes=c.get("likes"),
                    comments=c.get("comments"),
                    shares=c.get("shares"),
                    duration=c.get("duration"),
                    analysis_status=AnalysisStatus.ANALYZED,
                    analysis_json=analysis.model_dump(),
                    format_id=fmt.id,
                )
                db.add(row)
            # Ensure BUILD/WATCH from scoring
            assert fmt.status in {FormatStatus.BUILD, FormatStatus.WATCH, FormatStatus.DISCOVERED}
        db.commit()
        print("Seed complete: 3 formats, 12 candidates.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TrendForge demo data")
    parser.add_argument("--reset", action="store_true", help="Wipe DB and reseed")
    parser.add_argument("--db", type=Path, default=None, help="Optional DB path")
    args = parser.parse_args()
    seed(db_path=args.db, reset=args.reset)


if __name__ == "__main__":
    main()
