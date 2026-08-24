"""AIモデリングアプリのAPIサーバー。

起動: uvicorn app.main:app --reload
"""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 他モジュールが環境変数を読む前に .env を反映する

from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai, db, jobs, modeling, printer, slicer, version

app = FastAPI(title="T-Lab")


@app.middleware("http")
async def prevent_stale_app_assets(request: Request, call_next):
    """PWAや通常ブラウザが古い画面資産を再利用しないようにする。"""
    response = await call_next(request)
    path = request.url.path
    no_cache = (
        path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest", "/api/version")
        or path.endswith((".js", ".css", ".html", ".webmanifest"))
    )
    if no_cache:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if path == "/sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
    return response

db.init_db()
jobs.start(lambda project_id, progress: _run_ai_turn(project_id, progress))


class NewProjectRequest(BaseModel):
    message: str
    # 写真を添えてから相談を始めたい場合は false にして、画像の登録後に
    # /start を呼ぶ。写真は先にプロジェクトが無いとアップロードできないため。
    start: bool = True


class MessageRequest(BaseModel):
    message: str


class StatusRequest(BaseModel):
    status: str


class SettingsRequest(BaseModel):
    model_mode: str
    interview_model: str
    modeling_model: str
    web_search: bool = True


class NoteRequest(BaseModel):
    note: str = ""


# スマホの写真をそのまま受けても困らない上限。ブラウザ側で縮小してから送る。
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _project_or_404(project_id: int) -> dict:
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="プロジェクトが見つかりません")
    return project


def _history_text(message: dict) -> str:
    """AIへ渡す履歴用のテキスト。

    質問はタップUI用に構造化して別カラムへ退避してあるため、表示用テキストには
    残っていない。セッションが切れて履歴を畳み直すときに文脈が欠けないよう、
    ここで質問文を復元して付け直す。
    """
    content = message["content"]
    questions = message.get("questions")
    if not questions:
        return content
    asked = "\n".join(f"- {q['text']}" for q in questions if q.get("text"))
    return f"{content}\n{asked}".strip()


def _latest_scad(project_id: int) -> str | None:
    """最新バージョンのSCADコード。会話を見ていないモデリング担当へ渡す用。"""
    versions = db.list_model_versions(project_id)
    if not versions:
        return None
    path = Path(versions[-1]["scad_path"])
    return path.read_text(encoding="utf-8") if path.exists() else None


def _run_ai_turn(project_id: int, progress: ai.Progress | None = None) -> dict:
    """AI応答（必要ならモデル生成）まで行う。ワーカースレッドから呼ばれる。

    ユーザー発言は送信時点で保存済み。履歴はここで読み直すため、待機中に
    追加で送られた発言もまとめて拾える。
    """
    progress = progress or ai.Progress()
    project = db.get_project(project_id)
    if project is None:
        return {}  # 処理を待っている間に削除された
    history = [
        {"role": m["role"], "content": _history_text(m)}
        for m in db.list_messages(project_id)
    ]
    if not history:
        return {}
    reply = ai.chat(
        history,
        claude_session_id=project.get("claude_session_id"),
        previous_scad=_latest_scad(project_id),
        progress=progress,
        images=db.latest_images(project_id),
    )

    for entry in reply.usage:
        db.log_usage(
            model=entry.get("model", "unknown"),
            role=entry.get("role", "unknown"),
            input_tokens=entry.get("input_tokens", 0),
            output_tokens=entry.get("output_tokens", 0),
            cache_read_tokens=entry.get("cache_read_tokens", 0),
            cache_write_tokens=entry.get("cache_write_tokens", 0),
            cost_usd=entry.get("cost_usd"),
        )

    if reply.session_id:
        db.update_project(project_id, claude_session_id=reply.session_id)

    new_model = None
    display_text = reply.text
    if reply.scad_code:
        progress.stage("rendering")
        new_model = _create_model_version(project_id, reply)
        version = new_model["version"]
        if new_model["render_error"]:
            display_text += (
                f"\n\n⚠️ モデル v{version} のコードを保存しましたが、STL変換に失敗しました。"
                "エラー内容を伝えて修正を依頼してください。"
            )
        else:
            display_text += f"\n\n✅ モデル v{version} を生成しました。右のプレビューで確認できます。"

    # 表示用テキスト（コード除去済み）を履歴として保存する。コード本体は
    # model_versions に保存されるため会話コンテキストからは失われるが、
    # 修正依頼時はAIが最新仕様を要約から再構成できる。
    db.add_message(project_id, "assistant", display_text, questions=reply.questions)

    return {
        "reply": display_text,
        "questions": reply.questions,
        "model": new_model,
        "project": db.get_project(project_id),
    }


