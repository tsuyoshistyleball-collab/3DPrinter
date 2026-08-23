"""Claude との対話（壁打ちインタビュー → OpenSCAD コード生成）。

バックエンドは3種類:
- claude-code: Claude Agent SDK 経由。Claude Code にログイン済みの
  Pro/Max サブスクリプションの利用枠で動く（APIキー不要・追加課金なし）
- api:         Anthropic API を APIキーで直接呼ぶ（従量課金）
- mock:        動作確認用のサンプル応答

AI_BACKEND 環境変数で明示指定できる。未指定時は
MOCK_AI=1 → mock、ANTHROPIC_API_KEY あり → api、それ以外 → claude-code。

モデルの使い分け（settings テーブルに保存。設定画面から変更できる）:
- split（既定）: 質問の往復は安いモデル、OpenSCAD 生成だけ高性能モデルが担当する
- single:       質問もモデリングも1つのモデルが担当する（従来の動作）
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app import db

MODEL_ALIASES = ("haiku", "sonnet", "opus")
MODEL_MODES = ("split", "single")

# api バックエンド（従量課金）で使う実際のモデルID
API_MODEL_IDS = {
    "opus": os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}
FALLBACK_MODEL_ID = "claude-opus-4-8"

# 設定画面で未設定のときの既定値。環境変数でも上書きできる。
DEFAULTS = {
    "model_mode": os.environ.get("MODEL_MODE", "split"),
    "interview_model": os.environ.get("INTERVIEW_MODEL", "sonnet"),
    "modeling_model": os.environ.get("CLAUDE_CODE_MODEL", "opus"),
}


def backend_name() -> str:
    if os.environ.get("MOCK_AI") == "1":
        return "mock"
    explicit = os.environ.get("AI_BACKEND")
    if explicit in ("claude-code", "api", "mock"):
        return explicit
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-code"


def model_config() -> dict:
    """保存済みの設定（なければ既定値）を返す。不正な値は既定値に落とす。"""
    saved = db.get_settings()
    cfg = {key: (saved.get(key) or default) for key, default in DEFAULTS.items()}
    if cfg["model_mode"] not in MODEL_MODES:
        cfg["model_mode"] = "split"
    for key in ("interview_model", "modeling_model"):
        if cfg[key] not in MODEL_ALIASES:
            cfg[key] = DEFAULTS[key] if DEFAULTS[key] in MODEL_ALIASES else "opus"
    return cfg


def save_model_config(model_mode: str, interview_model: str, modeling_model: str) -> None:
    if model_mode not in MODEL_MODES:
        raise ValueError(f"不正なモードです: {model_mode}")
    for name in (interview_model, modeling_model):
        if name not in MODEL_ALIASES:
            raise ValueError(f"不正なモデルです: {name}")
    db.set_settings(
        {
            "model_mode": model_mode,
            "interview_model": interview_model,
            "modeling_model": modeling_model,
        }
    )


# ---------- プロンプト ----------

_PRINTER_CONTEXT = """\
ユーザーは Bambu Lab P2S（FDM方式、造形サイズ 256×256×256mm）で日用品（壁掛けフック、卓上スタンド、ケーブルホルダーなど）を作ろうとしています。ユーザーは3Dモデリングの知識がない前提で、専門用語を避けて話してください。常に日本語で応答します。"""

_INTERVIEW_RULES = """\
- 質問は一度に2〜4個まで。
- 聞くべきこと: 寸法、設置方法（壁掛け/置き型、ネジ/テープ）、載せる・掛ける物とその重さ、形の好み。
- 1〜2往復で十分な情報が揃ったら設計に進む。細部を聞きすぎない。ユーザーが「おまかせ」と言ったら常識的な値で決めて設計に進む。"""

# 質問はスマホでタップして答えられるよう、必ず機械可読な形式で出させる
_QUESTION_FORMAT = """\
質問するときは、1〜2行の短い前置きに続けて、必ず次の形式で出力します:

[QUESTIONS]
質問文 | 選択肢1 | 選択肢2 | 選択肢3
質問文 | 選択肢1 | 選択肢2 | おまかせ
[/QUESTIONS]

この形式の厳守事項:
- 1行が1つの質問。行頭に番号や記号（1. や - ）は付けない。
- 質問文と選択肢は半角の `|` で区切る。質問文・選択肢の中で `|` と改行は使わない。
- 選択肢は2〜4個。最後は必ず「おまかせ」にする。
- 選択肢には具体的な数値や寸法を入れる（悪い例:「大きめ」/ 良い例:「幅120mm（3本掛け）」）。
- 選択肢は短く保つ（20文字程度まで）。長い補足は前置きに書く。
- 質問が1つもないターンでは [QUESTIONS] ブロックを出さない。
- 強調したい語は **太字** で書ける。それ以外の記法（見出し、表、コードブロック）は使わない。"""

_OUTPUT_FORMAT = """\
[MODEL_TITLE]: モデルの短い名前（例: スマホスタンド）
[MODEL_SUMMARY]: 主要寸法と仕様のまとめ（1〜3行）

