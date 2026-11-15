"""Telegram-бот: чеки из чата ДДС → карточка на подтверждение → операция в TrueStats.

Запуск: python bot.py   (настройки в .env рядом)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

import fx
import receipt_parser as rp
from rules import ALL, OZON_ALL, STORE_ALIASES, WB_ALL
from truestats_client import TrueStatsClient, TrueStatsError

load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_IDS = {int(x) for x in os.getenv("TELEGRAM_CHAT_ID", "").replace(";", ",").split(",") if x.strip()}
APPROVERS = {int(x) for x in os.getenv("APPROVER_USER_IDS", "").replace(";", ",").split(",") if x.strip()}
DRY_RUN = os.getenv("DRY_RUN", "1") not in ("0", "false", "False", "")
BILL_NAME = os.getenv("TRUESTATS_BILL", "Основной счёт")
DATA_DIR = Path(__file__).with_name("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "state.db"

router = Router()
ts: TrueStatsClient | None = None
if os.getenv("TRUESTATS_EMAIL") and os.getenv("TRUESTATS_PASSWORD"):
    ts = TrueStatsClient(os.environ["TRUESTATS_EMAIL"], os.environ["TRUESTATS_PASSWORD"])

# ---------- хранилище ----------

def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS pending(
        key TEXT PRIMARY KEY, chat_id INTEGER, src_msg_id INTEGER, card_msg_id INTEGER,
        payload TEXT, status TEXT, created_at TEXT, ts_id INTEGER, approved_by INTEGER)"""
    )
    c.execute("CREATE TABLE IF NOT EXISTS seen(file_uid TEXT PRIMARY KEY, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS media_groups(group_id TEXT PRIMARY KEY, caption TEXT)")
    return c


def save_pending(key: str, chat_id: int, src_msg_id: int, card_msg_id: int, payload: dict[str, Any], status: str = "pending") -> None:
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending(key,chat_id,src_msg_id,card_msg_id,payload,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (key, chat_id, src_msg_id, card_msg_id, json.dumps(payload, ensure_ascii=False), status, dt.datetime.now().isoformat()),
        )


def get_pending(key: str) -> tuple[dict[str, Any], str] | None:
    with db() as c:
        row = c.execute("SELECT payload,status FROM pending WHERE key=?", (key,)).fetchone()
    return (json.loads(row[0]), row[1]) if row else None


def set_status(key: str, status: str, ts_id: int | None = None, approved_by: int | None = None) -> None:
    with db() as c:
        c.execute("UPDATE pending SET status=?, ts_id=COALESCE(?,ts_id), approved_by=COALESCE(?,approved_by) WHERE key=?", (status, ts_id, approved_by, key))


def already_seen(file_uid: str) -> bool:
    with db() as c:
        if c.execute("SELECT 1 FROM seen WHERE file_uid=?", (file_uid,)).fetchone():
            return True
        c.execute("INSERT INTO seen VALUES(?,?)", (file_uid, dt.datetime.now().isoformat()))
    return False


# ---------- карточка ----------

def fmt_amount(v: float | None, cur: str = "RUB") -> str:
    if v is None:
        return "?"
    s = f"{v:,.2f}".replace(",", " ").replace(".00", "")
    return f"{s} {'₽' if cur == 'RUB' else cur}"


def stores_label(stores: list[str]) -> str:
    if not stores:
        return "?"
    if sorted(stores) == sorted(ALL):
        return "все 12 магазинов"
    if sorted(stores) == sorted(WB_ALL):
        return "все магазины WB (7)"
    if sorted(stores) == sorted(OZON_ALL):
        return "все магазины Ozon (5)"
    return ", ".join(stores)


