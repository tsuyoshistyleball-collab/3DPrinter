"""SQLite によるプロジェクト（作りたいもの）とモデルバージョンの管理。"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "app.db"
MODELS_DIR = DATA_DIR / "models"

# プロジェクトの状態遷移: planning(相談中) -> modeled(モデル生成済み) -> printed(印刷済み)
STATUSES = ("planning", "modeled", "printed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning',
    claude_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    questions TEXT,
    created_at TEXT NOT NULL
);
-- AI応答はバックグラウンドで処理する。1プロジェクトにつき同時に1件だけ走らせ、
-- 別プロジェクトの相談は並行して進められる。
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT,
    summary TEXT,
    scad_path TEXT NOT NULL,
    stl_path TEXT,
    render_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, version)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # v1 で作成された DB への追加カラムのマイグレーション
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN claude_session_id TEXT")
        except sqlite3.OperationalError:
            pass  # 既に存在する
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN questions TEXT")
        except sqlite3.OperationalError:
            pass  # 既に存在する


# ---------- ジョブ（AI応答のバックグラウンド処理） ----------

JOB_ACTIVE = ("queued", "running")


def create_job(project_id: int) -> dict:
    """待機中のジョブがあればそれを返す（履歴は実行時に読むので新しい発言も拾える）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? AND status = 'queued' ORDER BY id LIMIT 1",
            (project_id,),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO jobs (project_id, status, created_at) VALUES (?, 'queued', ?)",
            (project_id, _now()),
        )
        job_id = cur.lastrowid
    return get_job(job_id)


def get_job(job_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_active_jobs() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def claim_next_job(busy_project_ids: set[int]) -> dict | None:
    """実行中でないプロジェクトの待機ジョブを1件だけ running にして返す。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id"
        ).fetchall()
        for row in rows:
            if row["project_id"] in busy_project_ids:
                continue
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            job = dict(row)
            job["status"] = "running"
            return job
    return None


def finish_job(job_id: int, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            ("error" if error else "done", error, _now(), job_id),
        )


def fail_running_jobs(reason: str) -> int:
    """サーバー再起動で宙に浮いた running ジョブを片付ける。"""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'error', error = ?, finished_at = ? WHERE status = 'running'",
            (reason, _now()),
        )
        return cur.rowcount


def get_settings() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_settings(values: dict) -> None:
    with connect() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )


def create_project(title: str) -> dict:
    with connect() as conn:
        now = _now()
        cur = conn.execute(
            "INSERT INTO projects (title, status, created_at, updated_at) VALUES (?, 'planning', ?, ?)",
            (title, now, now),
        )
        project_id = cur.lastrowid
    return get_project(project_id)


def list_projects() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, COUNT(m.id) AS model_count
            FROM projects p LEFT JOIN model_versions m ON m.project_id = p.id
            GROUP BY p.id ORDER BY p.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None


def update_project(
    project_id: int,
    *,
    title: str | None = None,
    status: str | None = None,
    claude_session_id: str | None = None,
) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if title is not None:
            conn.execute("UPDATE projects SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), project_id))
        if status is not None:
            conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), project_id))
        if claude_session_id is not None:
            conn.execute(
                "UPDATE projects SET claude_session_id = ? WHERE id = ?",
                (claude_session_id, project_id),
            )


def delete_project(project_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def add_message(
    project_id: int, role: str, content: str, questions: list[dict] | None = None
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (project_id, role, content, questions, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, role, content, json.dumps(questions, ensure_ascii=False) if questions else None, _now()),
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))


def list_messages(project_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
    messages = []
    for r in rows:
        m = dict(r)
        try:
            m["questions"] = json.loads(m["questions"]) if m.get("questions") else None
        except (TypeError, ValueError):
            m["questions"] = None
        messages.append(m)
    return messages


def project_model_dir(project_id: int) -> Path:
    d = MODELS_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_model_version(
    project_id: int,
    *,
    title: str | None,
    summary: str | None,
    scad_path: str,
    stl_path: str | None,
    render_error: str | None,
) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM model_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        version = row["next"]
        conn.execute(
            """
            INSERT INTO model_versions (project_id, version, title, summary, scad_path, stl_path, render_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, version, title, summary, scad_path, stl_path, render_error, _now()),
        )
    return get_model_version(project_id, version)


def list_model_versions(project_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM model_versions WHERE project_id = ? ORDER BY version", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_model_version(project_id: int, version: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM model_versions WHERE project_id = ? AND version = ?",
            (project_id, version),
        ).fetchone()
        return dict(row) if row else None
