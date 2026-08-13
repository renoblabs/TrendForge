ANALYSIS_SYSTEM_PROMPT = """You are a short-form content format analyst for TrendForge.

Your job is to extract the REUSABLE FORMAT behind a video candidate — not to copy the video.

Critical distinction:
- Content instance: a specific video (e.g. a kid performing a comedian's routine)
- Format: the underlying mechanic (e.g. unexpected character + preserved performance)

Rules:
1. Identify the attention mechanic and reusable structure.
2. Suggest ORIGINAL variations that change character/scenario/script — never recommend reposting or close copies of copyrighted performances.
3. Flag IP/copyright risks honestly.
4. Score signals are 0-100 HINTS for a separate deterministic scoring engine. Do NOT decide commercial value, BUILD/WATCH status, or overall_score.
5. format_key must be a stable kebab-case slug for clustering related videos into one format family.
6. Respond with JSON only matching the required schema.
"""


def build_analysis_user_prompt(candidate: dict) -> str:
    lines = [
        "Analyze this short-form content candidate and extract the reusable format.",
        "",
        f"Platform: {candidate.get('platform') or 'unknown'}",
        f"URL: {candidate.get('url')}",
        f"Creator: {candidate.get('creator') or 'unknown'}",
        f"Title: {candidate.get('title') or 'unknown'}",
        f"Description: {candidate.get('description') or 'none'}",
        f"Views: {candidate.get('views')}",
        f"Likes: {candidate.get('likes')}",
        f"Comments: {candidate.get('comments')}",
        f"Shares: {candidate.get('shares')}",
        f"Duration: {candidate.get('duration')}",
        "",
        "Return JSON with these fields:",
        "what_happens, first_second_hook, why_stop_scrolling, attention_mechanic,",
        "format_name, format_key, format_category, format_description, hook_pattern, why_it_works,",
        "variables (array of strings), estimated_variation_count,",
        "novelty_signal, replicability_signal, saturation_signal, trend_velocity_signal,",
        "cross_platform_signal, hook_strength_signal, production_complexity_signal,",
        "comfy_feasibility_signal, ip_risk_signal, ip_notes,",
        "recommended_production_method, production_notes, estimated_generation_cost_usd,",
        "original_variations (array of {concept, character, scenario, hook, production_method, notes}).",
        "",
        "Do NOT include overall_score or status.",
    ]
    return "\n".join(lines)
