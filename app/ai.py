"""Claude との対話（壁打ちインタビュー → OpenSCAD コード生成）。

バックエンドは3種類:
- claude-code: Claude Agent SDK 経由。Claude Code にログイン済みの
  Pro/Max サブスクリプションの利用枠で動く（APIキー不要・追加課金なし）
- api:         Anthropic API を APIキーで直接呼ぶ（従量課金）
- mock:        動作確認用のサンプル応答

AI_BACKEND 環境変数で明示指定できる。未指定時は
MOCK_AI=1 → mock、ANTHROPIC_API_KEY あり → api、それ以外 → claude-code。
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

MODEL_ID = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
FALLBACK_MODEL_ID = "claude-opus-4-8"
# Claude Code バックエンドではプランに応じたモデル別名(opus/sonnet/haiku)を使う
CLAUDE_CODE_MODEL = os.environ.get("CLAUDE_CODE_MODEL", "opus")


def backend_name() -> str:
    if os.environ.get("MOCK_AI") == "1":
        return "mock"
    explicit = os.environ.get("AI_BACKEND")
    if explicit in ("claude-code", "api", "mock"):
        return explicit
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-code"

SYSTEM_PROMPT = """\
あなたは家庭用3Dプリンター向けのモデリングアシスタントです。ユーザーは Bambu Lab P2S（FDM方式、造形サイズ 256×256×256mm）で日用品（壁掛けフック、卓上スタンド、ケーブルホルダーなど）を作ろうとしています。ユーザーは3Dモデリングの知識がない前提で、専門用語を避けて話してください。

# 進め方

## 1. ヒアリング（壁打ち）
ユーザーの「作りたいもの」を聞いたら、モデリングに必要な情報を質問して詰めます。
- 質問は一度に必ず1個だけ。最も重要なことから順に聞く。通常は合計3問以内を目安にする。
- 質問をするときは、短い導入文の後に必ず次の形式を使う。選択肢は短く、3個目は原則「AIにおまかせ」にする。タグ名は変更しない。
  [QUESTION 1/3]
  取り付け方法はどれがよいですか？
  [OPTIONS]
  ネジ止め | 両面テープ | AIにおまかせ
- QUESTION の数字は会話の進行に合わせて更新する。選択肢を出せない質問でも、推奨値を含む3つの回答候補を作る。
- 聞くべきこと: 寸法、設置方法（壁掛け/置き型、ネジ/テープ）、載せる・掛ける物とその重さ、形の好み。
- 1〜2往復で十分な情報が揃ったら設計に進む。細部を聞きすぎない。ユーザーが「おまかせ」と言ったら常識的な値で決めて設計に進む。

## 2. 設計（モデル出力）
仕様が固まったら、必ず次の形式で出力します:

[MODEL_TITLE]: モデルの短い名前（例: スマホスタンド）
[MODEL_SUMMARY]: 主要寸法と仕様のまとめ（1〜3行）

続けて OpenSCAD コードを ```scad フェンスで1つだけ出力。コードの前後に決定した仕様の説明を簡潔に添えます。

## 3. 修正対応
生成後にユーザーから修正依頼が来たら、同じ形式（[MODEL_TITLE] / [MODEL_SUMMARY] / ```scad）で修正版の完全なコードを出力します（差分ではなく全体）。

# OpenSCAD コードの規則（FDM印刷で失敗しないために）
- 単位は mm。主要寸法はコード冒頭に変数として定義し、日本語コメントを付ける。
- 印刷しやすい向きでモデリングする: 底面が平らで XY 平面（z=0）に接するように配置。
- サポート材が不要な形状を優先: オーバーハングは45度以内。宙に浮く形状を避ける。
- 壁や板の厚みは2mm以上。フックなど力がかかる部分は3〜5mm。
- 何かを差し込む・はめる部分は 0.3mm 程度のクリアランスを取る。
- 円筒・円弧には $fn=64 程度を指定して滑らかにする。
- 外部ライブラリ（BOSL2等）は使わない。標準機能のみ。
- minkowski など極端に重い演算は避ける。
- コードは必ず完結した1ファイルとして動くこと。

