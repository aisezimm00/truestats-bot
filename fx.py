"""Курсы валют для автоконвертации чеков в рубли.

Источник: официальные курсы НБКР (https://www.nbkr.kg/XML/daily.xml): сомов за единицу USD/EUR/CNY/KZT/RUB.
Рубли считаем кроссом через сом: RUB = amount * KGS_per_unit(cur) / KGS_per_RUB.
Переопределение: KGS_PER_RUB=1.06 (сомов за рубль) и/или RUB_PER_USD=82.5, RUB_PER_EUR=... в .env.
Запасной источник: open.er-api.com (база RUB). Кэш на сутки в памяти.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re

import httpx

log = logging.getLogger("fx")
_cache: dict[str, tuple[dict[str, float], str]] = {}  # date -> ({cur: KGS за единицу}, источник)


async def _nbkr() -> tuple[dict[str, float], str] | None:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://www.nbkr.kg/XML/daily.xml")
            r.raise_for_status()
        xml = r.content.decode("windows-1251", errors="replace")
        rates: dict[str, float] = {"KGS": 1.0}
        for code, nominal, value in re.findall(r'ISOCode="([A-Z]{3})">\s*<Nominal>([\d,\.]+)</Nominal>\s*<Value>([\d,\.]+)</Value>', xml):
            rates[code] = float(value.replace(",", ".")) / float(nominal.replace(",", "."))
        d = re.search(r'Date="([^"]+)"', xml)
        if "RUB" not in rates:
            return None
        return rates, f"НБКР {d.group(1) if d else ''}".strip()
    except Exception as e:  # noqa: BLE001
        log.warning("НБКР недоступен: %s", e)
        return None


async def _erapi() -> tuple[dict[str, float], str] | None:
    """er-api даёт курсы к RUB; переводим в «сомов за единицу», чтобы формула была одна."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://open.er-api.com/v6/latest/RUB")
            r.raise_for_status()
        per_rub = r.json()["rates"]  # сколько валюты за 1 RUB
        kgs_per_rub = float(per_rub["KGS"])
        rates = {cur: kgs_per_rub / float(v) for cur, v in per_rub.items() if v}
        rates["RUB"] = kgs_per_rub
        rates["KGS"] = 1.0
        return rates, "er-api.com"
    except Exception as e:  # noqa: BLE001
        log.warning("er-api недоступен: %s", e)
        return None


async def rates_table() -> tuple[dict[str, float], str] | None:
    today = dt.date.today().isoformat()
    if today not in _cache:
        got = await _nbkr() or await _erapi()
        if not got:
            return None
        _cache[today] = got
    rates, src = _cache[today]
    rates = dict(rates)
    env = os.getenv("KGS_PER_RUB", "").strip().replace(",", ".")
    if env:
        try:
            rates["RUB"] = float(env)
            src = "курс из настроек"
        except ValueError:
            log.warning("KGS_PER_RUB задан криво: %r", env)
    return rates, src


async def rub_per_unit(currency: str) -> tuple[float, str] | None:
    """Сколько рублей за 1 единицу валюты + подпись источника."""
    cur = currency.upper()
    if cur == "RUB":
        return 1.0, "RUB"
    env = os.getenv(f"RUB_PER_{cur}", "").strip().replace(",", ".")
    if env:
        try:
            return float(env), "курс из настроек"
        except ValueError:
            log.warning("RUB_PER_%s задан криво: %r", cur, env)
    got = await rates_table()
    if not got:
        return None
    rates, src = got
    if cur not in rates:
        log.warning("Нет курса для %s в %s", cur, src)
        return None
    return rates[cur] / rates["RUB"], src


async def kgs_per_rub() -> tuple[float, str] | None:
    """Совместимость: сомов за 1 рубль."""
    got = await rates_table()
    return (got[0]["RUB"], got[1]) if got else None


async def convert_to_rub(amount: float, currency: str) -> dict | None:
    """Для любой не-рублёвой валюты вернёт {'amount_rub', 'rate', 'source'}; rate = рублей за единицу (для KGS: сомов за рубль, как раньше)."""
    cur = (currency or "RUB").upper()
    if cur == "RUB":
        return None
    got = await rub_per_unit(cur)
    if not got:
        return None
    r, src = got
    shown = round(1 / r, 4) if cur == "KGS" else round(r, 2)
    return {"amount_rub": round(amount * r), "rate": shown, "source": src}
