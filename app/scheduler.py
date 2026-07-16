import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot import send_for_review
from app.generate import generate_draft
from app.llm import draft_release_notes

log = logging.getLogger(__name__)


async def run_deploy_poll(*, store, github, get_prod_sha, settings, llm, send_review) -> str:
    """One poll tick. Returns a disposition string; raises on generate/send failure
    so the caller logs and retries next tick (last_seen left unchanged)."""
    prod = await get_prod_sha()
    if prod is None:
        return "no_prod_sha"
    if prod == store.get_last_seen_prod_sha():
        return "already_seen"
    if store.has_pending():
        return "pending_exists"
    res = await generate_draft(trigger="deploy", store=store, github=github,
                               get_prod_sha=get_prod_sha, settings=settings, llm=llm,
                               to_sha=prod)
    if res["result"] == "drafted":
        try:
            await send_review(res["draft_id"], res["text"])
        except Exception:
            store.cancel(res["draft_id"])   # roll out of pending so the next poll retries
            raise
    store.set_last_seen_prod_sha(prod)       # durable outcome reached for this SHA
    return res["result"]


def build_scheduler(bot, store, settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.schedule_tz)

    async def job() -> None:
        if store.has_pending():
            log.info("scheduled run skipped: pending draft exists")
            return
        try:
            res = await generate_draft(trigger="scheduled", store=store, github=bot._gh,
                                       get_prod_sha=bot._get_prod_sha, settings=settings,
                                       llm=draft_release_notes)
        except Exception:
            log.exception("scheduled generate_draft failed")
            await bot.send_message(settings.admin_chat_id, "Ошибка при сборке дайджеста, см. логи.")
            return
        if res["result"] == "drafted":
            await send_for_review(bot, store, settings.admin_chat_id, res["draft_id"], res["text"])
        elif res["result"] == "skipped":
            await bot.send_message(
                settings.admin_chat_id,
                f"Пропущено: {res['feature_count']} фич, накоплено с {store.get_last_published_at()}.")
        # no_changes / no_prod_sha: stay silent

    scheduler.add_job(job, CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.schedule_tz),
                      max_instances=1, misfire_grace_time=3600)
    return scheduler
