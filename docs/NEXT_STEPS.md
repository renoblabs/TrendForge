# TrendForge — next steps (after Session 1)

Phase 1 answered: can we turn short-form signals into a ranked list of reusable formats?

## Phase 2 — live loops (suggested order)

1. **OpenRouter / LLM ops**
   - Confirm model quality on real URLs
   - Persist analysis prompts/versioning
   - Human review queue for format_key merges/splits

2. **YouTube Data API adapter**
   - Search + video details with quota budget
   - Cache responses; never scrape as primary path

3. **Postiz distribution**
   - Auth with `POSTIZ_API_KEY`
   - Upload media → create/schedule posts → store `postiz_post_id`
   - Pull analytics into `performance_records`

4. **ComfyUI production mapping**
   - Map `recommended_production_method` → workflow templates
   - Local ComfyUI and/or Comfy Cloud
   - Write `content_assets` with cost + workflow id

5. **Performance learning**
   - Attribute results to FORMAT × CHARACTER × HOOK × PLATFORM
   - Feed back into score weights / status transitions

6. **Optional later**
   - Light embedding assist for format_key suggestions (still human-confirm)
   - CapCut / Instagram only if a legitimate API path appears
   - Multi-user auth only if sharing beyond local research

## Explicit non-goals until intelligence loop proves useful

- SaaS multi-tenancy
- Kubernetes / Kafka / microservices
- Undocumented scraping farms
- Automated copyrighted-performance cloning
