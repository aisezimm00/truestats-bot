"""Клиент внутреннего API TrueStats (api.truestats.ru).

Эндпоинты и формат payload сняты с фронтенда app.truestats.ru (бандл prod-270585b1-20261016).
Публичной документации нет, поэтому всё, что здесь, может сломаться при обновлении TrueStats.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("truestats")

API_URL = "https://api.truestats.ru"
AUTH_URL = "https://api.truestats.ru/auth"


class TrueStatsError(RuntimeError):
    pass


@dataclass
class Account:
    id: int
    name: str
    marketplace: str | None = None


@dataclass
class Dicts:
    accounts: list[Account] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)  # name -> id (только расход)
    bills: dict[str, int] = field(default_factory=dict)  # name -> id
    loaded_at: float = 0.0

    def account_by_name(self, name: str) -> Account | None:
        n = name.strip().lower()
        for a in self.accounts:
            if a.name.strip().lower() == n:
                return a
        return None


class TrueStatsClient:
    def __init__(self, email: str, password: str, timeout: float = 30.0):
        self._email = email
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._legacy = False
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})
        self.dicts = Dicts()

    # ---------- auth ----------
    async def login(self) -> None:
        payload = {"email": self._email, "password": self._password}
        # Новый auth-сервис (JWT + refresh)
        try:
            r = await self._http.post(f"{AUTH_URL}/api/v1/auth/login", json=payload)
            if r.status_code == 200:
                data = r.json()
                if data.get("access_token"):
                    self._access = data["access_token"]
                    self._refresh = data.get("refresh_token")
                    self._legacy = False
                    log.info("TrueStats: вход через auth-сервис выполнен")
                    return
            else:
                log.warning("auth-service login %s: %s", r.status_code, r.text[:300])
        except httpx.HTTPError as e:
            log.warning("auth-service недоступен: %s", e)
        # Старый вход
        r = await self._http.post(f"{API_URL}/login", json=payload)
        if r.status_code != 200 or not r.json().get("token"):
            raise TrueStatsError(f"Не удалось войти в TrueStats: {r.status_code} {r.text[:300]}")
        self._access = r.json()["token"]
        self._refresh = None
        self._legacy = True
        log.info("TrueStats: вход через /login выполнен")

    async def _refresh_token(self) -> bool:
        if self._legacy or not self._refresh:
            return False
        try:
            r = await self._http.post(f"{AUTH_URL}/api/v1/auth/token/refresh", json={"refresh_token": self._refresh})
            if r.status_code == 200 and r.json().get("access_token"):
                self._access = r.json()["access_token"]
                self._refresh = r.json().get("refresh_token", self._refresh)
                return True
        except httpx.HTTPError:
            pass
        return False

    def _headers(self) -> dict[str, str]:
        if not self._access:
            raise TrueStatsError("Нет токена, сначала login()")
        return {"Authorization": f"Bearer {self._access}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, *, json: Any = None, params: Any = None, _retry: bool = True) -> Any:
        async with self._lock:
            if not self._access:
                await self.login()
        r = await self._http.request(method, f"{API_URL}{path}", json=json, params=params, headers=self._headers())
        if r.status_code in (401, 403) and _retry:
            async with self._lock:
                ok = await self._refresh_token()
                if not ok:
                    await self.login()
            return await self._request(method, path, json=json, params=params, _retry=False)
        if r.status_code >= 400:
            raise TrueStatsError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # ---------- справочники ----------
    async def load_dicts(self, force: bool = False) -> Dicts:
        if not force and self.dicts.loaded_at and time.time() - self.dicts.loaded_at < 6 * 3600:
            return self.dicts
        d = Dicts()
        ent = await self._request("GET", "/v1/operation/distribution-entities")
        for a in _as_list(ent, "accounts"):
            d.accounts.append(Account(id=int(a["id"]), name=str(a.get("name", "")), marketplace=_mp(a)))
        cats: dict[str, int] = {}
        page = 1
        while True:
            res = await self._request("POST", "/v1/operation/category/list", json={"page": page, "limit": 100, "operationType": ["expense"]})
            items = _as_list(res, "items")
            for c in items:
                cats[str(c["name"]).strip()] = int(c["id"])
            if len(items) < 100:
                break
            page += 1
        d.categories = cats
        bills: dict[str, int] = {}
        res = await self._request("POST", "/v1/operation/bill/list", json={"page": 1, "limit": 100})
        for b in _as_list(res, "items"):
            bills[str(b["name"]).strip()] = int(b["id"])
        d.bills = bills
        d.loaded_at = time.time()
        self.dicts = d
        log.info("Справочники: %d кабинетов, %d статей, %d счетов", len(d.accounts), len(d.categories), len(d.bills))
        return d

    # ---------- операции ----------
    async def create_expense(
        self,
        *,
        amount: float,
        date: dt.date,
        category_id: int,
        bill_id: int,
        description: str,
        account_ids: list[int],
        method: str = "proportional",
    ) -> dict[str, Any]:
        acc_by_id = {a.id: a for a in self.dicts.accounts}
        entities = [{"id": i, "name": acc_by_id[i].name if i in acc_by_id else str(i)} for i in account_ids]
        payload = {
            "operationType": "expense",
            "amount": round(float(amount), 2),
            "vatAmount": None,
            "operationDate": date.isoformat(),
            "accrualDate": date.isoformat(),
            "isRegularOption": False,
            "billId": bill_id,
            "categoryId": category_id,
            "description": description,
            "isOfficial": False,
            "distribution": {"method": method, "target": "account", "entities": entities},
        }
        res = await self._request("POST", "/v1/operation", json=payload)
        log.info("Создана операция: %s", str(res)[:300])
        return res if isinstance(res, dict) else {"raw": res}

    async def list_operations(self, date_from: dt.date, date_to: dt.date, limit: int = 100) -> list[dict[str, Any]]:
        res = await self._request(
            "POST",
            "/v1/operation/list",
            json={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(), "page": 1, "limit": limit},
        )
        return _as_list(res, "items")

    async def delete_operation(self, op_id: int) -> None:
        await self._request("DELETE", f"/v1/operation/{op_id}")

    async def balance(self) -> Any:
        return await self._request("GET", "/v1/operation/bill/balance")

    async def close(self) -> None:
        await self._http.aclose()


def _as_list(res: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        if isinstance(res.get(key), list):
            return res[key]
        if isinstance(res.get("data"), dict) and isinstance(res["data"].get(key), list):
            return res["data"][key]
        if isinstance(res.get("data"), list):
            return res["data"]
    return []


def _mp(a: dict[str, Any]) -> str | None:
    for k in ("marketplace", "marketplaceName", "marketplaceType", "type"):
        v = a.get(k)
        if isinstance(v, str):
            return v
    return None
