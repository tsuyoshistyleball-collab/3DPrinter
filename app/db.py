"""SQLite によるプロジェクト（作りたいもの）とモデルバージョンの管理。"""

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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
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


def update_project(project_id: int, *, title: str | None = None, status: str | None = None) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if title is not None:
            conn.execute("UPDATE projects SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), project_id))
        if status is not None:
            conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), project_id))


def delete_project(project_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def add_message(project_id: int, role: str, content: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (project_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (project_id, role, content, _now()),
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))


def list_messages(project_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


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
