# ミニPC セットアップ手順（引き継ぎガイド）

自宅のミニPCでこのアプリを常時稼働させ、スマホから使えるようにするまでの手順です。
OS は **Ubuntu Desktop / Server 24.04 LTS** を推奨します（無料・軽量・以下の手順が
そのまま使えます）。Windows で動かす場合は最後の補足を参照。

## 1. リポジトリを取得

```bash
sudo apt update && sudo apt install -y git
git clone -b claude/ai-auto-modeling-3d-printer-dv2g60 https://github.com/tsuyoshistyleball-collab/3DPrinter.git
cd 3DPrinter
```

## 2. セットアップスクリプトを実行

```bash
./scripts/setup.sh
```

OpenSCAD・Python依存の導入、`.env` の作成まで自動で行い、
足りないもの（Claude Code / Tailscale など）をチェックリストで表示します。
❌ や ⚠️ が出たら、表示されたコマンドで解消して再実行してください。

## 3. Claude Code にログイン（AIをMaxサブスクで動かす）

```bash
curl -fsSL https://claude.ai/install.sh | bash   # 未導入の場合
claude   # 起動するとブラウザが開く → Maxプランのアカウントでログイン → 終了してOK
```

> `.env` に `ANTHROPIC_API_KEY` を書かないこと（書くとサブスクではなく従量課金になります）。

## 4. Tailscale（スマホからのアクセス経路）

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up   # 表示されるURLをブラウザで開いてログイン
tailscale status    # このPCの名前を確認（例: minipc）
```

スマホにも Tailscale アプリを入れて **同じアカウント** でログインしておきます。

## 5. プリンタ（P2S）の設定

1. P2S 本体の画面: 設定 → ネットワーク → **LANモードを有効** + **開発者モードを有効**
2. 本体画面に表示される **IPアドレス** と **アクセスコード**、**シリアル番号** を控える
3. `.env` に記入:

   ```
   BAMBU_IP=192.168.1.xx
   BAMBU_ACCESS_CODE=xxxxxxxx
   BAMBU_SERIAL=01Sxxxxxxxxxxxx
   ```

## 6. ワンタップ印刷（スライサー設定）

1. ミニPCに Bambu Studio か OrcaSlicer をインストール
2. P2S 用のプリンタ設定・印刷設定・フィラメント設定を作成し、それぞれ JSON で
   エクスポートして `profiles/` に保存
3. `.env` に記入（例は OrcaSlicer）:

   ```
   SLICER_CMD=orca-slicer --load-settings "profiles/machine.json;profiles/process.json" --load-filaments profiles/filament.json --slice 0 --export-3mf {output} {input}
   ```

※ この手順だけ後回しでもOK。未設定の間は「STLダウンロード→Bambu Studioで印刷」の
手動フローが使えます。

## 7. 常時起動サービスとして登録

```bash
./scripts/install-service.sh
```

以降はPCを起動するだけでアプリが立ち上がります。
状態確認は `systemctl status ai-modeling`、ログは `journalctl -u ai-modeling -f`。

## 8. スマホをアプリ化

1. スマホのブラウザで `http://<ミニPCのTailscale名>:8000` を開く
2. 共有メニューから「**ホーム画面に追加**」
3. 以降はホーム画面のアイコンからアプリとして起動できます

## 9. 動作確認チェックリスト

- [ ] `http://<PC名>:8000` がスマホで開く
- [ ] 「作りたいもの」を送るとAIが質問してくる（Maxサブスクで動作）
- [ ] モデル生成後、3Dプレビューが表示される
- [ ] 画面左下に「🖨 P2S: 状態」が表示される（プリンタ設定済みの場合）
- [ ] モデル画面に「🖨 P2Sで印刷開始」ボタンが出る（スライサー+プリンタ設定済みの場合）

## このPCでの開発の続け方

リポジトリ直下で `claude` を起動すれば、`CLAUDE.md` が自動で読み込まれ、
プロジェクトの構成・設計判断・テスト方法を把握した状態で会話を始められます。
機能追加や不具合の相談はそのまま日本語で伝えてください。

```bash
cd ~/3DPrinter
claude
```

## 補足: Windows で動かす場合

- Python / OpenSCAD / Bambu Studio / Tailscale / Claude Code を各公式サイトから導入
- `pip install -r requirements.txt bambulabs_api`
- 起動: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 自動起動: タスクスケジューラで「ログオン時」に上記コマンドを登録
- `scripts/*.sh` は Linux 用のため使いません
