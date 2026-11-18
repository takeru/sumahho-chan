#!/usr/bin/env python3
"""Rakuten Hand を「生きてるAIエージェント」にする MVP ループ。

Termux 上で動かす前提:
  - 聞く: termux-speech-to-text (Termux:API / Android音声認識・日本語)
  - 考える: Claude API (ANTHROPIC_API_KEY を環境変数で渡す)
  - 喋る: termux-tts-speak (Android標準TTS・日本語)

使い方:
  export ANTHROPIC_API_KEY=...   # 端末上でだけ入力する。チャットに貼らない
  python agent.py

Enter を押すと聞き取り開始。「おわり」と言うか Ctrl+C で終了。
"""

import json
import os
import queue
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 300
HISTORY_TURNS = 6  # 直近の会話だけ覚える（音声対話なので短く）

SYSTEM = (
    "あなたの名前はスマッホチャン。机の上に住んでいる小さなAIエージェントです。"
    "楽天ハンドという中古スマホがあなたの身体です。"
    "返事は音声で読み上げられるので、短く、自然な話し言葉の日本語で答えてください。"
    "原則2〜3文まで。箇条書きや記号は使わないでください。"
)


# ---- 首と顔への意図キュー -------------------------------------------------
# agent は「意図」だけ出す。実際のサーボ制御は首側 (ATOM Lite)、顔表示と
# BLE 転送は face.html 側が担当する(face.html が /intent をポーリング)。
INTENTS: "queue.Queue[str]" = queue.Queue()

# (旧 Wi-Fi 版の首への直送先。BLE 版では未設定でよい: 意図キュー経由で
#  face.html が BLE 転送する)
NECK_URL = os.environ.get("NECK_URL", "").rstrip("/")


def look(cmd: str) -> None:
    """首へのコマンド: PAN/TILT/LOOK/CENTER/NOD/SHAKE/SWAY (NECK.md 参照)"""
    if NECK_URL:
        try:
            req = urllib.request.Request(
                f"{NECK_URL}/cmd", data=cmd.encode("utf-8"), method="POST"
            )
            urllib.request.urlopen(req, timeout=3).close()
            return
        except Exception as exc:  # 首に届かなくても会話は止めない
            print(f"(neck error: {exc})", file=sys.stderr)
    INTENTS.put(cmd)


def face(state: str) -> None:
    """顔の状態: normal / happy / sleep / talk"""
    INTENTS.put(f"FACE:{state}")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler の規約)
        if self.path == "/intent":
            try:
                cmd = INTENTS.get_nowait()
            except queue.Empty:
                cmd = None
            body = json.dumps({"cmd": cmd}).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/face.html"):
            html = Path(__file__).with_name("face.html").read_bytes()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args) -> None:
        pass


def start_face_server(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"顔UI: http://127.0.0.1:{port}/ をスマホの Chrome で開いてください")


def speak(text: str) -> None:
    print(f"🤖 {text}")
    face("talk")
    subprocess.run(["termux-tts-speak", text], check=False)
    face("normal")


def listen() -> str:
    """Android音声認識で1発話ぶん聞き取って文字列で返す。"""
    result = subprocess.run(
        ["termux-speech-to-text"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def ask_claude(history: list[dict]) -> str:
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": history,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()


def main() -> None:
    if "ANTHROPIC_API_KEY" not in os.environ:
        sys.exit("ANTHROPIC_API_KEY が未設定です。端末上で export してください。")
    history: list[dict] = []
    start_face_server()
    look("CENTER")
    speak("おはようございます。エンターを押してから話しかけてください。")
    while True:
        try:
            input("\n[Enter] で聞き取り開始 > ")
        except (EOFError, KeyboardInterrupt):
            speak("おつかれさまでした。")
            return
        user_text = listen()
        if not user_text:
            speak("うまく聞き取れませんでした。")
            continue
        print(f"🗣  {user_text}")
        if user_text.rstrip("。").endswith("おわり"):
            speak("おつかれさまでした。")
            return
        history.append({"role": "user", "content": user_text})
        history = history[-HISTORY_TURNS * 2:]
        try:
            reply = ask_claude(history)
        except Exception as exc:  # ネットワーク/APIエラーでもループは止めない
            speak("ごめんなさい、今ちょっと頭がつながりません。")
            print(f"(error: {exc})", file=sys.stderr)
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        look("NOD")  # 返事の合図にうなずく
        speak(reply)


if __name__ == "__main__":
    main()
