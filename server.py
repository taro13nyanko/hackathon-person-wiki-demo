"""人物Wikiデモを配信し、AI早送りを生成する小さなローカルサーバー。"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WIKI_DIR = ROOT / "人物wiki"
ENV_FILE = ROOT / ".env"
RATE_LOCK = threading.Lock()
RATE_BUCKETS: dict[str, list[float]] = {}


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        name = key.strip()
        if not os.environ.get(name, "").strip():
            os.environ[name] = value.strip().strip('"').strip("'")


load_env_file()


def allowed_origins() -> set[str]:
    configured = os.environ.get(
        "ALLOWED_ORIGINS",
        "https://taro13nyanko.github.io,http://127.0.0.1:8765,http://localhost:8765",
    )
    return {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}


def check_rate_limit(client: str) -> tuple[bool, int]:
    window = max(10, int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")))
    maximum = max(1, int(os.environ.get("RATE_LIMIT_REQUESTS", "5")))
    now = time.time()
    with RATE_LOCK:
        recent = [stamp for stamp in RATE_BUCKETS.get(client, []) if now - stamp < window]
        if len(recent) >= maximum:
            retry_after = max(1, int(window - (now - recent[0])))
            RATE_BUCKETS[client] = recent
            return False, retry_after
        recent.append(now)
        RATE_BUCKETS[client] = recent
    return True, 0


def validate_request_data(request_data: object) -> dict:
    if not isinstance(request_data, dict):
        raise ValueError("リクエスト形式が不正です")
    settings = request_data.get("settings")
    records = request_data.get("records")
    if not isinstance(settings, dict) or not isinstance(records, list):
        raise ValueError("設定または人物記録が不足しています")
    if len(records) > 100:
        raise ValueError("一度に処理できる人物は100人までです")
    notes = settings.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 500:
        raise ValueError("備考は500文字以内にしてください")
    return request_data


def story_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "slides"],
        "properties": {
            "title": {"type": "string"},
            "slides": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["date", "heading", "narration", "sourceEvents"],
                    "properties": {
                        "date": {"type": "string"},
                        "heading": {"type": "string"},
                        "narration": {"type": "string"},
                        "sourceEvents": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }


def extract_response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def parse_json_text(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def call_ai(request_data: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    api_url = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    settings = request_data.get("settings", {})
    records = request_data.get("records", [])
    prompt_data = {
        "generationSettings": settings,
        "records": records,
    }
    instructions = """あなたは人物Wikiの記録を短い物語へ編集するナラティブエディターです。
入力された記録だけを事実の根拠として使ってください。未記録の場所、会話、告白、事件は作らないでください。
出来事の箇条書きではなく、各スライドが前後につながる物語にしてください。同じ見出しを繰り返さないでください。
期間外、対象外の情報は使わないでください。sourceEventsには根拠にした日付と人物名を短く入れてください。
generationSettingsのnotesが空でない場合は、記録の範囲内でその希望を反映してください。
focusPersonが神谷ハルの場合は本人自身の人生の早送りです。他人との関係を語る定型文を使わず、高校時代、卒業後、30歳の現在という本人の選択と変化を一人称でまとめてください。「神谷ハルと過ごした時間」「自分と過ごした時間」「今の私の一部」という表現は禁止します。
内容を必ず3枚にまとめてください。"""
    body = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(prompt_data, ensure_ascii=False),
        "store": False,
        "max_output_tokens": 5000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "fast_forward_story",
                "strict": True,
                "schema": story_schema(),
            }
        },
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API error {exc.code}: {detail[:600]}") from exc
    text = extract_response_text(raw)
    if not text:
        raise RuntimeError("AI APIの応答から文章を取得できませんでした")
    return parse_json_text(text)


class WikiHandler(SimpleHTTPRequestHandler):
    def request_origin(self) -> str:
        return self.headers.get("Origin", "").rstrip("/")

    def cors_origin(self) -> str:
        origin = self.request_origin()
        return origin if origin and origin in allowed_origins() else ""

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean in ("", "/"):
            return str(WIKI_DIR / "people_wiki_ver1.0.html")
        candidate = (ROOT / clean.lstrip("/")).resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            return str(ROOT / "__not_found__")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        origin = self.cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/health"):
            self.send_json({
                "ok": True,
                "aiConfigured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            })
            return
        super().do_GET()

    def do_OPTIONS(self) -> None:
        if not self.cors_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_POST(self) -> None:
        if not self.path.startswith("/api/generate"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            client = forwarded or self.client_address[0]
            permitted, retry_after = check_rate_limit(client)
            if not permitted:
                self.send_json({"ok": False, "error": "生成回数が多すぎます。少し待ってから再度お試しください。"}, status=429)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("リクエストサイズが不正です")
            request_data = validate_request_data(json.loads(self.rfile.read(length).decode("utf-8")))
            story = call_ai(request_data)
            self.send_json({"ok": True, "story": story})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            print(f"[wiki] AI generation failed: {exc}")
            self.send_json(
                {"ok": False, "error": "AI生成に失敗しました。しばらく待ってからもう一度お試しください。"},
                status=502,
            )

    def log_message(self, fmt: str, *args) -> None:
        print(f"[wiki] {self.address_string()} - {fmt % args}")


def main() -> None:
    host = os.environ.get("WIKI_HOST", "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("WIKI_PORT", "8765")))
    server = ThreadingHTTPServer((host, port), WikiHandler)
    print(f"人物Wiki: http://{host}:{port}")
    print("終了するには Ctrl+C を押してください")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n人物Wikiを終了しました")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
