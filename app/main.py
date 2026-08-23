"""AIモデリングアプリのAPIサーバー。

起動: uvicorn app.main:app --reload
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 他モジュールが環境変数を読む前に .env を反映する

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai, db, modeling, printer, slicer, version

app = FastAPI(title="T-Lab")

db.init_db()


class NewProjectRequest(BaseModel):
    message: str


class MessageRequest(BaseModel):
    message: str


class StatusRequest(BaseModel):
    status: str


class SettingsRequest(BaseModel):
    model_mode: str
    interview_model: str
    modeling_model: str


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


def _run_ai_turn(project_id: int, user_message: str) -> dict:
    """ユーザー発言を保存し、AI応答（必要ならモデル生成）まで行う。"""
    db.add_message(project_id, "user", user_message)
    project = db.get_project(project_id)
    history = [
        {"role": m["role"], "content": _history_text(m)}
        for m in db.list_messages(project_id)
    ]
    try:
        reply = ai.chat(
            history,
            claude_session_id=project.get("claude_session_id"),
            previous_scad=_latest_scad(project_id),
        )
    except ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if reply.session_id:
        db.update_project(project_id, claude_session_id=reply.session_id)

    new_model = None
    display_text = reply.text
    if reply.scad_code:
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
    return {"projects": db.list_projects(), "openscad_available": modeling.openscad_available()}


@app.post("/api/projects")
def api_create_project(req: NewProjectRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="作りたいものを入力してください")
    title = message if len(message) <= 30 else message[:30] + "…"
    project = db.create_project(title)
    result = _run_ai_turn(project["id"], message)
    return {"project": result["project"], "reply": result["reply"], "model": result["model"]}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: int):
    project = _project_or_404(project_id)
    return {
        "project": project,
        "messages": db.list_messages(project_id),
        "models": db.list_model_versions(project_id),
    }


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: int):
    _project_or_404(project_id)
    db.delete_project(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/messages")
def api_send_message(project_id: int, req: MessageRequest):
    _project_or_404(project_id)
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください")
    return _run_ai_turn(project_id, message)


@app.post("/api/projects/{project_id}/status")
def api_update_status(project_id: int, req: StatusRequest):
    _project_or_404(project_id)
    try:
        db.update_project(project_id, status=req.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="不正なステータスです")
    return {"project": db.get_project(project_id)}


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
        ai.save_model_config(req.model_mode, req.interview_model, req.modeling_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"settings": ai.model_config()}


# ---------- フロントエンド ----------

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
