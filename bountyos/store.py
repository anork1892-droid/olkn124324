"""Persistent local state. Advertised rewards are never booked as income."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse
import uuid


DEFAULT_SETTINGS = {
    "running": False,
    "search_enabled": True,
    "max_agents": 2,
    "autoscale": False,
    "auto_submit": False,
    "poll_interval_minutes": 1,
    "daily_budget_sek": 0,
}
TASK_COLUMNS = {
    "id", "title", "url", "repo", "kind", "reward_amount", "reward_currency",
    "status", "reason", "created_at", "updated_at", "artifact_path",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "bountyos.sqlite3"
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                    repo TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'code',
                    reward_amount REAL, reward_currency TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'discovered', reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    artifact_path TEXT, metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                    status TEXT NOT NULL, task TEXT, metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                    amount_ore INTEGER NOT NULL CHECK(amount_ore > 0),
                    reference TEXT NOT NULL UNIQUE, received_at TEXT NOT NULL,
                    verification TEXT NOT NULL DEFAULT 'manual'
                );
                CREATE TABLE IF NOT EXISTS imports (name TEXT PRIMARY KEY, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS internal_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at);
            """)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, json.dumps(value)))
            if not conn.execute("SELECT 1 FROM imports WHERE name='automatic-search-v1'").fetchone():
                conn.execute("UPDATE settings SET value='1' WHERE key='poll_interval_minutes' AND value='30'")
                conn.execute("INSERT INTO imports(name,imported_at) VALUES (?,?)", ('automatic-search-v1', utcnow()))
            # This version has no paid provider, including when opening an older database.
            conn.execute("UPDATE settings SET value='0' WHERE key='daily_budget_sek'")

    @contextmanager
    def _connection(self, *, write: bool = False):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if conn.in_transaction:
                conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def get_settings(self) -> dict:
        with self._connection() as conn:
            settings = dict(DEFAULT_SETTINGS)
            settings.update({row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT * FROM settings")})
        settings["daily_budget_sek"] = 0
        return settings

    def get_internal_state(self, key: str) -> Any:
        """Read connector state without exposing it as a user setting."""
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM internal_state WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return None

    def set_internal_state(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, allow_nan=False)
        with self._connection(write=True) as conn:
            conn.execute(
                "INSERT INTO internal_state(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encoded),
            )

    def update_settings(self, changes: dict) -> dict:
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("Okända inställningar: " + ", ".join(sorted(unknown)))
        for key, value in changes.items():
            if key in {"running", "search_enabled", "autoscale", "auto_submit"} and type(value) is not bool:
                raise ValueError(f"{key} måste vara true eller false.")
            if key == "max_agents" and (type(value) is not int or not 1 <= value <= 10):
                raise ValueError("max_agents måste vara ett heltal mellan 1 och 10.")
            if key == "poll_interval_minutes" and (type(value) is not int or not 1 <= value <= 1440):
                raise ValueError("poll_interval_minutes måste vara ett heltal mellan 1 och 1440.")
            if key == "daily_budget_sek" and (type(value) not in {int, float} or value != 0):
                raise ValueError("Driftbudgeten är låst till 0 kr. Betalda API-anrop stöds inte.")
        with self._connection(write=True) as conn:
            for key, value in changes.items():
                conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
        return self.get_settings()

    @staticmethod
    def _task(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        metadata = json.loads(result.pop("metadata"))
        # Database columns always win over arbitrary connector metadata.
        return {**metadata, **result}

    def list_tasks(self) -> list[dict]:
        with self._connection() as conn:
            return [self._task(row) for row in conn.execute("SELECT * FROM tasks ORDER BY created_at DESC, id")]

    def get_task(self, task_id: str) -> dict | None:
        with self._connection() as conn:
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    @staticmethod
    def _validate_task(task: dict) -> None:
        if not isinstance(task.get("url"), str) or urlparse(task["url"]).scheme not in {"https", "http"}:
            raise ValueError("Uppdraget behöver en giltig http- eller https-länk.")
        reward = task.get("reward_amount")
        if reward is not None and (type(reward) not in {int, float} or not math.isfinite(reward) or reward < 0):
            raise ValueError("Annonserad ersättning måste vara ett positivt ändligt tal eller null.")

    def upsert_task(self, task: dict) -> str:
        self._validate_task(task)
        now = utcnow()
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE url=?", (task["url"],)).fetchone()
            if row:
                existing = self._task(row)
                protected = {"id", "created_at", "status", "reason", "artifact_path", "attempts", "worker_id", "submission_url"}
                fields = {key: value for key, value in task.items() if key not in protected}
                self._update_task(conn, existing, fields)
                return existing["id"]
            item = {
                "id": task.get("id") or uuid.uuid4().hex,
                "title": task.get("title") or task["url"], "url": task["url"],
                "repo": task.get("repo", ""), "kind": task.get("kind", "code"),
                "reward_amount": task.get("reward_amount"), "reward_currency": task.get("reward_currency", ""),
                "status": task.get("status", "discovered"), "reason": task.get("reason", ""),
                "created_at": task.get("created_at", now), "updated_at": now,
                "artifact_path": task.get("artifact_path"),
            }
            metadata = {key: value for key, value in task.items() if key not in TASK_COLUMNS}
            cols = list(item) + ["metadata"]
            conn.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [*item.values(), json.dumps(metadata)])
            return item["id"]

    def _update_task(self, conn: sqlite3.Connection, existing: dict, fields: dict) -> None:
        if "id" in fields and fields["id"] != existing["id"]:
            raise ValueError("Ett uppdrags id kan inte ändras.")
        item = {**existing, **fields, "updated_at": utcnow()}
        self._validate_task(item)
        cols = sorted(TASK_COLUMNS - {"id"})
        metadata = {key: value for key, value in item.items() if key not in TASK_COLUMNS}
        conn.execute(
            "UPDATE tasks SET " + ",".join(f"{column}=?" for column in cols) + ",metadata=? WHERE id=?",
            [*(item.get(column) for column in cols), json.dumps(metadata), existing["id"]],
        )

    def update_task(self, task_id: str, **fields) -> None:
        with self._connection(write=True) as conn:
            existing = self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if existing is None:
                raise ValueError("Uppdraget finns inte.")
            self._update_task(conn, existing, fields)

    def claim_task(self, worker_id: str) -> dict | None:
        with self._connection(write=True) as conn:
            task = self._task(conn.execute("SELECT * FROM tasks WHERE status='qualified' ORDER BY created_at, id LIMIT 1").fetchone())
            if task is None:
                return None
            self._update_task(conn, task, {"status": "preparing", "worker_id": worker_id, "attempts": int(task.get("attempts", 0)) + 1})
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())

    def log(self, message: str, level: str = "info") -> None:
        with self._connection(write=True) as conn:
            conn.execute("INSERT INTO activity(timestamp,level,message) VALUES (?,?,?)", (utcnow(), level, str(message)[:4000]))
            conn.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 1000)")

    def set_agent(self, agent_id: str, **fields) -> None:
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
            current = dict(row) if row else {"id": agent_id, "name": agent_id, "role": "worker", "status": "idle", "task": None, "metadata": "{}"}
            metadata = json.loads(current.pop("metadata"))
            item = {**metadata, **current, **fields, "id": agent_id}
            metadata = {key: value for key, value in item.items() if key not in {"id", "name", "role", "status", "task"}}
            conn.execute("INSERT OR REPLACE INTO agents(id,name,role,status,task,metadata) VALUES (?,?,?,?,?,?)", (agent_id, item["name"], item["role"], item["status"], item["task"], json.dumps(metadata)))

    def list_agents(self) -> list[dict]:
        with self._connection() as conn:
            result = []
            for row in conn.execute("SELECT * FROM agents ORDER BY id"):
                item = dict(row)
                metadata = json.loads(item.pop("metadata"))
                result.append({**metadata, **item})
            return result

    def record_payment(self, task_id: str, amount_sek: float, reference: str) -> str:
        try:
            amount = Decimal(str(amount_sek))
        except (InvalidOperation, ValueError):
            raise ValueError("Beloppet måste vara ett positivt antal SEK.") from None
        if not amount.is_finite() or amount <= 0 or amount > Decimal("100000000") or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Beloppet måste vara mellan 0,01 och 100 000 000 SEK med högst två decimaler.")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
            raise ValueError("Ange en unik betalningsreferens på högst 200 tecken.")
        reference = reference.strip()
        payment_id = uuid.uuid4().hex
        with self._connection(write=True) as conn:
            if conn.execute("SELECT 1 FROM payments WHERE reference=?", (reference,)).fetchone():
                raise ValueError("Betalningsreferensen är redan registrerad.")
            task = self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if task is None:
                raise ValueError("Uppdraget finns inte.")
            if task["status"] not in {"submitted", "awaiting_payment"}:
                raise ValueError("Betalning kan bara registreras för ett inskickat uppdrag.")
            conn.execute("INSERT INTO payments(id,task_id,amount_ore,reference,received_at) VALUES (?,?,?,?,?)", (payment_id, task_id, int(amount * 100), reference, utcnow()))
            self._update_task(conn, task, {"status": "paid", "payment_verification": "manual", "paid_amount_sek": float(amount)})
            conn.execute("INSERT INTO activity(timestamp,level,message) VALUES (?,?,?)", (utcnow(), "success", f"Mottagen betalning manuellt bekräftad: {amount:.2f} kr för {task['title']}."))
        return payment_id

    def get_state(self, connections: dict | None = None) -> dict:
        tasks = self.list_tasks()
        agents = self.list_agents()
        with self._connection() as conn:
            earned_ore = conn.execute("SELECT COALESCE(SUM(amount_ore),0) FROM payments").fetchone()[0]
            activity = [dict(row) for row in conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")]
            daily = {row["date"]: row["ore"] / 100 for row in conn.execute("SELECT substr(received_at,1,10) AS date,SUM(amount_ore) AS ore FROM payments GROUP BY date")}
        today = datetime.now(timezone.utc).date()
        chart = [{"date": (today - timedelta(days=offset)).isoformat(), "earned": daily.get((today - timedelta(days=offset)).isoformat(), 0), "cost": 0} for offset in range(6, -1, -1)]
        pending = sum(task["reward_amount"] or 0 for task in tasks if task["status"] in {"submitted", "awaiting_payment"} and task["reward_currency"] == "SEK")
        return {
            "settings": self.get_settings(),
            "metrics": {
                "earned_sek": earned_ore / 100, "pending_sek": round(pending, 2),
                "estimated_cost_sek": 0, "net_sek": earned_ore / 100,
                "active_agents": sum(agent["status"] in {"scouting", "preparing", "working", "reviewing", "checking", "submitting"} for agent in agents),
                "completed_tasks": sum(task["status"] in {"ready", "submitted", "awaiting_payment", "paid"} for task in tasks),
            },
            "agents": agents, "tasks": tasks, "activity": activity,
            "connections": connections or {"github": {"configured": False, "status": "unknown", "detail": "Anslutning har inte kontrollerats."}, "ai": {"configured": False, "status": "unknown", "detail": "Lokal AI har inte kontrollerats."}},
            "chart": chart,
        }

    def import_research(self, path: str | Path) -> int:
        """Import the previous read-only pilot exactly once, as blocked history."""
        path = Path(path)
        if not path.exists():
            return 0
        with self._connection() as conn:
            if conn.execute("SELECT 1 FROM imports WHERE name='pilot-research-v1'").fetchone():
                return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        imported = 0
        date = payload.get("checked_date", utcnow()[:10]) + "T00:00:00+00:00"
        candidates = [(candidate, "code") for candidate in payload.get("code_research", {}).get("candidates", [])]
        candidates += [(candidate, "research") for candidate in payload.get("documentation_and_research", {}).get("candidates", [])]
        for candidate, kind in candidates:
            source_url = candidate.get("url") or candidate.get("source_url")
            if not source_url:
                continue
            url = source_url
            # Both Chronicle rows point to the same program section. Keep them as
            # one source entry, listing both deliverables rather than inventing links.
            existing = next((item for item in self.list_tasks() if item["url"] == url), None)
            if existing and existing.get("historical_research"):
                items = existing.get("historical_candidates", [existing.get("original_research", {})])
                items.append(candidate)
                self.update_task(existing["id"], title="Chronicle: PromQL-exempel och FAQ", reward_amount=None, advertised_reward_description="$25 per uppgift (två historiska kandidater; valuta ej bekräftad)", historical_candidates=items, reason=existing["reason"] + " " + candidate.get("pilot_decision", ""))
                continue
            amount = candidate.get("advertised_amount")
            currency = candidate.get("advertised_currency", "")
            if amount is None:
                reward = candidate.get("reward", "")
                match = re.search(r"[\d,]+", reward)
                amount = float(match.group().replace(",", "")) if match else None
                currency = "USD" if "USD" in reward else "$ (valuta ej bekräftad)" if "$" in reward else ""
            if currency != "USD" and "$" not in currency and "Dollar" in currency:
                currency = "$ (valuta ej bekräftad)"
            parts = urlparse(source_url).path.strip("/").split("/")
            reason = "; ".join(candidate.get("blockers", [])) or candidate.get("pilot_decision") or candidate.get("status", "Historisk research; behöver kontrolleras på nytt.")
            self.upsert_task({"url": url, "title": candidate["title"], "repo": "/".join(parts[:2]), "kind": kind, "reward_amount": amount, "reward_currency": currency, "status": "blocked", "reason": reason, "created_at": date, "historical_research": True, "source_url": source_url, "original_research": candidate})
            imported += 1
        with self._connection(write=True) as conn:
            conn.execute("INSERT OR IGNORE INTO imports(name,imported_at) VALUES (?,?)", ("pilot-research-v1", utcnow()))
        self.log(f"{imported} källor från tidigare research importerade. Alla väntar på ny bedömning; inga intäkter registrerade.")
        return imported
