# 🐙 tentacle-apply

A reliability-first, autonomous **job-application co-pilot**. Set your preferences and upload a
resume; it finds matching jobs, tailors your resume + cover letter to each one, applies on its
own, verifies the submission, and tracks everything on a dashboard — until it hits your target.

Part of the **Octopodia 🐙** project family.

> Full design & roadmap: [`docs/DESIGN.md`](docs/DESIGN.md)

## Status
**Partial Phase 1 — usable prototype, not production.** The per-job building blocks work
end-to-end *when run by hand*: intake → sources → match → tailor → apply (Greenhouse Tier-1 only)
→ dedupe → dashboard. What's **not** built yet: the autonomous "keep applying until target" loop
(no scheduler — `Run` rows are never created), any ATS besides Greenhouse, the Tier-2 agentic
fallback, and auth/multi-user. See [`docs/DESIGN.md` §0](docs/DESIGN.md) for an honest status table
and [`docs/ROADMAP.md`](docs/ROADMAP.md) for the prioritized path to production.

## Quick start
```bash
# from the tentacle-apply/ folder
cp .env.example .env        # then add your free API keys (NVIDIA NIM or Gemini)

uv run python -m tentacle_apply.cli init-db            # create DB + data dirs
uv run python -m tentacle_apply.cli ping               # sanity-check the LLM provider
```

## Full pipeline
```bash
# 1) resume -> structured profile
uv run python -m tentacle_apply.cli intake data/samples/sample_resume.pdf --email you@example.com
# 2) pull real jobs (free APIs + Greenhouse/Lever boards)
uv run python -m tentacle_apply.cli sources --query "python backend" --limit 20
# 3) rank jobs against the profile (embeddings + eligibility)
uv run python -m tentacle_apply.cli match --email you@example.com --top 12
# 4) tailor a grounded resume + cover letter for a job (Writer<->Critic loop)
uv run python -m tentacle_apply.cli tailor --email you@example.com --job-id 18
# 5) prepare an application (DRY RUN by default — fills + screenshots, no submit)
uv run python -m tentacle_apply.cli apply --email you@example.com --job-id 21
#    add --submit to actually send (human solves any CAPTCHA in --headful), or --url to target a posting
# 6) live dashboard + JSON API
uv run python -m tentacle_apply.cli serve --port 8001   # open http://127.0.0.1:8001
```

> **Greenhouse reality:** modern Greenhouse boards use React-select dropdowns and reCAPTCHA on
> submit. We auto-fill *everything* (fields, tailored resume, dropdowns, EEO→decline) and honestly
> leave nuanced questions for review; final submit is human-in-the-loop unless a CAPTCHA solver is
> configured. See `docs/DESIGN.md`.

## Principles
- **Free by default**, paid by config (swap a key in `.env` — no code changes).
- **Quality over spray**: only apply when the match + tailored resume clear a bar.
- **Human-in-the-loop master switch**; no ToS-violating LinkedIn server automation (Phase 2, client-side).
