"""SQLite によるプロジェクト（作りたいもの）とモデルバージョンの管理。"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "app.db"
MODELS_DIR = DATA_DIR / "models"
UPLOADS_DIR = DATA_DIR / "uploads"

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
-- 参考画像。送信前は message_id が NULL（下書き状態）で、送信時に紐づける。
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
-- AI応答はバックグラウンドで処理する。1プロジェクトにつき同時に1件だけ走らせ、
-- 別プロジェクトの相談は並行して進められる。
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    stage TEXT,
    stage_started_at TEXT,
    progress_chars INTEGER NOT NULL DEFAULT 0,
    produced_model INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
-- トークン消費の記録。プロジェクトを消しても残るよう外部キーを持たせない。
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
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
        for column, ddl in (
            ("stage", "TEXT"),
            ("stage_started_at", "TEXT"),
            ("progress_chars", "INTEGER NOT NULL DEFAULT 0"),
            ("produced_model", "INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass  # 既に存在する


# ---------- 参考画像 ----------

def project_upload_dir(project_id: int) -> Path:
    d = UPLOADS_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_attachment(project_id: int, path: str, media_type: str, note: str | None) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (project_id, path, media_type, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, path, media_type, note, _now()),
        )
        attachment_id = cur.lastrowid
    return get_attachment(attachment_id)


def get_attachment(attachment_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        return dict(row) if row else None


def update_attachment_note(attachment_id: int, note: str | None) -> None:
    with connect() as conn:
        conn.execute("UPDATE attachments SET note = ? WHERE id = ?", (note, attachment_id))


def delete_attachment(attachment_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))


def list_draft_attachments(project_id: int) -> list[dict]:
    """まだ送信していない（メッセージに紐づいていない）画像。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE project_id = ? AND message_id IS NULL ORDER BY id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_attachments(project_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def attach_to_message(project_id: int, message_id: int) -> None:
    """下書き状態の画像をまとめて、いま送ったメッセージに紐づける。"""
    with connect() as conn:
        conn.execute(
            "UPDATE attachments SET message_id = ? WHERE project_id = ? AND message_id IS NULL",
            (message_id, project_id),
        )


def latest_images(project_id: int, limit: int = 4) -> list[dict]:
    """最後に画像が添えられたメッセージの画像。AIへ渡す参考資料になる。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT message_id FROM attachments WHERE project_id = ? AND message_id IS NOT NULL "
            "ORDER BY message_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            return []
        rows = conn.execute(
            "SELECT * FROM attachments WHERE message_id = ? ORDER BY id LIMIT ?",
            (row["message_id"], limit),
        ).fetchall()
        return [dict(r) for r in rows]


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


def set_job_stage(job_id: int, stage: str) -> None:
    """処理段階を進める。文字数カウンタは段階ごとにリセットする。"""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET stage = ?, stage_started_at = ?, progress_chars = 0 WHERE id = ?",
            (stage, _now(), job_id),
        )


def set_job_progress(job_id: int, chars: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE jobs SET progress_chars = ? WHERE id = ?", (chars, job_id))


def finish_job(job_id: int, error: str | None = None, produced_model: bool = False) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, finished_at = ?, stage = NULL, "
            "produced_model = ? WHERE id = ?",
            ("error" if error else "done", error, _now(), 1 if produced_model else 0, job_id),
        )


def typical_duration_seconds(produced_model: bool) -> float | None:
    """直近の同種ジョブの所要時間の中央値。所要時間の目安表示に使う。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT started_at, finished_at FROM jobs "
            "WHERE status = 'done' AND produced_model = ? AND started_at IS NOT NULL "
            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 20",
            (1 if produced_model else 0,),
        ).fetchall()
    durations = []
    for r in rows:
        try:
            start = datetime.fromisoformat(r["started_at"])
            end = datetime.fromisoformat(r["finished_at"])
        except (TypeError, ValueError):
            continue
        durations.append((end - start).total_seconds())
    if not durations:
        return None
    durations.sort()
    return durations[len(durations) // 2]


# ---------- トークン消費 ----------

def log_usage(
    model: str,
    role: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO usage_log (created_at, model, role, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(),
                model,
                role,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
            ),
        )


_USAGE_COLUMNS = (
    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
    "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
    "COUNT(*) AS calls"
)


def usage_summary(since: str | None = None) -> dict:
    where, params = ("WHERE created_at >= ?", (since,)) if since else ("", ())
    with connect() as conn:
        total = dict(
            conn.execute(f"SELECT {_USAGE_COLUMNS} FROM usage_log {where}", params).fetchone()
        )
        by_model = [
            dict(r)
            for r in conn.execute(
                f"SELECT model, {_USAGE_COLUMNS} FROM usage_log {where} "
                "GROUP BY model ORDER BY output_tokens DESC",
                params,
            ).fetchall()
        ]
    total["by_model"] = by_model
    return total


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
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (project_id, role, content, questions, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, role, content, json.dumps(questions, ensure_ascii=False) if questions else None, _now()),
        )
        message_id = cur.lastrowid
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))
    return message_id


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
