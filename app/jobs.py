"""AI応答をバックグラウンドで処理するジョブキュー。

モデリングは数分かかることがあるため、リクエストは即座に返し、実際のAI呼び出しは
ワーカースレッドで走らせる。これにより待っている間も別のプロジェクトの相談を進められる。

- **1プロジェクトにつき同時1件**。会話の順序が入れ替わらないようにするため。
- **プロジェクトをまたいだ並行実行は MAX_PARALLEL 件まで**。
- 待機中のジョブが既にあるプロジェクトへ追加送信した場合、新しいジョブは作らない。
  履歴はジョブ実行時に読み直すので、待機中のジョブがそのまま新しい発言も拾う。
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from app import db

logger = logging.getLogger(__name__)

# 同時に走らせるAI呼び出しの上限。増やしすぎるとプランの利用枠を一気に消費する。
MAX_PARALLEL = max(1, int(os.environ.get("MAX_PARALLEL_JOBS", "3")))

_lock = threading.Lock()
_busy_projects: set[int] = set()      # 実行中のプロジェクトID
_wake = threading.Event()
_pool: ThreadPoolExecutor | None = None
_dispatcher: threading.Thread | None = None
_runner = None                        # ジョブ本体を実行する関数（main.py が注入する）


def start(runner) -> None:
    """ワーカーを起動する。runner(project_id) が1ターン分の処理を行う。"""
    global _pool, _dispatcher, _runner
    if _dispatcher is not None:
        return
    _runner = runner
    stale = db.fail_running_jobs("サーバーが再起動したため中断されました。もう一度送信してください。")
    if stale:
        logger.warning("中断された実行中ジョブを %d 件エラーにしました", stale)
    _pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="ai-job")
    _dispatcher = threading.Thread(target=_dispatch_loop, name="ai-dispatcher", daemon=True)
    _dispatcher.start()


def enqueue(project_id: int) -> dict:
    job = db.create_job(project_id)
    _wake.set()
    return job


def status_for(project_id: int) -> dict | None:
    """そのプロジェクトの処理中ジョブ（なければ None）。"""
    for job in db.list_active_jobs():
        if job["project_id"] == project_id:
            return job
    return None


def _dispatch_loop() -> None:
    while True:
        # 起動待ちの合図が来るか、取りこぼし防止のタイムアウトで起きる
        _wake.wait(timeout=2.0)
        _wake.clear()
        while True:
            with _lock:
                if len(_busy_projects) >= MAX_PARALLEL:
                    break
                job = db.claim_next_job(set(_busy_projects))
                if job is None:
                    break
                _busy_projects.add(job["project_id"])
            _pool.submit(_run_job, job)


def _run_job(job: dict) -> None:
    project_id = job["project_id"]
    try:
        _runner(project_id)
        db.finish_job(job["id"])
    except Exception as e:  # ワーカーは何があっても落とさない
        logger.exception("ジョブ %s の処理に失敗しました", job["id"])
        db.finish_job(job["id"], error=str(e))
    finally:
        with _lock:
            _busy_projects.discard(project_id)
        _wake.set()
