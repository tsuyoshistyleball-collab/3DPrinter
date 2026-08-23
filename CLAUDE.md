# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

「T-Lab」— Bambu Lab P2S 向けのAI自動モデリングWebアプリ。ユーザーが日本語で
「作りたいもの」を伝えると、AIが壁打ち形式で仕様を詰め、OpenSCADコードを生成してSTLに変換し、
ワンタップでスライス→プリンタ送信→印刷開始まで行う。個人利用前提（認証機能なし。公開せず
Tailscale内で運用する）。UI・エラーメッセージ・コミットメッセージはすべて日本語。

## 作業リポジトリと共同編集のルール（重要）

このプロジェクトは**複数のAI（Claude / ChatGPT）が並行して触る**。作業クローンが2つある:

| 場所 | 用途 | git remote 名 |
|---|---|---|
| `~\Dev\3DPrinter` | **本番**。常時稼働のサービスがここを動かす | 相手からは `tlab` |
| `~\OneDrive\Desktop\T-Lab\3DPrinter` | ChatGPT 側の作業コピー | こちらからは `chatgpt` |

互いの変更を潰さないための手順:

1. **作業前に必ず取り込む**: `git fetch chatgpt && git log --oneline HEAD..chatgpt/<branch>`
   で相手の新しいコミットを確認し、あれば `git cherry-pick` か `git merge` してから始める。
2. **作業後は必ずコミットする**。未コミットのまま放置すると、相手が pull したときに消える。
3. **同じファイルを長時間開きっぱなしにしない**。特に `app/static/index.html` と
   `style.css` は両者が触りやすく、実際に競合した実績がある。
4. **サーバーは1つだけ**。両方のクローンから uvicorn を起動するとポート8000を奪い合い、
   古い方が表に出てユーザーを混乱させる。起動は本番クローンのタスク `AIModelingKobo` のみ。
5. 画面右上のバージョンバッジ（`app/version.py` + `/api/version`）でコミットと
   未コミット有無を確認できる。`*` が付いていたら誰かの作業が commit されていない。

## コマンド

```bash
pip install -r requirements.txt          # 依存導入（OpenSCADは別途: apt install openscad）
uvicorn app.main:app --reload            # 開発サーバー起動 → http://localhost:8000
MOCK_AI=1 uvicorn app.main:app           # AIなしで全パイプラインを動かす（課金・ログイン不要）
```

テストスイートはない。動作確認は MOCK_AI=1 で起動し、curl か Playwright で行う:

```bash
# モック対話→モデル生成まで（1通目=質問が返る、2通目でSCAD生成→STL変換まで走る）
curl -s -X POST localhost:8000/api/projects -H 'Content-Type: application/json' -d '{"message":"フックが欲しい"}'
curl -s -X POST localhost:8000/api/projects/1/messages -H 'Content-Type: application/json' -d '{"message":"おまかせ"}'

# 印刷パイプラインの配管テスト（スライサーを偽物に差し替える）
echo -e '#!/bin/sh\ncp "$1" "$2"' > /tmp/fakeslicer.sh && chmod +x /tmp/fakeslicer.sh
SLICER_CMD="/tmp/fakeslicer.sh {input} {output}" MOCK_AI=1 uvicorn app.main:app
```

UIの検証は Playwright + Chromium でスクリーンショットを撮って目視する（`#viewer canvas` の
出現でSTLプレビュー成功を判定できる）。

## アーキテクチャ

FastAPI + SQLite + 素のJS（ビルド工程なし）。中心となる1ターンの流れは `app/main.py` の
`_run_ai_turn()`。**HTTPリクエストの中では実行されない**（下記のジョブキュー参照）:

```
ユーザー発言を保存 → jobs.enqueue() で即レスポンス
    ↓（ワーカースレッド）
_run_ai_turn() → ai.chat(履歴) → 応答テキストから ai.parse_reply() が
[MODEL_TITLE] / [MODEL_SUMMARY] / [QUESTIONS] / ```scad ブロックを抽出
→ SCADがあれば modeling.render_stl() (OpenSCAD CLI) で STL化
→ model_versions にバージョン追記 (v1, v2, ...) + projects.status を更新
```

### ジョブキュー（app/jobs.py）— 応答を待たせない仕組み

モデリングは数分かかる。UIを固めないため、AI応答はワーカースレッドで処理する。

- `POST /api/projects` と `POST .../messages` は**発言を保存してジョブを積むだけ**で即座に返る。
  フロントは `GET /api/jobs` を2秒間隔で見て進捗を反映する。
- **1プロジェクト同時1件**（会話順が入れ替わらないように）、**プロジェクトをまたいで
  MAX_PARALLEL_JOBS 件まで並行**（既定3）。
- 待機中ジョブがあるプロジェクトへ追加送信しても新しいジョブは作らない。
  履歴はジョブ実行時に読み直すため、待機中のジョブがそのまま新しい発言を拾う
  （`db.create_job()` のこの挙動に依存しているので変えないこと）。
- `_run_ai_turn()` はワーカーから呼ばれるので **HTTPException を投げてはいけない**。
  例外メッセージがそのまま `jobs.error` に入り、フロントがチャットに赤字で出す。
- サーバー再起動で running のまま残ったジョブは `jobs.start()` がエラーとして片付ける。

- **AIの挙動はすべて `app/ai.py` のプロンプト定数で制御**している（質問の仕方、P2Sの
  造形サイズ256mm³、FDM印刷制約、出力フォーマット）。モデリング品質の調整はここを触る。
  共通部品（`_PRINTER_CONTEXT` / `_INTERVIEW_RULES` / `_QUESTION_FORMAT` / `_OUTPUT_FORMAT` /
  `_SCAD_RULES`）から `SYSTEM_PROMPT`（single用）/ `INTERVIEW_SYSTEM_PROMPT` /
  `MODELING_SYSTEM_PROMPT` を合成する。
- **質問は `[QUESTIONS]` ブロック（1行 = 質問文 | 選択肢 | 選択肢…）で出させる**。
  `ai.parse_questions()` が `{text, choices}` に変換し、`messages.questions` 列（JSON）へ
  保存、フロントがタップ選択+手入力のフォームとして描画する。表示用テキストからは
  ブロックを除去するため、セッションが切れて履歴を畳み直すときに質問文が失われる。
  これを補うのが `main._history_text()`。`_QUESTION_FORMAT` の書式を変えるときは
  `parse_questions()` と `_history_text()` も合わせて直すこと。
- チャット履歴には表示用テキスト（SCAD除去後）だけを保存する。コード本体は
  `model_versions` テーブルと `data/models/<project_id>/v<n>.scad|.stl` に置く。
- プロジェクトのステータス遷移は planning（相談中)→ modeled → printed の一方向。

### AIバックエンド（app/ai.py）— 最重要の設計判断

3系統を `AI_BACKEND` で切り替える（未指定時は自動判定）:

| backend | 経路 | 課金 |
|---|---|---|
| `claude-code`（既定） | Claude Agent SDK → Claude Code CLI | **Pro/Maxサブスクの利用枠**（追加費用なし） |
| `api` | anthropic SDK 直接 | APIキー従量課金 |
| `mock` | 固定応答 | なし |

claude-code バックエンドの注意点:
- `ANTHROPIC_API_KEY` が環境にあるとサブスクではなくAPI課金が優先されるため、
  **query実行中は環境変数から一時的に隠す**実装になっている。`.env` にも書かせない。
- `CLAUDE_CODE_SESSION_ID` も同時に隠す（Claude Code内から起動した場合に子セッションが
  親に固定されるのを防ぐ。通常のマシンでは存在しない変数）。
- 会話はプロジェクトごとに `projects.claude_session_id` に保存したセッションIDで
  resume する。セッションが無い場合は全履歴を1プロンプトに畳んで送る。
- api バックエンドは `claude-opus-5` + サーバーサイドフォールバック
  （拒否時 `claude-opus-4-8`）+ プロンプトキャッシュ。

### モデルの使い分け（split モード）— 2番目に重要な設計判断

`settings` テーブル（設定画面の ⚙ から変更、`ai.model_config()` が読む）で切り替える。
既定は `split`。環境変数 `MODEL_MODE` / `INTERVIEW_MODEL` / `CLAUDE_CODE_MODEL` は
「DBが空のときの初期値」でしかない点に注意。

split の1ターン（`ai._chat_split()`）:

```
ユーザー発言 → 聞き取り担当(既定 sonnet, プロジェクトのセッションを resume)
   ├ [READY_TO_MODEL] なし → 質問を返して終了（Opusは呼ばれない）
   └ [READY_TO_MODEL] + [SPEC] あり
        → モデリング担当(既定 opus, resume=None の1回きり)に [SPEC] と
          最新の .scad だけを渡す → SCAD を生成