def _create_model_version(project_id: int, reply: ai.AIReply) -> dict:
    model_dir = db.project_model_dir(project_id)
    versions = db.list_model_versions(project_id)
    next_version = versions[-1]["version"] + 1 if versions else 1

    scad_path = model_dir / f"v{next_version}.scad"
    stl_path = model_dir / f"v{next_version}.stl"
    scad_path.write_text(reply.scad_code, encoding="utf-8")

    render_error = modeling.render_stl(scad_path, stl_path)

    record = db.add_model_version(
        project_id,
        title=reply.model_title,
        summary=reply.model_summary,
        scad_path=str(scad_path),
        stl_path=str(stl_path) if render_error is None else None,
        render_error=render_error,
    )
    if render_error is None:
        db.update_project(project_id, status="modeled")
    if reply.model_title:
        db.update_project(project_id, title=reply.model_title)
    return record


# ---------- プロジェクト ----------

@app.get("/api/projects")
def api_list_projects():
    return {
        "projects": db.list_projects(),
        "openscad_available": modeling.openscad_available(),
        "active_jobs": db.list_active_jobs(),
    }


@app.post("/api/projects")
def api_create_project(req: NewProjectRequest):
    """プロジェクトを作って発言を保存し、AI応答はキューに積んですぐ返す。"""
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="作りたいものを入力してください")
    title = message if len(message) <= 30 else message[:30] + "…"
    project = db.create_project(title)
    message_id = db.add_message(project["id"], "user", message)
    if not req.start:
        # 写真の登録を待つ。AIはまだ動かさない。
        return {"project": db.get_project(project["id"]), "job": None}
    db.attach_to_message(project["id"], message_id)
    job = jobs.enqueue(project["id"])
    return {"project": db.get_project(project["id"]), "job": job}


@app.post("/api/projects/{project_id}/start")
def api_start_project(project_id: int):
    """写真の登録が終わったプロジェクトのAI応答を開始する。"""
    _project_or_404(project_id)
    messages = db.list_messages(project_id)
    user_messages = [m for m in messages if m["role"] == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="発言がありません")
    db.attach_to_message(project_id, user_messages[-1]["id"])
    return {"job": jobs.enqueue(project_id), "project": db.get_project(project_id)}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: int):
    project = _project_or_404(project_id)
    attachments = db.list_attachments(project_id)
    by_message: dict[int, list] = {}
    for a in attachments:
        if a["message_id"] is not None:
            by_message.setdefault(a["message_id"], []).append(_public_attachment(a))

    messages = db.list_messages(project_id)
    for m in messages:
        m["images"] = by_message.get(m["id"], [])

    return {
        "project": project,
        "messages": messages,
        "models": db.list_model_versions(project_id),
        "job": jobs.status_for(project_id),
        # まだ送っていない下書きの画像
        "draft_images": [_public_attachment(a) for a in attachments if a["message_id"] is None],
    }


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: int):
    _project_or_404(project_id)
    # DBの行は外部キーで消えるが、実ファイルは残るので明示的に片付ける
    for directory in (db.project_upload_dir(project_id), db.project_model_dir(project_id)):
        shutil.rmtree(directory, ignore_errors=True)
    db.delete_project(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/messages")
def api_send_message(project_id: int, req: MessageRequest):
    """発言を保存してキューに積み、すぐ返す。AI応答は /api/jobs で待つ。"""
    _project_or_404(project_id)
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください")
    message_id = db.add_message(project_id, "user", message)
    db.attach_to_message(project_id, message_id)
    return {"job": jobs.enqueue(project_id), "project": db.get_project(project_id)}


@app.get("/api/jobs")
def api_list_jobs():
    """処理中・待機中のジョブ一覧。フロントはこれを見て進捗を表示する。"""
    return {
        "jobs": db.list_active_jobs(),
        # 所要時間の目安（過去の実績の中央値）。質問だけの往復とモデリングで大きく違う。
        "typical_seconds": {
            "question": db.typical_duration_seconds(produced_model=False),
            "modeling": db.typical_duration_seconds(produced_model=True),
        },
    }


@app.get("/api/usage")
def api_usage():
    """トークン消費量。合計と、直近24時間分。"""
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {"total": db.usage_summary(), "last_24h": db.usage_summary(since=since)}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job


@app.post("/api/projects/{project_id}/status")
def api_update_status(project_id: int, req: StatusRequest):
    _project_or_404(project_id)
    try:
        db.update_project(project_id, status=req.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="不正なステータスです")
    return {"project": db.get_project(project_id)}


# ---------- 参考画像 ----------

def _public_attachment(a: dict) -> dict:
    """フロントへ返す形。サーバー内のファイルパスは出さない。"""
    return {
        "id": a["id"],
        "note": a["note"],
        "url": f"/api/attachments/{a['id']}",
        "sent": a["message_id"] is not None,
    }


@app.post("/api/projects/{project_id}/images")
async def api_upload_image(project_id: int, file: UploadFile = File(...), note: str = Form("")):
    """写真を1枚受け取って下書きに積む。送信時にメッセージへ紐づく。"""
    _project_or_404(project_id)
    suffix = IMAGE_TYPES.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(status_code=400, detail="画像ファイル（JPEG/PNG/WebP/GIF）を選んでください")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="画像が空でした")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="画像が大きすぎます（8MBまで）")

    path = db.project_upload_dir(project_id) / f"{uuid4().hex}{suffix}"
    path.write_bytes(data)
    attachment = db.add_attachment(
        project_id, str(path), file.content_type, note.strip() or None
    )
    return _public_attachment(attachment)


