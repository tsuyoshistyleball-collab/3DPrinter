# ミニPC セットアップ手順（引き継ぎガイド）

自宅のミニPCでこのアプリを常時稼働させ、スマホから使えるようにするまでの手順です。

**Windows のミニPCで動かす場合は、末尾の「Windows のミニPCで動かす場合」を参照してください**
（専用のセットアップスクリプトがあり、検証済みです）。以下の 1〜8 は Ubuntu 24.04 LTS 向けです。

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

## Windows のミニPCで動かす場合（検証済みの手順）

`scripts/*.sh` は Linux 用です。Windows では専用スクリプトを使います。

### 1. 事前に入れるもの（それぞれ通常のインストーラ）

- [Python 3.11+](https://www.python.org/downloads/) … インストール時に「Add to PATH」にチェック
- [Git](https://git-scm.com/)
- [Tailscale](https://tailscale.com/download) … 入れたらスマホと**同じアカウント**でログイン
- Claude Code … PowerShell で `irm https://claude.ai/install.ps1 | iex` の後、`claude` を起動してMaxアカウントでログイン

> `ANTHROPIC_API_KEY` を環境変数にも `.env` にも設定しないこと（あるとサブスクではなく従量課金が優先されます）。

### 2. 取得してセットアップ

```powershell
git clone https://github.com/tsuyoshistyleball-collab/3DPrinter.git
cd 3DPrinter
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

このスクリプトが以下をまとめて行い、最後にスマホで開くURLを表示します:

- Python仮想環境(`.venv`)と依存パッケージの導入
- `.env` の作成
- OpenSCAD のポータブル版を導入（管理者権限不要。`winget` 版は昇格を求められて止まることがあるため）
- タスクスケジューラに `AIModelingKobo` を登録（**ログオン時に自動起動**）
- Tailscale serve を設定（**tailnet内限定のHTTPS**。LAN・インターネットには公開しない）
- アプリを起動して疎通確認

以降はPCを起動してログオンするだけでアプリが立ち上がります。ログは `data\server.log`。
手動で止める・動かすときは:

```powershell
Stop-ScheduledTask  -TaskName AIModelingKobo
Start-ScheduledTask -TaskName AIModelingKobo
```

### 3. 常時稼働のための設定

- スリープしないように: 設定 → システム → 電源 → 画面とスリープ を「なし」に
- OneDrive 配下にリポジトリを置かないこと（同期とgitが競合します。`~\Dev\3DPrinter` などを推奨）

### 4. このPCで開発を続ける

`claude.exe` は `~\.local\bin` にありPATHに載っていないため、フルパスで起動します:

```powershell
cd ~\Dev\3DPrinter
~\.local\bin\claude
```