```

- **モデリング担当には会話履歴を渡さない**。`[SPEC]` は単体で完結させる契約なので、
  聞き取り側プロンプトの「[SPEC] は単体で完結させる」指示を弱めないこと。
- 修正依頼は聞き取り担当が「変更後の完全な仕様」を `[SPEC]` に書き直して引き継ぐ。
  ベースとなる直前のコードは `main._latest_scad()` が読んで渡す。
- **モデリング呼び出しが返すセッションIDは捨てる**（`projects.claude_session_id` は
  聞き取り側の会話のもの。上書きすると会話履歴が切れる）。
- 聞き取り担当が指示を無視して ```scad を書いた場合も、そのコードは採用せず
  モデリング担当に作り直させる（安いモデルの造形品質を混ぜない）。

### 印刷パイプライン（app/slicer.py + app/printer.py）

`POST /api/projects/{id}/models/{v}/print` → SLICER_CMD テンプレート（`{input}`/`{output}`
置換）でスライサーCLIを実行 → `bambulabs_api` で FTPS アップロード（応答に "226" を含めば
成功）→ `start_print()`。**BambuのACS仕様によりサードパーティの印刷開始はLAN内の
開発者モード経由のみ**。クラウドからは不可能なので、このアプリはプリンタと同一LANで
動かすことが前提。スライサー/プリンタが未設定なら機能は自動的に隠れる（`can_print`）。

### フロントエンド（app/static/）

- three.js はCDNではなく `app/static/vendor/three/` に同梱（オフライン動作のため）。
  importmap（index.html）経由でESM解決し、プレビュー表示時に遅延importする。
  読み込み失敗してもチャット機能は生き残る設計。
- PWA（manifest + 最小sw.js）。キャッシュ戦略は意図的に持たない（常に最新を取得）。
- 900px以下でモバイルレイアウトに切り替わる（style.css の @media 1箇所）。
- `state.canPrint` は printer/status の応答（約5秒かかる）で遅れて更新されるため、
  更新時に `updatePrintButton()` を再実行する。この遅延を壊さないこと。

### DB（app/db.py）

sqlite3 標準ライブラリのみ。リクエストごとに接続を開閉。スキーマ変更は
`init_db()` 内の try/except ALTER TABLE で後方互換マイグレーションする流儀
（例: `claude_session_id` 列）。テーブル追加は SCHEMA に
`CREATE TABLE IF NOT EXISTS` を足すだけでよい（例: `settings`）。
`with connect()` ブロック内で別接続を開くと未コミットのデータが見えないので注意
（コミットはブロック終了時）。

## 環境設定

`.env`（gitignore済み）で設定。テンプレートは `.env.example` に全項目コメント付きで
ある。`data/` ディレクトリ（DB + 生成ファイル）もgitignore済み。

## 検証状態（2026-08-23 時点）

**実機検証済み**: 対話→SCAD生成→STL変換→3Dプレビューの全パイプライン
（claude-codeバックエンド実接続 + セッションresume含む）、モバイル/デスクトップUI、
PWA配信、スライス呼び出しの配管（偽スライサー）。
split モードも実機確認済み（質問ターンで Opus が呼ばれないこと、`[SPEC]` 引き継ぎで
sonnet→opus と切り替わること、修正依頼で v2 が生成されることをセッション記録で確認）。
設定画面の保存・再読込・スマホ幅表示も確認済み。

**未検証（実物がないため）**: 実スライサーCLI（SLICER_CMDの実プロファイル）と
実P2Sへの送信・印刷開始。`printer.upload_and_print()` は bambulabs_api の
シグネチャ確認のみ。**初回の実印刷時はここでの問題発生を想定**して、まず
状態表示（GET /api/printer/status）→ 手動スライスしたファイルの送信 → ワンタップ印刷
の順に切り分けるとよい。api バックエンド（ANTHROPIC_API_KEY 従量課金）も
コードレビューのみで実呼び出しは未検証。

次の拡張候補は README のロードマップ参照（応答ストリーミング、印刷完了通知など）。