@app.get("/api/attachments/{attachment_id}")
def api_get_attachment(attachment_id: int):
    attachment = db.get_attachment(attachment_id)
    if attachment is None or not Path(attachment["path"]).exists():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return FileResponse(attachment["path"], media_type=attachment["media_type"])


@app.post("/api/attachments/{attachment_id}/note")
def api_update_attachment_note(attachment_id: int, req: NoteRequest):
    if db.get_attachment(attachment_id) is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    db.update_attachment_note(attachment_id, req.note.strip() or None)
    return _public_attachment(db.get_attachment(attachment_id))


@app.delete("/api/attachments/{attachment_id}")
def api_delete_attachment(attachment_id: int):
    attachment = db.get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    Path(attachment["path"]).unlink(missing_ok=True)
    db.delete_attachment(attachment_id)
    return {"ok": True}


# ---------- モデルファイル ----------

def _model_or_404(project_id: int, version: int) -> dict:
    model = db.get_model_version(project_id, version)
    if model is None:
        raise HTTPException(status_code=404, detail="モデルが見つかりません")
    return model


@app.get("/api/projects/{project_id}/models/{version}/stl")
def api_download_stl(project_id: int, version: int):
    model = _model_or_404(project_id, version)
    if not model["stl_path"] or not Path(model["stl_path"]).exists():
        raise HTTPException(status_code=404, detail="STLファイルがありません")
    name = f"{model['title'] or 'model'}_v{version}.stl"
    return FileResponse(model["stl_path"], filename=name, media_type="model/stl")


@app.get("/api/projects/{project_id}/models/{version}/scad")
def api_download_scad(project_id: int, version: int):
    model = _model_or_404(project_id, version)
    if not Path(model["scad_path"]).exists():
        raise HTTPException(status_code=404, detail="SCADファイルがありません")
    name = f"{model['title'] or 'model'}_v{version}.scad"
    return FileResponse(model["scad_path"], filename=name, media_type="text/plain")


# ---------- 印刷 ----------

@app.get("/api/printer/status")
def api_printer_status():
    status = printer.get_status()
    status["can_print"] = slicer.slicer_configured() and printer.printer_configured()
    return status


@app.post("/api/projects/{project_id}/models/{version}/print")
def api_print_model(project_id: int, version: int):
    """STL をスライスしてプリンタへ送信し、印刷を開始する。"""
    model = _model_or_404(project_id, version)
    if not model["stl_path"] or not Path(model["stl_path"]).exists():
        raise HTTPException(status_code=404, detail="STLファイルがありません")

    stl_path = Path(model["stl_path"])
    sliced_path = stl_path.parent / f"v{version}.gcode.3mf"

    error = slicer.slice_stl(stl_path, sliced_path)
    if error:
        raise HTTPException(status_code=502, detail=error)

    # プリンタ上でのファイル名（ASCII安全な名前にする）
    remote_name = f"project{project_id}_v{version}.gcode.3mf"
    error = printer.upload_and_print(sliced_path, remote_name)
    if error:
        raise HTTPException(status_code=502, detail=error)

    db.update_project(project_id, status="printed")
    return {"ok": True, "project": db.get_project(project_id)}


@app.get("/api/version")
def api_version():
    return version.info()


# ---------- 設定 ----------

@app.get("/api/settings")
def api_get_settings():
    return {
        "settings": ai.model_config(),
        "backend": ai.backend_name(),
        "model_options": list(ai.MODEL_ALIASES),
    }


@app.post("/api/settings")
def api_update_settings(req: SettingsRequest):
    try:
        ai.save_model_config(
            req.model_mode, req.interview_model, req.modeling_model, req.web_search
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"settings": ai.model_config()}


# ---------- フロントエンド ----------

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
