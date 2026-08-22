"""人物Wikiデモを配信し、AI早送りを生成する小さなローカルサーバー。"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WIKI_DIR = ROOT / "人物wiki"
ENV_FILE = ROOT / ".env"


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


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

    def do_POST(self) -> None:
        if not self.path.startswith("/api/generate"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("リクエストサイズが不正です")
            request_data = json.loads(self.rfile.read(length).decode("utf-8"))
            story = call_ai(request_data)
            self.send_json({"ok": True, "story": story})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[wiki] {self.address_string()} - {fmt % args}")


def main() -> None:
    host = os.environ.get("WIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("WIKI_PORT", "8765"))
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
