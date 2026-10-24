"""Command-line interface: `summer-hunter <command>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from summer_hunter.database.migrations import backup_sqlite, init_db, make_engine
from summer_hunter.database.repository import Repository
from summer_hunter.utils.logging import setup_logging
from summer_hunter.utils.settings import Settings

log = logging.getLogger("summer_hunter")


class App:
    """Lazy wiring of settings, database, LLM provider and Telegram."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.sessions = init_db(self.engine)
        self._hunter = None

    @property
    def hunter(self):  # noqa: ANN201
        if self._hunter is None:
            from summer_hunter.pipeline import Hunter

            self._hunter = Hunter(self.settings, self.sessions)
        return self._hunter

    def telegram(self):  # noqa: ANN201
        if not self.settings.telegram_configured:
            return None
        from summer_hunter.integrations.telegram_client import TelegramClient

        return TelegramClient(self.settings.telegram_bot_token)

    def notifier(self):  # noqa: ANN201
        from summer_hunter.notifications.notifier import Notifier

        return Notifier(self.settings, self.sessions, self.telegram())

    @property
    def db_path(self) -> Path:
        return Path(self.settings.database_url.removeprefix("sqlite:///"))


def _print(obj) -> None:  # noqa: ANN001
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# Commands --------------------------------------------------------------------------------------


