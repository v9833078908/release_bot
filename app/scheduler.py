import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.bot import send_for_review
from app.generate import generate_draft
from app.llm import draft_release_notes

log = logging.getLogger(__name__)


async def run_deploy_poll(*, store, github, get_prod_sha, settings, llm, send_review, notify,
                          disable_review=None) -> str:
    """One poll tick. Returns a disposition string; raises on generate/send failure
    so the caller logs and retries next tick (last_seen left unchanged)."""
    prod = await get_prod_sha()
    if prod is None:
        return "no_prod_sha"
    if prod == store.get_last_seen_prod_sha():
        return "already_seen"
    pending = store.get_pending()
    if pending is not None:
        # A draft still targeting the live prod build (or one mid-publish) is a
        # review legitimately in flight - leave it be.
        if pending["status"] == "publishing" or pending["to_sha"] == prod:
            return "pending_exists"
        # Prod moved past the draft's target: it can no longer be published
        # (publish_block_reason), so supersede it instead of blocking forever.
        # Its commits fold back into the next range (marker unmoved).
        store.cancel(pending["id"])
        if disable_review is not None:
            await disable_review(pending["admin_msg_id"])
    res = await generate_draft(trigger="deploy", store=store, github=github,
                               get_prod_sha=get_prod_sha, settings=settings, llm=llm,
                               to_sha=prod)
    if res["result"] == "drafted":
        try:
            await send_review(res["draft_id"], res["text"])
        except Exception:
            store.cancel(res["draft_id"])   # roll out of pending so the next poll retries
            raise
    elif res["result"] == "no_release_worthy":
        await notify(_format_missed(res))    # raises -> cursor not advanced -> retry next tick
    elif res["result"] == "no_user_facing":
        await notify(_format_no_user_facing(res))   # same contract: raises -> retry next tick
    store.set_last_seen_prod_sha(prod)       # durable outcome reached for this SHA
    return res["result"]


def _format_missed(res: dict) -> str:
    head = (f"⚠️ Задеплоено {res['raw_count']} коммит(ов) "
            f"({res['from_sha'][:8]}..{res['to_sha'][:8]}), релиз-достойных - 0.\n"
            "Возможно, есть изменения без conventional-commit префикса "
            "(или нужен новый FEATURE_PREFIXES). Проверь: /release_draft.\n\nКоммиты:")
    return head + "\n" + "\n".join(f"• {s}" for s in res["dropped"])


def _format_no_user_facing(res: dict) -> str:
    head = (f"🔍 Задеплоено {res['commit_count']} коммит(ов) "
            f"({res['from_sha'][:8]}..{res['to_sha'][:8]}), но пользовательских изменений нет - "
            "всё внутреннее (мониторинг, инфраструктура, операционные задачи). Пост не создан.\n\n"
            "Коммиты:")
    return head + "\n" + "\n".join(f"• {s}" for s in res["dropped"])


def build_scheduler(bot, store, settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.schedule_tz)

    async def deploy_job() -> None:
        async def _send(did, text):
            await send_for_review(bot, store, settings, did, text)

        async def _notify(text):
            await bot.send_message(settings.admin_chat_id, text)   # plain text, admin DM only

        async def _disable(msg_id):   # best-effort: strip buttons off a superseded review
            if msg_id is None:
                return
            try:
                await bot.edit_message_reply_markup(settings.admin_chat_id, msg_id, reply_markup=None)
            except Exception:
                log.debug("could not disable stale review message %s", msg_id, exc_info=True)
        try:
            await run_deploy_poll(store=store, github=bot._gh, get_prod_sha=bot._get_prod_sha,
                                  settings=settings, llm=draft_release_notes,
                                  send_review=_send, notify=_notify, disable_review=_disable)
        except Exception:
            log.exception("deploy_poll tick failed")

    scheduler.add_job(deploy_job, IntervalTrigger(seconds=settings.deploy_poll_seconds),
                      max_instances=1, coalesce=True)
    return scheduler
