"""展示用デモの汎用タグ抽出。固有の学校・部活動マッピングは含めない。"""

import re


def tags_from_norm(value):
    """脚注と括弧内の補足を除き、値があれば単一タグとして返す。"""
    if not value:
        return []
    cleaned = re.sub(r"\[\^\d+\]", "", str(value)).strip()
    cleaned = re.sub(r"（.*?）", "", cleaned).strip()
    return [cleaned] if cleaned else []