続けて OpenSCAD コードを ```scad フェンスで1つだけ出力。コードの前後に決定した仕様の説明を簡潔に添えます。"""

_SCAD_RULES = """\
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
- 造形サイズ 256×256×256mm を超える提案はしない。"""

# single モード（1つのモデルが質問もコード生成も担当）
SYSTEM_PROMPT = f"""\
あなたは家庭用3Dプリンター向けのモデリングアシスタントです。{_PRINTER_CONTEXT}

# 進め方

## 1. ヒアリング（壁打ち）
ユーザーの「作りたいもの」を聞いたら、モデリングに必要な情報を質問して詰めます。
{_INTERVIEW_RULES}

{_QUESTION_FORMAT}

## 2. 設計（モデル出力）
仕様が固まったら、必ず次の形式で出力します:

{_OUTPUT_FORMAT}

## 3. 修正対応
生成後にユーザーから修正依頼が来たら、同じ形式（[MODEL_TITLE] / [MODEL_SUMMARY] / ```scad）で修正版の完全なコードを出力します（差分ではなく全体）。

{_SCAD_RULES}

# その他
- 危険物・武器類のモデリング依頼は断る。
"""

# split モードの聞き取り担当（安いモデル）。コードは書かせない。
INTERVIEW_SYSTEM_PROMPT = f"""\
あなたは家庭用3Dプリンター向けのモデリングアシスタントの「聞き取り担当」です。{_PRINTER_CONTEXT}

# あなたの役割
仕様を固めるまでの会話だけを担当します。**OpenSCAD のコードは絶対に書かないでください**（コード生成は別の担当AIが行います）。

## 1. ヒアリング（壁打ち）
ユーザーの「作りたいもの」を聞いたら、モデリングに必要な情報を質問して詰めます。
{_INTERVIEW_RULES}

{_QUESTION_FORMAT}

## 2. 仕様が固まったら引き継ぐ
1〜2行で「では作ります」と伝えたうえで、応答の最後に必ず次の形式を出力します:

[READY_TO_MODEL]
[SPEC]
- 作るもの:
- 用途・使う場所:
- 全体寸法（mm）:
- 取付・設置方法:
- 形状の特徴:
- 強度・注意点:
[/SPEC]

各項目は具体的な数値と言葉で埋めます（「おまかせ」と言われた項目は常識的な値を自分で決めて書く）。
コード生成担当AIにはこの [SPEC] だけが渡されます。会話を読んでいない相手でも作れるよう、単体で完結した内容にしてください。

## 3. 修正依頼への対応
すでにモデルがある状態で修正依頼が来たら、追加の質問はせず（依頼の意味が分からないときだけ質問）、
すぐに [READY_TO_MODEL] と**変更後の完全な仕様**を [SPEC] に書いて引き継ぎます。
どこをどう変えるのかも [SPEC] 内に明記してください。

# その他
- 危険物・武器類の依頼は断る（その場合 [READY_TO_MODEL] は出さない）。
- 造形サイズ 256×256×256mm を超える提案はしない。
"""

# split モードのモデリング担当（高性能モデル）。仕様だけを受け取る1回きりの呼び出し。
MODELING_SYSTEM_PROMPT = f"""\
あなたは OpenSCAD のエキスパートです。渡された仕様から、FDM 3Dプリンターで失敗せずに印刷できる OpenSCAD コードを書きます。{_PRINTER_CONTEXT}

ユーザーへの質問はできません。渡された仕様だけで判断し、書かれていない値は常識的な値を自分で決めて、必ずコードを完成させてください。

# 出力形式（必ずこの形式で出力する）
{_OUTPUT_FORMAT}

説明はユーザー（3Dモデリングの知識がない人）向けに、決めた寸法と印刷のコツを簡潔に書きます。

{_SCAD_RULES}
"""


@dataclass
class AIReply:
    text: str            # チャットに表示するテキスト（scadコードは除去済み）
    scad_code: str | None
    model_title: str | None
    model_summary: str | None
    # claude-code バックエンドの会話継続用セッションID（他バックエンドでは None）
    session_id: str | None = None
    # [{"text": 質問文, "choices": [選択肢, ...]}, ...]。タップで答える UI に使う
    questions: list[dict] | None = None


MOCK_QUESTION = """\
いいですね！**壁掛けフック**を作りましょう。いくつか教えてください。