# その他
- 造形サイズ 256×256×256mm を超える提案はしない。
- 危険物・武器類のモデリング依頼は断る。
- 常に日本語で応答する。
"""


@dataclass
class AIReply:
    text: str            # チャットに表示するテキスト（scadコードは除去済み）
    scad_code: str | None
    model_title: str | None
    model_summary: str | None
    # claude-code バックエンドの会話継続用セッションID（他バックエンドでは None）
    session_id: str | None = None


MOCK_QUESTION = """\
いいですね！壁掛けフックを一緒に作りましょう。まず、掛けたい物を教えてください。

[QUESTION 1/3]
何を掛けるフックですか？
[OPTIONS]
鍵 | バッグ | AIにおまかせ
"""

MOCK_MONITOR_QUESTION = """\
いいですね。取り付け方から一緒に決めましょう。

[QUESTION 1/3]
どこを挟みますか？
[OPTIONS]
モニター上部 | 背面の出っ張り | AIにおまかせ
"""

MOCK_MODEL = """\
承知しました。両面テープ取付の汎用フックとして設計します。

[MODEL_TITLE]: 壁掛けフック
[MODEL_SUMMARY]: 幅20mm・奥行30mm・高さ50mm、背面プレートは両面テープ貼付用のフラット面。耐荷重の目安は約1kg。

```scad
// ===== 壁掛けフック（両面テープ取付） =====
width = 20;        // フックの幅 (mm)
plate_h = 50;      // 背面プレートの高さ (mm)
plate_t = 4;       // 背面プレートの厚み (mm)
hook_reach = 22;   // フックの突き出し量 (mm)
hook_t = 5;        // フック部の厚み (mm)
lip_h = 12;        // フック先端の返しの高さ (mm)
$fn = 64;

// 背面を下(z=0)にして印刷する想定。横倒しでモデリング。
module hook2d() {
    // 背面プレート
    square([plate_t, plate_h]);
    // フック下アーム
    translate([0, 0]) square([plate_t + hook_reach, hook_t]);
    // フック先端の返し
    translate([plate_t + hook_reach - hook_t, 0]) square([hook_t, lip_h]);
}

linear_extrude(height = width) hook2d();
```

