"""Bambu Lab P2S との連携（任意機能）。

LAN モード + 開発者モードを有効にしたプリンタに対して、bambulabs_api 経由で
状態取得とファイル送信・印刷開始を行う。未設定・未インストールの場合は
「未接続」として扱い、アプリ本体の動作には影響しない。
"""

import os
import time
from pathlib import Path

CONNECT_WAIT_SEC = 8


def _config() -> dict | None:
    ip = os.environ.get("BAMBU_IP")
    access_code = os.environ.get("BAMBU_ACCESS_CODE")
    serial = os.environ.get("BAMBU_SERIAL")
    if not (ip and access_code and serial):
        return None
    return {"ip": ip, "access_code": access_code, "serial": serial}


def printer_configured() -> bool:
    return _config() is not None


def get_status() -> dict:
    """プリンタの接続状態と現在の状態を返す。"""
    cfg = _config()
    if cfg is None:
        return {
            "configured": False,
            "message": "プリンタ未設定です。.env に BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL を設定すると状態を表示できます。",
        }
    try:
        import bambulabs_api as bl
    except ImportError:
        return {
            "configured": True,
            "connected": False,
            "message": "bambulabs_api がインストールされていません。`pip install bambulabs_api` を実行してください。",
        }
    try:
        printer = bl.Printer(cfg["ip"], cfg["access_code"], cfg["serial"])
        printer.connect()
        try:
            state = printer.get_state()
            bed_temp = printer.get_bed_temperature()
            nozzle_temp = printer.get_nozzle_temperature()
            percentage = printer.get_percentage()
        finally:
            printer.disconnect()
        return {
            "configured": True,
            "connected": True,
            "state": str(state),
            "bed_temperature": bed_temp,
            "nozzle_temperature": nozzle_temp,
            "progress_percent": percentage,
        }
    except Exception as e:  # 接続失敗は種類が多いためまとめてユーザー向けに返す
        return {
            "configured": True,
            "connected": False,
            "message": f"プリンタに接続できませんでした: {e}",
        }


def upload_and_print(file_path: Path, filename: str) -> str | None:
    """スライス済み 3MF をプリンタへ送信して印刷を開始する。

    成功なら None、失敗ならユーザー向けエラーメッセージを返す。
    """
    cfg = _config()
    if cfg is None:
        return (
            "プリンタが未設定です。.env に BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL を"
            "設定してください（プリンタ本体で LAN モードと開発者モードを有効にする必要があります）。"
        )
    try:
        import bambulabs_api as bl
    except ImportError:
        return "bambulabs_api がインストールされていません。`pip install bambulabs_api` を実行してください。"

    use_ams = os.environ.get("BAMBU_USE_AMS", "1") != "0"
    plate = int(os.environ.get("BAMBU_PLATE", "1"))

    printer = bl.Printer(cfg["ip"], cfg["access_code"], cfg["serial"])
    try:
        printer.connect()
        deadline = time.time() + CONNECT_WAIT_SEC
        while not printer.mqtt_client_ready() and time.time() < deadline:
            time.sleep(0.5)

        with open(file_path, "rb") as f:
            result = printer.upload_file(f, filename)
        # upload_file は FTP の応答コードを含む文字列を返す（226 = 転送成功）
        if "226" not in result:
            return f"プリンタへのファイル送信に失敗しました: {result}"

        if not printer.start_print(filename, plate, use_ams=use_ams):
            return "印刷開始コマンドの送信に失敗しました。プリンタの状態を確認してください。"
        return None
    except Exception as e:
        return f"プリンタへの送信中にエラーが発生しました: {e}"
    finally:
        try:
            printer.disconnect()
        except Exception:
            pass
