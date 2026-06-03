"""Tiny CLI for tentacle-apply scaffolding tasks.

    uv run python -m tentacle_apply.cli init-db                       # create DB + data dirs
    uv run python -m tentacle_apply.cli ping                          # verify the LLM provider works
    uv run python -m tentacle_apply.cli intake <resume> [--email ..]  # resume -> structured profile
    uv run python -m tentacle_apply.cli sources [--query ..] [--location ..] [--limit N]  # fetch jobs
    uv run python -m tentacle_apply.cli match [--email ..] [--top N] [--min SCORE]  # rank jobs vs profile
    uv run python -m tentacle_apply.cli tailor [--email ..] [--job-id N] [--target T] [--max-iters K]
    uv run python -m tentacle_apply.cli apply [--email ..] [--job-id N] [--url URL] [--submit|--hitl] [--headful]
        #   (no flag) dry run · --submit auto-submit (skips CAPTCHA) · --hitl auto-fill + you solve any CAPTCHA
    uv run python -m tentacle_apply.cli serve [--host ..] [--port N]  # dashboard + JSON API
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def _extract_phone(text: str) -> str:
    import re

    m = re.search(r"(\+?\d[\d\s().\-]{7,}\d)", text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _extract_links(text: str) -> dict[str, str]:
    import re

    links: dict[str, str] = {}
    for kind, pat in (
        ("linkedin", r"(?:https?://)?(?:www\.)?linkedin\.com/[\w\-/]+"),
        ("github", r"(?:https?://)?(?:www\.)?github\.com/[\w\-/]+"),
    ):
        m = re.search(pat, text or "", re.I)
        if m:
            links[kind] = m.group(0)
    return links


def _resume_pdf_for(profile, job_id: int) -> Path | None:
    from tentacle_apply.config import DATA_DIR
    from tentacle_apply.tailor.render import markdown_to_pdf

    tailored_md = DATA_DIR / "tailored" / f"job{job_id}_resume.md"
    if tailored_md.exists():
        return markdown_to_pdf(tailored_md.read_text(encoding="utf-8"), DATA_DIR / "tailored" / f"job{job_id}_resume.pdf")
    if profile and profile.resume_path and Path(profile.resume_path).suffix.lower() == ".pdf" and Path(profile.resume_path).exists():
        return Path(profile.resume_path)
    if profile and profile.raw_text:
        return markdown_to_pdf(profile.raw_text, DATA_DIR / "tailored" / f"profile_{profile.user_id}_resume.pdf")
    return None


def apply(args: list[str]) -> None:
    from sqlmodel import select

    from tentacle_apply.apply import GreenhouseApplier, find_duplicate
    from tentacle_apply.apply.base import Applicant, split_name
    from tentacle_apply.db.models import Application, ApplicationStatus, Job, Profile, User
    from tentacle_apply.db.session import get_session, init_db

    email = _opt(args, "--email") or None
    job_id_arg = _opt(args, "--job-id")
    url_override = _opt(args, "--url")
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
            top_gh = session.exec(
                select(Job).where(Job.ats_type == "greenhouse").order_by(Job.id.desc())
            ).first()
            job = top_gh
        if job is None and not url_override:
            console.print("[red]No Greenhouse job found. Pass --job-id or --url, or run `sources`.[/red]")
            sys.exit(1)

        target_url = url_override or (job.url if job else "")
        if job and job.ats_type and job.ats_type != "greenhouse":
            console.print(f"[yellow]job ats_type={job.ats_type!r} is not greenhouse — Step 6 supports Greenhouse only.[/yellow]")
            sys.exit(2)

        # Reliability guard: never apply twice.
        if job:
            dup = find_duplicate(session, user.id, job)
            if dup:
                console.print(f"[yellow]Duplicate:[/yellow] already {dup.status} for this role (application #{dup.id}). Skipping.")
                sys.exit(0)

        first, last = split_name(profile.full_name if profile else "")
        job_id_for_assets = job.id if job else 0
        resume_pdf = _resume_pdf_for(profile, job_id_for_assets)
        cover_path = (
            Path(__file__).resolve().parent.parent / "data" / "tailored" / f"job{job_id_for_assets}_cover.txt"
        )
        cover = cover_path.read_text(encoding="utf-8") if cover_path.exists() else ""

        applicant = Applicant(
            first_name=first,
            last_name=last,
            email=user.email,
            phone=_extract_phone(profile.raw_text if profile else ""),
            location=(profile.locations[0] if profile and profile.locations else ""),
            work_auth=(profile.work_auth if profile else ""),
            min_salary=(profile.min_salary if profile else None),
            years_exp=(profile.years_exp if profile else 0.0),
            resume_pdf=resume_pdf,
            resume_text=(profile.raw_text if profile else ""),
            cover_letter=cover,
            links=_extract_links(profile.raw_text if profile else ""),
        )
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

    result = GreenhouseApplier(headful=headful).apply(
        target_url, applicant, job_text, submit=do_submit, interactive=interactive
    )

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
    elif cmd == "match":
        match(sys.argv[2:])
    elif cmd == "tailor":
        tailor(sys.argv[2:])
    elif cmd == "apply":
        apply(sys.argv[2:])
    elif cmd == "serve":
        serve(sys.argv[2:])
    else:
        console.print(
            f"[red]Unknown command:[/red] {cmd}. "
            "Use 'init-db', 'ping', 'intake', 'sources', 'match', 'tailor', 'apply' or 'serve'."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