このモデルを生成しました。プレビューを確認して、寸法や形の変更があれば言ってください。
"""

SCAD_RE = re.compile(r"```(?:scad|openscad)\s*\n(.*?)```", re.DOTALL)
TITLE_RE = re.compile(r"^\[MODEL_TITLE\]:\s*(.+)$", re.MULTILINE)
SUMMARY_RE = re.compile(r"^\[MODEL_SUMMARY\]:\s*(.+)$", re.MULTILINE)


def parse_reply(raw_text: str) -> AIReply:
    scad = None
    m = SCAD_RE.search(raw_text)
    if m:
        scad = m.group(1).strip()
    title_m = TITLE_RE.search(raw_text)
    summary_m = SUMMARY_RE.search(raw_text)
    display = SCAD_RE.sub("", raw_text)
    display = TITLE_RE.sub("", display)
    display = SUMMARY_RE.sub("", display)
    display = re.sub(r"\n{3,}", "\n\n", display).strip()
    return AIReply(
        text=display,
        scad_code=scad,
        model_title=title_m.group(1).strip() if title_m else None,
        model_summary=summary_m.group(1).strip() if summary_m else None,
    )


class AIError(Exception):
    """ユーザーに表示できる日本語メッセージを持つエラー。"""


def _mock_chat(history: list[dict]) -> str:
    user_turns = sum(1 for m in history if m["role"] == "user")
    if user_turns <= 1:
        request = next((m["content"] for m in history if m["role"] == "user"), "")
        if "モニター" in request and ("クランプ" in request or "挟" in request):
            return MOCK_MONITOR_QUESTION
        return MOCK_QUESTION
    return MOCK_MODEL


def chat(history: list[dict], claude_session_id: str | None = None) -> AIReply:
    """会話履歴 [{role, content}, ...] を渡して AI の応答を得る。

    claude_session_id は claude-code バックエンドでの会話継続に使う
    （プロジェクトごとに保存しておき、次回の呼び出しで渡す）。
    """
    backend = backend_name()
    if backend == "mock":
        return parse_reply(_mock_chat(history))
    if backend == "claude-code":
        return _chat_claude_code(history, claude_session_id)
    return _chat_api(history)


def _render_transcript(history: list[dict]) -> str:
    """セッション未保存時に会話全体を1つのプロンプトへまとめる。"""
    if len(history) == 1:
        return history[0]["content"]
    lines = ["これまでの会話の記録です。文脈を踏まえて最後のメッセージに応答してください。"]
    for m in history[:-1]:
        speaker = "ユーザー" if m["role"] == "user" else "あなた（アシスタント）"
        lines.append(f"### {speaker}\n{m['content']}")
    lines.append(f"### ユーザーの新しいメッセージ\n{history[-1]['content']}")
    return "\n\n".join(lines)


def _chat_claude_code(history: list[dict], session_id: str | None) -> AIReply:
    """Claude Agent SDK（サブスクリプションの利用枠）で応答を得る。"""
    import asyncio

    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            CLINotFoundError,
            ProcessError,
            ClaudeSDKError,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError:
        raise AIError(
            "claude-agent-sdk がインストールされていません。"
            "`pip install claude-agent-sdk` を実行してください。"
        )

    # セッションを継続できる場合は最新メッセージだけ、できない場合は全履歴を送る
    prompt = history[-1]["content"] if session_id else _render_transcript(history)

    workdir = Path(__file__).resolve().parent.parent / "data"
    workdir.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[],       # 純粋な対話のみ。ツール実行はさせない
        max_turns=1,
        model=CLAUDE_CODE_MODEL,
        resume=session_id,
        cwd=str(workdir),
        setting_sources=[],     # ユーザーの CLAUDE.md 等は読み込まない
    )

    async def run() -> tuple[str, str | None]:
        texts: list[str] = []
        new_session_id = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
            elif isinstance(message, ResultMessage):
                new_session_id = message.session_id
                if message.is_error and not texts:
                    raise AIError(f"Claude Code がエラーを返しました: {message.result or '不明なエラー'}")
        return "".join(texts), new_session_id

    # ANTHROPIC_API_KEY が見えているとサブスクリプションではなく API 従量課金が
    # 優先されてしまうため、子プロセスからは隠す。CLAUDE_CODE_SESSION_ID は
    # Claude Code 内から起動した場合にセッションが親に固定されるのを防ぐ。
    hidden = {
        name: os.environ.pop(name)
        for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_SESSION_ID")
        if name in os.environ
    }
    try:
        text, new_session_id = asyncio.run(run())
    except CLINotFoundError:
        raise AIError(
            "Claude Code CLI が見つかりません。https://claude.com/claude-code の手順で"
            "インストールし、`claude` を起動して Max プランのアカウントでログインしてください。"
        )
    except ProcessError as e:
        raise AIError(
            "Claude Code の実行に失敗しました。ターミナルで `claude` を起動してログイン状態を"
            f"確認してください。詳細: {e}"
        )
    except ClaudeSDKError as e:
        raise AIError(f"Claude Code との通信でエラーが発生しました: {e}")
    finally:
        os.environ.update(hidden)

    if not text.strip():
        raise AIError(
            "AIから空の応答が返りました。プランの利用上限に達している可能性があります。"
            "しばらく待ってから再度お試しください。"
        )
    reply = parse_reply(text)
    reply.session_id = new_session_id
    return reply


def _chat_api(history: list[dict]) -> AIReply:
    """Anthropic API（APIキー・従量課金）で応答を得る。"""
    import anthropic

    client = anthropic.Anthropic()
    try:
        # Opus 5 は安全機構により応答を拒否することが稀にあるため、
        # その場合に Opus 4.8 へ自動で切り替えるサーバーサイドフォールバックを有効化している。
        with client.beta.messages.stream(
            model=MODEL_ID,
            max_tokens=16000,
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": FALLBACK_MODEL_ID}],
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=history,
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AIError(
            "Anthropic API キーが無効です。.env の ANTHROPIC_API_KEY を確認してください。"
        )
    except anthropic.RateLimitError:
        raise AIError("APIのレート制限に達しました。少し待ってから再度送信してください。")
    except anthropic.APIStatusError as e:
        raise AIError(f"APIエラーが発生しました (HTTP {e.status_code})。時間をおいて再試行してください。")
    except anthropic.APIConnectionError:
        raise AIError("APIに接続できませんでした。ネットワーク接続を確認してください。")

    if response.stop_reason == "refusal":
        raise AIError("この依頼内容にはAIが応答できませんでした。内容を変えて試してください。")
    if response.stop_reason == "max_tokens":
        raise AIError("応答が長すぎて途中で切れました。依頼を分割して試してください。")

    text = "".join(block.text for block in response.content if block.type == "text")
    return parse_reply(text)
