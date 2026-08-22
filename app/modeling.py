"""OpenSCAD コードを STL に変換する。"""

import shutil
import subprocess
from pathlib import Path

RENDER_TIMEOUT_SEC = 180


def openscad_available() -> bool:
    return shutil.which("openscad") is not None


def render_stl(scad_path: Path, stl_path: Path) -> str | None:
    """scad ファイルを STL に変換する。成功なら None、失敗ならエラーメッセージを返す。"""
    exe = shutil.which("openscad")
    if exe is None:
        return (
            "OpenSCAD がインストールされていないため STL を生成できませんでした。"
            "https://openscad.org/downloads.html からインストールして、モデルを再生成してください。"
        )
    try:
        proc = subprocess.run(
            [exe, "-o", str(stl_path), str(scad_path)],
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"モデルの変換が{RENDER_TIMEOUT_SEC}秒以内に終わりませんでした。形状を簡略化してみてください。"

    if proc.returncode != 0 or not stl_path.exists():
        # OpenSCAD はエラーを stderr に出す。長すぎる場合は末尾のみ返す。
        err = (proc.stderr or proc.stdout or "").strip()
        if len(err) > 1500:
            err = "...\n" + err[-1500:]
        return f"OpenSCAD の変換でエラーが発生しました:\n{err}"
    return None
