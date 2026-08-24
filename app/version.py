"""アプリのバージョン情報。

APP_VERSION は機能を足したときに手で上げる。git のコミットと作業ツリーの状態も
返すので、「いま動いている画面がどのコードなのか」「未コミットの変更が乗っているか」を
画面右上のバッジから確認できる。複数人（複数のAI）で同じリポジトリを触るときに、
自分が見ている版を取り違えないための表示。
"""

import subprocess
from pathlib import Path

APP_VERSION = "1.4.0"

_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # git が無い / リポジトリでない
    return result.stdout.strip() if result.returncode == 0 else None


def info() -> dict:
    status = _git("status", "--porcelain")
    return {
        "version": APP_VERSION,
        "commit": _git("rev-parse", "--short", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
        "changed_files": len(status.splitlines()) if status else 0,
    }