def render_card(p: dict[str, Any], status: str = "pending", extra: str = "") -> str:
    d = p.get("date")
    try:
        d_h = dt.date.fromisoformat(d).strftime("%d.%m.%Y") if d else "?"
    except ValueError:
        d_h = str(d)
    lines = [
        "<b>Операция в TrueStats</b>" + (" <i>(тест, без записи)</i>" if DRY_RUN else ""),
        f"Дата: <b>{d_h}</b>",
        f"Сумма: <b>{fmt_amount(p.get('amount'), p.get('currency', 'RUB'))}</b>",
        f"Статья: <b>{p.get('article') or '?'}</b>",
        f"Назначение: {p.get('purpose') or '?'}",
        f"Магазины: {stores_label(p.get('stores') or [])}",
    ]
    if p.get("amount_orig") and p.get("currency_orig"):
        lines.append(f"Исходно: {fmt_amount(p['amount_orig'], p['currency_orig'])}, курс {p.get('fx_rate')} ({p.get('fx_source')})")
    if p.get("recipient"):
        lines.append(f"Получатель на чеке: {p['recipient']}")
    if p.get("inherited_caption"):
        lines.append(f"↩️ Подписи не было, взял от предыдущего чека: «{p['inherited_caption']}»")
    w = [x for x in (p.get("warnings") or []) if x]
    if w:
        lines.append("")
        lines.append("⚠️ " + "\n⚠️ ".join(w))
    if status == "done":
        lines.append("")
        lines.append("✅ <b>Внесено в TrueStats</b>" + (f" (id {p.get('ts_id')})" if p.get("ts_id") else ""))
    elif status == "skipped":
        lines.append("")
        lines.append("⏭ Пропущено")
    elif status == "error":
        lines.append("")
        lines.append("❌ Ошибка: " + (extra or "см. лог"))
    if extra and status == "pending":
        lines.append("")
        lines.append(extra)
    return "\n".join(lines)


