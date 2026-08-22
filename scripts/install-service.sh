#!/usr/bin/env bash
# アプリを systemd サービスとして登録し、起動時に自動実行されるようにする。
set -eu

cd "$(dirname "$0")/.."
DIR="$(pwd)"
RUN_USER="${SUDO_USER:-$USER}"

if ! command -v systemctl >/dev/null; then
    echo "❌ systemd がない環境です。手動で uvicorn を起動してください。"
    exit 1
fi

# venv があればその python を使う（Claude Code のログイン情報を持つユーザーで実行する）
if [ -x "$DIR/.venv/bin/python" ]; then
    PYTHON="$DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

sed -e "s|__USER__|$RUN_USER|g" \
    -e "s|__DIR__|$DIR|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    scripts/ai-modeling.service | sudo tee /etc/systemd/system/ai-modeling.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now ai-modeling.service

echo "✅ サービスを登録して起動しました。"
echo "   状態確認: systemctl status ai-modeling"
echo "   ログ:     journalctl -u ai-modeling -f"
echo "   このPCのTailscale名を確認: tailscale status"
echo "   → スマホから http://<このPCの名前>:8000 でアクセスできます"
