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

from app import ai, db, modeling, printer

app = FastAPI(title="AI Auto Modeling for Bambu Lab P2S")

db.init_db()


class NewProjectRequest(BaseModel):
    message: str


class MessageRequest(BaseModel):
    message: str


class StatusRequest(BaseModel):
    status: str


def _project_or_404(project_id: int) -> dict:
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="プロジェクトが見つかりません")
    return project


def _run_ai_turn(project_id: int, user_message: str) -> dict:
    """ユーザー発言を保存し、AI応答（必要ならモデル生成）まで行う。"""
    db.add_message(project_id, "user", user_message)
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.list_messages(project_id)
    ]
    try:
        reply = ai.chat(history)
    except ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))

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
    db.add_message(project_id, "assistant", display_text)

    return {
        "reply": display_text,
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


# ---------- プリンタ ----------

@app.get("/api/printer/status")
def api_printer_status():
    return printer.get_status()


# ---------- フロントエンド ----------

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