def cmd_init(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.models.candidate import CandidateProfile

    profile = CandidateProfile.from_yaml(app.settings.profile_raw)
    unknown = [k for k, v in {"graduation_month": profile.graduation_month, "citizenship": profile.citizenship or None,
                              "requires_sponsorship": profile.requires_sponsorship,
                              "returning_to_studies": profile.returning_to_studies}.items() if v is None]
    print(f"Database ready: {app.db_path}")
    print(f"Profile loaded: {len(profile.experiences)} experiences, languages {sorted(profile.language_names)}")
    print(f"Watchlist: {len(app.settings.companies)} companies, "
          f"{sum(1 for c in app.settings.companies if c.get('ats') not in (None, 'none'))} with a public feed")
    if unknown:
        print(f"UNKNOWN profile fields (keep eligibility at NEEDS_VERIFICATION until set): {', '.join(unknown)}")
    _print(app.settings.public_summary())
    return 0


def cmd_run(app: App, args) -> int:  # noqa: ANN001
    only = set(args.source) if args.source else None
    stats = asyncio.run(app.hunter.run_cycle(trigger="manual", dry_run=args.dry_run, only=only))
    summary = stats.as_dict()
    summary["errors"] = summary["errors"][:10]
    _print(summary)
    if args.notify and not args.dry_run:
        report = app.notifier().morning_digest(dry_run=False, force=True)
        print(f"Telegram: sent {report.sent}, duplicates skipped {report.skipped_duplicates}, errors {report.errors}")
    return 0


def cmd_evaluate(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.pipeline import CycleStats

    stats = CycleStats()
    with app.sessions() as session:
        repo = Repository(session)
        app.hunter.evaluate_pending(repo, stats, force=args.force)
        session.commit()
    _print({"evaluated": stats.evaluated, "llm_analyses": stats.llm_analyses, "llm_cost_usd": round(stats.llm_cost_usd, 4),
            "errors": stats.errors[:10]})
    return 0


def cmd_night(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.scheduler.nightly_runner import AlreadyRunning, StopFlag, run_night, single_instance

    stop = StopFlag()
    stop.install()
    try:
        with single_instance(app.settings.data_dir / "night.lock"):
            summary = run_night(app.hunter, app.notifier(), app.settings, stop)
            backup_sqlite(app.db_path, app.settings.data_dir / "backups")
    except AlreadyRunning:
        log.info("another night run holds the lock; exiting")
        print("Another run is in progress; nothing to do.")
        return 0
    _print(summary)
    return 0


def cmd_catch_up(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.scheduler.nightly_runner import AlreadyRunning, single_instance

    try:
        with single_instance(app.settings.data_dir / "night.lock"):
            stats = asyncio.run(app.hunter.run_cycle(trigger="catch_up"))
            report = app.notifier().morning_digest(dry_run=args.dry_run, force=args.force)
    except AlreadyRunning:
        print("Another run is in progress; nothing to do.")
        return 0
    _print({"new": stats.new_opportunities, "evaluated": stats.evaluated, "digest_sent": report.sent,
            "digest_dry_run": report.dry_run, "errors": stats.errors[:10] + report.errors})
    return 0


def cmd_digest(app: App, args) -> int:  # noqa: ANN001
    report = app.notifier().morning_digest(dry_run=args.dry_run, force=args.force)
    if report.dry_run:
        for message in report.messages:
            print(message)
            print("-" * 60)
        if not app.settings.telegram_configured:
            print("(Telegram not configured: printed instead of sent.)")
    print(f"sent={report.sent} duplicates_skipped={report.skipped_duplicates} errors={report.errors}")
    return 0 if not report.errors else 1


def cmd_bot(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.bot.handlers import Bot

    telegram = app.telegram()
    if telegram is None:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (see docs/SETUP.md).")
        return 2
    Bot(app.settings, app.sessions, telegram, app.hunter.provider).poll_forever(args.minutes * 60 if args.minutes else None)
    return 0


def cmd_telegram_setup(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.integrations.telegram_client import TelegramClient, TelegramError

    if not app.settings.telegram_bot_token:
        print("Set TELEGRAM_BOT_TOKEN in .env first (from @BotFather).")
        return 2
    client = TelegramClient(app.settings.telegram_bot_token)
    try:
        me = client.get_me()
        print(f"Bot OK: @{me.get('username')}")
        chats = {}
        for upd in client.get_updates(None, timeout=0):
            chat = (upd.get("message") or {}).get("chat") or {}
            if chat:
                chats[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
        if chats:
            for cid, name in chats.items():
                print(f"chat_id={cid}  ({name})  -> put TELEGRAM_CHAT_ID={cid} in .env")
        else:
            print("No messages yet: open the bot in Telegram, send /start, then run this command again.")
        if args.test and app.settings.telegram_chat_id:
            client.send_message(app.settings.telegram_chat_id, "✅ Summer Hunter 2027 is connected.")
            print("Test message sent.")
    except TelegramError as exc:
        print(f"Telegram error: {exc}")
        return 1
    return 0


def cmd_list(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.engine.geo import country_name

    with app.sessions() as session:
        repo = Repository(session)
        if args.decision:
            rows = repo.by_eligibility(args.decision.upper(), limit=args.limit)
        elif args.user_status:
            rows = repo.by_user_status(args.user_status, limit=args.limit)
        else:
            rows = repo.top(args.limit, include_dismissed=args.all)
        for o in rows:
            prio = f"{o.priority_score:5.1f}" if o.priority_score is not None else "  -  "
            print(f"#{o.id:<5} prio {prio} fit {o.fit_score!s:>3} net {o.networking_score!s:>3} "
                  f"{(o.eligibility_status or '?'):<18} {o.company_name[:22]:<22} {o.title[:55]:<55} {country_name(o.country_code)}")
        if not rows:
            print("No opportunities match.")
    return 0


def cmd_show(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.notifications import formatter

    with app.sessions() as session:
        repo = Repository(session)
        opp = repo.opportunity(args.id)
        if not opp:
            print("Not found.")
            return 1
        ev, fit = repo.latest_eligibility(opp.id), repo.latest_fit(opp.id)
        print(f"{opp.title} @ {opp.company_name}, {opp.location_text}\n{opp.official_url}")
        print(f"Sources: {', '.join(s.source for s in opp.sources)}; LinkedIn post: {opp.linkedin_post_url or '-'}")
        if ev and fit:
            import re

            text = formatter.explain(opp, ev.criteria, fit.report, fit.networking, fit.priority, ev.sponsorship)
            print(re.sub(r"</?[a-z]+>", "", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        for c in repo.contacts(opp.id):
            print(f"Contact: {c.name} [{c.relationship_type}, {c.confidence:.2f}] {c.profile_url or c.source_post_url or ''}\n  evidence: {c.evidence}")
    return 0


def _read_text(args) -> str:  # noqa: ANN001
    if args.text_file == "-":
        return sys.stdin.read()
    return Path(args.text_file).read_text(encoding="utf-8")


def cmd_add_post(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.collectors.linkedin_public import build_post
    from summer_hunter.pipeline import CycleStats

    stats = CycleStats()
    with app.sessions() as session:
        repo = Repository(session)
        post = build_post(args.url, _read_text(args), args.author, args.author_url, args.headline, "manual",
                          analyzer=app.hunter.analyzer(repo))
        row, opp = app.hunter.ingest_post(repo, post, stats, company=args.company, location=args.location, title=args.title)
        if opp:
            app.hunter.evaluate(repo, opp, None, stats)
        session.commit()
        print(f"Post classified as {post.classification.value} (confidence {post.classification_confidence:.2f}; "
              f"signals: {', '.join(post.classification_reasons)})")
        if opp:
            print(f"Opportunity #{opp.id}: {opp.title} @ {opp.company_name}: eligibility {opp.eligibility_status}, "
                  f"fit {opp.fit_score}, networking {opp.networking_score}")
        else:
            print("Stored as a lead only (not actionable, or company unknown: pass --company).")
    return 0


def cmd_add_job(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.models.opportunity import RawOpportunity
    from summer_hunter.pipeline import CycleStats
    from summer_hunter.utils.text import clean_text, sha256

    raw = RawOpportunity(source="manual", source_id=sha256(args.url)[:24], url=args.url, apply_url=args.url, title=args.title,
                         company=args.company, location_text=args.location, description_text=clean_text(_read_text(args)))
    stats = CycleStats()
    with app.sessions() as session:
        repo = Repository(session)
        opp = app.hunter.ingest(repo, raw, stats)
        from summer_hunter.engine.ai_analysis import Analyzer
        from summer_hunter.integrations.anthropic_client import NullProvider

        analyzer = None if isinstance(app.hunter.provider, NullProvider) else \
            Analyzer(app.hunter.provider, repo, app.hunter.profile, app.settings.llm, app.settings.timezone)
        app.hunter.evaluate(repo, opp, analyzer, stats)
        session.commit()
        print(f"Opportunity #{opp.id} ({'merged with existing' if stats.merged_duplicates else 'new'}): "
              f"eligibility {opp.eligibility_status}, fit {opp.fit_score}, priority {opp.priority_score}")
    return 0


def cmd_sources(app: App, args) -> int:  # noqa: ANN001
    collectors = app.hunter.build_collectors()
    with app.sessions() as session:
        health = {h.source: h for h in Repository(session).health()}
    for c in collectors:
        h = health.get(c.name)
        state = f"{h.last_status} ({h.last_item_count} items, {h.consecutive_failures} consecutive failures)" if h else "never run"
        print(f"{c.name:<45} {state}")
    return 0


def cmd_probe_boards(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.collectors.base import CollectContext

    async def probe() -> list[tuple[str, str, int]]:
        out = []
        async with app.hunter._http() as http:
            ctx = CollectContext(http=http, title_rules=app.hunter.title_rules, max_detail_fetches=0)
            for col in app.hunter.build_collectors():
                if col.name.split(":")[0] not in {"greenhouse", "lever", "ashby", "workday", "career_page"}:
                    continue
                res = await col.collect(ctx)
                out.append((col.name, res.status, res.scanned))
        return out

    for name, status, scanned in asyncio.run(probe()):
        print(f"{name:<45} {status:<8} {scanned} listings scanned")
    return 0


def cmd_safety(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.integrations.advisories import LEVELS, fetch_advisory

    with app.sessions() as session:
        repo = Repository(session)
        if args.action == "refresh":
            geo = app.settings.preferences.get("geography", {})
            codes = args.countries or sorted({c for g in (geo.get("priority_countries") or {}).values() for c in g})

            async def run() -> list:
                async with app.hunter._http() as http:
                    return [(code, *(await fetch_advisory(http, code))) for code in codes]

            for code, level, summary, url in asyncio.run(run()):
                repo.upsert_advisory(code, level, summary, url or "https://www.diplomatie.gouv.fr/")
                print(f"{code}: {level}  {summary[:120]}")
            session.commit()
            print("Automatic levels never exclude a country. Use `safety set` after reading the page to decide.")
        elif args.action == "set":
            if args.level not in LEVELS:
                print(f"level must be one of {LEVELS}")
                return 2
            for code in args.countries:
                repo.upsert_advisory(code.upper(), args.level, args.note or "Set manually by the user.", args.url or "manual", manual=True)
            session.commit()
            print("Saved. Re-run `summer-hunter evaluate --force` to apply.")
        else:
            for code, adv in sorted(repo.advisories().items()):
                print(f"{code}: {adv['level']:<32} retrieved {adv['retrieved_at']} {'(manual)' if adv['manual'] else ''} {adv['source_url']}")
    return 0


def cmd_preferences(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.engine.feedback import recompute

    with app.sessions() as session:
        repo = Repository(session)
        if args.action == "reset":
            n = repo.reset_preferences()
            session.commit()
            print(f"Cleared {n} learned adjustments (feedback history is kept but ignored before now).")
        elif args.action == "recompute":
            rows = recompute(repo, app.settings.scoring)
            session.commit()
            _print(rows)
        else:
            for p in repo.preference_adjustments():
                print(f"{p.dimension:<11} {p.key:<35} {p.delta:+5.1f}  ({p.evidence_count} feedback)")
    return 0


def cmd_feedback(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.engine.feedback import recompute

    with app.sessions() as session:
        repo = Repository(session)
        opp = repo.opportunity(args.id)
        if not opp:
            print("Not found.")
            return 1
        repo.record_feedback(opp, args.action, args.reason, source="cli", data_dir=app.settings.data_dir,
                             timezone=app.settings.timezone)
        recompute(repo, app.settings.scoring)
        session.commit()
        print(f"Recorded {args.action} for #{opp.id}.")
    return 0


def cmd_crm(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.crm import followups, metrics, sync
    from summer_hunter.crm.excel_export import export_workbook
    from summer_hunter.crm.excel_import import WorkbookError
    from summer_hunter.crm.service import CRM
    from summer_hunter.crm.statuses import TransitionError
    from summer_hunter.crm.validation import ValidationError, app_code, parse_app_id, parse_date

    settings = app.settings
    if args.action == "export":
        print(sync.export(settings, app.sessions, Path(args.path) if args.path else None))
        return 0
    if args.action == "template":
        from summer_hunter.database.migrations import init_db, make_engine

        target = Path(args.path or settings.root / "templates" / "Summer_Hunter_2027_CRM.xlsx")
        with init_db(make_engine("sqlite://"))() as empty:
            export_workbook(CRM(empty, None, settings.timezone), target, settings.timezone)
        print(target)
        return 0
    if args.action == "import":
        try:
            report = sync.import_file(settings, app.sessions, Path(args.target or sync.workbook_path(settings)),
                                      prefer_excel=args.prefer_excel, dry_run=args.dry_run)
        except WorkbookError as exc:
            print(f"Refused: {exc}")
            return 2
        print(report.summary())
        for line in report.applied + report.errors + report.ignored:
            print(" ", line)
        for c in report.conflicts:
            print(f"  CONFLICT {c.record} {c.field}: workbook={c.workbook!r} database={c.database!r}")
        if report.conflicts:
            print("Nothing was applied for conflicting rows. Re-run with --prefer-excel to keep the workbook values, "
                  "or `crm export` to keep the database.")
        return 1 if report.conflicts or report.errors else 0
    with app.sessions() as session:
        crm = CRM(session, settings.data_dir, settings.timezone)
        try:
            if args.action == "summary":
                _print(metrics.compute(crm).__dict__)
            elif args.action == "list":
                for a in crm.applications():
                    print(f"{app_code(a.id)}  {a.status:<18} {a.opportunity.company_name[:28]:<28} {a.opportunity.title[:60]}")
            elif args.action == "followups":
                for r in followups.all_suggestions(crm):
                    print(f"[{r.kind}] {r.text}")
            elif args.action == "track":
                opp = Repository(session).opportunity(int(args.target))
                if not opp:
                    print("Opportunity not found.")
                    return 1
                a, created = crm.track(opp, "cli")
                print(f"{app_code(a.id)} {'created' if created else 'already tracked'}")
            elif args.action == "applied":
                opp = Repository(session).opportunity(int(args.target))
                if not opp:
                    print("Opportunity not found.")
                    return 1
                applied_on = parse_date(args.date, "date") if args.date else None   # validated before any file is copied
                a = crm.confirm_applied(opp, "cli", applied_on)
                Repository(session).record_feedback(opp, "applied", source="cli", data_dir=settings.data_dir,
                                                    timezone=settings.timezone)
                print(f"{app_code(a.id)} APPLIED on {a.date_applied}")
            elif args.action == "status":
                a = crm.application(parse_app_id(args.target))
                if not a:
                    print("Application not found.")
                    return 1
                crm.set_status(a, args.value, "cli", force=args.force)
                print(f"{app_code(a.id)} -> {a.status}")
        except (ValidationError, TransitionError, ValueError) as exc:
            print(f"Refused: {exc}")
            return 2
        session.commit()
    return 0


def cmd_fx(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.engine import fx

    with app.sessions() as session:
        if args.action == "refresh":
            _print(fx.refresh(session, app.settings.compensation))
            session.commit()
        table = fx.load(session, app.settings.compensation)
        for code in sorted(table.rates):
            r = table.rates[code]
            print(f"{code} {r.per_eur:>14.4f} per EUR  {r.rate_date or '-'}  {r.source}")
    return 0


def cmd_os(app: App, args) -> int:  # noqa: ANN001
    """Summer Hunter OS: API server, password, networking refresh, sync with the hosted copy."""
    import getpass
    import os
    import secrets

    from summer_hunter.os import security

    env_path = app.settings.root / ".env"
    if args.action == "set-password":
        pw = getpass.getpass("New Summer Hunter OS password (10+ characters): ")
        if pw != getpass.getpass("Repeat: "):
            print("Passwords differ.")
            return 2
        lines = [l for l in env_path.read_text().splitlines() if not l.startswith(("SH_OS_PASSWORD_HASH=", "SH_OS_SECRET="))] \
            if env_path.exists() else []
        lines += [f"SH_OS_PASSWORD_HASH={security.hash_password(pw)}",
                  f"SH_OS_SECRET={os.environ.get('SH_OS_SECRET') or secrets.token_urlsafe(48)}"]
        env_path.write_text("\n".join(lines) + "\n")
        env_path.chmod(0o600)
        print(f"Saved to {env_path} (mode 600).")
        return 0
    if args.action == "serve":
        import uvicorn

        from summer_hunter.os.api import create_app

        uvicorn.run(create_app(app.settings, app.sessions, hosted=False), host="127.0.0.1", port=args.port, log_level="warning")
        return 0
    if args.action == "networking":
        from summer_hunter.os import networking as net
        from summer_hunter.utils.settings import load_yaml

        cfg = load_yaml(app.settings.config_dir / "networking.yaml")
        with app.sessions() as session:
            out = net.import_public_contacts(session)
            out["scored"] = net.recompute(session, cfg)
            session.commit()
        _print(out)
        return 0
    if args.action == "sync":
        from summer_hunter.os import sync

        url, token = os.environ.get("SH_OS_HOSTED_URL"), os.environ.get("SH_SYNC_TOKEN")
        if not url or not token:
            print("Set SH_OS_HOSTED_URL and SH_SYNC_TOKEN in .env first (see docs/OS.md).")
            return 2
        out = sync.run(app.settings, app.sessions, url, token)
        _print({"applied": out["applied"], "failed": out["failed"], "tables": len(out["pushed"])})
        return 1 if out["failed"] else 0
    return 2


def cmd_backup(app: App, args) -> int:  # noqa: ANN001
    print(backup_sqlite(app.db_path, app.settings.data_dir / "backups"))
    return 0


def cmd_status(app: App, args) -> int:  # noqa: ANN001
    with app.sessions() as session:
        repo = Repository(session)
        run = repo.last_run(successful_only=False)
        _print({"settings": app.settings.public_summary(), "counts": repo.counts(),
                "last_run": {"id": run.id, "trigger": run.trigger, "status": run.status, "started_at": run.started_at,
                             "finished_at": run.finished_at, "dry_run": run.dry_run} if run else None,
                "llm_spend_7d": repo.usage_summary(7), "last_digest": repo.get_kv("last_digest_at")})
    return 0


def cmd_launchd(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.scheduler import launchd

    exe = Path(sys.executable).parent / "summer-hunter"
    log_dir = app.settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    labels = [launchd.NIGHT_LABEL] + ([launchd.BOT_LABEL, launchd.WEB_LABEL] if args.with_bot else [])
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    if args.action == "install":
        for label in labels:
            data = {launchd.NIGHT_LABEL: launchd.night_plist, launchd.BOT_LABEL: launchd.bot_plist,
                    launchd.WEB_LABEL: launchd.web_plist}[label](exe, app.settings.root, log_dir)
            path = launchd.write_plist(data, label)
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
            res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], capture_output=True, text=True)
            print(f"{label}: {'loaded' if res.returncode == 0 else 'bootstrap failed: ' + res.stderr.strip()} ({path})")
    elif args.action == "uninstall":
        for label in (launchd.NIGHT_LABEL, launchd.BOT_LABEL, launchd.WEB_LABEL):
            path = launchd.AGENTS_DIR / f"{label}.plist"
            if path.exists():
                subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
                path.unlink()
                print(f"{label}: removed")
    else:
        for label in (launchd.NIGHT_LABEL, launchd.BOT_LABEL, launchd.WEB_LABEL):
            res = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True)
            state = next((l.strip() for l in res.stdout.splitlines() if l.strip().startswith("state =")), "not loaded")
            print(f"{label}: {state}")
        tz = subprocess.run(["readlink", "/etc/localtime"], capture_output=True, text=True).stdout.strip()
        print(f"System time zone: {tz or 'unknown'} (launchd fires on local time; expected Europe/Paris)")
        if shutil.which("pmset"):
            sched = subprocess.run(["pmset", "-g", "sched"], capture_output=True, text=True).stdout.strip()
            print("Wake schedule (pmset -g sched):\n" + (sched or "none"))
    return 0


def cmd_web(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.web.server import create_app
    from summer_hunter.web.tunnel import serve

    if not app.settings.telegram_configured:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (the Mini App authenticates through Telegram).")
        return 2
    serve(create_app, app.settings, app.sessions, port=args.port)
    return 0


def cmd_llm_check(app: App, args) -> int:  # noqa: ANN001
    from summer_hunter.integrations.anthropic_client import LLMError, NullProvider

    provider = app.hunter.provider
    if isinstance(provider, NullProvider):
        print("LLM disabled: set ANTHROPIC_API_KEY in .env (provider: anthropic in config/llm.yaml).")
        return 2
    if not args.yes:
        print("This makes one small paid API call. Re-run with --yes to confirm.")
        return 2
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    try:
        resp = provider.complete_json("Reply with JSON.", "Return {\"ok\": true}.", schema, 1000)
    except LLMError as exc:
        print(f"LLM check failed: {exc}")
        return 1
    print(f"LLM OK: model {resp.model}, {resp.input_tokens} in / {resp.output_tokens} out tokens, payload {resp.data}")
    return 0


# Parser ----------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="summer-hunter", description="Summer Hunter 2027: internship intelligence agent.")
    p.add_argument("--quiet", action="store_true", help="log to file only")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and validate configuration").set_defaults(fn=cmd_init)
    r = sub.add_parser("run", help="one full collection + evaluation cycle now")
    r.add_argument("--dry-run", action="store_true", help="collect and evaluate without persisting anything")
    r.add_argument("--source", action="append", help="limit to a source, e.g. greenhouse:stripe or greenhouse")
    r.add_argument("--notify", action="store_true", help="send the Telegram digest afterwards")
    r.set_defaults(fn=cmd_run)
    ev = sub.add_parser("evaluate", help="(re)evaluate stored opportunities")
    ev.add_argument("--force", action="store_true", help="re-evaluate everything, not only changed listings")
    ev.set_defaults(fn=cmd_evaluate)
    sub.add_parser("night", help="scheduled entry point (night window loop, then digest)").set_defaults(fn=cmd_night)
    cu = sub.add_parser("catch-up", help="run a missed cycle now and send the digest")
    cu.add_argument("--dry-run", action="store_true")
    cu.add_argument("--force", action="store_true", help="send a digest even if one went out today")
    cu.set_defaults(fn=cmd_catch_up)
    dg = sub.add_parser("digest", help="send (or print with --dry-run) the morning digest")
    dg.add_argument("--dry-run", action="store_true")
    dg.add_argument("--force", action="store_true")
    dg.set_defaults(fn=cmd_digest)
    b = sub.add_parser("bot", help="run the Telegram bot (long polling)")
    b.add_argument("--minutes", type=float, help="stop after N minutes (default: run forever)")
    b.set_defaults(fn=cmd_bot)
    ts = sub.add_parser("telegram-setup", help="check the bot token and discover your chat id")
    ts.add_argument("--test", action="store_true", help="send a test message to TELEGRAM_CHAT_ID")
    ts.set_defaults(fn=cmd_telegram_setup)
    ls = sub.add_parser("list", help="list opportunities by priority")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--decision", choices=["ELIGIBLE", "NEEDS_VERIFICATION", "REJECTED", "eligible", "needs_verification", "rejected"])
    ls.add_argument("--user-status", choices=["new", "saved", "dismissed", "applied"])
    ls.add_argument("--all", action="store_true", help="include dismissed and applied")
    ls.set_defaults(fn=cmd_list)
    sh = sub.add_parser("show", help="full explanation of one opportunity")
    sh.add_argument("id", type=int)
    sh.set_defaults(fn=cmd_show)
    ap = sub.add_parser("add-post", help="ingest a LinkedIn post you found (text pasted to a file or stdin)")
    ap.add_argument("--url", required=True)
    ap.add_argument("--text-file", required=True, help="path, or - for stdin")
    ap.add_argument("--author")
    ap.add_argument("--author-url")
    ap.add_argument("--headline", help="author's headline, e.g. 'Head of Solutions Engineering at Acme'")
    ap.add_argument("--company")
    ap.add_argument("--location")
    ap.add_argument("--title", help="exact role title if the post states it")
    ap.set_defaults(fn=cmd_add_post)
    aj = sub.add_parser("add-job", help="ingest a job you found anywhere (e.g. a LinkedIn job page you opened)")
    aj.add_argument("--url", required=True)
    aj.add_argument("--title", required=True)
    aj.add_argument("--company", required=True)
    aj.add_argument("--location")
    aj.add_argument("--text-file", required=True, help="description text: path, or - for stdin")
    aj.set_defaults(fn=cmd_add_job)
    sub.add_parser("sources", help="configured sources and their health").set_defaults(fn=cmd_sources)
    sub.add_parser("probe-boards", help="check every watchlist ATS endpoint (no detail fetches)").set_defaults(fn=cmd_probe_boards)
    sf = sub.add_parser("safety", help="France Diplomatie advisories")
    sf.add_argument("action", choices=["refresh", "show", "set"])
    sf.add_argument("countries", nargs="*")
    sf.add_argument("--level")
    sf.add_argument("--note")
    sf.add_argument("--url")
    sf.set_defaults(fn=cmd_safety)
    pr = sub.add_parser("preferences", help="learned preferences")
    pr.add_argument("action", choices=["show", "reset", "recompute"])
    pr.set_defaults(fn=cmd_preferences)
    fb = sub.add_parser("feedback", help="record feedback from the terminal")
    fb.add_argument("id", type=int)
    fb.add_argument("action", choices=["save", "dismiss", "applied", "reason"])
    fb.add_argument("--reason", choices=["cold", "role", "company", "country", "other"])
    fb.set_defaults(fn=cmd_feedback)
    cr = sub.add_parser("crm", help="application CRM: export/import the Excel workbook, track, statuses")
    cr.add_argument("action", choices=["summary", "list", "followups", "export", "import", "template", "track", "applied",
                                       "status"])
    cr.add_argument("target", nargs="?", help="opportunity id (track/applied), APP-id (status) or workbook (import)")
    cr.add_argument("value", nargs="?", help="new status (status)")
    cr.add_argument("--path", help="output path (export/template)")
    cr.add_argument("--date", help="application date YYYY-MM-DD (applied)")
    cr.add_argument("--prefer-excel", action="store_true", help="on conflict, keep the workbook values")
    cr.add_argument("--dry-run", action="store_true", help="import: show what would change")
    cr.add_argument("--force", action="store_true", help="status: allow a deliberate backward correction")
    cr.set_defaults(fn=cmd_crm)
    osp = sub.add_parser("os", help="Summer Hunter OS: serve the API, set the password, refresh networking, sync")
    osp.add_argument("action", choices=["serve", "set-password", "networking", "sync"])
    osp.add_argument("--port", type=int, default=8800)
    osp.set_defaults(fn=cmd_os)
    fxp = sub.add_parser("fx", help="exchange rates used by the compensation engine (ECB, then fallback)")
    fxp.add_argument("action", choices=["show", "refresh"])
    fxp.set_defaults(fn=cmd_fx)
    sub.add_parser("backup", help="snapshot the SQLite database").set_defaults(fn=cmd_backup)
    sub.add_parser("status", help="runs, counts, spend").set_defaults(fn=cmd_status)
    ld = sub.add_parser("launchd", help="install/uninstall/status of the macOS LaunchAgents")
    ld.add_argument("action", choices=["install", "uninstall", "status"])
    ld.add_argument("--with-bot", action="store_true", help="also keep the Telegram bot and Mini App running")
    ld.set_defaults(fn=cmd_launchd)
    wb = sub.add_parser("web", help="run the Telegram Mini App (local server + HTTPS tunnel)")
    wb.add_argument("--port", type=int, default=8787)
    wb.set_defaults(fn=cmd_web)
    lc = sub.add_parser("llm-check", help="verify the Anthropic key with one tiny call")
    lc.add_argument("--yes", action="store_true")
    lc.set_defaults(fn=cmd_llm_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    setup_logging(settings.data_dir / "logs", settings.log_level, settings.secret_values(), console=not args.quiet)
    app = App(settings)
    return int(args.fn(app, args) or 0)


if __name__ == "__main__":
    sys.exit(main())