[QUESTIONS]
掛けたい物は何ですか？ | 鍵 | 帽子 | バッグ | おまかせ
壁への取り付け方法は？ | 両面テープ | ネジ止め | おまかせ
フックの幅は？ | 15mm（鍵用） | 25mm（バッグ用） | おまかせ
[/QUESTIONS]
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
# split モードで聞き取り担当がモデリング担当へ引き継ぐときのマーカー
READY_MARKER = "[READY_TO_MODEL]"
SPEC_RE = re.compile(r"\[SPEC\]\s*(.*?)\s*(?:\[/SPEC\]|\Z)", re.DOTALL)
# タップで答えられる質問（1行 = 質問文 | 選択肢 | 選択肢 ...）
QUESTIONS_RE = re.compile(r"\[QUESTIONS\]\s*(.*?)\s*(?:\[/QUESTIONS\]|\Z)", re.DOTALL)
_LIST_PREFIX_RE = re.compile(r"^(?:[-*・]|\d+[.)])\s*")


def parse_questions(raw_text: str) -> list[dict] | None:
    """[QUESTIONS] ブロックを {text, choices} のリストに変換する。"""
    m = QUESTIONS_RE.search(raw_text)
    if not m:
        return None
    questions = []
    for line in m.group(1).splitlines():
        parts = [p.strip() for p in line.strip().split("|")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        # 形式を守らず行頭に番号や記号を付けてきた場合に備えて剥がす
        text = _LIST_PREFIX_RE.sub("", parts[0]).strip()
        if not text:
            continue
        questions.append({"text": text, "choices": parts[1:]})
    return questions or None


def parse_reply(raw_text: str) -> AIReply:
    scad = None
    m = SCAD_RE.search(raw_text)
    if m:
        scad = m.group(1).strip()
    title_m = TITLE_RE.search(raw_text)
    summary_m = SUMMARY_RE.search(raw_text)
    questions = parse_questions(raw_text)
    display = SCAD_RE.sub("", raw_text)
    display = TITLE_RE.sub("", display)
    display = SUMMARY_RE.sub("", display)
    display = QUESTIONS_RE.sub("", display)
    display = re.sub(r"\n{3,}", "\n\n", display).strip()
    return AIReply(
        text=display,
        scad_code=scad,
        model_title=title_m.group(1).strip() if title_m else None,
        model_summary=summary_m.group(1).strip() if summary_m else None,
        questions=questions,
    )


class AIError(Exception):
    """ユーザーに表示できる日本語メッセージを持つエラー。"""


def _mock_chat(history: list[dict]) -> str:
    user_turns = sum(1 for m in history if m["role"] == "user")
    return MOCK_QUESTION if user_turns <= 1 else MOCK_MODEL


def chat(
    history: list[dict],
    claude_session_id: str | None = None,
    previous_scad: str | None = None,
) -> AIReply:
    """会話履歴 [{role, content}, ...] を渡して AI の応答を得る。

    claude_session_id は claude-code バックエンドでの会話継続に使う
    （プロジェクトごとに保存しておき、次回の呼び出しで渡す）。
    previous_scad は最新バージョンのコード。split モードで修正依頼を受けたとき、
    モデリング担当（会話を見ていない）へベースとして渡す。
    """
    backend = backend_name()
    if backend == "mock":
        return parse_reply(_mock_chat(history))
    cfg = model_config()
    if cfg["model_mode"] == "single":
        return _chat_single(history, claude_session_id, backend, cfg["modeling_model"])
    return _chat_split(history, claude_session_id, previous_scad, backend, cfg)


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


def _require_text(text: str | None) -> str:
    if not text or not text.strip():
        raise AIError(
            "AIから空の応答が返りました。プランの利用上限に達している可能性があります。"
            "しばらく待ってから再度お試しください。"
        )
    return text


def _chat_single(
    history: list[dict], session_id: str | None, backend: str, model: str
) -> AIReply:
    """1つのモデルが質問もコード生成も担当する（従来の動作）。"""
    if backend == "claude-code":
        prompt = history[-1]["content"] if session_id else _render_transcript(history)
        text, new_session_id = _claude_code_call(SYSTEM_PROMPT, prompt, model, session_id)
    else:
        text = _api_call(SYSTEM_PROMPT, history, model)
        new_session_id = None
    reply = parse_reply(_require_text(text))
    reply.session_id = new_session_id
    return reply


def _strip_handoff(raw: str) -> str:
    """引き継ぎ用のマーカーと [SPEC] ブロックを表示用テキストから取り除く。"""
    text = SPEC_RE.sub("", raw).replace(READY_MARKER, "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_spec(raw: str) -> str | None:
    """聞き取り担当がモデリングへ引き継ぐ意思を示していれば、その仕様を返す。"""
    if READY_MARKER not in raw:
        return None
    m = SPEC_RE.search(raw)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # マーカーはあるが [SPEC] ブロックがない場合は本文をそのまま仕様として渡す
    return _strip_handoff(raw) or None


def _build_modeling_prompt(spec: str, previous_scad: str | None) -> str:
    parts = [f"次の仕様どおりの OpenSCAD コードを作成してください。\n\n{spec}"]
    if previous_scad:
        parts.append(
            "現在のバージョンのコードは次のとおりです。修正依頼の場合はこれをベースにして、"
            "差分ではなく完全な新しいコードを出力してください。\n\n"
            f"```scad\n{previous_scad}\n```"
        )
    return "\n\n".join(parts)


def _chat_split(
    history: list[dict],
    session_id: str | None,
    previous_scad: str | None,
    backend: str,
    cfg: dict,
) -> AIReply:
    """質問は安いモデル、OpenSCAD 生成だけ高性能モデルに担当させる。"""
    # --- 1. 聞き取り（安いモデル。プロジェクトの会話セッションを継続する）---
    if backend == "claude-code":
        prompt = history[-1]["content"] if session_id else _render_transcript(history)
        raw, new_session_id = _claude_code_call(
            INTERVIEW_SYSTEM_PROMPT, prompt, cfg["interview_model"], session_id
        )
    else:
        raw = _api_call(INTERVIEW_SYSTEM_PROMPT, history, cfg["interview_model"])
        new_session_id = None
    raw = _require_text(raw)

    spec = _extract_spec(raw)
    reply = parse_reply(_strip_handoff(raw))
    reply.session_id = new_session_id

    # 聞き取り担当が指示に反してコードを書いた場合も、そのコードは採用せず
    # モデリング担当に作り直させる（安いモデルの造形品質を混ぜないため）。
    if spec is None and reply.scad_code:
        spec = "\n\n".join(filter(None, [reply.model_summary, reply.text]))
    if spec is None:
        reply.scad_code = None
        return reply  # まだ質問中

    # --- 2. モデリング（高性能モデル。仕様だけを渡す1回きりの呼び出し）---
    modeling_prompt = _build_modeling_prompt(spec, previous_scad)
    if backend == "claude-code":
        # 会話セッションは聞き取り担当のものを維持する。ここで返るセッションIDは捨てる。
        raw_model, _ = _claude_code_call(
            MODELING_SYSTEM_PROMPT, modeling_prompt, cfg["modeling_model"], None
        )
    else:
        raw_model = _api_call(
            MODELING_SYSTEM_PROMPT,
            [{"role": "user", "content": modeling_prompt}],
            cfg["modeling_model"],
        )

    modeled = parse_reply(raw_model or "")
    if not modeled.scad_code:
        reply.text = (
            f"{reply.text}\n\n⚠️ モデリング担当のAIがコードを返しませんでした。"
            "もう一度送信してみてください。"
        ).strip()
        reply.scad_code = None
        return reply

    return AIReply(
        text="\n\n".join(t for t in (reply.text, modeled.text) if t),
        scad_code=modeled.scad_code,
        model_title=modeled.model_title,
        model_summary=modeled.model_summary,
        session_id=new_session_id,
    )


def _claude_code_call(
    system_prompt: str, prompt: str, model: str, session_id: str | None
) -> tuple[str, str | None]:
    """Claude Agent SDK（サブスクリプションの利用枠）を1往復だけ呼ぶ。"""
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

    workdir = Path(__file__).resolve().parent.parent / "data"
    workdir.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=[],       # 純粋な対話のみ。ツール実行はさせない
        max_turns=1,
        model=model,
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
        return asyncio.run(run())
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


def _api_call(system_prompt: str, messages: list[dict], model: str) -> str:
    """Anthropic API（APIキー・従量課金）を呼ぶ。"""
    import anthropic

    client = anthropic.Anthropic()
    # Opus 5 は安全機構により応答を拒否することが稀にあるため、その場合に
    # Opus 4.8 へ切り替えるサーバーサイドフォールバックを有効にする。
    extra = (
        {
            "betas": ["server-side-fallback-2026-06-01"],
            "fallbacks": [{"model": FALLBACK_MODEL_ID}],
        }
        if model == "opus"
        else {}
    )
    try:
        with client.beta.messages.stream(
            model=API_MODEL_IDS[model],
            max_tokens=16000,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            **extra,
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

    return "".join(block.text for block in response.content if block.type == "text")
