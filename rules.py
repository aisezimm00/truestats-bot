"""Правила разбора подписей из чата ДДС → статья, набор кабинетов, назначение.

Все соглашения сняты с существующих записей в TrueStats (октябрь 2026).
Если команда начнёт писать иначе, править сюда.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Имена кабинетов ровно как в TrueStats (Настройки → Магазины)
WB_ALL = [
    "Kirpix WB",
    "Letics",
    "New MakeEyes",
    "Tattuma WB",
    "Letics WB РФ льготный",
    "Kirpix WB РФ Льготный",
    "MakeEyes WB РФ Льготный",
]
OZON_ALL = ["MakeEyes Ozon", "Letics OZON", "Letics Иностр ЮЛ", "Kirpix иностр ЮЛ", "Aitanova"]
ALL = WB_ALL + OZON_ALL

# Как в чате называют кабинеты → список имён в TrueStats
STORE_ALIASES: dict[str, list[str]] = {
    "все магазины вб": WB_ALL,
    "все магазины wb": WB_ALL,
    "все вб": WB_ALL,
    "все магазины озон": OZON_ALL,
    "все магазины ozon": OZON_ALL,
    "все озон": OZON_ALL,
    "все магазины": ALL,
    "kirpix wb": ["Kirpix WB"],
    "kirpix wb льготный": ["Kirpix WB РФ Льготный"],
    "kirpix льготный": ["Kirpix WB РФ Льготный"],
    "kirpix иностр": ["Kirpix иностр ЮЛ"],
    "kirpix ozon": ["Kirpix иностр ЮЛ"],
    "letics wb": ["Letics"],
    "letics": ["Letics"],
    "letics wb льготный": ["Letics WB РФ льготный"],
    "letics льготный": ["Letics WB РФ льготный"],
    "letics ozon": ["Letics OZON"],
    "letics озон": ["Letics OZON"],
    "letics иностр": ["Letics Иностр ЮЛ"],
    "makeeyes wb": ["New MakeEyes", "MakeEyes WB РФ Льготный"],
    "makeeyes вб": ["New MakeEyes", "MakeEyes WB РФ Льготный"],
    "new makeeyes": ["New MakeEyes"],
    "makeeyes льготный": ["MakeEyes WB РФ Льготный"],
    "makeeyes ozon": ["MakeEyes Ozon"],
    "makeeyes озон": ["MakeEyes Ozon"],
    "tattuma": ["Tattuma WB"],
    "tattuma wb": ["Tattuma WB"],
    "aitanova": ["Aitanova"],
    "айтанова": ["Aitanova"],
}

# Ключевые слова в подписи → статья TrueStats и шаблон назначения
# (порядок важен: первое совпадение побеждает)
ARTICLE_RULES: list[tuple[list[str], str, str]] = [
    (["фбс", "вешк"], "ФБС, вешки", "ФБС, вешки"),
    (["грузчик"], "Грузчики", "Грузчики"),
    (["реклам"], "Реклама внешняя", "Реклама"),
    (["выкуп"], "Выкупы кэшбэк", "Выкупы"),
    (["доставк", "перевозк", "скиф", "груз", "водител", "курьер"], "Доставка / Перевозка", "Доставка"),
    (["фулфилмент", "фулфил", "ф/ф", "фф "], "Расходы", "Фулфилмент"),
    (["коробк", "упаковочн", "скотч", "пакеты", "расходник", "канцеляр", "хозтовар", "подписк", "chat gpt", "chatgpt", "сервис"], "Расходы", ""),
    (["дизайн"], "Дизайн", "Дизайнер"),
    (["фото"], "Фотосессия", "Фотосессия"),
    (["налог"], "Налог", "Налог"),
    (["честн"], "Честный знак РФ", "Честный знак"),
    (["аренд"], "Аренда склада", "Аренда"),
    (["интернет"], "Интернет, склад", "Интернет"),
    (["связь", "телефон"], "Связь", "Связь"),
    (["компенсац"], "Компенсация покупателю", "Компенсация покупателю"),
    (["з/п", "зп", "зарплат", "аванс", "уборщиц", "упаковщиц", "подработк", "остаток"], "Зарплата", "Зарплата"),
]

# Бренд × площадка → кабинеты (для свободных формулировок: «MakeEyes магазины все на вб»)
BRAND_WORDS: dict[str, str] = {
    "kirpix": "kirpix", "кирпикс": "kirpix",
    "letics": "letics", "летикс": "letics",
    "makeeyes": "makeeyes", "make eyes": "makeeyes", "мейкайс": "makeeyes", "мейк айс": "makeeyes", "мэйкайс": "makeeyes",
    "tattuma": "tattuma", "таттума": "tattuma", "татума": "tattuma",
    "aitanova": "aitanova", "айтанова": "aitanova",
}
BRAND_STORES: dict[str, dict[str, list[str]]] = {
    "kirpix": {"wb": ["Kirpix WB", "Kirpix WB РФ Льготный"], "ozon": ["Kirpix иностр ЮЛ"]},
    "letics": {"wb": ["Letics", "Letics WB РФ льготный"], "ozon": ["Letics OZON", "Letics Иностр ЮЛ"]},
    "makeeyes": {"wb": ["New MakeEyes", "MakeEyes WB РФ Льготный"], "ozon": ["MakeEyes Ozon"]},
    "tattuma": {"wb": ["Tattuma WB"], "ozon": []},
    "aitanova": {"wb": [], "ozon": ["Aitanova"]},
}
WB_RE = re.compile(r"(?<![a-zа-я])(вб|wb|вайлдберриз|wildberries)(?![a-zа-я])")
OZON_RE = re.compile(r"(?<![a-zа-я])(озон|ozon)(?![a-zа-я])")


def stores_by_brand(n: str) -> tuple[list[str], str | None]:
    """«makeeyes магазины все на вб» → оба кабинета MakeEyes на WB. Бренд без площадки → все кабинеты бренда."""
    brands = {b for w, b in BRAND_WORDS.items() if w in n}
    if not brands:
        return [], None
    wb, oz = bool(WB_RE.search(n)), bool(OZON_RE.search(n))
    plats = [p for p, f in (("wb", wb), ("ozon", oz)) if f] or ["wb", "ozon"]
    out: list[str] = []
    for b in sorted(brands):
        for p in plats:
            out += [x for x in BRAND_STORES[b][p] if x not in out]
    if "льготн" in n:
        out = [x for x in out if "льготн" in x.lower()]
    label = "+".join(sorted(brands)) + " " + "/".join(plats)
    return out, label


# Упоминание банка в подписи: в TrueStats не вносится, но полезно для контроля
BANK_WORDS = ["модуль", "модульбанк", "тбанк", "tbank", "тинькофф", "оптима", "optima", "сбер", "альфа"]

# Статьи ФБС Вешки идут только на WB, даже если написано «все магазины»
WB_ONLY_ARTICLES = {"ФБС, вешки"}


@dataclass
class Hint:
    article: str | None = None
    purpose_prefix: str | None = None
    stores: list[str] = field(default_factory=list)
    store_alias: str | None = None
    article_defaulted: bool = False
    bank: str | None = None
    person: str | None = None
    period_note: str | None = None
    warnings: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[ ]+", " ", s)
    s = re.sub(r"[.,;:!()\[\]«»\"']+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_caption(caption: str) -> Hint:
    """Детерминированный разбор подписи. Claude потом дополняет/проверяет."""
    h = Hint()
    if not caption:
        h.warnings.append("Подписи нет")
        return h
    n = _norm(caption)

    # статья
    for keys, article, prefix in ARTICLE_RULES:
        if any(k in n for k in keys):
            h.article, h.purpose_prefix = article, prefix
            break

    # кабинеты в три прохода: «все магазины вб/озон» → конкретные кабинеты → «все магазины»
    generic_group = {a for a in STORE_ALIASES if a.startswith("все") and a not in ("все магазины",)}
    specific = [a for a in STORE_ALIASES if not a.startswith("все")]
    for pool in (sorted(generic_group, key=len, reverse=True), sorted(specific, key=len, reverse=True), ["все магазины"]):
        for alias in pool:
            if alias in n:
                h.store_alias = alias
                h.stores = list(STORE_ALIASES[alias])
                break
        if h.stores:
            break
    # «Kirpix WB и Letics WB льготные» → оба льготных
    if "льготн" in n and ("kirpix" in n and "letics" in n):
        h.stores = ["Kirpix WB РФ Льготный", "Letics WB РФ льготный"]
        h.store_alias = "kirpix+letics льготные"
    # бренд в подписи: «Kirpix все магазины на вб», «Kirpix и Letics на вб» → кабинеты бренда, а не всех
    bstores, blabel = stores_by_brand(n)
    if bstores and (not h.stores or (h.store_alias or "").startswith("все") or "+" in (blabel or "")):
        h.stores, h.store_alias = bstores, blabel
    if h.article in WB_ONLY_ARTICLES:
        if h.stores == ALL:
            h.stores = list(WB_ALL)
            h.warnings.append("ФБС Вешки: «все магазины» заменил на 7 кабинетов WB (как в прошлых записях)")
        elif not h.stores:
            h.stores = list(WB_ALL)
            h.store_alias = "все магазины вб (по умолчанию для ФБС)"

    # банк
    for b in BANK_WORDS:
        if b in n:
            h.bank = b
            break

    # имя человека (первое слово с заглавной, не служебное)
    m = re.match(r"\s*([А-ЯЁA-Z][а-яёa-z]+)\s*,", caption.strip())
    role_words = {"фбс", "реклама", "выкупы", "грузчики", "доставка", "все", "уборщица", "упаковщица", "зарплата", "аванс", "налог", "дизайн", "фото", "аренда", "подработка"}
    if m and m.group(1).lower() not in role_words:
        h.person = m.group(1)

    # период («за 14 окт», «за 4 дня по 12 октября», «15 окт»)
    pm = re.search(r"(за\s+[^.,]*?(?:\d{1,2}\s*(?:окт|ноя|дек|янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен)[а-я]*)|\b\d{1,2}\s*(?:окт|ноя|дек|янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен)[а-я]*)", n)
    if pm:
        h.period_note = pm.group(1)

    if not h.article:
        # неизвестная статья → «Расходы», назначение = текст подписи без магазинов и банка
        h.article = "Расходы"
        h.article_defaulted = True
        h.warnings.append("Статью по подписи не узнал, поставил «Расходы»")
    if not h.stores:
        h.warnings.append("Не понял кабинеты по подписи")
    return h


def _strip_bank(text: str) -> str:
    """Убирает хвост с банком: «…, Kirpix WB льготные. Модуль» → «…, Kirpix WB льготные»."""
    t = text.strip()
    for b in sorted(BANK_WORDS, key=len, reverse=True):
        t = re.sub(r"[\s,.;]*\b" + re.escape(b) + r"\w*(\s+банк\w*)?\s*$", "", t, flags=re.I)
    return t.strip(" .,;")


def _strip_stores(text: str, h: Hint) -> str:
    """Убирает из подписи фразу про магазины: «Коробки. Все магазины Озон» → «Коробки»."""
    t = text
    aliases = [h.store_alias] if h.store_alias and h.store_alias in STORE_ALIASES else []
    aliases += [a for a in STORE_ALIASES if a.startswith("все")]
    for a in sorted(set(aliases), key=len, reverse=True):
        pat = r"[\s,.;]*" + r"\s+".join(re.escape(w) for w in a.split()) + r"\w*"
        t = re.sub(pat, "", t, flags=re.I)
    return t.strip(" .,;")


def build_purpose(h: Hint, fallback: str = "") -> str:
    """Назначение платежа в стиле существующих записей."""
    fallback = _strip_bank(fallback or "")
    if h.article == "Расходы" and not h.purpose_prefix:
        return _strip_stores(fallback, h)[:120] or "Расходы"
    if h.article == "Зарплата":
        who = h.person or "Сотрудник"
        low = (fallback or "").lower()
        if "аванс" in low:
            return f"{who}, аванс"
        if "остаток" in low:
            return f"{who}, остаток зарплаты"
        if "уборщиц" in low:
            return f"{who}, уборщица, зарплата" if h.person else "Уборщица, зарплата"
        if "упаковщиц" in low:
            return f"{who}, упаковщица, зарплата" if h.person else "Упаковщица, зарплата"
        return f"{who}, зарплата"
    if h.article == "ФБС, вешки":
        return f"ФБС, вешки, {h.period_note}" if h.period_note else "ФБС, вешки"
    if h.article == "Реклама внешняя":
        return "Реклама" + (f", {fallback.split(',')[1].strip()}" if fallback and "," in fallback else "")
    if h.purpose_prefix:
        return h.purpose_prefix
    return (fallback or "").strip()[:120]
