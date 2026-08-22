"""Claude との対話（壁打ちインタビュー → OpenSCAD コード生成）。"""

import os
import re
from dataclasses import dataclass

MODEL_ID = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
FALLBACK_MODEL_ID = "claude-opus-4-8"
MOCK_AI = os.environ.get("MOCK_AI", "") == "1"

SYSTEM_PROMPT = """\
あなたは家庭用3Dプリンター向けのモデリングアシスタントです。ユーザーは Bambu Lab P2S（FDM方式、造形サイズ 256×256×256mm）で日用品（壁掛けフック、卓上スタンド、ケーブルホルダーなど）を作ろうとしています。ユーザーは3Dモデリングの知識がない前提で、専門用語を避けて話してください。

# 進め方

## 1. ヒアリング（壁打ち）
ユーザーの「作りたいもの」を聞いたら、モデリングに必要な情報を質問して詰めます。
- 質問は一度に2〜4個まで。番号付きで、選択肢や推奨値を添えて答えやすくする（例:「幅はどれくらいですか？ 目安: スマホ用なら80mm程度」）。
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


MOCK_QUESTION = """\
いいですね！壁掛けフックを作りましょう。いくつか教えてください。

1. **掛けたい物は何ですか？**（例: 鍵 / 帽子 / バッグ / ケーブル）
2. **壁への取り付け方法は？**（A: ネジ止め / B: 両面テープ）
3. **フックの幅の希望はありますか？** 目安: 鍵なら15mm、バッグなら25mm程度

「おまかせ」と言っていただければ、汎用的な仕様で作ります。
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
    return MOCK_QUESTION if user_turns <= 1 else MOCK_MODEL


def chat(history: list[dict]) -> AIReply:
    """会話履歴 [{role, content}, ...] を渡して AI の応答を得る。"""
    if MOCK_AI:
        return parse_reply(_mock_chat(history))

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
