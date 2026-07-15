import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from app.formatter import split_message
from app.generate import generate_draft, regenerate_draft
from app.llm import draft_release_notes

log = logging.getLogger(__name__)


class EditState(StatesGroup):
    waiting_for_text = State()


def _review_kb(draft_id: int) -> InlineKeyboardMarkup:
    d = str(draft_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{d}"),
        InlineKeyboardButton(text="🔁 Перегенерировать", callback_data=f"rg:{d}"),
    ], [
        InlineKeyboardButton(text="✏️ Правка", callback_data=f"ed:{d}"),
        InlineKeyboardButton(text="🗑 Отмена", callback_data=f"cx:{d}"),
    ]])


async def send_for_review(bot, store, admin_chat_id, draft_id: int, text: str) -> None:
    chunks = split_message(text)
    for chunk in chunks[:-1]:
        await bot.send_message(admin_chat_id, chunk, parse_mode="HTML")
    msg = await bot.send_message(admin_chat_id, chunks[-1], parse_mode="HTML",
                                 reply_markup=_review_kb(draft_id))
    store.set_admin_msg(draft_id, msg.message_id)


def build_dispatcher(bot: Bot, store, settings) -> Dispatcher:
    dp = Dispatcher()

    def _is_admin(chat_id: int) -> bool:
        return chat_id == settings.admin_chat_id

    @dp.message(Command("release_draft"))
    async def cmd_release_draft(message: Message) -> None:
        if not _is_admin(message.chat.id):
            return
        if store.has_pending():
            await message.answer("Уже есть черновик на ревью. Заверши его сначала.")
            return
        res = await generate_draft(trigger="manual", store=store, github=bot._gh,
                                   get_prod_sha=bot._get_prod_sha, settings=settings,
                                   llm=draft_release_notes)
        if res["result"] == "drafted":
            await send_for_review(bot, store, settings.admin_chat_id, res["draft_id"], res["text"])
        elif res["result"] == "no_prod_sha":
            await message.answer("Не удалось получить prod SHA (/version недоступен).")
        elif res["result"] == "no_changes":
            await message.answer("С прошлой публикации нет задеплоенных изменений.")
        else:
            await message.answer(f"Нет релиз-достойных изменений (найдено {res['commit_count']}).")

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not _is_admin(message.chat.id):
            return
        await message.answer(
            f"Маркер: {store.get_marker()}\n"
            f"Последняя публикация: {store.get_last_published_at()}\n"
            f"Черновик на ревью: {'да' if store.has_pending() else 'нет'}")

    @dp.callback_query(F.data.startswith("pub:"))
    async def on_publish(cb: CallbackQuery) -> None:
        did = int(cb.data.split(":")[1])
        d = store.get_draft(did)
        if not d or d["status"] != "pending":
            await cb.answer("Черновик неактуален.")
            return
        first = None
        for chunk in split_message(d["draft_text"]):
            sent = await bot.send_message(settings.channel_id, chunk, parse_mode="HTML")
            first = first or sent
        ok = store.publish(did, to_sha=d["to_sha"], channel_msg_id=first.message_id)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Опубликовано" if ok else "Уже опубликовано")

    @dp.callback_query(F.data.startswith("rg:"))
    async def on_regenerate(cb: CallbackQuery) -> None:
        did = int(cb.data.split(":")[1])
        await cb.answer("Генерирую заново...")
        text = await regenerate_draft(store=store, draft_id=did, settings=settings,
                                      llm=draft_release_notes)
        await send_for_review(bot, store, settings.admin_chat_id, did, text)

    @dp.callback_query(F.data.startswith("cx:"))
    async def on_cancel(cb: CallbackQuery) -> None:
        store.cancel(int(cb.data.split(":")[1]))
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Отменено")

    @dp.callback_query(F.data.startswith("ed:"))
    async def on_edit(cb: CallbackQuery, state: FSMContext) -> None:
        did = int(cb.data.split(":")[1])
        await state.set_state(EditState.waiting_for_text)
        await state.update_data(draft_id=did)
        await cb.answer()
        await cb.message.answer("Пришли новый текст поста ответным сообщением.")

    @dp.message(EditState.waiting_for_text)
    async def on_edit_text(message: Message, state: FSMContext) -> None:
        did = (await state.get_data())["draft_id"]
        escaped = html.escape(message.text or "", quote=False)
        store.set_draft_text(did, escaped)
        await state.clear()
        await send_for_review(bot, store, settings.admin_chat_id, did, escaped)

    return dp
