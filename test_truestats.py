"""Проверка связи с TrueStats: вход, справочники, тестовая операция на 1 ₽ (создать и удалить).

python test_truestats.py            — только вход и справочники
python test_truestats.py --write    — плюс создать операцию 1 ₽ «ТЕСТ БОТА, удалить» и сразу удалить её
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from truestats_client import TrueStatsClient

load_dotenv(Path(__file__).with_name(".env"))


async def main() -> None:
    email, pwd = os.getenv("TRUESTATS_EMAIL"), os.getenv("TRUESTATS_PASSWORD")
    if not email or not pwd:
        print("Заполни TRUESTATS_EMAIL и TRUESTATS_PASSWORD в .env")
        sys.exit(1)
    ts = TrueStatsClient(email, pwd)
    await ts.login()
    d = await ts.load_dicts(force=True)
    print("Кабинеты:")
    for a in d.accounts:
        print(f"  {a.id:>6}  {a.name}  ({a.marketplace})")
    print("Статьи (расход):", ", ".join(sorted(d.categories)))
    print("Счета:", d.bills)
    missing = [n for n in __import__("rules").ALL if not d.account_by_name(n)]
    print("Нет в TrueStats из rules.ALL:", missing or "все найдены")
    ops = await ts.list_operations(dt.date.today() - dt.timedelta(days=7), dt.date.today(), limit=5)
    print("Пример операции из списка:", ops[0] if ops else "(пусто)")

    if "--write" in sys.argv:
        bill = d.bills.get("Основной счёт") or next(iter(d.bills.values()))
        cat = d.categories.get("Расходы") or next(iter(d.categories.values()))
        acc = d.account_by_name("Kirpix WB") or d.accounts[0]
        res = await ts.create_expense(amount=1, date=dt.date.today(), category_id=cat, bill_id=bill, description="ТЕСТ БОТА, удалить", account_ids=[acc.id])
        print("Создано:", res)
        op_id = res.get("id") if isinstance(res, dict) else None
        if op_id is None:
            today = dt.date.today()
            for o in await ts.list_operations(today, today):
                if (o.get("description") or "") == "ТЕСТ БОТА, удалить":
                    op_id = o.get("id")
        if op_id is not None:
            await ts.delete_operation(int(op_id))
            print("Удалено:", op_id)
        else:
            print("!!! Не нашёл id тестовой операции, удали руками «ТЕСТ БОТА, удалить»")
    await ts.close()


if __name__ == "__main__":
    asyncio.run(main())
