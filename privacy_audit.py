"""Release gate: compare the submission against the private people corpus.

The report deliberately contains counts and file locations only. It never prints
the private token or source sentence that caused a match.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parent / "exbrain" / "entities" / "people"
DEMO_PEOPLE = HERE / "exbrain" / "entities" / "people"
TEXT_SUFFIXES = {".md", ".py", ".js", ".html", ".css", ".json", ".txt", ".bat"}
SKIP_PARTS = {".git", "__pycache__", "node_modules"}
GENERIC_TITLES = {
    "人物", "友人", "知人", "先生", "メンバー", "グループ", "コミュニティ",
    "未整理", "前提知識", "その他", "高校", "大学", "社会人", "不明", "用語集",
}
BOILERPLATE = {
    "基本情報", "特徴", "関係", "履歴", "関連", "出典", "現在の状態",
    "一行サマリ", "人物wikiの記録",
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\s\W_]+", "", value)


def private_titles() -> set[str]:
    result = set()
    for path in PRIVATE.rglob("*.md"):
        title = norm(path.stem)
        if len(title) >= 3 and path.stem not in GENERIC_TITLES:
            result.add(title)
    return result


def private_fragments() -> dict[str, str]:
    fragments: dict[str, str] = {}
    for path in PRIVATE.rglob("*.md"):
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip().lstrip("-#> ")
            if not line or any(label in line.lower() for label in BOILERPLATE):
                continue
            cleaned = norm(re.sub(r"\[\^\d+\]", "", line))
            if len(cleaned) >= 24:
                fragments.setdefault(cleaned, hashlib.sha256(cleaned.encode()).hexdigest()[:12])
    return fragments


def demo_files():
    for path in HERE.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.name == Path(__file__).name or any(p in SKIP_PARTS for p in path.parts):
            continue
        yield path


def main() -> int:
    titles = private_titles()
    fragments = private_fragments()
    entity_hits: list[str] = []
    overlap_hits: list[str] = []
    scanned = 0
    for path in demo_files():
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        normalized = norm(text)
        # Latin names must match a token boundary; otherwise a short title such
        # as "alex" would falsely match an implementation identifier like scaleX.
        if any((re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text.lower()) if token.isascii() else token in normalized) for token in titles):
            entity_hits.append(str(path.relative_to(HERE)))
        for raw in text.splitlines():
            cleaned = norm(re.sub(r"\[\^\d+\]", "", raw.strip().lstrip("-#> ")))
            if len(cleaned) >= 24 and cleaned in fragments:
                overlap_hits.append(str(path.relative_to(HERE)))
                break

    print(f"privacy audit: {scanned} text files")
    print(f"private entity matches: {len(entity_hits)} files")
    print(f"long personal-line matches: {len(overlap_hits)} files")
    for label, paths in (("entity", entity_hits), ("overlap", overlap_hits)):
        for path in sorted(set(paths)):
            print(f"  {label}: {path}")
    return 1 if entity_hits or overlap_hits else 0


if __name__ == "__main__":
    sys.exit(main())
