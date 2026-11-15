"""Регрессия парсера на реальных чеках из экспорта Telegram (11–16.10.2026).

python test_parse_export.py  — прогоняет все фото, сравнивает с эталоном, печатает таблицу.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import receipt_parser as rp

EXPORT = Path(r"C:/Users/aisez/Downloads/Telegram Desktop/ChatExport_2026-10-17/photos")

# файл, подпись, ожидаемые сумма/дата/статья/кол-во магазинов
CASES = [
    ("photo_126@11-10-2026_20-20-27.jpg", "З/п, новенькая упаковщица. Все магазины модульбанк", 21000, "2026-10-11", "Зарплата", 12),
    ("photo_127@12-10-2026_18-59-42.jpg", "ФБС Вешки, за 4 дня, включая сегодня 12 октября. Модуль", 12000, "2026-10-12", "ФБС, вешки", 7),
    ("photo_128@12-10-2026_19-32-51.jpg", "Реклама, Kirpix WB и Letics WB льготные. Модуль", 25000, "2026-10-12", "Реклама внешняя", 2),
    ("photo_129@13-10-2026_15-54-42.jpg", "Выкупы, кабинет Tattuma WB. Модуль", 706, "2026-10-13", "Выкупы кэшбэк", 1),
    ("photo_130@14-10-2026_09-23-58.jpg", "ФБС, вешки, за 13 окт. все магазины Вб. Модульбанк", 3000, "2026-10-14", "ФБС, вешки", 7),
    ("photo_131@14-10-2026_15-33-09.jpg", "Доставка грузов на склад ФБО. Все магазины Вб. Модуль", 8000, "2026-10-14", "Доставка / Перевозка", 7),
    ("photo_132@14-10-2026_15-35-07.jpg", "Грузчики, все магазины Вб. Модуль", 3000, "2026-10-14", "Грузчики", 7),
    ("photo_133@15-10-2026_17-39-53.jpg", "Таня, з/п, все магазины. Модуль", 26500, "2026-10-15", "Зарплата", 12),
    ("photo_134@15-10-2026_17-43-45.jpg", "Фбс, Вешки, за 14 октября. Все магазины Вб. Модуль", 3000, "2026-10-15", "ФБС, вешки", 7),
    ("photo_135@16-10-2026_00-22-54.jpg", "Фбс, Вешки, 15 окт. все магазины Вб. Модуль", 3000, "2026-10-15", "ФБС, вешки", 7),
    ("photo_136@16-10-2026_18-21-18.jpg", "Жоомарт, аванс, все магазины Озон. Оптима", 10000, "2026-10-16", "Зарплата", 5),
    ("photo_137@16-10-2026_23-13-26.jpg", "Фбс, Вешки, 16 окт. все магазины Вб. Модуль", 3000, "2026-10-16", "ФБС, вешки", 7),
]


async def one(fn: str, cap: str, amt: float, date: str, art: str, n: int) -> tuple[bool, str]:
    p = EXPORT / fn
    if not p.exists():
        return False, f"{fn}: файла нет"
    r = await rp.parse(fn, p.read_bytes(), cap)
    ok = (r.amount == amt) and (r.date == date) and (r.article == art) and (len(r.stores) == n)
    return ok, f"{'OK ' if ok else 'ERR'} {fn[:9]} {r.amount} {r.currency} {r.date} | {r.article} | {len(r.stores)} | {r.purpose} | {r.warnings}"


async def main() -> None:
    sem = asyncio.Semaphore(3)

    async def run(c):
        async with sem:
            return await one(*c)

    results = await asyncio.gather(*(run(c) for c in CASES))
    good = 0
    for ok, line in results:
        print(line)
        good += ok
    print(f"\n{good}/{len(CASES)} совпали с эталоном")
    sys.exit(0 if good == len(CASES) else 1)


if __name__ == "__main__":
    asyncio.run(main())