def kb(key: str, ready: bool) -> InlineKeyboardMarkup:
    rows = []
    if ready:
        rows.append([InlineKeyboardButton(text="✅ Внести", callback_data=f"ok:{key}"), InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip:{key}")])
    else:
        rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip:{key}")])
    rows.append([InlineKeyboardButton(text="✏️ Как исправить", callback_data=f"help:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_ready(p: dict[str, Any]) -> bool:
    return bool(p.get("is_receipt") and p.get("amount") and p.get("date") and p.get("article") and p.get("stores") and p.get("currency", "RUB") == "RUB")


# ---------- обработка входящих ----------

async def file_bytes(bot: Bot, file_id: str) -> bytes:
    f = await bot.get_file(file_id)
    buf = await bot.download_file(f.file_path)
    return buf.read()


def chat_allowed(m: Message) -> bool:
    return not CHAT_IDS or m.chat.id in CHAT_IDS


def can_approve(user_id: int) -> bool:
    return not APPROVERS or user_id in APPROVERS


@router.message(Command("id"))
async def cmd_id(m: Message) -> None:
    await m.reply(f"chat_id: <code>{m.chat.id}</code>\nuser_id: <code>{m.from_user.id if m.from_user else '?'}</code>")


@router.message(Command("ping"))
async def cmd_ping(m: Message) -> None:
    mode = "тест (DRY_RUN)" if DRY_RUN else "боевой"
    tsinfo = "TrueStats: не настроен" if ts is None else "TrueStats: настроен"
    await m.reply(f"Я тут. Режим: {mode}. {tsinfo}. Chat: {m.chat.id}")


@router.message(Command("сверка", "sverka", "check"))
async def cmd_check(m: Message) -> None:
    if not chat_allowed(m):
        return
    today = dt.date.today()
    with db() as c:
        rows = c.execute("SELECT payload,status FROM pending WHERE created_at LIKE ?", (today.isoformat() + "%",)).fetchall()
    done = [json.loads(r[0]) for r in rows if r[1] == "done"]
    pend = [json.loads(r[0]) for r in rows if r[1] == "pending"]
    txt = [f"<b>Сверка за {today.strftime('%d.%m')}</b>", f"Внесено ботом: {len(done)} на {fmt_amount(sum(x.get('amount') or 0 for x in done))}", f"Ждут подтверждения: {len(pend)}"]
    if ts is not None:
        try:
            ops = await ts.list_operations(today, today)
            total = sum(abs(float(o.get("amount") or 0)) for o in ops)
            txt.append(f"В TrueStats за сегодня: {len(ops)} операций на {fmt_amount(total)}")
        except Exception as e:  # noqa: BLE001
            txt.append(f"TrueStats недоступен: {e}")
    await m.reply("\n".join(txt))


@router.message(Command("внести", "vnesti", "add"))
async def cmd_add(m: Message, bot: Bot) -> None:
    """Ответ на старый чек командой /внести — бот обработает чек из цитируемого сообщения.
    Текст после команды заменяет подпись: /внести Уборщица, все магазины"""
    if not chat_allowed(m):
        return
    src = m.reply_to_message
    log.info("/внести: reply_to=%s external=%s", bool(src), bool(getattr(m, "external_reply", None)))
    if src is not None:
        log.info("/внести src: photo=%s doc=%s caption=%r", bool(src.photo), bool(src.document), src.caption)
    if not src or not (src.photo or src.document):
        await m.reply("Ответь этой командой на сообщение с чеком (фото или PDF).")
        return
    override = (m.text or "").split(maxsplit=1)
    caption = override[1].strip() if len(override) > 1 else (src.caption or "").strip()
    await process_receipt(src, bot, caption=caption, reply_to=m, force=True)


@router.message(F.photo | F.document)
async def on_receipt(m: Message, bot: Bot) -> None:
    if not chat_allowed(m):
        return
    await process_receipt(m, bot, caption=(m.caption or "").strip(), reply_to=m)


CONTINUATION_RE = re.compile(r"^\s*(туда\s*же|тоже|то\s*же|так\s*же|также|ещё|еще|\+{1,3}|same|тот\s*же)(?![а-яёa-z])", re.I)
INHERIT_HOURS = 6


def is_continuation(caption: str) -> bool:
    """Пустая подпись или «туда же»/«тоже»/«+» = чек относится к предыдущему."""
    c = (caption or "").strip()
    return not c or bool(CONTINUATION_RE.match(c))


def last_caption_for_chat(chat_id: int, hours: int = INHERIT_HOURS) -> str | None:
    """Подпись последнего чека в этом чате за N часов (пропущенные не считаем)."""
    since = (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat()
    with db() as c:
        rows = c.execute(
            "SELECT payload FROM pending WHERE chat_id=? AND status!='skipped' AND created_at>? ORDER BY created_at DESC LIMIT 10",
            (chat_id, since),
        ).fetchall()
    for (payload,) in rows:
        cap = (json.loads(payload).get("caption") or "").strip()
        if cap and not is_continuation(cap):
            return cap
    return None


async def auto_convert(p: dict[str, Any]) -> None:
    """Чек в сомах → сумма в рублях по курсу НБКР. Исходник остаётся в карточке, число в ответе всё равно перебивает."""
    cur = (p.get("currency") or "RUB").upper()
    if cur == "RUB" or not p.get("amount"):
        return
    fxr = await fx.convert_to_rub(float(p["amount"]), cur)
    if not fxr:
        p["warnings"] = [w for w in (p.get("warnings") or []) if "рубл" not in w]
        p["warnings"].append(f"Сумма в {cur}: курс не удалось получить, ответь суммой в рублях")
        return
    p["amount_orig"], p["currency_orig"] = p["amount"], cur
    p["fx_rate"], p["fx_source"] = fxr["rate"], fxr["source"]
    p["amount"], p["currency"] = float(fxr["amount_rub"]), "RUB"
    p["warnings"] = [w for w in (p.get("warnings") or []) if "рубл" not in w]
    log.info("%s→RUB: %s %s → %s ₽ по %s (%s)", cur, p["amount_orig"], cur, p["amount"], fxr["rate"], fxr["source"])


async def process_receipt(m: Message, bot: Bot, *, caption: str, reply_to: Message, force: bool = False) -> None:
    # медиагруппа: подпись есть только у первого фото
    if m.media_group_id:
        with db() as c:
            if caption:
                c.execute("INSERT OR REPLACE INTO media_groups VALUES(?,?)", (m.media_group_id, caption))
            else:
                row = c.execute("SELECT caption FROM media_groups WHERE group_id=?", (m.media_group_id,)).fetchone()
                if row:
                    caption = row[0]
    if m.photo:
        ph = m.photo[-1]
        file_id, uid, name = ph.file_id, ph.file_unique_id, "photo.jpg"
    else:
        doc = m.document
        name = doc.file_name or "file"
        mime = doc.mime_type or ""
        if not (mime.startswith("image/") or mime == "application/pdf" or name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp"))):
            return
        file_id, uid = doc.file_id, doc.file_unique_id
    if already_seen(uid) and not force:
        return
    inherited = None
    if is_continuation(caption):
        inherited = last_caption_for_chat(m.chat.id)
        if inherited:
            log.info("Подписи нет / «туда же», беру от предыдущего чека: %r", inherited)
            caption = inherited
    status_msg = await reply_to.reply("Читаю чек…")
    try:
        data = await file_bytes(bot, file_id)
        parsed = await rp.parse(name, data, caption)
    except Exception as e:  # noqa: BLE001
        log.exception("parse failed")
        await status_msg.edit_text(f"Не смог разобрать: {e}")
        return
    p = parsed.to_dict()
    if inherited:
        p["inherited_caption"] = inherited
    await auto_convert(p)
    if not parsed.is_receipt and not p.get("amount"):
        await status_msg.edit_text("Не вижу суммы траты, пропускаю. Если это всё же оплата, перешли ещё раз с подписью.")
        return
    p["is_receipt"] = True
    key = f"{m.chat.id}:{m.message_id}:{status_msg.message_id}"
    ready = is_ready(p)
    text = render_card(p) if ready else render_card(p, extra="Не хватает данных. Ответь на эту карточку: сумма в рублях / статья / магазины — и я пересоберу.")
    await status_msg.edit_text(text, reply_markup=kb(key, ready))
    save_pending(key, m.chat.id, m.message_id, status_msg.message_id, p)


@router.message(F.reply_to_message, F.text)
async def on_correction(m: Message, bot: Bot) -> None:
    """Ответ на карточку текстом = правка. Понимает: число (сумма в рублях), «статья: …», «магазины: …», «дата: …», «назначение: …»."""
    if not chat_allowed(m):
        return
    me = await bot.get_me()
    r = m.reply_to_message
    if not r or not r.from_user or r.from_user.id != me.id:
        return
    with db() as c:
        row = c.execute("SELECT key,payload,status FROM pending WHERE chat_id=? AND card_msg_id=?", (m.chat.id, r.message_id)).fetchone()
    if not row:
        return
    key, p, status = row[0], json.loads(row[1]), row[2]
    if status != "pending":
        await m.reply("Эта карточка уже закрыта.")
        return
    changed = apply_correction(p, m.text or "")
    if not changed:
        await m.reply("Не понял правку. Примеры: <code>9500</code> (сумма в ₽), <code>статья: Грузчики</code>, <code>магазины: все магазины озон</code>, <code>дата: 16.10.2026</code>, <code>назначение: Жоомарт, аванс</code>")
        return
    ready = is_ready(p)
    save_pending(key, m.chat.id, int(key.split(":")[1]), r.message_id, p)
    await r.edit_text(render_card(p) if ready else render_card(p, extra="Ещё не хватает данных."), reply_markup=kb(key, ready))
    await m.reply("Обновил карточку" + (", можно вносить." if ready else "."))


def apply_correction(p: dict[str, Any], text: str) -> bool:
    t = text.strip()
    low = t.lower()
    changed = False
    num = t.replace(" ", "").replace("₽", "").replace("р", "").replace(",", ".")
    try:
        v = float(num)
        p["amount"], p["currency"] = v, "RUB"
        p["warnings"] = [w for w in p.get("warnings", []) if "рубл" not in w]
        return True
    except ValueError:
        pass
    for part in [x.strip() for x in t.replace("\n", ";").split(";") if x.strip()]:
        if ":" not in part:
            continue
        k, v = [x.strip() for x in part.split(":", 1)]
        kl = k.lower()
        if kl.startswith("стат"):
            p["article"] = v
            changed = True
        elif kl.startswith("маг") or kl.startswith("каб"):
            alias = v.lower().strip()
            if alias in STORE_ALIASES:
                p["stores"] = list(STORE_ALIASES[alias])
            else:
                names = [s.strip() for s in v.split(",") if s.strip()]
                known = {a.lower(): a for a in ALL}
                p["stores"] = [known.get(n.lower(), n) for n in names]
            changed = True
        elif kl.startswith("дат"):
            d = rp._norm_date(v)  # noqa: SLF001
            if d:
                p["date"] = d
                changed = True
        elif kl.startswith("назн") or kl.startswith("коммент"):
            p["purpose"] = v
            changed = True
        elif kl.startswith("сум"):
            try:
                p["amount"] = float(v.replace(" ", "").replace(",", "."))
                p["currency"] = "RUB"
                changed = True
            except ValueError:
                pass
    if changed:
        p["warnings"] = [w for w in p.get("warnings", []) if not w.startswith("Не понял")]
    return changed


# ---------- кнопки ----------

@router.callback_query(F.data.startswith(("ok:", "skip:", "help:")))
async def on_button(cq: CallbackQuery, bot: Bot) -> None:
    action, key = cq.data.split(":", 1)
    row = get_pending(key)
    if not row:
        await cq.answer("Карточка не найдена", show_alert=True)
        return
    p, status = row
    if status != "pending":
        await cq.answer("Уже обработано")
        return
    if action == "help":
        await cq.answer()
        await cq.message.reply("Ответь на карточку текстом:\n<code>9500</code> — сумма в рублях\n<code>статья: Грузчики</code>\n<code>магазины: все магазины озон</code> или список через запятую\n<code>дата: 16.10.2026</code>\n<code>назначение: Жоомарт, аванс</code>")
        return
    if not can_approve(cq.from_user.id):
        await cq.answer("Подтверждать может только ответственный", show_alert=True)
        return
    if action == "skip":
        set_status(key, "skipped", approved_by=cq.from_user.id)
        await cq.message.edit_text(render_card(p, "skipped"))
        await cq.answer("Пропущено")
        return
    if not is_ready(p):
        await cq.answer("Сначала дополни карточку", show_alert=True)
        return
    await cq.answer("Вношу…")
    try:
        ts_id = await create_in_truestats(p)
    except Exception as e:  # noqa: BLE001
        log.exception("create failed")
        await cq.message.edit_text(render_card(p, "error", str(e)[:300]), reply_markup=kb(key, True))
        return
    p["ts_id"] = ts_id
    set_status(key, "done", ts_id=ts_id, approved_by=cq.from_user.id)
    save_pending(key, cq.message.chat.id, int(key.split(":")[1]), cq.message.message_id, p, status="done")
    await cq.message.edit_text(render_card(p, "done"))


async def create_in_truestats(p: dict[str, Any]) -> int | None:
    if DRY_RUN or ts is None:
        log.info("DRY_RUN: %s", json.dumps(p, ensure_ascii=False))
        return None
    d = await ts.load_dicts()
    cat_id = d.categories.get(p["article"])
    if cat_id is None:
        # мягкий поиск по началу/без регистра
        for name, cid in d.categories.items():
            if name.lower().replace(" ", "") == p["article"].lower().replace(" ", ""):
                cat_id = cid
                break
    if cat_id is None:
        raise TrueStatsError(f"В TrueStats нет статьи «{p['article']}». Доступные: {', '.join(sorted(d.categories))}")
    bill_id = d.bills.get(BILL_NAME) or next(iter(d.bills.values()), None)
    if bill_id is None:
        raise TrueStatsError("Не нашёл счёт в TrueStats")
    acc_ids = []
    missing = []
    for s in p["stores"]:
        a = d.account_by_name(s)
        (acc_ids.append(a.id) if a else missing.append(s))
    if missing:
        raise TrueStatsError(f"Не нашёл кабинеты: {', '.join(missing)}. Есть: {', '.join(a.name for a in d.accounts)}")
    # защита от дубля: та же дата, сумма, назначение
    date = dt.date.fromisoformat(p["date"])
    for o in await ts.list_operations(date, date):
        if abs(abs(float(o.get("amount") or 0)) - float(p["amount"])) < 0.01 and (o.get("description") or "").strip().lower() == p["purpose"].strip().lower():
            raise TrueStatsError("Такая операция уже есть в TrueStats (та же дата, сумма и назначение)")
    res = await ts.create_expense(amount=p["amount"], date=date, category_id=cat_id, bill_id=bill_id, description=p["purpose"], account_ids=acc_ids)
    rid = res.get("id") if isinstance(res, dict) else None
    try:
        return int(rid) if rid is not None else None
    except (TypeError, ValueError):
        return None


# ---------- запуск ----------

async def main() -> None:
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    if ts is not None and not DRY_RUN:
        try:
            await ts.login()
            await ts.load_dicts()
        except Exception:  # noqa: BLE001
            log.exception("TrueStats недоступен на старте, продолжаю; попробую при первом внесении")
    me = await bot.get_me()
    log.info("Бот @%s запущен. Чаты: %s. DRY_RUN=%s", me.username, CHAT_IDS or "все", DRY_RUN)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
