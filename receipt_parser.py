"""Чтение чека (картинка/PDF) + подпись → структура операции.

Два бэкенда:
  * anthropic API (ANTHROPIC_API_KEY) — основной;
  * Claude Code CLI (`claude -p`) — запасной, если ключа нет, а Claude Code на машине залогинен.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rules import ALL, Hint, build_purpose, parse_caption

log = logging.getLogger("parser")

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

SYSTEM = """Ты помощник финансиста. На входе скрин банковского перевода (или PDF-чек) и подпись из рабочего чата.
Верни СТРОГО JSON без пояснений:
{
 "amount": число (сумма перевода, без комиссии, положительное),
 "currency": "RUB" | "KGS" | "USD" | другое,
 "date": "YYYY-MM-DD" (дата исполнения перевода с чека; если на чеке нет года, считай текущий),
 "recipient": "как в чеке (ФИО/имя получателя) или null",
 "bank": "банк отправителя по оформлению скрина: Модульбанк / Тбанк / Optima / Сбер / другое / null",
 "is_receipt": true|false (есть ли на скрине потраченная сумма: чек, перевод, строка из истории операций, списание за подписку, скрин оплаты картой — всё это true; false только если суммы траты вообще нет),
 "notes": "короткое замечание, если что-то подозрительно (несколько сумм, нечитаемо, перевод НЕ исходящий и т.п.) или null"
}
Правила: сумму бери из крупной цифры «- 21 000,00 ₽» или «Сумма». Если на скрине Optima24 в сомах (KGS), currency = "KGS".
Подсказка по банкам: тёмный экран с «Исполнен, <дата>», «Платёжное поручение №…», «Со счёта: МаркетПлейс (•••• 6776)», «Перевод по номеру телефона» — это Модульбанк (не Тбанк).
Светлый PDF-чек «Квитанция» с жёлтым логотипом — Т-Банк. «Optima24» — Optima Bank (сомы).
Формат не важен: строка из истории операций, скрин списания за подписку (OpenAI, Adobe и т.п.), оплата картой — это тоже операция, is_receipt=true. Про то, что это «не чек», в notes НЕ пиши.
Дата: если написано «Вчера»/«Сегодня», посчитай от сегодняшней даты и молча подставь, замечание об этом не нужно.
В notes пиши только про реальные проблемы с суммой или читаемостью. Несовпадение банка с подписью в notes НЕ пиши.
Если суммы траты нет, is_receipt=false и остальные поля null."""


@dataclass
class Parsed:
    amount: float | None = None
    currency: str = "RUB"
    date: str | None = None  # ISO
    recipient: str | None = None
    bank: str | None = None
    is_receipt: bool = True
    notes: str | None = None
    # из подписи / правил
    article: str | None = None
    stores: list[str] = field(default_factory=list)
    purpose: str = ""
    caption: str = ""
    warnings: list[str] = field(default_factory=list)
    raw_model: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return bool(self.is_receipt and self.amount and self.date and self.article and self.stores and self.currency == "RUB")


# ---------- подготовка картинки ----------

def pdf_to_png(pdf_bytes: bytes) -> bytes:
    import fitz  # pymupdf

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    return pix.tobytes("png")


def _media_type(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".png") or data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if n.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


# ---------- бэкенды ----------

async def _ask_api(image: bytes, media_type: str, caption: str) -> dict[str, Any]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    msg = await client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode()}},
                    {"type": "text", "text": f"Подпись из чата: {caption or '(нет)'}\nСегодня: {dt.date.today().isoformat()}"},
                ],
            }
        ],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return _extract_json(text)


async def _ask_cli(image: bytes, media_type: str, caption: str) -> dict[str, Any]:
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("Нет ни ANTHROPIC_API_KEY, ни claude CLI")
    suffix = ".png" if media_type == "image/png" else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(image)
        path = f.name
    prompt = f"{SYSTEM}\n\nПрочитай изображение чека по пути {path} (используй инструмент Read). Подпись из чата: {caption or '(нет)'}. Сегодня: {dt.date.today().isoformat()}. Ответь только JSON."
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-p", prompt, "--output-format", "text", "--allowedTools", "Read",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI: {err.decode(errors='ignore')[:300]}")
        return _extract_json(out.decode(errors="ignore"))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"Модель не вернула JSON: {text[:200]}")
    return json.loads(m.group(0))


async def read_receipt(image: bytes, media_type: str, caption: str) -> dict[str, Any]:
    if os.getenv("ANTHROPIC_API_KEY"):
        return await _ask_api(image, media_type, caption)
    return await _ask_cli(image, media_type, caption)


# ---------- сборка ----------

async def parse(file_name: str, data: bytes, caption: str) -> Parsed:
    if file_name.lower().endswith(".pdf") or data[:4] == b"%PDF":
        data = pdf_to_png(data)
        file_name = "receipt.png"
    media_type = _media_type(file_name, data)
    hint: Hint = parse_caption(caption)
    p = Parsed(caption=caption or "")
    try:
        raw = await read_receipt(data, media_type, caption)
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка чтения чека")
        p.is_receipt = False
        p.warnings.append(f"Не смог прочитать чек: {e}")
        return p
    p.raw_model = raw
    p.is_receipt = bool(raw.get("is_receipt", True))
    try:
        p.amount = float(str(raw.get("amount")).replace(" ", "").replace(",", ".")) if raw.get("amount") is not None else None
    except ValueError:
        p.amount = None
    p.currency = (raw.get("currency") or "RUB").upper()
    p.date = _norm_date(raw.get("date"))
    p.recipient = raw.get("recipient")
    p.bank = raw.get("bank")
    p.notes = raw.get("notes")

    p.article = hint.article
    p.stores = hint.stores
    p.purpose = build_purpose(hint, fallback=caption)
    p.warnings.extend(hint.warnings)
    if p.notes and not re.search(r"не чек|не\s+является\s+чеком|история операций|истории операций|вчера|сегодня", str(p.notes), re.I):
        p.warnings.append(str(p.notes))
    if p.currency != "RUB":
        p.warnings.append(f"Сумма в {p.currency}: нужна сумма в рублях")
    if not p.date:
        p.warnings.append("Не понял дату с чека")
    if p.date and (dt.date.today() - dt.date.fromisoformat(p.date)).days > 45:
        p.warnings.append("Дата на чеке старше 45 дней, проверь")
    # банк из подписи в TrueStats не вносится; расхождение с чеком только логируем
    if hint.bank and p.bank and not _bank_match(hint.bank, p.bank):
        log.info("Банк в подписи «%s», модель видит «%s»", hint.bank, p.bank)
    return p


def _norm_date(v: Any) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s[: len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _bank_match(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    pairs = [("модуль", "модуль"), ("тбанк", "тбанк"), ("tbank", "тбанк"), ("тинькофф", "тбанк"), ("оптима", "optima"), ("optima", "optima"), ("сбер", "сбер"), ("альфа", "альфа")]
    for x, y in pairs:
        if x in a and (y in b or x in b):
            return True
    return False


def all_store_names() -> list[str]:
    return list(ALL)
