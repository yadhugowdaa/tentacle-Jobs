"""Tiny CLI for tentacle-apply scaffolding tasks.

    uv run python -m tentacle_apply.cli init-db                       # create DB + data dirs
    uv run python -m tentacle_apply.cli ping                          # verify the LLM provider works
    uv run python -m tentacle_apply.cli intake <resume> [--email ..]  # resume -> structured profile
    uv run python -m tentacle_apply.cli sources [--query ..] [--location ..] [--limit N]  # fetch jobs
    uv run python -m tentacle_apply.cli preferences [--email ..] [--work-modes ..] [--locations ..] [--roles ..] [--skills ..]
    uv run python -m tentacle_apply.cli companies [add <name|careers-url> | list | seed]  # add resolves ANY careers URL → ATS (Tier-0 detect)
    uv run python -m tentacle_apply.cli discover [--email ..] [--limit N]           # preferences -> fresh ranked jobs
    uv run python -m tentacle_apply.cli match [--email ..] [--top N] [--min SCORE]  # rank jobs vs profile
    uv run python -m tentacle_apply.cli tailor [--email ..] [--job-id N] [--target T] [--max-iters K]
    uv run python -m tentacle_apply.cli apply [--email ..] [--job-id N] [--url URL] [--ats greenhouse|lever|ashby|workable|smartrecruiters|workday] [--submit|--hitl] [--headful]
        #   (no flag) dry run · --submit auto-submit (skips CAPTCHA) · --hitl auto-fill + you solve any CAPTCHA
    uv run python -m tentacle_apply.cli run [--email ..] [--target N] [--mode prepare|submit|hitl] [--min-score S] [--no-discover] [--headful]
        #   autonomous loop: discover -> rank -> quality-gate -> tailor -> apply -> record, until target. Resumable.
    uv run python -m tentacle_apply.cli serve [--host ..] [--port N]  # dashboard + JSON API
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

from tentacle_apply.config import settings

console = Console()


def init_db() -> None:
    from sqlmodel import SQLModel

    from tentacle_apply.db.session import init_db as _init

    _init()
    tables = ", ".join(sorted(SQLModel.metadata.tables))
    console.print(f"[green]DB ready[/green] at [cyan]{settings.db_url}[/cyan]")
    console.print(f"[dim]tables:[/dim] {tables}")


def ping() -> None:
    if not settings.llm_key:
        console.print(f"[red]No API key for provider '{settings.llm_provider}'. Set it in .env.[/red]")
        sys.exit(1)
    from tentacle_apply.llm import complete

    console.print(f"[dim]provider={settings.llm_provider} pool={len(settings.nvidia_pool)}[/dim]")
    out = complete("Reply with exactly the word: ok", temperature=0.0)
    console.print(f"[green]LLM responded:[/green] {out.strip()[:120]}")


def intake(args: list[str]) -> None:
    if not args:
        console.print("[red]Usage:[/red] intake <resume_path> [--email you@example.com]")
        sys.exit(2)
    path = args[0]
    email = None
    if "--email" in args:
        try:
            email = args[args.index("--email") + 1]
        except IndexError:
            console.print("[red]--email needs a value[/red]")
            sys.exit(2)
    if not settings.llm_key:
        console.print(f"[red]No API key for provider '{settings.llm_provider}'. Set it in .env.[/red]")
        sys.exit(1)

    from tentacle_apply.intake import ingest_resume

    console.print(f"[dim]Parsing[/dim] {path} …")
    profile = ingest_resume(path, email)

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan", justify="right")
    table.add_column()
    table.add_row("name", profile.full_name or "—")
    table.add_row("years", str(profile.years_exp))
    table.add_row("titles", ", ".join(profile.titles) or "—")
    table.add_row("skills", ", ".join(profile.skills[:15]) or "—")
    table.add_row("locations", ", ".join(profile.locations) or "—")
    table.add_row("work auth", profile.work_auth or "—")
    table.add_row("min salary", str(profile.min_salary) if profile.min_salary else "—")
    console.print(f"[green]Profile saved[/green] (user_id={profile.user_id}, profile_id={profile.id})")
    console.print(table)


def _opt(args: list[str], flag: str, default: str = "") -> str:
    if flag in args:
        try:
            return args[args.index(flag) + 1]
        except IndexError:
            return default
    return default


def sources(args: list[str]) -> None:
    query = _opt(args, "--query")
    location = _opt(args, "--location")
    limit = int(_opt(args, "--limit", "15") or 15)

    from tentacle_apply.sources import fetch_jobs

    console.print(f"[dim]Fetching jobs[/dim] query='{query}' location='{location}' limit={limit} …")
    report = fetch_jobs(query=query, location=location, limit=limit)
    console.print(
        f"[green]Fetched {report.fetched}[/green] · added [cyan]{report.added}[/cyan] · "
        f"skipped(dup) {report.skipped}"
    )
    for name, err in report.errors.items():
        console.print(f"[yellow]source '{name}' failed:[/yellow] {err}")

    table = Table(title="Sample of fetched jobs", show_lines=False)
    table.add_column("source", style="magenta")
    table.add_column("company", style="cyan")
    table.add_column("title")
    table.add_column("location", style="dim")
    for j in report.jobs[:12]:
        table.add_row(j.source, j.company, j.title[:48], j.location[:24])
    if report.jobs:
        console.print(table)


def match(args: list[str]) -> None:
    email = _opt(args, "--email") or None
    top = int(_opt(args, "--top", "12") or 12)
    min_score = float(_opt(args, "--min", "0") or 0)

    from tentacle_apply.matching import rank_jobs

    console.print("[dim]Ranking jobs against your profile …[/dim]")
    ranked = rank_jobs(user_email=email, top=top, min_score=min_score)
    if not ranked:
        console.print("[yellow]No jobs to rank. Run `sources` (and `intake`) first.[/yellow]")
        return

    table = Table(title=f"Top {len(ranked)} matches")
    table.add_column("score", style="green", justify="right")
    table.add_column("fit", justify="center")
    table.add_column("company", style="cyan")
    table.add_column("title")
    table.add_column("location", style="dim")
    for r in ranked:
        fit = "[green]✓[/green]" if r.eligible else "[red]✗[/red]"
        table.add_row(f"{r.score:.0f}", fit, r.job.company, r.job.title[:46], r.job.location[:22])
    console.print(table)


def _resolve_user_or_exit(session, email):
    from sqlmodel import select

    from tentacle_apply.db.models import User

    user = (
        session.exec(select(User).where(User.email == email.lower())).first()
        if email
        else session.exec(select(User)).first()
    )
    if user is None:
        console.print("[red]No user. Run `intake` on a resume first.[/red]")
        sys.exit(1)
    return user


def preferences(args: list[str]) -> None:
    from tentacle_apply.db.session import get_session, init_db
    from tentacle_apply.discovery import preferences as prefs_mod

    email = _opt(args, "--email") or None
    init_db()
    with get_session() as session:
        user = _resolve_user_or_exit(session, email)

        setters = {}
        if "--work-modes" in args:
            setters["work_modes"] = _opt(args, "--work-modes")
        if "--locations" in args:
            setters["locations"] = _opt(args, "--locations")
        if "--roles" in args:
            setters["roles"] = _opt(args, "--roles")
        if "--skills" in args:
            setters["skills"] = _opt(args, "--skills")
        if "--seniority" in args:
            setters["seniority"] = _opt(args, "--seniority")
        if "--min-salary" in args:
            setters["min_salary"] = int(_opt(args, "--min-salary", "0") or 0)
        if "--needs-sponsorship" in args:
            setters["needs_sponsorship"] = True

        if setters:
            prefs = prefs_mod.upsert_preferences(session, user.id, **setters)
            console.print("[green]Preferences saved.[/green]")
        else:
            prefs = prefs_mod.get_preferences(session, user.id)
            if prefs is None:
                console.print("[yellow]No preferences set yet.[/yellow] Use --work-modes/--locations/--roles/--skills/…")
                return

        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="cyan", justify="right")
        table.add_column()
        table.add_row("work modes", ", ".join(prefs.work_modes) or "—")
        table.add_row("locations", ", ".join(prefs.locations) or "—")
        table.add_row("roles", ", ".join(prefs.roles) or "—")
        table.add_row("skills", ", ".join(prefs.skills[:15]) or "—")
        table.add_row("seniority", prefs.seniority or "—")
        table.add_row("min salary", str(prefs.min_salary) if prefs.min_salary else "—")
        table.add_row("needs sponsorship", "yes" if prefs.needs_sponsorship else "no")
        console.print(table)


def companies(args: list[str]) -> None:
    from tentacle_apply.db.session import get_session, init_db
    from tentacle_apply.discovery import registry

    action = args[0] if args else "list"
    init_db()
    with get_session() as session:
        if action == "add":
            raw = args[1] if len(args) > 1 else ""
            if not raw:
                console.print("[red]Usage:[/red] companies add <company-name-or-careers-url>")
                sys.exit(2)
            console.print(f"[dim]Resolving[/dim] {raw!r} …")
            company = registry.add_company(session, raw)
            if company is None:
                console.print(f"[yellow]Could not find {raw!r} on supported ATS {registry.SUPPORTED_ATS}.[/yellow]")
                sys.exit(1)
            console.print(f"[green]Added[/green] {company.name} → [cyan]{company.ats}/{company.token}[/cyan]")
            return
        if action == "seed":
            added = registry.seed_registry(session)
            console.print(f"[green]Seeded[/green] {added} new companies.")
        # list (default)
        rows = registry.list_companies(session)
        if not rows:
            console.print("[yellow]Registry empty.[/yellow] Run `companies seed` or `companies add <name>`.")
            return
        table = Table(title=f"Company registry ({len(rows)})")
        table.add_column("name", style="cyan")
        table.add_column("ats", style="magenta")
        table.add_column("token")
        table.add_column("origin", style="dim")
        table.add_column("on", justify="center")
        for c in rows:
            table.add_row(c.name, c.ats, c.token, c.origin, "✓" if c.enabled else "✗")
        console.print(table)


def discover(args: list[str]) -> None:
    from tentacle_apply.discovery import run_discovery

    email = _opt(args, "--email") or None
    limit = int(_opt(args, "--limit", "20") or 20)

    console.print("[dim]Discovering jobs (free, no LLM) — aggregators + company registry …[/dim]")
    report = run_discovery(user_email=email, limit=limit)
    console.print(
        f"[green]Fetched {report.fetched}[/green] from {report.companies_queried} companies + aggregators · "
        f"kept after filter (added [cyan]{report.added}[/cyan], dup {report.skipped_dup}) · "
        f"filtered out {report.filtered_out}"
    )
    for name, err in report.errors.items():
        console.print(f"[yellow]source '{name}' failed:[/yellow] {err}")

    if not report.ranked:
        console.print("[yellow]No ranked matches. Add companies (`companies add`) or set preferences.[/yellow]")
        return
    table = Table(title=f"Top {len(report.ranked)} matches")
    table.add_column("score", style="green", justify="right")
    table.add_column("fit", justify="center")
    table.add_column("company", style="cyan")
    table.add_column("title")
    table.add_column("location", style="dim")
    for r in report.ranked:
        fit = "[green]✓[/green]" if r.eligible else "[red]✗[/red]"
        table.add_row(f"{r.score:.0f}", fit, r.job.company, r.job.title[:44], r.job.location[:22])
    console.print(table)


def tailor(args: list[str]) -> None:
    email = _opt(args, "--email") or None
    job_id_arg = _opt(args, "--job-id")
    target = float(_opt(args, "--target", "80") or 80)
    max_iters = int(_opt(args, "--max-iters", "3") or 3)

    if not settings.llm_key:
        console.print(f"[red]No API key for provider '{settings.llm_provider}'. Set it in .env.[/red]")
        sys.exit(1)

    from sqlmodel import select

    from tentacle_apply.config import DATA_DIR
    from tentacle_apply.db.models import Job, Match, Profile, User
    from tentacle_apply.db.session import get_session, init_db
    from tentacle_apply.tailor import TailorStudio

    init_db()
    with get_session() as session:
        user = (
            session.exec(select(User).where(User.email == email.lower())).first()
            if email
            else session.exec(select(User)).first()
        )
        if user is None:
            console.print("[red]No user. Run `intake` on a resume first.[/red]")
            sys.exit(1)
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()

        if job_id_arg:
            job = session.get(Job, int(job_id_arg))
        else:
            top = session.exec(
                select(Match).where(Match.user_id == user.id).order_by(Match.score.desc())
            ).first()
            job = session.get(Job, top.job_id) if top else None
        if job is None:
            console.print("[red]No job found. Run `sources` and `match` first, or pass --job-id.[/red]")
            sys.exit(1)

    job_text = f"{job.title} at {job.company}\nLocation: {job.location}\n\n{job.description}"
    facts = profile.raw_text if profile and profile.raw_text else ", ".join(profile.skills if profile else [])

    console.print(f"[dim]Tailoring for[/dim] [cyan]{job.title}[/cyan] @ {job.company} (job_id={job.id}) …")
    result = TailorStudio(target=target, max_iters=max_iters).run(job_text, facts, facts)

    out_dir = DATA_DIR / "tailored"
    out_dir.mkdir(parents=True, exist_ok=True)
    resume_path = out_dir / f"job{job.id}_resume.md"
    cover_path = out_dir / f"job{job.id}_cover.txt"
    resume_path.write_text(result.resume, encoding="utf-8")
    cover_path.write_text(result.cover_letter, encoding="utf-8")

    c = result.critique
    console.print(
        f"[green]Done[/green] overall=[bold]{c.overall:.0f}[/bold] "
        f"history={'->'.join(f'{h:.0f}' for h in result.history)}"
    )
    console.print(
        f"[dim]relevance={c.scores.get('relevance',0):.0f} "
        f"keywords={c.scores.get('keyword_coverage',0):.0f} "
        f"grounding={c.scores.get('grounding',0):.0f} "
        f"clarity={c.scores.get('clarity',0):.0f}[/dim]"
    )
    if c.missing_keywords:
        console.print(f"[yellow]still missing:[/yellow] {', '.join(c.missing_keywords[:8])}")
    console.print(f"[dim]saved:[/dim] {resume_path}  |  {cover_path}")
    console.print("\n[bold]Cover letter preview:[/bold]")
    console.print(result.cover_letter[:600])


def apply(args: list[str]) -> None:
    from sqlmodel import select

    from tentacle_apply.apply import find_duplicate, get_applier, supported_ats
    from tentacle_apply.apply.assets import build_applicant
    from tentacle_apply.db.models import Application, ApplicationStatus, Job, Profile, User
    from tentacle_apply.db.session import get_session, init_db

    email = _opt(args, "--email") or None
    job_id_arg = _opt(args, "--job-id")
    url_override = _opt(args, "--url")
    ats_override = _opt(args, "--ats") or None
    interactive = "--hitl" in args
    do_submit = "--submit" in args or interactive
    headful = "--headful" in args or interactive

    init_db()
    with get_session() as session:
        user = (
            session.exec(select(User).where(User.email == email.lower())).first()
            if email
            else session.exec(select(User)).first()
        )
        if user is None:
            console.print("[red]No user. Run `intake` first.[/red]")
            sys.exit(1)
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()

        job = session.get(Job, int(job_id_arg)) if job_id_arg else None
        if job is None and not url_override:
            job = session.exec(
                select(Job).where(Job.ats_type.in_(supported_ats())).order_by(Job.id.desc())
            ).first()
        if job is None and not url_override:
            console.print(f"[red]No applyable job found (supported: {', '.join(supported_ats())}). Pass --job-id or --url.[/red]")
            sys.exit(1)

        target_url = url_override or (job.url if job else "")
        ats = ats_override or (job.ats_type if job else "greenhouse")
        applier = get_applier(ats, headful=headful)
        if applier is None:
            console.print(f"[yellow]No Tier-1 applier for ats={ats!r}. Supported: {', '.join(supported_ats())}.[/yellow]")
            sys.exit(2)

        # Reliability guard: never apply twice.
        if job:
            dup = find_duplicate(session, user.id, job)
            if dup:
                console.print(f"[yellow]Duplicate:[/yellow] already {dup.status} for this role (application #{dup.id}). Skipping.")
                sys.exit(0)

        applicant = build_applicant(user, profile, job)
        job_text = f"{job.title} at {job.company}\n{job.description}" if job else ""
        job_db_id = job.id if job else None

    if not target_url:
        console.print("[red]No application URL.[/red]")
        sys.exit(1)

    if interactive:
        mode = "[yellow]HITL SUBMIT[/yellow] (auto-fill; you solve CAPTCHA if one appears)"
    elif do_submit:
        mode = "[red]LIVE SUBMIT[/red] (auto; skips on CAPTCHA)"
    else:
        mode = "[green]DRY RUN[/green] (no submit)"
    console.print(f"{mode} -> [cyan]{target_url}[/cyan]")
    if applicant.resume_pdf:
        console.print(f"[dim]resume:[/dim] {applicant.resume_pdf}")

    console.print(f"[dim]ats:[/dim] {ats}")
    result = applier.apply(target_url, applicant, job_text, submit=do_submit, interactive=interactive)

    color = "green" if result.ok else ("yellow" if result.status in (ApplicationStatus.SKIPPED_CAPTCHA, ApplicationStatus.DUPLICATE) else "red")
    console.print(f"[{color}]status={result.status}[/{color}]  filled={', '.join(result.filled) or '-'}")
    if result.missing_required:
        console.print(f"[yellow]missing required:[/yellow] {', '.join(result.missing_required[:10])}")
    if result.confirmation_url:
        console.print(f"[green]confirmation:[/green] {result.confirmation_url}")
    if result.error:
        console.print(f"[red]error:[/red] {result.error}")
    for n in result.notes:
        console.print(f"[dim]- {n}[/dim]")
    if result.screenshot:
        console.print(f"[dim]screenshot:[/dim] {result.screenshot}")

    # Persist the attempt (skip pure --url smoke tests that aren't tied to a stored job).
    if job_db_id is not None:
        with get_session() as session:
            existing = session.exec(
                select(Application).where(Application.user_id == user.id, Application.job_id == job_db_id)
            ).first()
            app = existing or Application(user_id=user.id, job_id=job_db_id)
            app.status = result.status
            app.confirmation_url = result.confirmation_url
            app.resume_version_path = str(applicant.resume_pdf or "")
            app.cover_letter = applicant.cover_letter
            app.error = result.error
            app.attempts = (app.attempts or 0) + 1
            if result.submitted:
                from tentacle_apply.db.models import utcnow

                app.applied_at = utcnow()
            session.add(app)
            session.commit()
            console.print(f"[dim]recorded application (job_id={job_db_id}, status={result.status}).[/dim]")


def run(args: list[str]) -> None:
    email = _opt(args, "--email") or None
    target = int(_opt(args, "--target", str(settings.default_target_applications)) or settings.default_target_applications)
    mode = _opt(args, "--mode", settings.run_mode) or settings.run_mode
    min_score_arg = _opt(args, "--min-score")
    discover = "--no-discover" not in args
    headful = "--headful" in args or mode == "hitl"

    if mode not in ("prepare", "submit", "hitl"):
        console.print(f"[red]--mode must be prepare|submit|hitl (got {mode!r}).[/red]")
        sys.exit(2)
    if not settings.llm_key:
        console.print(f"[red]No API key for provider '{settings.llm_provider}'. Set it in .env.[/red]")
        sys.exit(1)

    from tentacle_apply.orchestrator import run_apply_loop

    label = {"prepare": "PREPARE (fill + screenshot, no submit)", "submit": "AUTO-SUBMIT (skips CAPTCHA)", "hitl": "HITL (you solve CAPTCHA)"}[mode]
    console.print(f"[bold]Run[/bold] target=[cyan]{target}[/cyan] mode=[magenta]{label}[/magenta] discover={discover}")
    result = run_apply_loop(
        user_email=email,
        target=target,
        mode=mode,
        min_score=float(min_score_arg) if min_score_arg else None,
        discover=discover,
        headful=headful,
    )

    console.print(
        f"[green]Run #{result.run_id} {result.stopped_reason}[/green] — "
        f"prepared [bold]{result.prepared}[/bold]/{result.target} · submitted {result.submitted} · "
        f"gated_out {result.gated_out} · duplicates {result.duplicates} · "
        f"skipped_captcha {result.skipped_captcha} · failed {result.failed} · unsupported {result.unsupported}"
    )
    if not result.outcomes:
        console.print("[yellow]No candidates. Set preferences, add companies, or lower --min-score.[/yellow]")
        return
    table = Table(title="Run outcomes")
    table.add_column("score", style="green", justify="right")
    table.add_column("status")
    table.add_column("company", style="cyan")
    table.add_column("title")
    table.add_column("reason", style="dim")
    for o in result.outcomes[:40]:
        table.add_row(f"{o.score:.0f}", o.status, o.company[:18], o.title[:38], o.reason[:40])
    console.print(table)


def serve(args: list[str]) -> None:
    import uvicorn

    host = _opt(args, "--host", "127.0.0.1") or "127.0.0.1"
    port = int(_opt(args, "--port", "8001") or 8001)
    console.print(f"[green]tentacle-apply[/green] dashboard -> [cyan]http://{host}:{port}[/cyan]")
    uvicorn.run("tentacle_apply.api:app", host=host, port=port, log_level="info")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init-db"
    if cmd == "init-db":
        init_db()
    elif cmd == "ping":
        ping()
    elif cmd == "intake":
        intake(sys.argv[2:])
    elif cmd == "sources":
        sources(sys.argv[2:])
    elif cmd == "preferences":
        preferences(sys.argv[2:])
    elif cmd == "companies":
        companies(sys.argv[2:])
    elif cmd == "discover":
        discover(sys.argv[2:])
    elif cmd == "match":
        match(sys.argv[2:])
    elif cmd == "tailor":
        tailor(sys.argv[2:])
    elif cmd == "apply":
        apply(sys.argv[2:])
    elif cmd == "run":
        run(sys.argv[2:])
    elif cmd == "serve":
        serve(sys.argv[2:])
    else:
        console.print(
            f"[red]Unknown command:[/red] {cmd}. "
            "Use 'init-db', 'ping', 'intake', 'sources', 'preferences', 'companies', 'discover', "
            "'match', 'tailor', 'apply', 'run' or 'serve'."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
