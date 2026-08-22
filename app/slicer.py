"""STL をスライスして印刷用 3MF (G-code) に変換する。

スライスは Bambu Studio / OrcaSlicer の CLI に任せる。プリンタ・フィラメント・
印刷設定のプロファイルが必要なため、コマンドは SLICER_CMD 環境変数で
テンプレートとして指定する（{input} と {output} が実パスに置換される）。

例（OrcaSlicer、事前にプロファイルを json でエクスポートしておく）:
  SLICER_CMD=orca-slicer --load-settings "machine.json;process.json" \
      --load-filaments filament.json --slice 0 --export-3mf {output} {input}
"""

import os
import shlex
import subprocess
from pathlib import Path

SLICE_TIMEOUT_SEC = 600


def slicer_configured() -> bool:
    return bool(os.environ.get("SLICER_CMD"))


def slice_stl(stl_path: Path, out_path: Path) -> str | None:
    """STL をスライスする。成功なら None、失敗ならエラーメッセージを返す。"""
    template = os.environ.get("SLICER_CMD")
    if not template:
        return (
            "スライサーが未設定です。.env の SLICER_CMD に Bambu Studio / OrcaSlicer の"
            "CLI コマンドを設定してください（README「スマホから印刷まで自動化する」参照）。"
        )
    if "{input}" not in template or "{output}" not in template:
        return "SLICER_CMD には {input} と {output} のプレースホルダを含めてください。"

    cmd = [
        part.replace("{input}", str(stl_path)).replace("{output}", str(out_path))
        for part in shlex.split(template)
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SLICE_TIMEOUT_SEC)
    except FileNotFoundError:
        return f"スライサーのコマンドが見つかりません: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return f"スライスが{SLICE_TIMEOUT_SEC}秒以内に終わりませんでした。"

    if proc.returncode != 0 or not out_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        if len(err) > 1200:
            err = "...\n" + err[-1200:]
        return f"スライスに失敗しました (exit {proc.returncode}):\n{err}"
    return None
