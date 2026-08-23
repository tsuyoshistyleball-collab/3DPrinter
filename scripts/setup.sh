#!/usr/bin/env bash
# ミニPC（Ubuntu/Debian系）への初期セットアップ。何度実行しても安全。
set -u

cd "$(dirname "$0")/.."
OK="✅"; NG="❌"; WARN="⚠️ "
MISSING=0

echo "=== T-Lab セットアップ ==="
echo

# --- Python ---
if command -v python3 >/dev/null; then
    PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo "$OK Python $PYVER"
else
    echo "$NG Python3 がありません: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# --- OpenSCAD ---
if command -v openscad >/dev/null; then
    echo "$OK OpenSCAD"
else
    echo "$WARN OpenSCAD をインストールします..."
    if sudo apt-get install -y openscad; then
        echo "$OK OpenSCAD をインストールしました"
    else
        echo "$NG OpenSCAD のインストールに失敗: 手動で sudo apt install openscad を実行してください"
        MISSING=1
    fi
fi

# --- Python 依存 ---
echo "→ Python パッケージをインストール中..."
PIP="pip3 install --quiet"
# Ubuntu 23.04+ は外部管理環境のため venv を作る
if ! pip3 install --quiet --dry-run fastapi >/dev/null 2>&1; then
    if [ ! -d .venv ]; then python3 -m venv .venv; fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PIP="pip install --quiet"
    echo "   (仮想環境 .venv を使用します)"
fi
if $PIP -r requirements.txt && $PIP bambulabs_api; then
    echo "$OK Python パッケージ"
else
    echo "$NG pip インストールに失敗しました"
    MISSING=1
fi

# --- Claude Code CLI ---
if command -v claude >/dev/null; then
    echo "$OK Claude Code CLI ($(claude --version 2>/dev/null | head -1))"
    if [ -f "$HOME/.claude/.credentials.json" ]; then
        echo "$OK Claude Code ログイン済みの形跡あり"
    else
        echo "$WARN 未ログインの可能性: \`claude\` を一度起動して Max アカウントでログインしてください"
    fi
else
    echo "$NG Claude Code CLI がありません。インストール:"
    echo "     curl -fsSL https://claude.ai/install.sh | bash"
    echo "   インストール後、\`claude\` を起動して Max アカウントでログインしてください"
    MISSING=1
fi

# --- Tailscale ---
if command -v tailscale >/dev/null; then
    if tailscale status >/dev/null 2>&1; then
        echo "$OK Tailscale (接続中)"
    else
        echo "$WARN Tailscale はありますが未接続: sudo tailscale up を実行してください"
    fi
else
    echo "$WARN Tailscale がありません（外出先からスマホで使う場合に必要）:"
    echo "     curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
fi

# --- .env ---
if [ -f .env ]; then
    echo "$OK .env"
else
    cp .env.example .env
    echo "$OK .env を .env.example から作成しました"
fi
grep -q "^ANTHROPIC_API_KEY=" .env 2>/dev/null && \
    echo "$WARN .env に ANTHROPIC_API_KEY があります。サブスクで使うなら削除してください（API従量課金が優先されます）"
grep -q "^SLICER_CMD=" .env 2>/dev/null || \
    echo "$WARN SLICER_CMD が未設定です（ワンタップ印刷に必要。README「ワンタップ印刷まで自動化する」参照）"
grep -q "^BAMBU_IP=" .env 2>/dev/null || \
    echo "$WARN BAMBU_IP 等が未設定です（プリンタ連携に必要。P2S本体でLANモード+開発者モードを有効に）"

echo
if [ "$MISSING" -eq 0 ]; then
    echo "=== 完了。動作確認: ==="
    echo "  uvicorn app.main:app --host 0.0.0.0 --port 8000"
    echo "  (venv利用時は先に: source .venv/bin/activate)"
    echo "常時起動にするには: ./scripts/install-service.sh"
else
    echo "=== $NG の項目を解消してから再実行してください ==="
    exit 1
fi
