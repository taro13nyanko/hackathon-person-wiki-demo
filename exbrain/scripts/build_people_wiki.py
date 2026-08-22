import os, re, glob, json, sys, difflib, subprocess, tempfile, shutil

# 2026-07-22: どのフォルダに置かれても(クローン先のフォルダ名が「任意の親フォルダ」でなくても)
# 動くよう、パスをスクリプト自身の場所からの相対パスに変更(カレントディレクトリ非依存化)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_DIR = os.path.dirname(SCRIPT_DIR)          # exbrain/ (このリポジトリのルート)
OUTER_DIR = os.path.dirname(VAULT_DIR)            # exbrain/ の1つ上(このリポジトリを置いた場所)

PEOPLE_DIR = os.path.join(VAULT_DIR, "entities", "people")
WIKI_DIR = os.path.join(OUTER_DIR, "人物wiki")
RAW_DIR = os.path.join(VAULT_DIR, "raw")
AI_SUGGESTIONS_PATH = os.path.join(VAULT_DIR, "AI_SUGGESTIONS.md")

# 2026-08-08: 「AIからの提案」ダッシュボード用。exbrain直下のAI_SUGGESTIONS.mdを
# ビルド時に読み込み、JS側にそのまま埋め込む（週1回程度、スケジュールタスクでこの
# ファイルを書き換えて再ビルドする運用を想定。中身を変えるだけでUI側の変更は不要）。
def load_ai_suggestions():
    if os.path.exists(AI_SUGGESTIONS_PATH):
        with open(AI_SUGGESTIONS_PATH, encoding="utf-8") as f:
            return f.read()
    return ""

# club_univ_norm.py (大学名・高校時部活名のタグ抽出ロジック) をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from club_univ_norm import tags_from_norm

# 2026-08-04: サイドバー左上「人物Wiki」の横に「最終更新はいつ・誰か」を表示するための
# ビルド時情報取得。git logの最新コミット（マージコミットは実質的な中身の変更ではないため
# --no-merges で除外し、実際に内容を変更した最後のコミットを基準にする）の日時・作者から、
# 作者名をユーザー（Demo User）／共同編集者（Demo Collaborator）に判別する。取得に失敗した場合
# （gitが無い環境等）は空文字を返し、ページ上には何も表示しない。
def get_last_update_label():
    # 2026-08-05（修正）: サイドバー幅が狭いと日時込みのバッジが省略記号で切れ、
    # 肝心の「誰が」の部分が見えなくなってしまう問題があったため、表示は「誰が」だけの
    # 短いテキストにする。日時は消さず、ホバー時のtitle属性（ツールチップ）に回す。
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--no-merges", "--format=%ai|%an"],
            cwd=VAULT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=10
        ).stdout.strip()
        if not out:
            return "", ""
        date_part, author_part = out.split("|", 1)
        dt_label = date_part[:16]  # "2026-08-04 21:13"
        author = author_part.strip()
        if "Ota" in author or "共同編集者" in author:
            who = "共同編集者"
        elif "Demo User" in author or "ユーザー" in author:
            who = "ユーザー"
        else:
            # 公開デモではローカルGit設定の実名をHTMLへ出さない。
            who = "制作チーム"
        return f"最終更新: {who}", f"{dt_label}（{who}）"
    except Exception:
        return "", ""

LAST_UPDATE_LABEL, LAST_UPDATE_TOOLTIP = get_last_update_label()
LAST_UPDATE_BADGE_HTML = (
    f'<span id="lastUpdateBadge" class="last-update-badge" title="{LAST_UPDATE_TOOLTIP}">{LAST_UPDATE_LABEL}</span>'
    if LAST_UPDATE_LABEL else ""
)

# 2026-08-06:「最近更新されたページ」（showRecentUpdates）が、各ページのfrontmatter
# 「updated: YYYY-MM-DD」（日付のみ・手動記入）に依存していたため、(1) 分単位の精度がなく
# 同日中に複数ページを更新した際の順序が区別できない、(2) 編集のたびに手動でこの欄を
# 書き換える運用が徹底されておらず、実際には更新したのに古い日付のまま反映されない、
# という2つの不具合があった。この2つを根本的に解消するため、frontmatterへの依存をやめ、
# git logの実コミット日時（分単位、%ai形式でLAST_UPDATE_LABELと同じ書式に統一）を
# 正とする方式に切り替える。まだコミットされていない直近の編集は、ファイルの
# 更新日時（mtime）と比較していずれか新しい方を採用することで、コミット前でも
# 正しく「今編集したばかり」として反映されるようにする。
def get_people_file_git_times():
    times = {}
    try:
        out = subprocess.run(
            ["git", "log", "--no-merges", "--name-only", "--format=@@%ai", "--", "entities/people/"],
            cwd=VAULT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=30
        ).stdout
        current = None
        for line in out.splitlines():
            if line.startswith("@@"):
                current = line[2:][:16]  # "2026-08-06 21:13"
            elif line.strip() and current:
                if line not in times:
                    times[line] = current
    except Exception:
        pass
    return times

PEOPLE_FILE_GIT_TIMES = get_people_file_git_times()

def get_file_updated_label(path):
    import datetime as _dt
    rel = os.path.relpath(path, VAULT_DIR).replace(os.sep, "/")
    git_label = PEOPLE_FILE_GIT_TIMES.get(rel, "")
    try:
        mtime_label = _dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        mtime_label = ""
    return max(git_label, mtime_label) if git_label else mtime_label

def parse_section(body, header):
    m = re.search(rf'^##\s*{re.escape(header)}\s*$(.*?)(?=^##\s|\Z)', body, re.M | re.S)
    return m.group(1).strip() if m else ""

KNOWN_HEADERS = {"基本情報", "特徴", "価値観", "ユーザーとの関係", "共同編集者との関係", "関係", "現在の状態", "履歴",
                  "予定", "ユーザーの予定", "共同編集者の予定", "関連", "出典"}

def parse_extra_sections(body):
    # 2026-07-21追記: 「主要ページ」の一部は標準6見出し以外にページ固有の見出し
    # （例: 「## プロジェクト会議からの補強情報」）を持つ。これらは従来
    # 静かに無視され、Wikiに一切表示されないバグがあったため、標準見出し
    # （関連を含む）以外の全"## "見出しを拾って可変個のセクションとして返す。
    extras = []
    for m in re.finditer(r'^##\s*(.+?)\s*$', body, re.M):
        header = m.group(1).strip()
        if header in KNOWN_HEADERS:
            continue
        start = m.end()
        nxt = re.search(r'^##\s', body[start:], re.M)
        end = start + nxt.start() if nxt else len(body)
        text = body[start:end].strip()
        if text:
            extras.append((header, text))
    return extras

def parse_kv_list(section_text):
    kv = []
    for line in section_text.splitlines():
        line = line.strip()
        m = re.match(r'^-\s*([^:：]+)[:：]\s*(.*)$', line)
        if m:
            kv.append((m.group(1).strip(), m.group(2).strip()))
    return kv

# 2026-08-03: 「読み」欄はページによってカタカナ表記と
# ひらがな表記が混在していた
# （ユーザー指摘：読みをひらがなにしてほしい、かつ今後もずっと
# ひらがなになるように）。手作業での統一は今後また表記ゆれが混入し得るため、
# ビルド時に必ず正規化する：(1) 見出しキーが「読み」の場合は「ひらがな」に統一、
# (2) 値の中のカタカナは機械的にひらがなへ変換する（Unicode上でカタカナ→ひらがなは
# コードポイントを0x60引くだけで変換できる）。
_KATAKANA_RE = re.compile(r'[ァ-ヶ]')

def katakana_to_hiragana(s):
    return _KATAKANA_RE.sub(lambda m: chr(ord(m.group(0)) - 0x60), s)

def normalize_reading_field(kv_list):
    normalized = []
    for k, v in kv_list:
        if k in ("読み", "ひらがな"):
            normalized.append(("ひらがな", katakana_to_hiragana(v)))
        else:
            normalized.append((k, v))
    return normalized

# 2026-07-26: 「出典」セクション内の `[^N]: 説明文` 形式の脚注定義を構造化データとして
# 取り出す。これにより本文中の [^N] 参照をクリックすると出典欄の該当項目へジャンプする
# UIを実装できる（従来は出典欄もただの自由文として表示されるだけで、本文の脚注記号との
# 対応づけがUI上に一切なかった）。
# 2026-07-26: 「ビルド時の壊れチェック」— 2026-07-24に実際に起きた「<script>内でconst/letが
# 重複宣言されJS全体がSyntaxErrorで死に、モバイル版のリストが空白になる」事故を、ビルドの
# たびに自動検出して未然に防ぐための安全装置。壊れたHTMLは一切書き出さず、既存の
# （最後に正常だった）ファイルをそのまま残してビルドを異常終了(exit 1)させる。
def find_duplicate_top_level_decls(js):
    names = {}
    for m in re.finditer(r'^(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=', js, re.M):
        names.setdefault(m.group(1), []).append(m.start())
    for m in re.finditer(r'^function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(', js, re.M):
        names.setdefault(m.group(1), []).append(m.start())
    return [(name, len(pos)) for name, pos in names.items() if len(pos) > 1]

def check_js_syntax_with_node(js):
    node_path = shutil.which("node")
    if not node_path:
        return None  # Node未検出 = チェックをスキップ（重複宣言チェックは別途必ず実行される）
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as tmp:
            tmp.write(js)
            tmp_path = tmp.name
        result = subprocess.run([node_path, "--check", tmp_path], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
        return result.stderr.strip() if result.returncode != 0 else ""
    except Exception as e:
        print(f"[build check] node --check の実行に失敗（スキップ扱い）: {e}", file=sys.stderr)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def validate_wiki_html(html):
    errors = []
    # 2026-07-26: <head>内にダークモード判定用の小さな<script>を追加したことで、
    # <script>ブロックが複数存在するようになった。クラシックscript（type=module以外）は
    # 複数の<script>タグ間でも同じトップレベルscopeを共有し、同名のconst/letは
    # タグをまたいでも衝突するため、全<script>ブロックを結合してからチェックする
    # （先頭のブロックだけを見ると本体の大きなscriptを検証し損なう）。
    scripts = re.findall(r'<script>([\s\S]*?)</script>', html)
    if not scripts:
        errors.append("生成HTML内に<script>ブロックが見つからない")
        return errors
    js = "\n".join(scripts)

    for name, count in find_duplicate_top_level_decls(js):
        errors.append(
            f"トップレベル宣言 '{name}' が{count}回重複しています"
            "（2026-07-24にモバイル版が真っ白になった事故と同じ原因＝同一スコープでのconst/let重複はJSをSyntaxErrorで全停止させる）"
        )

    node_result = check_js_syntax_with_node(js)
    if node_result is None:
        print("[build check] nodeコマンドが見つからないため、実際の構文チェックはスキップ（重複宣言チェックは実施済み）", file=sys.stderr)
    elif node_result:
        errors.append(f"node --checkが構文エラーを報告:\n{node_result}")

    return errors

def parse_footnotes(section_text):
    notes = []
    for m in re.finditer(r'^\[\^(\d+)\]:\s*(.*)$', section_text, re.M):
        notes.append((m.group(1), m.group(2).strip()))
    notes.sort(key=lambda kv: int(kv[0]))
    return notes

# NON_UNIV: 「浪人」等はUI上、大学名としてリンク化しない除外リスト
# （実際の大学名・部活名の表記ゆれ正規化は club_univ_norm.py の CLUB_MAP/UNIV_MAP で
#   entities/people/高校の同級生/*.md の基本情報フィールド自体を正規化済みにする方式に統一。
#   2026-07-22、共同編集者の指示による。詳細は project_chibako_78ki_roster.md【07-22追記7】参照）
NON_UNIV = {"浪人", "浪人経験あり", "浪人中"}

def get_all_headers(body):
    return [h.strip() for h in re.findall(r'^##\s*(.+?)\s*$', body, re.M)]

# 2026-07-23: 共同編集者の指示により、表示上の文字数を減らすため「どのトークから・いつ反映したか」
# という出典・抽出メタ情報のタグを wiki 表示時のみ除去する（.md ソース自体は一切変更しない。
# 出典情報は将来また必要になる可能性があるため entities/people/ 配下には残したまま）。
def strip_meta(text):
    if not text:
        return text
    # 例:「【新規反映（バド部LINE、3950行、2023-04〜2026-07、2026-07-22）】」
    #     「【共同編集者とのLINE(59888行)より、2025-03〜2025-09精読分】」
    # のような、抽出元トーク名・行数・反映日を示すだけの角括弧タグを除去。
    # 「【訂正】」「【要注意】」「【重要】」等、事実の訂正・注意喚起を示すタグは
    # 反映/精読/行数のいずれも含まないため、意図的に残す。
    text = re.sub(r'【[^】]*(?:反映|精読|\d+行)[^】]*】\s*', '', text)
    # 「※本ページの記述は〜を精読済み。以下は〜」のような、精読状況だけを説明する行を除去
    # （箇条書きの「- 」が前置されている場合も含む）
    text = re.sub(r'^-?\s*※本ページの記述は.*$\n?', '', text, flags=re.M)
    # 除去した結果、中身が空になった箇条書き行を掃除
    text = re.sub(r'^-\s*$\n?', '', text, flags=re.M)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# 「## 〇〇とのトーク（△△）からの補強情報」のような、抽出元トーク名を含む見出しは
# 汎用の「補足情報」に置き換える（どのトークから取ったかという情報を表示から消すため）
def clean_extra_header(h):
    if re.search(r'からの補強情報$', h):
        return "補足情報"
    return h

# 2026-07-26: 重複エントリ（同一人物が別ファイルで二重登録されている状態、
# 例: 同じ読みの表記違いが別ファイルだった場合）の自動検出。
# 外部ライブラリ(pykakasi等)はvault-sync.ps1が両者のPCで15分おきに無人実行するため
# 導入せず、標準ライブラリのみで「読み」フィールドを比較する簡易ヒューリスティックとする。
def kata_to_hira(s):
    if not s:
        return s
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in s)

def norm_reading(s):
    if not s:
        return ""
    s = kata_to_hira(s)
    for ch in (" ", "　", "・", "-", "ー"):
        s = s.replace(ch, "")
    return s

# 2026-07-26: ローマ字検索対応。基本情報の「ひらがな」「読み」「ふりがな」フィールド（ひらがな・
# カタカナどちらでも可）からヘボン式ローマ字を機械生成する。外部ライブラリ(pykakasi等)は
# vault-sync.ps1が無人実行するため導入せず、標準ライブラリのみの単純な変換表で対応する
# （読みが無い・変換できない文字を含む場合は空文字を返し、無理に推測しない）。
_ROMAJI_YOUON = {
    "きゃ":"kya","きゅ":"kyu","きょ":"kyo","しゃ":"sha","しゅ":"shu","しょ":"sho",
    "ちゃ":"cha","ちゅ":"chu","ちょ":"cho","にゃ":"nya","にゅ":"nyu","にょ":"nyo",
    "ひゃ":"hya","ひゅ":"hyu","ひょ":"hyo","みゃ":"mya","みゅ":"myu","みょ":"myo",
    "りゃ":"rya","りゅ":"ryu","りょ":"ryo","ぎゃ":"gya","ぎゅ":"gyu","ぎょ":"gyo",
    "じゃ":"ja","じゅ":"ju","じょ":"jo","びゃ":"bya","びゅ":"byu","びょ":"byo",
    "ぴゃ":"pya","ぴゅ":"pyu","ぴょ":"pyo","ぢゃ":"ja","ぢゅ":"ju","ぢょ":"jo",
}
_ROMAJI_SINGLE = {
    "あ":"a","い":"i","う":"u","え":"e","お":"o",
    "か":"ka","き":"ki","く":"ku","け":"ke","こ":"ko",
    "さ":"sa","し":"shi","す":"su","せ":"se","そ":"so",
    "た":"ta","ち":"chi","つ":"tsu","て":"te","と":"to",
    "な":"na","に":"ni","ぬ":"nu","ね":"ne","の":"no",
    "は":"ha","ひ":"hi","ふ":"fu","へ":"he","ほ":"ho",
    "ま":"ma","み":"mi","む":"mu","め":"me","も":"mo",
    "や":"ya","ゆ":"yu","よ":"yo",
    "ら":"ra","り":"ri","る":"ru","れ":"re","ろ":"ro",
    "わ":"wa","を":"wo","ん":"n",
    "が":"ga","ぎ":"gi","ぐ":"gu","げ":"ge","ご":"go",
    "ざ":"za","じ":"ji","ず":"zu","ぜ":"ze","ぞ":"zo",
    "だ":"da","ぢ":"ji","づ":"zu","で":"de","ど":"do",
    "ば":"ba","び":"bi","ぶ":"bu","べ":"be","ぼ":"bo",
    "ぱ":"pa","ぴ":"pi","ぷ":"pu","ぺ":"pe","ぽ":"po",
    "ゔ":"vu",
}
def kana_word_to_romaji(word):
    """1単語（スペースなし）のひらがなをローマ字に変換する。変換できない文字が
    混じっていたら None を返す（無理に一部だけ変換して不正確な結果を出さないため）。"""
    if not word:
        return ""
    out = []
    i = 0
    n = len(word)
    while i < n:
        ch = word[i]
        two = word[i:i+2]
        if two in _ROMAJI_YOUON:
            out.append(_ROMAJI_YOUON[two])
            i += 2
            continue
        if ch == "っ":
            # 促音: 次の音の子音を重ねる（次がさ行等でなければ変換不可扱い）
            nxt = word[i+1:i+3]
            nxt_r = _ROMAJI_YOUON.get(nxt) or _ROMAJI_SINGLE.get(word[i+1:i+2])
            if not nxt_r:
                return None
            out.append(nxt_r[0])
            i += 1
            continue
        if ch == "ー":
            if out and out[-1]:
                out.append(out[-1][-1])
            i += 1
            continue
        if ch in _ROMAJI_SINGLE:
            out.append(_ROMAJI_SINGLE[ch])
            i += 1
            continue
        # 未対応文字（漢字・記号等が混じっている）→ 変換不可
        return None
    return "".join(out)

def kana_to_romaji(reading_raw):
    """基本情報の読みフィールド生データ（カタカナ混在・スペース区切り可）から
    ローマ字（単語ごとに先頭大文字、スペース区切り）を生成する。変換できなければ ""。"""
    if not reading_raw:
        return ""
    hira = kata_to_hira(reading_raw)
    # 全角スペース・区切り記号をスペースに統一し、それ以外の区切りは保持しない
    hira = hira.replace("　", " ").replace("・", " ").strip()
    words = [w for w in hira.split(" ") if w]
    if not words:
        return ""
    romaji_words = []
    for w in words:
        rw = kana_word_to_romaji(w)
        if rw is None:
            return ""
        romaji_words.append(rw)
    return " ".join(w.capitalize() for w in romaji_words if w)

def detect_duplicate_candidates(records):
    reading_of = {}
    for r in records:
        basic_dict = dict(r["basic"])
        raw = basic_dict.get("読み") or basic_dict.get("ひらがな") or basic_dict.get("ふりがな") or ""
        nr = norm_reading(raw)
        if not nr:
            # 読みフィールドが無い場合のフォールバック: タイトル自体が仮名のみならそれを読みとみなす
            title_ns = r["title"].replace(" ", "").replace("　", "")
            if title_ns and all(("ぁ" <= c <= "ん") or ("ァ" <= c <= "ヶ") or c == "ー" for c in title_ns):
                nr = norm_reading(title_ns)
        reading_of[r["id"]] = nr

    candidates = []
    n = len(records)
    for i in range(n):
        ri = records[i]
        for j in range(i + 1, n):
            rj = records[j]
            if ri["title"] == rj["title"]:
                # 2026-07-26発見: 「表示名(H1見出し)は完全同一だがファイルは別」という、
                # 当初の想定（ファイル名衝突は既存チェックで防止済み＝完全同名は起きない）が
                # 誤りだったケースがあった（例: 人物A.md／人物A（別人）.md、
                # どちらもH1見出しは「人物A」で表示上区別できない）。最優先の警告として扱う。
                candidates.append((ri["title"], rj["title"], "表示名(H1見出し)が完全一致する別ファイル（同姓同名の別人でも要区別、または重複登録の可能性）"))
                continue
            nr_i = reading_of[ri["id"]]
            nr_j = reading_of[rj["id"]]
            # 読みの完全一致のみを検出対象にする（曖昧な類似度判定は、読みの長さが近い
            # 別人同士でも高スコアになりやすくノイズが多いため、2026-07-26に採用を見送った）
            if nr_i and nr_j and len(nr_i) >= 3 and nr_i == nr_j:
                candidates.append((ri["title"], rj["title"], "読みが完全一致"))
    return candidates

def write_duplicate_report(vault_dir, candidates):
    path = os.path.join(vault_dir, "memory", "reference", "reference_duplicate_candidates.md")
    lines = [
        "---",
        "name: reference-duplicate-candidates",
        "description: build_people_wiki.pyが自動検出した「別ページだが同一人物の可能性がある」候補一覧（ビルドのたび上書き）",
        "metadata:",
        "  node_type: memory",
        "  type: reference",
        "---",
        "",
        "このファイルは `scripts/build_people_wiki.py` 実行のたびに自動生成・上書きされる。",
        "「読み」フィールドの完全一致・類似度から機械的に検出した候補であり、誤検知（単なる同姓・偶然の音の一致）も含まれる。鵜呑みにせず人力で確認すること。",
        "",
    ]
    if not candidates:
        lines.append("現在、重複候補は検出されていない。")
    else:
        lines.append(f"検出件数: {len(candidates)}件")
        lines.append("")
        for a, b, reason in candidates:
            lines.append(f"- 「{a}」 ⇔ 「{b}」 — {reason}")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[WARN] duplicate report write failed: {e}", file=sys.stderr)

# 2026-08-05: raw/フォルダの各生データファイル（LINE等の会話エクスポート原本）について、
# 行数・本文中から検出できる日付範囲を機械的に集計する。「このwikiの構造」ページで
# 「どのようなLINE会話データを保持しているか」を確認できるようにするためのデータ源。
# （旧・矛盾自動検出／raw-LINE読了状況マップの2機能は2026-08-05にユーザー要望で撤去し、
# 「予定」「このwikiの構造」の2機能に置き換えた。raw/フォルダのファイル一覧・行数・
# 日付範囲の集計ロジックのみ、新機能「このwikiの構造」ページ内の一覧表示に転用している）
# 「精読済み範囲」の抽出はfootnotes本文中の「YYYY-MM〜MM精読分」等の表記を正規表現で
# 拾うベストエフォート方式であり、表記ゆれにより漏れることがある（完全な保証はしない）。
RAW_READ_RANGE_RE = re.compile(r'(\d{4}-\d{1,2}(?:[〜～\-][\d〜～\-]{0,10})?)\s*精読分')

def scan_raw_line_files(vault_dir, records):
    files_info = []
    if not os.path.isdir(RAW_DIR):
        return files_info

    footnote_texts = []
    for r in records:
        for _n, t in (r.get("footnotes") or []):
            footnote_texts.append(t)
        if r.get("footnotesRaw"):
            footnote_texts.append(r["footnotesRaw"])
    all_footnote_text = "\n".join(footnote_texts)

    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.md"))):
        fname = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"[WARN] raw file read failed ({fname}): {e}", file=sys.stderr)
            continue
        line_count = content.count("\n") + 1

        dates = re.findall(r'(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})', content)
        date_range = ""
        if dates:
            norm = sorted(set(dates), key=lambda d: (int(d[0]), int(d[1]), int(d[2])))
            first, last = norm[0], norm[-1]
            date_range = f"{first[0]}-{int(first[1]):02d}-{int(first[2]):02d} 〜 {last[0]}-{int(last[1]):02d}-{int(last[2]):02d}"

        # このファイルへの言及箇所（ファイル名 or 行数表記）の近傍から「精読分」範囲を拾う
        read_ranges = set()
        mention_markers = [fname, f"{line_count}行"]
        for marker in mention_markers:
            for m in re.finditer(re.escape(marker), all_footnote_text):
                window = all_footnote_text[m.end(): m.end() + 80]
                rm = RAW_READ_RANGE_RE.search(window)
                if rm:
                    read_ranges.add(rm.group(1))
        cited = bool(mention_markers[0] in all_footnote_text or mention_markers[1] in all_footnote_text)
        # README・ロードマップ・スナップショット等、そもそも出典として引用される想定のない
        # 補助ファイルまで「未精読の可能性」と警告すると誤検知でノイズになるため、
        # ファイル名に「LINE」を含むものだけを「LINE生データ」として警告対象にする。
        is_line_export = "LINE" in fname.upper()

        files_info.append({
            "file": fname,
            "lineCount": line_count,
            "dateRange": date_range,
            "readRanges": sorted(read_ranges),
            "cited": cited,
            "isLineExport": is_line_export,
        })
    return files_info

# 2026-07-26: 「要確認事項ダッシュボード」用。本文中に不確実性マーカー（要確認・推測・
# 未確認・断定はできない・特定できず・未特定・要検討）を含むページを機械的に検出し、
# 一覧化する。誰にインタビューして確認すべきかを俯瞰できるようにするため。
UNCERTAIN_MARKERS = ["要確認", "推測", "未確認", "断定はできない", "特定できず", "未特定", "要検討"]

def extract_uncertain_snippets(text, max_count=4, pad=18):
    if not text:
        return []
    snippets = []
    for marker in UNCERTAIN_MARKERS:
        for m in re.finditer(re.escape(marker), text):
            start = max(0, m.start() - pad)
            end = min(len(text), m.end() + pad)
            snippet = text[start:end].replace("\n", " ").strip()
            snippet = re.sub(r'\s+', ' ', snippet)
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
            snippets.append(snippet)
            if len(snippets) >= max_count:
                return snippets
    return snippets

def count_uncertain(text):
    if not text:
        return 0
    return sum(text.count(m) for m in UNCERTAIN_MARKERS)

# 2026-07-26: 「全履歴の時系列ビュー」用。各ページの「履歴」欄（"- YYYY-MM-DD: 本文" 形式が
# 大半だが、"YYYY-MM〜MM"・"YYYY-MM-DD/DD"・"YYYY-MM-DD（注記）"・読点区切り等の表記ゆれもある）
# から日付と本文を分離して構造化する。「高2〜高3」等、絶対年月日を含まない相対表現は
# 年表上でソートしようがないため対象外（ベストエフォート、実測で97件中93件を捕捉）。
HISTORY_ENTRY_RE = re.compile(
    r'^-\s*(\d{4}(?:-\d{2}(?:-\d{2})?)?(?:[〜～/][\d〜～\-]*)?(?:頃|前後|（[^）]*）|\([^)]*\))?)\s*[:：、,]\s*(.*)$'
)
HISTORY_DATE_PREFIX_RE = re.compile(r'^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?')
# 2026-08-15: 「予定」欄の各項目が確定済みか未確定かを示す先頭タグ「[確]」「[予]」を抽出する。
SCHEDULE_STATUS_RE = re.compile(r'^\[(確|予)\]\s*')

def parse_history_entries(text):
    entries = []
    if not text:
        return entries
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        m = HISTORY_ENTRY_RE.match(line)
        if not m:
            continue
        date_label = m.group(1).strip()
        body_text = m.group(2).strip()
        if not body_text:
            continue
        dm = HISTORY_DATE_PREFIX_RE.match(date_label)
        if not dm:
            continue
        y, mo, d = dm.group(1), dm.group(2) or "01", dm.group(3) or "01"
        sort_key = f"{y}-{mo}-{d}"
        entries.append((sort_key, date_label, body_text))
    return entries

# 2026-08-03: 各人物ページの「履歴」欄は、これまで.mdファイルへの追記順（＝編集した順）で
# そのまま表示されており、時系列順になっていなかった（ユーザー指摘：デモユーザーのページで
# 情報が時系列順になっていない）。情報を一切削除せず、常にビルド時に自動で時系列順へ並び替える
# ことで、手作業での並べ替えや「今後は順番を守って追記する」という運用ルールに頼らずに済む
# 仕組みとして実装する（共同編集者側の自動化パイプラインが追記する場合も含め、常に効く）。
#
# 各行（"- "で始まる箇条書き）から、行内で最も左（＝最初）に現れる日付らしき表記を拾い、
# ソートキーとする。表記ゆれ（"YYYY-MM-DD"「YYYY-MM」「YYYY年M月D日」「YYYY年M月」「YYYY年」
# や、文頭以外に日付が出てくる文、範囲表記「〜」等）に対応する。日付が全く見つからない行は、
# 「中学1年」「高校2年」等の学年の相対表現から、高校の同級生生の学年歴（中1=2020年度〜大1=2026年度、
# 4月始業）を用いて近似の年月にフォールバックする。それでも見つからない行は、情報を失わないよう
# 元の順序を保ったまま最後に配置する。
HIST_DASH_FULL_RE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')
HIST_DASH_YM_RE = re.compile(r'(\d{4})-(\d{1,2})(?!-\d)')
HIST_NENGO_YMD_RE = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日')
HIST_NENGO_YM_RE = re.compile(r'(\d{4})年(\d{1,2})月')
HIST_NENGO_Y_RE = re.compile(r'(\d{4})年')
HIST_GRADE_HINTS = [
    (re.compile(r'中学\s*1\s*年|中1'), (2020, 4, 1)),
    (re.compile(r'中学\s*2\s*年|中2'), (2021, 4, 1)),
    (re.compile(r'中学\s*3\s*年|中3'), (2022, 4, 1)),
    (re.compile(r'高校\s*1\s*年|高1'), (2023, 4, 1)),
    (re.compile(r'高校\s*2\s*年|高2'), (2024, 4, 1)),
    (re.compile(r'高校\s*3\s*年|高3'), (2025, 4, 1)),
    (re.compile(r'大学\s*1\s*年|大1'), (2026, 4, 1)),
]

def _history_line_sort_key(line):
    candidates = []
    for pat, ngroups in [
        (HIST_DASH_FULL_RE, 3), (HIST_DASH_YM_RE, 2),
        (HIST_NENGO_YMD_RE, 3), (HIST_NENGO_YM_RE, 2), (HIST_NENGO_Y_RE, 1),
    ]:
        for m in pat.finditer(line):
            g = m.groups()
            y = int(g[0])
            mo = int(g[1]) if ngroups >= 2 else 1
            d = int(g[2]) if ngroups >= 3 else 1
            candidates.append((m.start(), (y, mo, d)))
    if candidates:
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]
    for pat, ymd in HIST_GRADE_HINTS:
        if pat.search(line):
            return ymd
    return None

def sort_history_text(text):
    if not text or not text.strip():
        return text
    lines = text.split("\n")
    # 全ての非空行が箇条書き（"- "始まり）である場合のみ並び替える。
    # 想定外のフォーマット（見出し混在等）が来た場合は安全側に倒し元のまま返す。
    non_blank = [l for l in lines if l.strip() != ""]
    if not non_blank or any(not l.strip().startswith("-") for l in non_blank):
        return text
    dated, undated = [], []
    for idx, line in enumerate(lines):
        if line.strip() == "":
            continue
        key = _history_line_sort_key(line)
        if key is None:
            undated.append((idx, line))
        else:
            dated.append((key, idx, line))
    dated.sort(key=lambda t: (t[0], t[1]))
    ordered = [line for _, _, line in dated] + [line for _, line in undated]
    return "\n".join(ordered)

# 2026-07-26: 「誕生日一覧」用。基本情報の「誕生日」フィールド（例: "4月1日"）を
# ソート可能な MM-DD 形式に変換する。現状データがある人はごく僅かだが、今後増えても
# 自動的に一覧に反映されるよう汎用的な形で実装しておく。
def parse_birthday(basic_dict):
    raw = basic_dict.get("誕生日", "")
    if not raw:
        return "", ""
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', raw)
    if not m:
        m = re.search(r'(?<!\d)(\d{1,2})[/\-](\d{1,2})(?!\d)', raw)
    if not m:
        return raw.strip(), ""
    mo, d = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return raw.strip(), ""
    return raw.strip(), f"{mo:02d}-{d:02d}"

# 2026-07-26: 「ごぶさたチェック」用。基本情報の「最終接触」フィールド（例: "2026-07-24（鎌取で
# バドミントン）"）から日付を取り出す。履歴からの自動推定はやめ、明示的にこのフィールドが
# 書かれている人のみを対象にする（ユーザー指摘：履歴には接触を伴わない出来事も混在するため）。
# 2026-07-26（同日追記）: ユーザーと共同編集者はそれぞれ別人として同じ人物と接触するため、
# 「ユーザーとの最終接触」「共同編集者との最終接触」の2フィールドに分離（ユーザー要望）。
def parse_last_contact(basic_dict, key):
    raw = basic_dict.get(key, "")
    if not raw:
        return "", ""
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if not m:
        m = re.search(r'(\d{4})-(\d{2})(?!-\d)', raw)
        if m:
            return raw.strip(), f"{m.group(1)}-{m.group(2)}-01"
        return raw.strip(), ""
    return raw.strip(), f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

# 2026-07-31: 「最終接触」フィールドを書き忘れても、履歴に明確な接触イベント
# （日付＋実際に会った/連絡した旨の記述＋本人名）が書かれていれば拾えるようにする
# フォールバック（対面記録が最終接触に反映されていなかったケースへの対応）。
# 2026-07-26の教訓（「LINEグループから削除された」等の非接触イベントを誤検知した）を
# 踏まえ、対象を「実際に会った/連絡を取った」ことが明確なキーワードを含む行に限定し、
# かつ対象人物（ユーザー／共同編集者）の名前がキーワードの近く（同じ節）にある行のみを候補にする、
# 控えめな設計。名前とキーワードが単に同じ行にあるだけでは誤検知するため
# （例:「共同編集者と回るも…翌日デート打診は断られる」＝共同編集者と無関係な誰かとのデート）、
# 近接判定（NAME_PROXIMITY_WINDOW文字以内）を必須にしている。
LAST_CONTACT_KEYWORDS = (
    "対面", "会った", "会えた", "会う", "食事", "ご飯", "デート", "飲み会",
    "カラオケ", "遊んだ", "遊びに", "電話", "通話", "ドライブ", "泊まっ",
    "誕生日を祝", "会に参加", "一緒に過ごし",
)
NAME_PROXIMITY_WINDOW = 40
# 2026-08-05: 「前々回接触」の書き忘れ候補を検出できるよう、探索範囲の上限（before）を
# 指定できるようにした（省略時は従来通り全期間から最新の1件を探す＝最終接触の推定用。
# beforeを指定すると「その日付より前で最新の1件」を探せる＝前々回接触の候補提示用）。
def infer_last_contact_from_history(history_text, name_keywords, before=None):
    best_sort_key, best_raw, best_body = "", "", ""
    for sort_key, date_label, body_text in parse_history_entries(history_text):
        if before and sort_key >= before:
            continue
        name_positions = [m.start() for nk in name_keywords for m in re.finditer(re.escape(nk), body_text)]
        kw_positions = [m.start() for kw in LAST_CONTACT_KEYWORDS for m in re.finditer(re.escape(kw), body_text)]
        if not name_positions or not kw_positions:
            continue
        close = any(abs(np - kp) <= NAME_PROXIMITY_WINDOW for np in name_positions for kp in kw_positions)
        if not close:
            continue
        if sort_key > best_sort_key:
            best_sort_key, best_raw, best_body = sort_key, date_label, body_text
    if not best_sort_key:
        return "", ""
    # 2026-07-31: 「デザインモード」のタイル表示（🤝ごぶさたチェック）で「何をしたか」の
    # 一言も見せたいというユーザー要望に対応するため、自動推定時も他の最終接触フィールドと
    # 同じ「日付（説明）」の形で、該当履歴行の抜粋を括弧書きに含めておく（JS側で正規表現抽出）。
    snippet = best_body if len(best_body) <= 60 else best_body[:60] + "…"
    return f"{best_raw}（{snippet}）", best_sort_key

# 2026-08-05: 「前々回接触の書き忘れ候補」検出。「最終接触はあるが前々回接触が書かれて
# いない」ページについて、履歴から「最終接触より前で、実際に会った/連絡したことが明確な
# 直近の記述」を機械的に拾い、ビルドのたびに候補一覧をレポート出力する（自動反映はしない）。
# ユーザー指摘（活動記録が前々回接触として拾われていなかったケース）を受けて、
# 手動追記に頼らず「新しい前々回接触の書き忘れが無いか」を毎回機械的に洗い出せる仕組みとして追加。
def detect_prev_contact_candidates(records):
    suggestions = []
    HUBS = [
        ("ユーザー", "hasKokubo", "lastContactDate", "prevContactRaw"),
        ("共同編集者", "hasOta", "lastContactDateOta", "prevContactRawOta"),
    ]
    for person_label, has_key, last_date_key, prev_raw_key in HUBS:
        for r in records:
            if r["id"] in ("ユーザー", "共同編集者"):
                continue
            if not r.get(has_key):
                continue
            last_date = r.get(last_date_key)
            if not last_date or r.get(prev_raw_key):
                continue
            found_raw, found_sort_key = infer_last_contact_from_history(
                r.get("history", ""), (person_label,), before=last_date
            )
            if found_raw:
                suggestions.append({
                    "title": r["title"], "id": r["id"], "person": person_label, "suggestion": found_raw,
                })
    return suggestions

def write_prev_contact_report(vault_dir, suggestions):
    path = os.path.join(vault_dir, "memory", "reference", "reference_prev_contact_candidates.md")
    lines = [
        "---",
        "name: reference-prev-contact-candidates",
        "description: build_people_wiki.pyが自動検出した「前々回接触の書き忘れ候補」一覧（ビルドのたび上書き）",
        "metadata:",
        "  node_type: memory",
        "  type: reference",
        "---",
        "",
        "このファイルは `scripts/build_people_wiki.py` 実行のたびに自動生成・上書きされる。",
        "「最終接触」はあるが「前々回接触」が書かれていないページについて、履歴から「最終接触より前で、"
        "実際に会った/連絡したことが明確な直近の記述」を機械的に拾った候補一覧。機械的な検出であり"
        "誤検知（会話に名前が出ただけ等）も含みうるため、内容を確認したうえで該当ページの"
        "「ユーザーとの関係」/「共同編集者との関係」欄に手動で「前々回接触: ...」として追記すること"
        "（このファイル自体はレポートのみで、Wikiには反映されない）。",
        "",
    ]
    if not suggestions:
        lines.append("現在、候補は検出されていない。")
    else:
        lines.append(f"検出件数: {len(suggestions)}件")
        lines.append("")
        for s in suggestions:
            lines.append(f"- 「{s['title']}」（{s['person']}視点）: 前々回接触の候補 → {s['suggestion']}")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[WARN] prev contact report write failed: {e}", file=sys.stderr)

def parse_md(path, category):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fmtext = parts[1]
            body = parts[2]
            for line in fmtext.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
    # 2026-07-31: フォルダ由来の主カテゴリ（絞り込み）とは別に、「このページも別の
    # 絞り込みに追加で出したい」場合のための補助フィールド。例: 活動グループ
    # ンインカレサークル）のメンバーは主カテゴリが「大学の友人」等でも、「サークル」の
    # 絞り込みにも出したいというユーザー要望への対応。frontmatterに
    # `extra_categories: [サークル]` のようにブラケット区切りで書く。
    extra_categories_raw = fm.get("extra_categories", "")
    extra_categories = [c.strip() for c in extra_categories_raw.strip("[]").split(",") if c.strip()]

    m = re.search(r'^#\s+(.+)$', body, re.M)
    title = m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0]
    m2 = re.search(r'^>\s*1行サマリ[:：]\s*(.+)$', body, re.M)
    summary = m2.group(1).strip() if m2 else ""
    links = sorted(set(re.findall(r'\[\[([^\]]+)\]\]', body)))
    basic = normalize_reading_field(parse_kv_list(parse_section(body, "基本情報")))
    traits = strip_meta(parse_section(body, "特徴"))
    values = strip_meta(parse_section(body, "価値観"))
    # 2026-07-31: 「本人固有の情報→ユーザーとの関係→共同編集者との関係」の順に読める構成にするため、
    # 従来「基本情報」の一行フィールドだった「ユーザーとの関係」「共同編集者との関係」を、内容が
    # ある場合は独立したセクションとして扱えるようにする（ユーザー要望：ある人物ページで
    # ユーザー視点の記述が本人紹介に混ざって読みにくいという指摘への対応）。
    kokubo_rel = strip_meta(parse_section(body, "ユーザーとの関係"))
    ota_rel = strip_meta(parse_section(body, "共同編集者との関係"))
    kokubo_rel_dict = dict(parse_kv_list(parse_section(body, "ユーザーとの関係")))
    ota_rel_dict = dict(parse_kv_list(parse_section(body, "共同編集者との関係")))
    relations = strip_meta(parse_section(body, "関係"))
    current = strip_meta(parse_section(body, "現在の状態"))
    history = sort_history_text(strip_meta(parse_section(body, "履歴")))
    # 2026-08-05: 「予定」— 今後の予定（同期会の開催予定など）を書けるセクション。
    # 「履歴」（過去の出来事）と書式は同じ「- YYYY-MM(-DD): 本文」の箇条書きのため、
    # 既存のparse_history_entries()をそのまま流用して構造化し、サイドバーの「予定」
    # ダッシュボードで全ページ横断の時系列一覧として集約表示する。
    # 2026-08-05（拡張）: 「予定をユーザーと共同編集者で分けてほしい」というユーザー要望に対応し、
    # 「## 予定」（両方の視点に出す・グループページ等）に加えて「## ユーザーの予定」
    # 「## 共同編集者の予定」（該当する視点でのみ出す）の3見出しを用意。どの見出し由来かを
    # "person"（both/kokubo/ota）としてエントリごとに保持し、JS側の視点トグルで絞り込む。
    # 2026-08-15: 予定が「相手に伝えて確定した約束」なのか「ユーザーが考えているだけの
    # 意向・妄想」なのかを一目で区別したいというユーザー要望への対応。本文の先頭に
    # 「[確]」または「[予]」タグを書く運用とし、ここで抽出してstatusフィールドに分離する
    # （表示テキストからはタグ自体を取り除く）。タグが書かれていない場合は、安全側に倒して
    # 「予」（未確定）扱いとする。
    # 2026-08-15（訂正）: 当初はparse_history_entries()にそのまま通してから[確]/[予]タグを
    # 取り除こうとしたが、HISTORY_ENTRY_REは行頭直後に日付が来ることを前提にしており、
    # タグが日付の手前に入ると正規表現が一致せず全件が消える不具合が発生した（ビルド後の
    # 検証で発覚、コミット前に発見・修正）。タグを日付より先に取り除いてから既存の日付解析
    # ロジックにかける自己完結型の実装に置き換える。
    def _schedule_entries(section_text, person_tag):
        out = []
        if not section_text:
            return out
        for line in section_text.splitlines():
            line = line.strip()
            if not line.startswith("-"):
                continue
            rest = line[1:].strip()
            sm = SCHEDULE_STATUS_RE.match(rest)
            if sm:
                status = sm.group(1)
                rest = rest[sm.end():]
            else:
                status = "予"
            m = HISTORY_ENTRY_RE.match("- " + rest)
            if not m:
                continue
            date_label = m.group(1).strip()
            body_text = m.group(2).strip()
            if not body_text:
                continue
            dm = HISTORY_DATE_PREFIX_RE.match(date_label)
            if not dm:
                continue
            y, mo, d = dm.group(1), dm.group(2) or "01", dm.group(3) or "01"
            sort_key = f"{y}-{mo}-{d}"
            out.append({"date": sort_key, "label": date_label, "text": body_text, "person": person_tag, "status": status})
        return out
    schedule_text = strip_meta(parse_section(body, "予定"))
    schedule_text_kokubo = strip_meta(parse_section(body, "ユーザーの予定"))
    schedule_text_ota = strip_meta(parse_section(body, "共同編集者の予定"))
    schedule_entries = (
        _schedule_entries(schedule_text, "both")
        + _schedule_entries(schedule_text_kokubo, "kokubo")
        + _schedule_entries(schedule_text_ota, "ota")
    )
    # 既知の見出し以外の独自セクション（例:「〜からの補強情報」）は extra に保持する。
    # これを落とすと過去に人物wiki側だけに情報が残り.mdと乖離する事故が起きたため必須。
    extra = []
    for h in get_all_headers(body):
        if h not in KNOWN_HEADERS:
            content = strip_meta(parse_section(body, h))
            if content:
                extra.append([clean_extra_header(h), content])

    # 「出典」は2026-07-26以降、脚注番号付きリストとして構造化して扱う（KNOWN_HEADERSに
    # 追加済みのためextraループには乗らない）。[^N]: 形式にマッチしなかった場合に備え、
    # 生テキストもフォールバックとして保持する。
    shutten_raw = parse_section(body, "出典")
    footnotes = parse_footnotes(shutten_raw)

    basic_dict = dict(basic)
    club_tags = tags_from_norm(basic_dict.get("高校時部活", ""))
    univ_tags = tags_from_norm(basic_dict.get("大学", ""))
    birthday_raw, birthday_sort = parse_birthday(basic_dict)

    # 2026-07-27: サイドバーの「ユーザーの」「共同編集者の」絞り込み用。「ユーザーとの関係」
    # 「共同編集者との関係」欄が「なし」（「なし（情報なし）」等の注記付きも含む）でなければ
    # 接点ありとみなす。
    def _has_relation(value):
        v = (value or "").strip()
        return bool(v) and not v.startswith("なし")
    # 2026-07-31: 「ユーザーとの関係」「共同編集者との関係」が独立セクション化されたページでは、
    # 基本情報の一行フィールドではなく新セクション内の「位置づけ」欄（または本文全体）を見る。
    has_kokubo = (_has_relation(basic_dict.get("ユーザーとの関係"))
                  or _has_relation(kokubo_rel_dict.get("位置づけ"))
                  or _has_relation(kokubo_rel))
    has_ota = (_has_relation(basic_dict.get("共同編集者との関係"))
               or _has_relation(ota_rel_dict.get("位置づけ"))
               or _has_relation(ota_rel))

    # 2026-07-26: ローマ字検索対応。「ひらがな」「読み」「ふりがな」のいずれかから
    # ローマ字を機械生成し、基本情報に「ローマ字」行として自動挿入する（元のmdファイルは
    # 一切書き換えない＝読みが変わればビルドのたびに追従する）。既に「ローマ字」欄が
    # 手動で書かれているページはそちらを優先し上書きしない。
    reading_field = basic_dict.get("ひらがな") or basic_dict.get("読み") or basic_dict.get("ふりがな") or ""
    # 「（」「(」「[^」（脚注番号）以降は読みではないので切り落とす
    reading_clean = re.split(r'[（(]|\[\^', reading_field)[0].strip()
    romaji = kana_to_romaji(reading_clean)
    basic_with_romaji = list(basic)
    if romaji and "ローマ字" not in basic_dict:
        insert_idx = len(basic_with_romaji)
        for idx, (k, _v) in enumerate(basic_with_romaji):
            if k in ("ひらがな", "読み", "ふりがな"):
                insert_idx = idx + 1
                break
        basic_with_romaji.insert(insert_idx, ("ローマ字", romaji))
    romaji_search = romaji if romaji else basic_dict.get("ローマ字", "")
    # 2026-07-26: 検索結果・一覧を「あいうえお順（五十音順）」で並べるための読みキー。
    # 漢字タイトルのlocaleCompareだけでは読みを正しく反映できないため、ひらがなの読みを
    # 正規化して持たせ、読みが無い場合はタイトル自体（かな書きのページ名等）にフォールバックする。
    reading_sort = norm_reading(reading_clean) if reading_clean else norm_reading(title)

    # 全文検索用: 主要テキストを1本に連結（小文字化はJS側で行う）
    search_parts = [title, summary, traits, values, kokubo_rel, ota_rel, relations, current, history,
                     schedule_text, schedule_text_kokubo, schedule_text_ota]
    search_parts += [f"{h} {t}" for h, t in extra]
    search_parts += [f"{k} {v}" for k, v in basic_with_romaji]
    search_parts.append(shutten_raw)
    search_parts.append(romaji_search)
    search_text = " ".join(p for p in search_parts if p)

    uncertain_count = count_uncertain(search_text)
    uncertain_snippets = extract_uncertain_snippets(search_text) if uncertain_count else []
    # 2026-07-26（同日修正）: 「ごぶさたチェック」は当初、履歴の最終日付を機械的に
    # 「最後の接触日」とみなしていたが、履歴には「LINEグループから削除された」
    # 「WhatsAppチャット精読で実名判明」のような、ユーザーとの実際の接触を伴わない
    # 出来事も混ざっており誤検知が多かった（ユーザー指摘、2026-07-26）。
    # 基本情報／関係セクションに明示的な「最終接触」フィールドがある場合はそちらを優先。
    # 2026-07-31: ただし「最終接触」の書き忘れで実際の接触が反映されない事故が発生した
    # （複数人で対面した記録が履歴にはあるのに最終接触に未反映だったケース）
    # ため、明示フィールドが無い場合に限り、履歴から「実際に会った/連絡した」ことが
    # 明確なキーワード＋対象人物名を含む行を拾うフォールバックを追加（infer_last_contact_from_history）。
    # 履歴からの自動推定は、ユーザー・共同編集者自身のハブページ（人物wiki全体で最も文章密度が
    # 高く、1つの履歴行に複数の出来事が同居しがちで誤検知しやすい）には適用しない。
    _rid = os.path.splitext(os.path.basename(path))[0]
    _is_hub_page = _rid in ("ユーザー", "共同編集者")
    last_contact_raw, last_contact_date = parse_last_contact(basic_dict, "ユーザーとの最終接触")
    if not last_contact_raw:
        last_contact_raw, last_contact_date = parse_last_contact(kokubo_rel_dict, "最終接触")
    if not last_contact_raw and has_kokubo and not _is_hub_page:
        last_contact_raw, last_contact_date = infer_last_contact_from_history(history, ("ユーザー",))
    last_contact_raw_ota, last_contact_date_ota = parse_last_contact(basic_dict, "共同編集者との最終接触")
    if not last_contact_raw_ota:
        last_contact_raw_ota, last_contact_date_ota = parse_last_contact(ota_rel_dict, "最終接触")
    if not last_contact_raw_ota and has_ota and not _is_hub_page:
        last_contact_raw_ota, last_contact_date_ota = infer_last_contact_from_history(history, ("共同編集者",))

    # 2026-08-05: 「ごぶさたチェック」の棒グラフに、最終接触（濃い青）だけでなく
    # 一つ前の接触＝前々回接触（薄い青、濃い青の下に重ねて表示）も出したいという
    # ユーザー要望への対応。「最終接触」と全く同じ書式・置き場所（基本情報の
    # 「(ユーザー|共同編集者)との前々回接触」、または各関係セクション内の「前々回接触」）の
    # 明示フィールドとして書く。最終接触と違い、書き忘れ時の履歴フォールバック推定は
    # 行わない（どれが「2番目に新しい」出来事かの自動判定は誤検知リスクが高いため、
    # 明示的に書かれた場合のみ表示する保守的な仕様）。
    prev_contact_raw, prev_contact_date = parse_last_contact(basic_dict, "ユーザーとの前々回接触")
    if not prev_contact_raw:
        prev_contact_raw, prev_contact_date = parse_last_contact(kokubo_rel_dict, "前々回接触")
    prev_contact_raw_ota, prev_contact_date_ota = parse_last_contact(basic_dict, "共同編集者との前々回接触")
    if not prev_contact_raw_ota:
        prev_contact_raw_ota, prev_contact_date_ota = parse_last_contact(ota_rel_dict, "前々回接触")

    return {
        "id": os.path.splitext(os.path.basename(path))[0],
        "title": title,
        "summary": summary,
        "basic": basic_with_romaji,
        "romaji": romaji_search,
        "traits": traits,
        "values": values,
        "kokuboRel": kokubo_rel,
        "otaRel": ota_rel,
        "relations": relations,
        "current": current,
        "extra": extra,
        "history": history,
        "schedule": schedule_entries,
        "footnotes": footnotes,
        "footnotesRaw": shutten_raw if not footnotes else "",
        "links": links,
        "category": category,
        "extraCategories": extra_categories,
        "univTags": univ_tags,
        "clubTags": club_tags,
        "hasKokubo": has_kokubo,
        "hasOta": has_ota,
        "searchText": search_text,
        "uncertainCount": uncertain_count,
        "uncertainSnippets": uncertain_snippets,
        "updated": get_file_updated_label(path) or fm.get("updated", ""),
        "lastContactDate": last_contact_date,
        "lastContactRaw": last_contact_raw,
        "lastContactDateOta": last_contact_date_ota,
        "lastContactRawOta": last_contact_raw_ota,
        "prevContactDate": prev_contact_date,
        "prevContactRaw": prev_contact_raw,
        "prevContactDateOta": prev_contact_date_ota,
        "prevContactRawOta": prev_contact_raw_ota,
        "birthdayRaw": birthday_raw,
        "birthdaySort": birthday_sort,
        "entityType": fm.get("entity", "person"),
        "readingSort": reading_sort,
    }

records = []
# 2026-07-23: entities/people/直下（サブフォルダ以外）の人物は、以前は自動的に
# 「主要人物」カテゴリに分類されていたが、共同編集者の指示により廃止。
# 「主要人物」は今後、閲覧者がページ上の★ボタンで自分自身が選ぶ完全カスタムの
# カテゴリ（ブラウザのlocalStorageに保存）とする。フォルダ由来のカテゴリとしては
# 中立的な「その他」を割り当てる（共同編集者側の変更を採用）。
for path in glob.glob(os.path.join(PEOPLE_DIR, "*.md")):
    records.append(parse_md(path, "その他"))
# 2026-07-22: サブフォルダを個別にハードコードするのをやめ、entities/people/配下の
# サブフォルダを自動で全部スキャンする方式に変更。フォルダ名がそのままカテゴリ名になる。
# これにより「高校の同級生」「大学の友人」「前提知識」以外の新しい関係グループ（職場、家族、等）を
# 追加したい場合も、entities/people/配下にフォルダを作って.mdを置くだけでよくなり、
# このスクリプトを毎回修正する必要がなくなる（ユーザー側の変更を維持。共同編集者側は高校の同級生／
# 大学の友人のみハードコード走査に戻っていたが、前提知識フォルダ等を拾えなくなるため統合時に復元）。
for entry in sorted(os.listdir(PEOPLE_DIR)):
    subdir = os.path.join(PEOPLE_DIR, entry)
    if os.path.isdir(subdir):
        for path in glob.glob(os.path.join(subdir, "*.md")):
            records.append(parse_md(path, entry))

title_to_id = {r["title"]: r["id"] for r in records}
for r in records:
    # 姓名間の全角カッコ書きを除いたバリエーションも解決できるようにする
    # 例: "ユーザー（デモユーザー）"→"ユーザー", "人物B（別名）"→"人物B"
    base = re.sub(r'（[^（）]*）$', '', r["title"])
    title_to_id.setdefault(base, r["id"])
    m_paren = re.search(r'（([^（）]*)）$', r["title"])
    if m_paren:
        title_to_id.setdefault(m_paren.group(1), r["id"])
    # スペース抜き表記（本文の地の文でよく使われる）も解決できるようにする
    no_space = r["title"].replace(" ", "").replace("　", "")
    title_to_id.setdefault(no_space, r["id"])
    base_no_space = base.replace(" ", "").replace("　", "")
    title_to_id.setdefault(base_no_space, r["id"])
    # ファイル名（id）そのものでも解決できるようにする（保険）。
    # タイトルを変更しても、既存の [[旧タイトル]] 形式のリンクがファイル名と
    # 一致していれば壊れないようにするための安全策。
    title_to_id.setdefault(r["id"], r["id"])

# 2026-07-26: 逆リンク（バックリンク）— 「このページへリンクしている他のページ」一覧。
# 従来は各ページの「関連」欄（そのページ自身が張っているリンク＝順方向）しか見えず、
# 逆に「自分がどのページから参照されているか」はUI上に一切出てこなかった。
# ここでは各レコードのlinks（[[wikilink]]から抽出済み）をtitle_to_idで解決し、
# 参照先ごとに「参照元id一覧」を集計する。表示順はJS側でタイトルの五十音順に揃える。
backlinks_map = {r["id"]: [] for r in records}
for r in records:
    seen_targets = set()
    for name in r["links"]:
        target_id = title_to_id.get(name)
        if target_id and target_id != r["id"] and target_id not in seen_targets:
            seen_targets.add(target_id)
            backlinks_map[target_id].append(r["id"])
for r in records:
    r["backlinks"] = backlinks_map[r["id"]]

# 重複候補の自動検出・レポート出力（ビルドのたび上書き。詳細はdetect_duplicate_candidates参照）
dup_candidates = detect_duplicate_candidates(records)
write_duplicate_report(VAULT_DIR, dup_candidates)
if dup_candidates:
    print(f"[WARN] duplicate candidates detected: {len(dup_candidates)}件 — see memory/reference/reference_duplicate_candidates.md", file=sys.stderr)
    for a, b, reason in dup_candidates:
        print(f"  - 「{a}」 <-> 「{b}」 ({reason})", file=sys.stderr)

# 前々回接触の書き忘れ候補の自動検出・レポート出力（2026-08-05追加。詳細はdetect_prev_contact_candidates参照）
prev_contact_candidates = detect_prev_contact_candidates(records)
write_prev_contact_report(VAULT_DIR, prev_contact_candidates)
if prev_contact_candidates:
    print(f"[WARN] prev-contact candidates detected: {len(prev_contact_candidates)}件 — see memory/reference/reference_prev_contact_candidates.md", file=sys.stderr)
    for s in prev_contact_candidates:
        print(f"  - 「{s['title']}」（{s['person']}視点）: {s['suggestion']}", file=sys.stderr)

# raw/フォルダのLINE等生データ一覧（2026-08-05: 「このwikiの構造」ページで使用。詳細はscan_raw_line_files参照）
raw_files_info = scan_raw_line_files(VAULT_DIR, records)

# --- alias map for auto-linking plain-text mentions ---
# 展示用デモでは、原本由来の名前・ニックネームを一切持ち込まない。
ALIASES = {}
ALIASES = {k: v for k, v in ALIASES.items() if v in title_to_id}
ALIAS_KEYS_SORTED = sorted(ALIASES.keys(), key=len, reverse=True)

# 大学ページ用: 展示用の概要
UNIV_OVERVIEWS = {
    "青凪大学": "海と情報をテーマにした大学。",
    "東雲工科大学": "デザインと工学を横断する大学。",
}

data_json = json.dumps({
    "records": records,
    "titleToId": title_to_id,
    "aliases": ALIASES,
    "aliasOrder": ALIAS_KEYS_SORTED,
    "univOverviews": UNIV_OVERVIEWS,
    "nonUnivTags": sorted(NON_UNIV),
    "rawFiles": raw_files_info,
}, ensure_ascii=False)

html = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- 2026-07-26: PWA的な「ホーム画面に追加」体験の改善。別ファイル(manifest.json/service worker)を
     追加するとchibako-wiki-mobileリポジトリの単一ファイル配布(index.htmlのみ)という設計を崩すため、
     オフラインキャッシュ(Service Worker)までは踏み込まず、ホーム画面追加時にブラウザUIなしの
     フルスクリーンアプリとして起動できるようにするメタタグのみを追加する。 -->
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="人物Wiki">
<meta name="theme-color" content="#f8f9fa">
<title>人物Wiki — デモ版</title>
<style>
  :root{
    --wiki-bg:#fff; --wiki-border:#a2a9b1; --wiki-link:#0645ad; --wiki-visited:#0b0080;
    --sidebar-bg:#f8f9fa; --infobox-bg:#f8f9fa; --infobox-border:#a2a9b1; --text-color:#202122;
  }
  /* 2026-07-29: 「ビジュアルモード」。ダークモードを廃止し、代わりに配色反転ではなく
     カード型インフォボックス・カテゴリ色タグ・タイムライン履歴表示などレイアウト面を
     作り込んだ「標準／ビジュアル」の2モード切替に置き換えた（ユーザー要望）。
     CSS変数は標準モードに近い落ち着いた配色のまま、形状・レイアウトだけを差し替える。 */
  html.visual{
    --wiki-bg:#faf9f5; --wiki-border:#e3e0d5; --sidebar-bg:#f3f1ea; --infobox-bg:#fff; --infobox-border:#ece9df;
  }
  html.visual #toolbar button{border-radius:12px;}
  html.visual .infobox{border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);}
  html.visual .infobox .ib-title{display:none;}
  html.visual .ib-avatar-row{display:flex;}
  html.visual .star-btn{border-radius:14px;}
  html.visual #visualToggle{border-color:#555;}
  /* 2026-07-29: ビジュアルモード限定で丸みのあるフォントに切り替える（ユーザー要望。
     教科書体は「丸みが足りない」とのことで、丸ゴシック系のM PLUS Rounded 1c
     （Google Fontsから読み込み）に変更。読み込めない環境ではシステムの
     丸ゴシック系フォントへ、それも無ければ標準のゴシック体へフォールバックする。 */
  html.visual body{font-family:"M PLUS Rounded 1c","HGP創英丸ゴシック","こども丸ゴシック","Rounded Mplus 1c","Hiragino Maru Gothic ProN","Yu Gothic UI",sans-serif;}
  *{box-sizing:border-box;}
  html,body{margin:0;height:100%;-webkit-text-size-adjust:100%;}
  body{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;background:var(--wiki-bg);color:var(--text-color);transition:background 0.2s,color 0.2s;}
  #layout{display:flex;height:100vh;height:100dvh;}
  #sidebar{width:280px;flex:0 0 280px;background:var(--sidebar-bg);border-right:1px solid var(--wiki-border);
    display:flex;flex-direction:column;padding:12px;overflow:hidden;}
  /* 2026-08-04（修正）: サイドバー幅280pxの中で「人物Wiki」タイトル＋最終更新バッジ＋
     🎨（デザインモード切替）＋絞り込みボタンを1行に収める。バッジが長いとデザインモード
     ボタンだけが2行目に折り返されて不自然だったため、flex-wrapをやめてバッジ側だけを
     min-width:0+ellipsisで縮められるようにし、ボタン類は常に折り返さず1行を維持する。 */
  #sidebar h2{font-size:15px;margin:4px 0 10px;border-bottom:1px solid var(--wiki-border);padding-bottom:6px;
    display:flex;align-items:center;gap:6px;flex-wrap:nowrap;}
  .wiki-title-text{flex:0 0 auto;white-space:nowrap;}
  .wiki-title-actions{flex:0 0 auto;display:flex;gap:4px;white-space:nowrap;margin-left:auto;}
  .last-update-badge{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;
    font-size:10px;font-weight:normal;color:#54595d;background:var(--sidebar-bg);
    border:1px solid var(--wiki-border);border-radius:10px;padding:2px 8px;white-space:nowrap;}
  #menuToggle{display:none;background:var(--wiki-link);color:#fff;border:none;border-radius:4px;
    font-size:13px;padding:6px 10px;cursor:pointer;}
  #visualToggle{background:none;border:1px solid var(--wiki-border);border-radius:4px;
    font-size:13px;padding:4px 8px;cursor:pointer;margin-right:6px;}
  #visualToggle.active{background:var(--wiki-link);color:#fff;border-color:var(--wiki-link);}
  #search{width:100%;padding:8px 8px;border:1px solid var(--wiki-border);border-radius:2px;font-size:14px;margin-bottom:8px;}
  #searchScopeRow{display:flex;align-items:center;gap:5px;font-size:11px;color:#54595d;margin-bottom:8px;}
  #personFilterRow{display:flex;gap:8px;margin-bottom:10px;}
  .person-filter-btn{flex:1;padding:11px 8px;font-size:14px;font-weight:bold;border:1px solid var(--wiki-border);
    border-radius:20px;background:#fff;color:#54595d;cursor:pointer;}
  .person-filter-btn:hover{background:#eaf3ff;}
  .person-filter-btn.active{background:var(--wiki-link);color:#fff;border-color:var(--wiki-link);}
  /* 2026-07-26: 検索UIコンパクト化。5つの全幅ボタンだったダッシュボード群を
     アイコン1行のツールバーに、カテゴリ/部活/大学の絞り込みを折りたたみパネルにまとめた。 */
  #toolbar{display:flex;gap:4px;margin-bottom:8px;}
  #toolbar button{position:relative;flex:1;padding:7px 0;font-size:16px;line-height:1;
    border-radius:4px;cursor:pointer;background:#fff;border:1px solid var(--wiki-border);}
  #toolbarSettingsBtn{width:100%;padding:5px 8px;margin-bottom:6px;font-size:11px;text-align:left;
    border:1px solid var(--wiki-border);border-radius:4px;background:#fff;color:#54595d;cursor:pointer;}
  #toolbarSettingsBtn:hover{background:#eaecf0;}
  #toolbarSettingsPanel{margin-bottom:8px;padding:8px;border:1px solid var(--wiki-border);border-radius:4px;background:#fff;font-size:12px;}
  #toolbarSettingsPanel.collapsed{display:none;}
  #toolbarSettingsPanel label{display:block;padding:3px 0;cursor:pointer;}
  #toolbarSettingsPanel .toolbar-settings-actions{display:flex;gap:5px;margin-top:6px;}
  #toolbarSettingsPanel .toolbar-settings-actions button{font-size:11px;padding:3px 7px;border:1px solid var(--wiki-border);border-radius:4px;background:#fff;cursor:pointer;}
  #aiSuggestBtn{border-color:#e0c94a;background:#fffbe6;color:#8a6d00;}
  #aiSuggestBtn:hover{background:#fff5c2;}
  #featuredBtn{border-color:var(--wiki-border);background:#eaf3ff;color:var(--wiki-link);}
  #featuredBtn:hover{background:#dbe9fb;}
  #recentBtn{border-color:#a3d9b8;background:#f0fbf4;color:#2a7a4d;}
  #recentBtn:hover{background:#e0f5e8;}
  #dormantBtn{border-color:#d9c3a3;background:#fbf5e8;color:#8a6a2a;}
  #dormantBtn:hover{background:#f5ecd8;}
  #birthdayBtn{border-color:#e0a3c9;background:#fdf0f8;color:#a02a72;}
  #birthdayBtn:hover{background:#fbe0f0;}
  #upcomingBtn{border-color:#c9a3d9;background:#f8f0fb;color:#7a2aa0;}
  #upcomingBtn:hover{background:#f0e0f7;}
  #charterBtn{border-color:#a3b3d9;background:#eef1fb;color:#2a3a8a;}
  #charterBtn:hover{background:#dfe4f5;}
  #structureBtn{border-color:#a3d9d3;background:#eefbf9;color:#1a7a70;}
  #structureBtn:hover{background:#dff5f2;}
  .rawfile-item{margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #eaecf0;}
  .rawfile-item .rf-name{font-weight:bold;}
  .rawfile-item .rf-meta{font-size:12px;color:#54595d;margin-top:3px;line-height:1.7;}
  .rawfile-item .rf-warn{color:#a55858;font-weight:bold;}
  .structure-section{margin-bottom:22px;}
  .structure-section h3{margin:0 0 8px;font-size:15px;}
  .structure-section h4{margin:14px 0 4px;font-size:13px;color:var(--wiki-link);}
  .structure-section p{font-size:13px;color:#333;line-height:1.8;margin:0 0 6px;}
  .structure-section ul{margin:4px 0 6px 1.2em;padding:0;font-size:13px;color:#333;line-height:1.8;}
  .structure-stat-grid{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 4px;}
  .structure-stat{background:#f5f6f7;border:1px solid #eaecf0;border-radius:6px;padding:8px 12px;font-size:12px;color:#54595d;}
  .structure-stat b{display:block;font-size:18px;color:#202122;}
  #filterToggleBtn{width:100%;padding:6px 8px;margin-bottom:6px;font-size:12px;text-align:left;
    border:1px solid var(--wiki-border);border-radius:4px;background:#fff;color:#54595d;cursor:pointer;}
  #filterToggleBtn:hover{background:#eaecf0;}
  #filterPanel{margin-bottom:6px;}
  #filterPanel.collapsed{display:none;}
  .timeline-item{margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #eaecf0;}
  .timeline-item .timeline-date{display:inline-block;font-weight:bold;color:#54595d;font-size:12px;margin-right:8px;}
  .timeline-item .timeline-text{font-size:13px;color:#333;margin-top:2px;line-height:1.7;}
  .sched-status{display:inline-block;font-weight:700;border-radius:3px;padding:0 6px;margin-right:8px;font-size:12px;line-height:1.6;}
  .sched-status.sched-confirmed{background:#d7f0d7;color:#1a7a1a;}
  .sched-status.sched-tentative{background:#fdecc8;color:#8a5a00;}
  .dormant-controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 16px;}
  .dormant-controls input{font-size:13px;padding:5px 8px;border:1px solid var(--wiki-border);
    border-radius:4px;background:#fff;color:var(--text-color);flex:1;min-width:140px;}
  .dormant-controls .dormant-count{font-size:12px;color:#54595d;margin-left:auto;}
  /* 2026-08-04: 視点（ユーザー/共同編集者）・並び順（古い順/新しい順）切り替えを、プルダウンではなく
     ワンタップのボタン切り替えに変更（ユーザー要望）。既存の.person-filter-btnと似た見た目に揃える。 */
  .dormant-toggle-group{display:flex;border:1px solid var(--wiki-border);border-radius:16px;overflow:hidden;}
  .dormant-toggle-btn{font-size:12px;font-weight:bold;padding:6px 12px;border:none;background:#fff;
    color:#54595d;cursor:pointer;}
  .dormant-toggle-btn + .dormant-toggle-btn{border-left:1px solid var(--wiki-border);}
  .dormant-toggle-btn.active{background:var(--wiki-link);color:#fff;}
  /* 2026-08-01: ごぶさたチェックの棒グラフ表示。横軸=人、バーの長さ=最終接触からの日数。
     並び順（古い順/新しい順）はdormantOrderをそのまま流用し、「多い順」が既定。 */
  .dormant-chart-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;cursor:pointer;}
  .dormant-chart-row:hover .dormant-chart-bar{filter:brightness(0.85);}
  .dormant-chart-name{flex:0 0 120px;font-size:12px;font-weight:bold;color:var(--text-color);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right;}
  .dormant-chart-bar-wrap{flex:1;background:#eaecf0;border-radius:3px;overflow:hidden;height:20px;position:relative;}
  .dormant-chart-bar{position:absolute;left:0;top:0;height:100%;background:var(--wiki-link);border-radius:3px;min-width:2px;
    transition:filter .1s;}
  /* 2026-08-05: 「前々回接触」の薄い青バー。濃い青バー（最終接触、上記.dormant-chart-bar）と
     同じ左端(left:0)から伸ばし、濃い青の下に敷いて重ねる。前々回の方が必ず古い＝経過日数が
     長いので、濃い青バーの右側にはみ出た部分だけが薄い青として見える形になる。 */
  .dormant-chart-bar-prev{position:absolute;left:0;top:0;height:100%;background:#d9e7f8;border-radius:3px;min-width:2px;}
  .dormant-chart-days{flex:0 0 auto;font-size:12px;color:#54595d;white-space:nowrap;min-width:60px;}
  /* 2026-08-04: ごぶさたチェック（通常モードの棒グラフのみ、ビジュアルモードのタイルには出さない）に
     「2ヶ月前」「半年前」「1年前」の目安ラインを重ねる。位置は日数バーの列（名前列120px＋gap8px を
     左オフセット、日数列min-width60px＋gap8pxを右オフセットとして除いた範囲）に対する%で計算し、
     JS側（renderDormantList内のDORMANT_THRESHOLDS）で「今日」からの経過日数として毎回算出するため、
     日付が進むたびに自動でラインの位置も後ろにずれていく。 */
  #dormantChart{position:relative;}
  .dormant-threshold-overlay{position:absolute;top:0;bottom:0;left:128px;right:68px;pointer-events:none;z-index:5;}
  .dormant-threshold-line{position:absolute;top:0;bottom:0;border-left:3px solid #d0021b;opacity:0.85;}
  .dormant-threshold-label{position:absolute;top:-2px;left:5px;font-size:11px;font-weight:bold;color:#fff;
    background:#d0021b;padding:1px 6px;border-radius:3px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.25);}
  /* 2026-07-31: 「デザインモード（ビジュアルモード）」限定のタイル表示。DOM構造は常に
     出力しておき、html.visual側でだけ見た目を切り替える（このファイル内の他の
     ビジュアルモード対応箇所と同じ方式）。通常モードでは非表示、ビジュアルモードでは
     従来のリスト（#dormantList）を隠してタイル側を表示する。 */
  #dormantTiles{display:none;}
  html.visual #dormantChart{display:none;}
  html.visual #dormantList{display:none;}
  html.visual #dormantTiles{display:flex;flex-wrap:wrap;gap:14px;}
  .dormant-tile{position:relative;width:150px;height:150px;flex:0 0 150px;border:1px solid var(--wiki-border);
    border-radius:16px;background:#fff;padding:10px;cursor:pointer;display:flex;flex-direction:column;
    justify-content:flex-end;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);transition:box-shadow .15s;}
  .dormant-tile:hover{box-shadow:0 3px 10px rgba(0,0,0,0.15);}
  .dormant-tile-days{position:absolute;top:8px;left:8px;width:32px;height:32px;border-radius:50%;
    background:var(--wiki-link);color:#fff;display:flex;align-items:center;justify-content:center;
    font-size:13px;font-weight:bold;box-shadow:0 1px 3px rgba(0,0,0,0.2);}
  .dormant-tile-name{font-size:14px;font-weight:bold;color:var(--text-color);margin-bottom:3px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .dormant-tile-desc{font-size:11px;color:#54595d;line-height:1.4;overflow:hidden;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;}
  #filters{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;}
  .filter-btn{font-size:11px;padding:4px 8px;border:1px solid var(--wiki-border);background:#fff;border-radius:10px;cursor:pointer;}
  .filter-btn.active{background:var(--wiki-link);color:#fff;border-color:var(--wiki-link);}
  .filter-more-btn{border-style:dashed;color:var(--wiki-link);}
  #clearFiltersBtn{width:100%;padding:5px 8px;margin-bottom:8px;font-size:11px;
    border:1px solid var(--wiki-border);border-radius:4px;background:#fff;color:#54595d;cursor:pointer;}
  #clearFiltersBtn:hover{background:#eaecf0;}
  #listHeaderRow{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:6px;}
  #expandResultsBtn{font-size:11px;padding:3px 7px;border:1px solid var(--wiki-border);border-radius:10px;
    background:#fff;color:var(--wiki-link);cursor:pointer;white-space:nowrap;}
  #expandResultsBtn:hover{background:#eaf3ff;}
  #list{overflow-y:auto;flex:1;font-size:13px;-webkit-overflow-scrolling:touch;}
  #list div.entry{padding:7px 6px;cursor:pointer;border-radius:2px;color:var(--wiki-link);}
  #list div.entry:hover{background:#eaecf0;text-decoration:underline;}
  #list div.entry .snippet{display:block;font-size:11px;color:#54595d;text-decoration:none;margin-top:2px;}
  #count{font-size:11px;color:#54595d;}
  #main{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:28px 40px;max-width:900px;}
  .home-hero{padding:46px 0 30px;max-width:760px;}
  .home-kicker{font-size:12px;font-weight:bold;letter-spacing:.12em;color:var(--wiki-link);margin-bottom:10px;}
  .home-title{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:46px;line-height:1.16;margin:0 0 18px;font-weight:normal;}
  .home-lead{font-size:18px;line-height:1.75;color:#3a3a3a;max-width:650px;margin:0 0 26px;}
  .home-actions{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:34px;}
  .home-primary,.home-secondary{border-radius:6px;padding:11px 18px;font-size:14px;font-weight:bold;cursor:pointer;}
  .home-primary{border:1px solid var(--wiki-link);background:var(--wiki-link);color:#fff;}
  .home-secondary{border:1px solid var(--wiki-border);background:#fff;color:var(--wiki-link);}
  .home-features{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:10px 0 30px;}
  .home-feature{border:1px solid var(--wiki-border);border-radius:8px;padding:17px;background:#fff;}
  .home-feature-icon{font-size:22px;margin-bottom:9px;}
  .home-feature h3{font-size:15px!important;border:0!important;margin:0 0 7px!important;padding:0!important;}
  .home-feature p{font-size:13px;line-height:1.65;color:#54595d;margin:0;}
  .home-demo-route{border-left:4px solid var(--wiki-link);background:#f5f9ff;padding:14px 17px;font-size:13px;line-height:1.7;}
  .wiki-title-text{cursor:pointer;}
  #article h1{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;font-size:28px;font-weight:normal;
    border-bottom:3px solid var(--wiki-border);padding-bottom:6px;margin-bottom:14px;}
  #article .summary{font-size:14px;color:#333;margin-bottom:16px;}
  .infobox{float:right;width:260px;margin:0 0 16px 20px;border:1px solid var(--infobox-border);
    background:var(--infobox-bg);font-size:12.5px;}
  .infobox .ib-title{background:#eaecf0;text-align:center;font-weight:bold;padding:6px;font-size:14px;}
  /* 2026-07-29: ビジュアルモード用のアバター行。標準モードでは非表示(display:none)にしておき、
     html.visual側で表示に切り替える（DOM構造は常に出力し、CSSだけで見た目を切り替える方式）。 */
  .ib-avatar-row{display:none;align-items:center;gap:10px;padding:10px;}
  .ib-avatar{width:38px;height:38px;border-radius:50%;flex:0 0 38px;
    display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:15px;}
  .ib-avatar-name{font-weight:bold;font-size:14px;}
  .ib-cat-badge{display:inline-block;font-size:10px;border-radius:8px;padding:1px 8px;margin-top:3px;}
  .infobox table{width:100%;border-collapse:collapse;}
  .infobox td{padding:5px 6px;border-top:1px solid #eaecf0;vertical-align:top;}
  .infobox td.k{color:#54595d;width:38%;}
  #article h3{font-size:17px;border-bottom:1px solid #eaecf0;padding-bottom:3px;margin-top:22px;}
  #article .section-text{font-size:14px;line-height:1.9;white-space:pre-wrap;}
  /* 2026-07-29: 履歴欄の表示。標準モードは従来通り「- 日付」の箇条書き風、
     ビジュアルモードはドット+縦線のタイムライン表示に切り替える。 */
  .hist-list{font-size:14px;line-height:1.7;}
  .hist-item{padding-left:1em;text-indent:-1em;margin-bottom:2px;}
  .hist-item::before{content:"- ";}
  .hist-date{font-weight:bold;}
  html.visual .hist-list{position:relative;padding-left:16px;}
  html.visual .hist-item{position:relative;padding-left:0;text-indent:0;margin-bottom:12px;}
  html.visual .hist-item::before{content:"";position:absolute;left:-16px;top:6px;width:7px;height:7px;
    border-radius:50%;background:var(--wiki-link);}
  html.visual .hist-list::after{content:"";position:absolute;left:-12px;top:6px;bottom:6px;width:1px;
    background:var(--wiki-border);z-index:-1;}
  html.visual .hist-date{display:block;font-size:11px;color:var(--wiki-link);font-weight:normal;margin-bottom:2px;}
  #article .badge{display:inline-block;background:#eaf3ff;color:var(--wiki-link);font-size:11px;
    padding:2px 8px;border-radius:10px;margin-right:6px;margin-bottom:10px;}
  .star-btn{display:inline-block;font-size:11px;padding:2px 10px;border-radius:10px;
    border:1px solid var(--wiki-border);background:#fff;color:#54595d;cursor:pointer;margin-bottom:10px;}
  .star-btn.active{background:#fff2a8;border-color:#c9a400;color:#5c4a00;}
  a.wikilink{color:var(--wiki-link);text-decoration:none;cursor:pointer;}
  a.wikilink:hover{text-decoration:underline;}
  a.wikilink.missing{color:#a55858;}
  #related a, #backlinks a, .related-group a{display:inline-block;margin:2px 6px 2px 0;}
  #empty-msg{color:#54595d;font-size:13px;padding:20px;}
  /* 2026-08-08: 関係図（正方形タイル敷き詰めスタイル） */
  .closeTileWrap{max-height:72vh;overflow:auto;border:1px solid var(--wiki-border);border-radius:4px;
    background:#f5f6f7;padding:0;-webkit-overflow-scrolling:touch;}
  .closeTileInner{position:relative;}
  .closeTile{position:absolute;box-sizing:border-box;border-radius:6px;padding:6px 7px;cursor:pointer;
    overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:center;
    text-align:center;border:1.5px solid #c8ccd1;background:#fff;transition:transform .1s,box-shadow .1s;}
  .closeTile:hover{transform:scale(1.05);z-index:2;box-shadow:0 2px 10px rgba(0,0,0,.18);}
  .closeTile.center{border-color:var(--wiki-link);border-width:2.5px;background:#eaf3ff;cursor:default;}
  .closeTile.center:hover{transform:none;box-shadow:none;}
  .closeTile .ct-name{font-size:12px;font-weight:bold;color:#202122;line-height:1.25;max-width:100%;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .closeTile .ct-snippet{font-size:10px;color:#54595d;margin-top:3px;line-height:1.32;overflow:hidden;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;}
  .closeTile.tier-vclose{border-color:#c0392b;background:#fdecea;}
  .closeTile.tier-close{border-color:#d9782f;background:#fdf1e6;}
  .closeTile.tier-mid{border-color:#8a9a4e;background:#f3f6ea;}
  .closeTile.tier-far{border-color:#c8ccd1;background:#f7f8f9;}
  .closeTile.tier-vfar{border-color:#e4e6e9;background:#fbfbfc;color:#9a9fa6;}
  .closeTile.tier-vfar .ct-snippet{color:#9a9fa6;}
  mark.hit{background:#fff2a8;padding:0 1px;}

  /* --- 脚注（出典）ジャンプ機能（2026-07-26） --- */
  sup.fnref{font-size:10px;line-height:0;border-radius:2px;transition:background 0.4s;}
  sup.fnref a{color:var(--wiki-link);cursor:pointer;text-decoration:none;padding:0 1px;}
  sup.fnref a:hover{text-decoration:underline;}
  sup.fnref.fn-highlight{background:#fff2a8;}
  ol.footnotes{font-size:12.5px;color:#333;line-height:1.7;padding-left:22px;margin:0;}
  ol.footnotes li{margin-bottom:4px;transition:background 0.4s;border-radius:2px;}
  ol.footnotes li.fn-highlight{background:#fff2a8;}
  /* 2026-08-12: 出典欄→本文の該当箇所へ戻るリンク */
  a.fn-backref{color:var(--wiki-link);cursor:pointer;text-decoration:none;font-size:11px;margin-left:2px;}
  a.fn-backref:hover{text-decoration:underline;}

  /* --- ハッカソン展示用「早送り」体験 --- */
  .fast-forward-btn{border:0;border-radius:999px;padding:9px 16px;background:#111827;color:#fff;
    font-weight:700;cursor:pointer;box-shadow:0 3px 10px rgba(17,24,39,.18);}
  .fast-forward-btn:hover{background:#26324a;transform:translateY(-1px);}
  .fast-forward-btn.secondary{width:100%;margin:7px 0;background:#fff;color:#111827;border:1px solid #aeb7c7;box-shadow:none;}
  .fast-forward-btn:disabled{opacity:.42;cursor:not-allowed;transform:none;}
  .ff-shell{max-width:860px;margin:0 auto;}
  .ff-topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px;}
  .ff-kicker{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#667085;font-weight:700;}
  .ff-stage{position:relative;overflow:hidden;min-height:390px;padding:48px;border-radius:24px;cursor:pointer;
    color:#fff;background:linear-gradient(135deg,#111827 0%,#26324a 58%,#475569 100%);
    box-shadow:0 18px 50px rgba(17,24,39,.22);display:flex;flex-direction:column;justify-content:center;}
  .ff-stage::after{content:'≫';position:absolute;right:24px;bottom:-28px;font-size:170px;font-weight:900;
    color:rgba(255,255,255,.075);line-height:1;}
  .ff-stage.ff-animate .ff-slide-body{animation:ffEnter .42s ease both;}
  @keyframes ffEnter{from{opacity:0;transform:translateX(34px)}to{opacity:1;transform:translateX(0)}}
  .ff-year{font-size:14px;letter-spacing:.12em;color:#cbd5e1;font-weight:700;margin-bottom:12px;}
  .ff-slide-title{font-size:34px;line-height:1.25;margin:0 0 20px;max-width:700px;}
  .ff-slide-text{font-size:18px;line-height:1.85;max-width:700px;white-space:pre-line;color:#f1f5f9;}
  .ff-people{margin-top:22px;font-size:13px;color:#cbd5e1;}
  .ff-dots{display:flex;justify-content:center;gap:9px;margin:16px 0 4px;}
  .ff-dot{width:9px;height:9px;border-radius:50%;background:#cbd2dc;transition:background .2s,transform .2s;}
  .ff-dot.active{background:#111827;transform:scale(1.25);}
  .ff-notice{margin:12px 0;padding:10px 12px;border-radius:8px;background:#fff7d6;color:#5f4b00;font-size:13px;}
  .ff-modal-backdrop{position:fixed;inset:0;z-index:200;background:rgba(15,23,42,.58);display:flex;align-items:center;justify-content:center;padding:20px;}
  .ff-modal-backdrop.hidden{display:none;}
  .ff-config{width:min(760px,100%);max-height:92vh;overflow:auto;background:#fff;border-radius:20px;padding:26px;box-shadow:0 24px 80px rgba(0,0,0,.28);}
  .ff-config h2{margin:0 0 4px;font-size:25px;}
  .ff-config-sub{margin:0 0 20px;color:#667085;}
  .ff-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
  .ff-field{display:flex;flex-direction:column;gap:6px;}
  .ff-field.full{grid-column:1/-1;}
  .ff-field label,.ff-field-title{font-size:12px;font-weight:700;color:#344054;}
  .ff-field input,.ff-field select,.ff-field textarea{width:100%;box-sizing:border-box;border:1px solid #cbd2dc;border-radius:9px;padding:9px 10px;background:#fff;color:#111827;font:inherit;}
  .ff-checks{display:flex;flex-wrap:wrap;gap:7px;}
  .ff-checks label{border:1px solid #cbd2dc;border-radius:999px;padding:6px 10px;font-weight:400;cursor:pointer;}
  .ff-checks input{width:auto;margin-right:4px;}
  .ff-modal-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;margin-top:20px;}
  .ff-modal-actions button{padding:9px 15px;border-radius:999px;border:1px solid #aeb7c7;background:#fff;cursor:pointer;}
  .ff-modal-actions .primary{background:#111827;color:#fff;border-color:#111827;font-weight:700;}
  #ffGenerateStatus{margin-right:auto;font-size:13px;color:#667085;}
  .ff-editing .ff-slide-title,.ff-editing .ff-slide-text{outline:1px dashed #94a3b8;outline-offset:6px;border-radius:3px;}
  .ff-sources{margin-top:22px;font-size:12px;color:#cbd5e1;}
  @media(max-width:760px){.ff-stage{min-height:330px;padding:30px 24px}.ff-slide-title{font-size:27px}.ff-slide-text{font-size:16px}}
  @media(max-width:620px){.ff-form-grid{grid-template-columns:1fr}.ff-field.full{grid-column:auto}.ff-config{padding:20px}}

  /* --- モバイル対応（2026-07-22） --- */
  @media (max-width: 760px){
    #layout{flex-direction:column;height:auto;min-height:100vh;min-height:100dvh;}
    #menuToggle{display:inline-block;}
    #sidebar{width:100%;flex:0 0 auto;border-right:none;border-bottom:1px solid var(--wiki-border);
      max-height:60vh;}
    #sidebar.collapsed #search,
    #sidebar.collapsed #toolbar,
    #sidebar.collapsed #filterToggleBtn,
    #sidebar.collapsed #filterPanel,
    #sidebar.collapsed #searchScopeRow,
    #sidebar.collapsed #personFilterRow,
    #sidebar.collapsed #filters,
    #sidebar.collapsed #listHeaderRow,
    #sidebar.collapsed #count,
    #sidebar.collapsed #list{display:none;}
    #main{padding:16px;max-width:100%;}
    #article h1{font-size:22px;}
    .infobox{float:none;width:100%;margin:0 0 16px 0;}
    #search{font-size:16px;} /* iOSでズームしないよう16px以上 */
  }
  /* 2026-07-29: スマホ（ホーム画面追加のPWAはブラウザの「戻る」ボタンが無い）向けの
     スワイプ操作フィードバック。右スワイプ=戻る／左スワイプ=進む。
     スワイプ量に応じてJS側でopacityを更新する（indicator自体はデフォルト非表示）。 */
  .swipe-indicator{position:fixed;top:50%;transform:translateY(-50%);font-size:30px;
    color:var(--wiki-link);opacity:0;pointer-events:none;z-index:50;}
  .swipe-indicator-left{left:6px;}
  .swipe-indicator-right{right:6px;}
  @media (min-width: 761px){ .swipe-indicator{display:none;} }
</style>
</head>
<body>
<div id="layout">
  <div id="sidebar">
    <h2><span class="wiki-title-text" onclick="goHome()" title="ホームへ戻る">人物Wiki</span><span class="wiki-title-actions"><button id="menuToggle" onclick="toggleSidebar()">絞り込み</button></span></h2>
    <input id="search" placeholder="名前・本文で検索..." oninput="renderList()">
    <div id="toolbar">
      <button id="featuredBtn" data-toolbar-key="featured" onclick="showFeaturedList()">⭐</button>
      <button id="recentBtn" data-toolbar-key="recent" onclick="showRecentUpdates()">🕘</button>
      <button id="dormantBtn" data-toolbar-key="dormant" onclick="showDormantCheck()">🤝</button>
      <button id="birthdayBtn" data-toolbar-key="birthday" onclick="showBirthdays()">🎂</button>
      <button id="upcomingBtn" data-toolbar-key="upcoming" onclick="showUpcomingEvents()" title="今後の予定">📅</button>
    </div>
    <button id="toolbarSettingsBtn" onclick="toggleToolbarSettings()">⚙️ 表示するアイコンを選ぶ</button>
    <div id="toolbarSettingsPanel" class="collapsed">
      <div>左側ツールバーに表示するアイコン</div>
      <label><input type="checkbox" data-toolbar-setting="featured" onchange="saveToolbarSettings()"> ⭐ 主要人物</label>
      <label><input type="checkbox" data-toolbar-setting="recent" onchange="saveToolbarSettings()"> 🕘 最近の更新</label>
      <label><input type="checkbox" data-toolbar-setting="dormant" onchange="saveToolbarSettings()"> 🤝 ごぶさたチェック</label>
      <label><input type="checkbox" data-toolbar-setting="birthday" onchange="saveToolbarSettings()"> 🎂 誕生日</label>
      <label><input type="checkbox" data-toolbar-setting="upcoming" onchange="saveToolbarSettings()"> 📅 今後の予定</label>
      <div class="toolbar-settings-actions"><button onclick="setAllToolbarSettings(true)">すべて表示</button><button onclick="setAllToolbarSettings(false)">すべて非表示</button></div>
    </div>
    <button id="filterToggleBtn" onclick="toggleFilterPanel()">🔧 詳細フィルタ ▾</button>
    <div id="filterPanel" class="collapsed">
      <div id="searchScopeRow">
        <label><input type="checkbox" id="fullTextToggle" checked onchange="renderList()"> 本文も検索対象にする</label>
      </div>
      <div id="personFilterRow">
        <button id="kokuboFilterBtn" class="person-filter-btn" onclick="togglePersonFilter('kokubo')">ユーザーの</button>
        <button id="otaFilterBtn" class="person-filter-btn" onclick="togglePersonFilter('ota')">共同編集者の</button>
      </div>
      <div id="filters"></div>
      <button id="clearFiltersBtn" onclick="clearAllFilters()">✕ 絞り込みを全解除</button>
    </div>
    <div id="listHeaderRow">
      <div id="count"></div>
      <button id="expandResultsBtn" onclick="showSearchResults()">🔎 大きく表示</button>
    </div>
    <button id="fastForwardFilterBtn" class="fast-forward-btn secondary" onclick="fastForwardActiveFilter()">≫ 選択中を早送り</button>
    <div id="list"></div>
  </div>
  <div id="main"><div id="article"></div></div>
  <div id="swipeBackIndicator" class="swipe-indicator swipe-indicator-left">‹</div>
  <div id="swipeFwdIndicator" class="swipe-indicator swipe-indicator-right">›</div>
</div>
<div id="ffConfigModal" class="ff-modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="ffConfigTitle">
  <div class="ff-config">
    <h2 id="ffConfigTitle">≫ 早送りをつくる</h2>
    <p class="ff-config-sub">期間とグループを選ぶと、Wikiの記録を3枚にまとめます。</p>
    <div class="ff-form-grid">
      <div class="ff-field"><label for="ffStartDate">開始日</label><input id="ffStartDate" type="date"></div>
      <div class="ff-field"><label for="ffEndDate">終了日</label><input id="ffEndDate" type="date"></div>
      <div class="ff-field"><label for="ffGroupSelect">グループ</label><select id="ffGroupSelect"></select></div>
      <div class="ff-field full"><label for="ffNotes">備考（任意）</label><textarea id="ffNotes" rows="3" placeholder="追加の希望があれば"></textarea></div>
    </div>
    <div class="ff-modal-actions"><span id="ffGenerateStatus"></span><button onclick="closeFastForwardConfig()">キャンセル</button><button id="ffGenerateBtn" class="primary" onclick="generateAiFastForward()">AIで生成</button></div>
  </div>
</div>
<script>
const DATA = __DATA_JSON__;
const AI_SUGGESTIONS_MD = __AI_SUGGESTIONS_JSON__;
const records = DATA.records;
const titleToId = DATA.titleToId;
const aliases = DATA.aliases;
const aliasOrder = DATA.aliasOrder;
const univOverviews = DATA.univOverviews || {};
const NON_UNIV_TAGS = new Set(DATA.nonUnivTags || []);
const rawFiles = DATA.rawFiles || [];
const byId = {};
records.forEach(r => byId[r.id] = r);

let activeFilter = "全員";
let currentArticleId = null;
// 2026-07-27: 「ユーザーの」「共同編集者の」絞り込み。カテゴリボタンの真上に大きめのトグルボタンとして
// 配置し、どちらもOFFなら絞り込みなし、両方ONなら両者に接点がある人のみに絞り込む。
let personFilter = { kokubo: false, ota: false };
function togglePersonFilter(which){
  personFilter[which] = !personFilter[which];
  const btn = document.getElementById(which === "kokubo" ? "kokuboFilterBtn" : "otaFilterBtn");
  if(btn) btn.classList.toggle("active", personFilter[which]);
  applyToolbarSettings();
  renderList();
}
// 2026-07-22: カテゴリ一覧をハードコードせず、実際にレコードに含まれるcategory値から
// 自動生成する。「主要人物」を先頭に固定し、それ以外は五十音順に並べる。これにより
// 新しいフォルダ（＝新しいカテゴリ）を追加してもこのファイルを直す必要がない。
const categorySet = new Set(records.flatMap(r => [r.category, ...(r.extraCategories || [])]));
// 2026-07-27: 「その他」「前提知識」「幼馴染」はカテゴリボタン一覧が煩雑になるため非表示にする
// （該当レコード自体は削除しない。検索・「全員」からは引き続き参照できる）。
const HIDDEN_CATS = new Set(["その他", "前提知識", "幼馴染"]);
const otherCats = Array.from(categorySet).filter(c => c !== "主要人物" && !HIDDEN_CATS.has(c)).sort((a,b) => a.localeCompare(b, 'ja'));

// 2026-07-31: カテゴリの絞り込みボタンが増えて見づらくなったため、人数の多い
// （＝よく使う）カテゴリだけ常時表示し、少数派のカテゴリは「もっと見る」の
// 折りたたみに回す。閾値は固定人数ではなく「フォルダが増えても自動判定される」
// ように、レコード数の実カウントで決める（ユーザー要望）。
const catCounts = {};
records.forEach(r => {
  catCounts[r.category] = (catCounts[r.category] || 0) + 1;
  (r.extraCategories || []).forEach(c => { catCounts[c] = (catCounts[c] || 0) + 1; });
});
const PRIMARY_CAT_MIN_COUNT = 10;
const primaryCats = ["全員"].concat(categorySet.has("主要人物") ? ["主要人物"] : [])
  .concat(otherCats.filter(c => (catCounts[c] || 0) >= PRIMARY_CAT_MIN_COUNT)
    .sort((a,b) => (catCounts[b]||0) - (catCounts[a]||0)));
const secondaryCats = otherCats.filter(c => (catCounts[c] || 0) < PRIMARY_CAT_MIN_COUNT);
let moreFiltersOpen = false;

// 2026-07-23: 「主要人物」はフォルダ由来の固定カテゴリではなく、閲覧者がページ上の
// ★ボタンで自由に追加・削除できるブラウザ側だけのカスタムカテゴリにする（共同編集者の指示）。
// 初期状態は0人。localStorageに保存するため、同じブラウザで再訪した時は復元される
// （別端末・別ブラウザには引き継がれない）。
const FEATURED_KEY = "wikiFeaturedIds";
function loadFeatured(){
  // ページのリネーム・統合等でidが変わると、古いidがlocalStorageに残ったまま
  // 存在しないレコードを指す「幽霊ID」になり、バッジの件数と一覧の件数がずれる。
  // 現存するレコードのidだけを残すことで自己修復する。
  let raw;
  try { raw = JSON.parse(localStorage.getItem(FEATURED_KEY) || "[]"); }
  catch(e){ raw = []; }
  const cleaned = raw.filter(id => byId[id]);
  if(cleaned.length !== raw.length) saveFeatured(new Set(cleaned));
  return new Set(cleaned);
}
function saveFeatured(set){
  try { localStorage.setItem(FEATURED_KEY, JSON.stringify(Array.from(set))); } catch(e){}
}
let featuredIds = loadFeatured();
function toggleFeatured(id){
  if(featuredIds.has(id)) featuredIds.delete(id); else featuredIds.add(id);
  saveFeatured(featuredIds);
  showArticle(id);
  if(activeFilter === "主要人物") renderList();
  const featuredBtn = document.getElementById("featuredBtn");
  if(featuredBtn){
    featuredBtn.title = `「主要人物」に追加された人一覧（${featuredIds.size}件）`;
  }
}

function toggleSidebar(){
  document.getElementById("sidebar").classList.toggle("collapsed");
}

// 2026-08-17: 左サイドバーのツールバーアイコンをユーザーごとに表示/非表示設定できるようにする。
const TOOLBAR_SETTINGS_KEY = "wikiToolbarVisibilityByPerspective";
const TOOLBAR_DEFAULTS = {featured:true, recent:true, dormant:true, birthday:true, upcoming:true};
function currentToolbarPerspective(){
  if(personFilter.kokubo && !personFilter.ota) return "kokubo";
  if(personFilter.ota && !personFilter.kokubo) return "ota";
  return "all";
}
function loadToolbarSettings(){
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(TOOLBAR_SETTINGS_KEY) || "{}"); } catch(e){}
  // 旧版の共通設定があれば「全員」視点へ一度だけ引き継ぐ。
  if(saved && !saved.all && Object.keys(saved).some(k => Object.prototype.hasOwnProperty.call(TOOLBAR_DEFAULTS, k))){
    saved = {all:saved};
  }
  return Object.assign({}, TOOLBAR_DEFAULTS, saved[currentToolbarPerspective()] || {});
}
function applyToolbarSettings(){
  const settings = loadToolbarSettings();
  document.querySelectorAll("[data-toolbar-key]").forEach(btn => {
    btn.style.display = settings[btn.dataset.toolbarKey] ? "" : "none";
  });
  document.querySelectorAll("[data-toolbar-setting]").forEach(input => {
    input.checked = !!settings[input.dataset.toolbarSetting];
  });
}
function saveToolbarSettings(){
  const settings = {};
  document.querySelectorAll("[data-toolbar-setting]").forEach(input => {
    settings[input.dataset.toolbarSetting] = input.checked;
  });
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(TOOLBAR_SETTINGS_KEY) || "{}"); } catch(e){}
  if(saved && !saved.all && Object.keys(saved).some(k => Object.prototype.hasOwnProperty.call(TOOLBAR_DEFAULTS, k))){
    saved = {all:saved};
  }
  saved[currentToolbarPerspective()] = settings;
  try { localStorage.setItem(TOOLBAR_SETTINGS_KEY, JSON.stringify(saved)); } catch(e){}
  applyToolbarSettings();
}
function setAllToolbarSettings(visible){
  document.querySelectorAll("[data-toolbar-setting]").forEach(input => { input.checked = visible; });
  saveToolbarSettings();
}
function toggleToolbarSettings(){
  const panel = document.getElementById("toolbarSettingsPanel");
  const collapsed = panel.classList.toggle("collapsed");
  document.getElementById("toolbarSettingsBtn").textContent = collapsed ? "⚙️ 表示するアイコンを選ぶ" : "⚙️ アイコン表示設定を閉じる";
}
applyToolbarSettings();

// 2026-07-29: スマホ限定のスワイプ戻る/進む。ホーム画面に追加したPWAとして開くと
// ブラウザの「戻る」ボタンが表示されず、history.back()を呼ぶ手段がタップ操作だけでは
// 無くなってしまうため、右スワイプ=戻る（history.back）、左スワイプ=進む（history.forward）
// というジェスチャーを追加する（ユーザー要望）。window.innerWidth<=760のときのみ有効にし、
// PC幅では既存の操作の邪魔をしないよう完全に無効化する。
(function(){
  const SWIPE_MIN_DIST = 60;
  const SWIPE_MAX_OFF_AXIS = 60;
  const SWIPE_MAX_TIME = 600;
  let startX = 0, startY = 0, startTime = 0, tracking = false;
  const backEl = document.getElementById("swipeBackIndicator");
  const fwdEl = document.getElementById("swipeFwdIndicator");
  function isMobile(){ return window.innerWidth <= 760; }
  function resetIndicators(){
    if(backEl) backEl.style.opacity = 0;
    if(fwdEl) fwdEl.style.opacity = 0;
  }
  document.addEventListener('touchstart', function(e){
    if(!isMobile() || e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    startTime = Date.now();
    tracking = true;
  }, {passive:true});
  document.addEventListener('touchmove', function(e){
    if(!tracking || !isMobile() || e.touches.length !== 1) return;
    const dx = e.touches[0].clientX - startX;
    const dy = e.touches[0].clientY - startY;
    if(Math.abs(dy) > SWIPE_MAX_OFF_AXIS){ resetIndicators(); return; }
    const ratio = Math.min(1, Math.abs(dx) / (SWIPE_MIN_DIST * 2));
    if(dx > 0){ if(backEl) backEl.style.opacity = ratio; if(fwdEl) fwdEl.style.opacity = 0; }
    else if(dx < 0){ if(fwdEl) fwdEl.style.opacity = ratio; if(backEl) backEl.style.opacity = 0; }
  }, {passive:true});
  document.addEventListener('touchend', function(e){
    if(!tracking){ return; }
    tracking = false;
    resetIndicators();
    if(!isMobile()) return;
    const touch = e.changedTouches[0];
    const dx = touch.clientX - startX;
    const dy = touch.clientY - startY;
    const dt = Date.now() - startTime;
    if(dt > SWIPE_MAX_TIME || Math.abs(dy) > SWIPE_MAX_OFF_AXIS) return;
    if(dx >= SWIPE_MIN_DIST) history.back();
    else if(dx <= -SWIPE_MIN_DIST) history.forward();
  }, {passive:true});
  document.addEventListener('touchcancel', function(){ tracking = false; resetIndicators(); }, {passive:true});
})();

// 2026-07-26: 詳細フィルタ（カテゴリ・部活・大学・本文検索チェック）の折りたたみ開閉
function toggleFilterPanel(){
  const panel = document.getElementById("filterPanel");
  const btn = document.getElementById("filterToggleBtn");
  const collapsed = panel.classList.toggle("collapsed");
  btn.textContent = collapsed ? "🔧 詳細フィルタ ▾" : "🔧 詳細フィルタ ▲";
}

// 2026-07-26: ダークモード切替（設定はlocalStorageに保存、初期判定は<head>内の早期スクリプトが担当）
// 2026-07-29: 従来のダークモードを廃止し、代わりに「ビジュアルモード」を追加（ユーザー要望）。
// 配色を反転させるのではなく、カード型インフォボックス・タイムライン履歴・ピル型ツールバーなど
// レイアウト面を作り込んだ見た目に切り替える。
// 2026-07-29: カテゴリ名の文字列から簡易ハッシュで固定パレットの色を割り当てる。
// entities/people/配下のサブフォルダ名がそのままカテゴリ名になる仕組み（新フォルダを追加しても
// このコードを変更しなくて済むよう、カテゴリ名のハードコードは避ける）。
const CAT_PALETTE = [
  {bg:"#eefbf9",text:"#0f6e56"}, {bg:"#f1eeff",text:"#4a3fa0"},
  {bg:"#fdf0e8",text:"#a0522a"}, {bg:"#fdf0f8",text:"#a02a72"},
  {bg:"#eaf3ff",text:"#0c447c"}, {bg:"#eaf3de",text:"#3b6d11"},
  {bg:"#faeeda",text:"#854f0b"}, {bg:"#fcebeb",text:"#a32d2d"},
];
function categoryColor(cat){
  const s = cat || "";
  let hash = 0;
  for(let i = 0; i < s.length; i++){ hash = (hash * 31 + s.charCodeAt(i)) >>> 0; }
  return CAT_PALETTE[hash % CAT_PALETTE.length];
}

// 2026-07-29: 「履歴」欄をビジュアルモードのタイムライン表示に対応させるため、
// テキストブロックを「- 日付: 本文」単位の行に分割して構造化する（表示専用の変換で、
// 元データやPython側のparse_history_entriesとは独立）。日付らしき接頭辞が無い行は
// そのまま本文としてフォールバックする。
const HIST_LINE_RE = /^-\s*([^\s:：][^:：]{0,30}?)[:：]\s*(.*)$/;
function renderHistoryBlock(text, fn){
  if(!text) return "";
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  const rows = lines.map(line => {
    const body = line.replace(/^-\s*/, "");
    const m = HIST_LINE_RE.exec(line);
    if(m && /\d/.test(m[1])){
      return `<div class="hist-item"><span class="hist-date">${escapeHtml(m[1])}</span>${fn(m[2])}</div>`;
    }
    return `<div class="hist-item">${fn(body)}</div>`;
  }).join("");
  return `<div class="hist-list">${rows}</div>`;
}

function renderFilters(){
  const el = document.getElementById("filters");
  // 2026-07-31: 今の絞り込みが「もっと見る」側のカテゴリなら、押した本人が
  // ボタンごと消えて混乱しないよう自動的に展開しておく。
  if(secondaryCats.includes(activeFilter)) moreFiltersOpen = true;
  let html = primaryCats.map(c =>
    `<button class="filter-btn ${c===activeFilter?'active':''}" onclick="setFilter('${c}')">${c}</button>`
  ).join("");
  if(secondaryCats.length){
    const label = moreFiltersOpen ? "▴ 閉じる" : `▾ もっと見る（${secondaryCats.length}）`;
    html += `<button class="filter-btn filter-more-btn" onclick="toggleMoreFilters()">${label}</button>`;
    if(moreFiltersOpen){
      html += secondaryCats.map(c =>
        `<button class="filter-btn ${c===activeFilter?'active':''}" onclick="setFilter('${c}')">${c}</button>`
      ).join("");
    }
  }
  el.innerHTML = html;
}
function toggleMoreFilters(){
  moreFiltersOpen = !moreFiltersOpen;
  renderFilters();
}
// 2026-07-26: 同じカテゴリボタンをもう一度押すと解除できるようにトグル化
function setFilter(c){
  activeFilter = (activeFilter === c) ? "全員" : c;
  renderFilters();
  renderList();
}
// 2026-07-27: 検索条件（カテゴリ・キーワード・本文検索チェック・ユーザーの/共同編集者の）を一括で解除する
function clearAllFilters(){
  activeFilter = "全員";
  const searchEl = document.getElementById("search");
  const fullTextEl = document.getElementById("fullTextToggle");
  if(searchEl) searchEl.value = "";
  if(fullTextEl) fullTextEl.checked = true;
  personFilter = { kokubo: false, ota: false };
  const kokuboBtn = document.getElementById("kokuboFilterBtn");
  const otaBtn = document.getElementById("otaFilterBtn");
  if(kokuboBtn) kokuboBtn.classList.remove("active");
  if(otaBtn) otaBtn.classList.remove("active");
  renderFilters();
  renderList();
}

function uniqueSorted(arr){
  return Array.from(new Set(arr)).sort((a,b) => a.localeCompare(b, 'ja'));
}

// 苗字と名前の間のスペース（半角・全角）を表示上は詰める
function dispName(s){
  return (s||"").replace(/[ 　]/g, "");
}

// 2026-07-26: 名前一致判定。日本語名（漢字/かな）だけでなく、自動生成したローマ字
// （r.romaji、例:"Takada Hayata"）にもマッチするようにする。本文検索チェックの
// ON/OFFに関係なく、名前としてのローマ字検索は常に有効にする。
function nameMatches(r, ql){
  if(dispName(r.title).toLowerCase().includes(dispName(ql))) return true;
  if(r.romaji && r.romaji.toLowerCase().replace(/\s+/g,"").includes(ql.toLowerCase().replace(/\s+/g,""))) return true;
  return false;
}

// 2026-08-05: 「人物C」で検索しても本文に「人物C」を含む他ページに埋もれて名前一致の
// 本人ページが8番目くらいに出てくるのが不便、という指摘を受けて追加。名前（スペース無視・
// 大文字小文字無視）が検索語と完全一致するページを、検索結果の一番上に来るようにする。
function isExactNameMatch(r, q){
  return dispName(r.title).toLowerCase() === dispName(q).toLowerCase();
}

function makeSnippet(text, q){
  if(!text || !q) return "";
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if(idx === -1) return "";
  const start = Math.max(0, idx - 15);
  const end = Math.min(text.length, idx + q.length + 25);
  return (start>0?"…":"") + text.slice(start, end) + (end<text.length?"…":"");
}

// 2026-07-26: 絞り込みロジックを共通化。サイドバーの一覧と「検索結果を大きく表示」の
// 両方から同じ条件で結果を得られるようにする。
function computeFiltered(){
  const q = document.getElementById("search").value.trim();
  const fullText = document.getElementById("fullTextToggle") ? document.getElementById("fullTextToggle").checked : true;
  // 2026-07-27: 「ユーザーの」「共同編集者の」絞り込み。どちらもOFFなら絞り込まず、
  // 両方ONなら両者に接点がある人だけに絞り込む（AND条件）。
  const kokuboQ = personFilter.kokubo;
  const otaQ = personFilter.ota;
  let filtered = records.filter(r => {
    if(activeFilter === "全員") return true;
    if(activeFilter === "主要人物") return featuredIds.has(r.id);
    return r.category === activeFilter || (r.extraCategories || []).includes(activeFilter);
  });
  if(kokuboQ) filtered = filtered.filter(r => r.hasKokubo);
  if(otaQ) filtered = filtered.filter(r => r.hasOta);

  if(q){
    const ql = q.toLowerCase();
    filtered = filtered.filter(r => {
      if(nameMatches(r, q)) return true;
      if(fullText && r.searchText && r.searchText.toLowerCase().includes(ql)) return true;
      return false;
    });
  }
  // 2026-07-26: あいうえお順（五十音順）で並べる。「読み」から生成したreadingSortを
  // 使い、漢字タイトルのlocaleCompareよりも実際の発音順に近い並びにする。また
  // 活動グループ等の「人物ではない」用語集エントリ(entityType
  // が"person"以外)は一覧の一番最後にまとめる。
  filtered.sort((a,b) => {
    // 2026-08-05: 検索語がある場合、(1)名前が完全一致 > (2)名前に部分一致（タイトル/ローマ字）
    // > (3)本文だけがヒット、の3段階で優先順位をつける（例:「人物C」で検索した時、本文に
    // 名前が出てくる他の多数のページより先に、「人物C」本人のページが一番上に来るようにする。
    // タイトルに検索語を含む他のページも、本文だけヒットのページより上に来るようにする）。
    if(q){
      const aExact = isExactNameMatch(a, q);
      const bExact = isExactNameMatch(b, q);
      if(aExact !== bExact) return aExact ? -1 : 1;
      if(!aExact){
        const aNameHit = nameMatches(a, q);
        const bNameHit = nameMatches(b, q);
        if(aNameHit !== bNameHit) return aNameHit ? -1 : 1;
      }
    }
    const aPerson = (a.entityType || "person") === "person";
    const bPerson = (b.entityType || "person") === "person";
    if(aPerson !== bPerson) return aPerson ? -1 : 1;
    const aKey = a.readingSort || a.title;
    const bKey = b.readingSort || b.title;
    return aKey.localeCompare(bKey, 'ja');
  });
  return {filtered, q, fullText, kokuboQ, otaQ};
}

function renderList(){
  const {filtered, q, fullText} = computeFiltered();
  document.getElementById("count").textContent = filtered.length + " 件";
  const ffBtn = document.getElementById("fastForwardFilterBtn");
  if(ffBtn){
    const currentCategory = currentArticleId && byId[currentArticleId] ? byId[currentArticleId].category : "";
    const targetCategory = (activeFilter !== "全員" && activeFilter !== "主要人物") ? activeFilter : currentCategory;
    const usable = !!targetCategory && records.some(r => r.category === targetCategory || (r.extraCategories || []).includes(targetCategory));
    ffBtn.disabled = !usable;
    ffBtn.textContent = usable ? `≫ ${targetCategory}を早送り` : "≫ カテゴリを選んで早送り";
    ffBtn.dataset.targetCategory = targetCategory || "";
  }
  document.getElementById("list").innerHTML = filtered.map(r => {
    const nameHit = nameMatches(r, q);
    let snippetHtml = "";
    if(q && fullText && !nameHit){
      const snip = makeSnippet(r.searchText, q);
      if(snip) snippetHtml = `<span class="snippet">${escapeHtml(snip)}</span>`;
    }
    return `<div class="entry" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}${snippetHtml}</div>`;
  }).join("");
}

// --- 早送り: Wiki内の日付付き履歴だけから文章スライドを組み立てる ---
let ffDeck = [];
let ffIndex = 0;
let ffTimer = null;
let ffPlaying = false;
let ffContext = {kind:"category", value:""};
let ffLastSettings = null;
let ffEditing = false;
let ffAiGenerated = false;
let ffStatusNotice = "";

function cleanTimelineText(text){
  return (text || "")
    .replace(/\[\^\d+\]/g, "")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/^[-\s]+/, "")
    .trim();
}

function datedEventsFromRecord(r){
  const source = [r.history || "", r.current || "", ...(r.extra || []).map(x => x[1] || "")].join("\n");
  const events = [];
  source.split(/\r?\n/).forEach(line => {
    const m = line.match(/^\s*-?\s*(\d{4})-(\d{2})(?:-(\d{2}))?\s*[:：]\s*(.+)$/);
    if(!m) return;
    const date = `${m[1]}-${m[2]}-${m[3] || "01"}`;
    events.push({date, label: m[3] ? `${m[1]}年${Number(m[2])}月${Number(m[3])}日` : `${m[1]}年${Number(m[2])}月`,
      text: cleanTimelineText(m[4]), person: dispName(r.title)});
  });
  return events;
}

function storyChapter(text, index){
  if(/出会|初対面|最初の会話|教科書|傘/.test(text)) return "まだ何者でもなかった春";
  if(/交際を始|意識するよう|親しくなった|夏祭り/.test(text)) return "関係に名前がついた日";
  if(/別れ|交際を終|友人関係に戻|友人に戻/.test(text)) return "離れることを選んだ日";
  if(/卒業/.test(text)) return "毎日会えることが終わった日";
  if(/文化祭|青嵐祭|後夜祭|体育祭/.test(text)) return "夢中で同じものを作った";
  if(/再会|同窓会|久しぶり/.test(text)) return "時間を越えて、また会った";
  if(/人物Wiki|記録|デジタル化|映像/.test(text)) return "忘れないための形を作った";
  return index === 0 ? "記憶のいちばん古い場所" : "あとから大切だったと気づく日";
}

function storyText(text, index, total, name){
  const fact = cleanTimelineText(text).replace(/[。\s]+$/, "");
  if(/出会|初対面|最初の会話|教科書|傘/.test(fact))
    return `${fact}。そのときの私は、${name}がこの先何年も記憶に残る人になるとは思っていなかった。`;
  if(/交際を始|意識するよう|親しくなった|夏祭り/.test(fact))
    return `${fact}。うれしいのに、今までの関係が変わってしまうことが少し怖かった。`;
  if(/別れ|交際を終|友人関係に戻|友人に戻/.test(fact))
    return `${fact}。何かを失ったようで、でも大切だった時間まで否定したくはなかった。`;
  if(/卒業/.test(fact))
    return `${fact}。明日から同じ教室に来ても、もう全員には会えない。その当たり前の終わりを、私はまだ実感できずにいた。`;
  if(/文化祭|青嵐祭|後夜祭|体育祭/.test(fact))
    return `${fact}。準備中は何度も面倒になったのに、終わった瞬間には、もう一度最初からやりたいと思った。`;
  if(/再会|同窓会|久しぶり/.test(fact))
    return `${fact}。変わったところを探していたはずなのに、話し始めると昔の距離にすぐ戻れた。`;
  if(index === total - 1)
    return `${fact}。当時は小さな出来事だった。今振り返ると、それが${name}との関係を次へ運んだ瞬間だったと思う。`;
  return `${fact}。その日の感情に、当時の私はまだうまく名前をつけられなかった。`;
}

function categoryStoryText(year, texts, people){
  const joined = texts.slice(0, 3).map(t => cleanTimelineText(t).replace(/[。\s]+$/, "")).join("。また、");
  if(year === "2012") return `${joined}。私たちはまだ、お互いが十五年後まで続く関係になるとは知らなかった。`;
  if(year === "2013" || year === "2014") return `${joined}。同じものを作って、衝突して、笑った。高校生活の中心に、少しずつこの仲間たちが入り込んでいった。`;
  if(year === "2015") return `${joined}。卒業は終わりではないと言いながら、毎日会える時間が終わることを、誰も口には出せなかった。`;
  if(Number(year) >= 2023) return `${joined}。長い時間が過ぎても、名前を呼ばれた瞬間だけは、教室にいた頃の自分へ戻ることができた。`;
  return `${joined}。離れてから初めて、あの教室で過ごした時間が自分の一部だったと気づいた。`;
}

function buildPersonFastForward(r){
  const events = datedEventsFromRecord(r).sort((a,b) => a.date.localeCompare(b.date));
  const relatedNames = (r.links || []).map(dispName).slice(0, 4);
  const basicMap = Object.fromEntries(r.basic || []);
  const episode = (r.extra || []).find(x => x[0] === "印象的なエピソード");
  const voice = (r.extra || []).find(x => x[0] === "本人らしい一言");
  const deck = [{
    year:"FAST FORWARD", title:`${dispName(r.title)}との時間をたどる`,
    text:r.summary || "Wikiに残された記録を、時間の流れに沿って振り返ります。",
    people:relatedNames.length ? `関わりのある人・場所: ${relatedNames.join("、")}` : ""
  }];
  if(episode || voice || basicMap["高校時代のあだ名"]){
    deck.push({
      year:"CHARACTER",
      title:`あの頃の「${basicMap["高校時代のあだ名"] || dispName(r.title)}」`,
      text:[episode ? cleanTimelineText(episode[1]) : "", voice ? cleanTimelineText(voice[1]) : ""].filter(Boolean).join("\n\n"),
      people:basicMap["いつも持っていたもの"] ? `いつも持っていたもの: ${basicMap["いつも持っていたもの"]}` : ""
    });
  }
  events.forEach((e, i) => deck.push({
    year:e.label,
    title:storyChapter(e.text, i),
    text:storyText(e.text, i, events.length, dispName(r.title)),
    people:e.person
  }));
  if(!events.length) deck.push({year:"NO RECORD", title:"日付付きの記録はまだありません", text:"このWikiに記録が増えると、ここに時間の流れが現れます。", people:"情報のない期間は推測していません"});
  deck.push({year:"NOW", title:"そして、現在へ", text:`出来事を並べただけでは、関係は説明できない。\nそれでも振り返ると、${dispName(r.title)}と過ごした時間が、今の私の一部になっていることが分かる。`, people:`${events.length}件の記録から振り返りました`});
  return deck;
}

function buildSelfFastForward(r){
  const events = datedEventsFromRecord(r).sort((a,b) => a.date.localeCompare(b.date));
  const facts = xs => xs.map(e => `${e.label}、${cleanTimelineText(e.text).replace(/[。\s]+$/, "")}`).join("。") + "。";
  const early = events.filter(e => e.date < "2015-04-01").slice(0, 3);
  const middle = events.filter(e => e.date >= "2015-04-01" && e.date < "2023-01-01").slice(-3);
  const recent = events.filter(e => e.date >= "2023-01-01").slice(-3);
  return [
    {
      year:"2012 — 2015", title:"記録を始めた高校時代",
      text:`${facts(early)}写真や短い言葉で出来事を残すうちに、何を覚えていたいのかを考えるようになった。`,
      people:"始まりは、高校一年生の教室だった"
    },
    {
      year:"2015 — 2022", title:"離れてから見えたもの",
      text:`${facts(middle)}卒業後、毎日会っていた人たちとの距離が変わった。記録を読み返すことで、自分がどんな場面で動き、迷い、誰を頼ってきたのかが少しずつ見えてきた。`,
      people:"出来事の記録が、自分の選択の記録へ変わった"
    },
    {
      year:"2023 — NOW", title:"30歳の現在地",
      text:`${facts(recent)}今の人物Wikiは、過去をきれいにまとめるためのものではない。自分が何を大切にしてきたかを確かめ、これから人とどう向き合うかを選ぶための場所になっている。`,
      people:`${events.length}件の自分の記録から振り返りました`
    }
  ];
}

function buildCategoryFastForward(category){
  const members = records.filter(r => r.category === category || (r.extraCategories || []).includes(category));
  const events = members.flatMap(datedEventsFromRecord).sort((a,b) => a.date.localeCompare(b.date));
  const years = [...new Set(events.map(e => e.date.slice(0,4)))];
  const deck = [{year:"FAST FORWARD", title:`${category}の時間をたどる`,
    text:`${members.length}人のページに残された出来事を、ひとつの時間軸で再生します。`,
    people:`登場する人: ${members.map(r => dispName(r.title)).join("、")}`}];
  // 人数が多いコミュニティは同じ年の出来事を束ね、十数枚で全期間を見渡せるようにする。
  const byYear = {};
  events.forEach(e => { (byYear[e.date.slice(0,4)] ||= []).push(e); });
  Object.keys(byYear).sort().forEach((year, i) => {
    const group = byYear[year];
    const uniqueTexts = [...new Set(group.map(e => e.text))].slice(0, 4);
    const people = [...new Set(group.map(e => e.person))];
    deck.push({
      year:`${year}年`,
      title:i === 0 ? "この時間のはじまり" : `${category}の${year}年`,
      text:categoryStoryText(year, uniqueTexts, people),
      people:`${people.slice(0, 6).join("、")}${people.length > 6 ? ` ほか${people.length - 6}人` : ""}`
    });
  });
  if(!events.length) deck.push({year:"NO RECORD", title:"日付付きの記録はまだありません", text:"人物ページの履歴が増えると、コミュニティ全体の変遷として再生できます。", people:"記録のない出来事は補完していません"});
  deck.push({year:"NOW", title:"積み重なった時間", text:`${years.length ? years.join("・") + "年の" : ""}${events.length}件の出来事を早送りしました。`, people:`${category}・${members.length}人の記録から構成`});
  return deck;
}

function allDatedEvents(){
  return records.flatMap(datedEventsFromRecord).sort((a,b) => a.date.localeCompare(b.date));
}

function populateFastForwardOptions(){
  const groupSelect = document.getElementById("ffGroupSelect");
  if(groupSelect) groupSelect.innerHTML = Array.from(categorySet).filter(Boolean).sort((a,b)=>a.localeCompare(b,"ja"))
    .map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
}

function openFastForwardConfig(kind, value, keepSettings=false){
  stopFastForward();
  ffContext = {kind, value};
  populateFastForwardOptions();
  const events = allDatedEvents();
  const current = kind === "person" ? byId[value] : null;
  const defaultGroup = current ? current.category : value;
  const group = document.getElementById("ffGroupSelect");
  if(group && Array.from(group.options).some(o => o.value === defaultGroup)) group.value = defaultGroup;
  if(!keepSettings || !ffLastSettings){
    document.getElementById("ffStartDate").value = events[0]?.date || "2012-01-01";
    document.getElementById("ffEndDate").value = events[events.length-1]?.date || new Date().toISOString().slice(0,10);
    document.getElementById("ffNotes").value = "";
  } else {
    document.getElementById("ffStartDate").value = ffLastSettings.startDate;
    document.getElementById("ffEndDate").value = ffLastSettings.endDate;
    group.value = ffLastSettings.group;
    document.getElementById("ffNotes").value = ffLastSettings.notes || "";
  }
  document.getElementById("ffGenerateStatus").textContent = location.protocol === "file:" ? "AI利用時は start_wiki.bat から起動" : "";
  document.getElementById("ffConfigModal").classList.remove("hidden");
}

function closeFastForwardConfig(){ document.getElementById("ffConfigModal").classList.add("hidden"); }

function collectFastForwardSettings(){
  return {
    startDate:document.getElementById("ffStartDate").value,
    endDate:document.getElementById("ffEndDate").value,
    group:document.getElementById("ffGroupSelect").value,
    notes:document.getElementById("ffNotes").value.trim(),
    slideCount:3,
    focusPerson:ffContext.kind === "person" ? ffContext.value : ""
  };
}

function recordsForAi(settings){
  return records.filter(r => (r.category === settings.group || (r.extraCategories || []).includes(settings.group)))
    .filter(r => settings.focusPerson === "神谷ハル" ? r.title === "神谷ハル" : true)
    .map(r => {
      const events = datedEventsFromRecord(r).filter(e => (!settings.startDate || e.date >= settings.startDate) && (!settings.endDate || e.date <= settings.endDate));
      return {
        name:r.title, summary:cleanTimelineText(r.summary), basic:Object.fromEntries(r.basic || []),
        traits:cleanTimelineText(r.traits), relations:cleanTimelineText(r.relations),
        memories:(r.extra || []).map(([heading,text]) => ({heading,text:cleanTimelineText(text)})),
        events:events.map(e => ({date:e.date,text:e.text,source:`${e.date} ${r.title}`}))
      };
    }).filter(r => r.events.length || r.name === settings.focusPerson);
}

function localDeckForSettings(settings){
  let deck;
  if(settings.focusPerson === "神谷ハル" && byId[settings.focusPerson]) deck = buildSelfFastForward(byId[settings.focusPerson]);
  else if(settings.focusPerson && byId[settings.focusPerson] && settings.group === byId[settings.focusPerson].category) deck = buildPersonFastForward(byId[settings.focusPerson]);
  else deck = buildCategoryFastForward(settings.group);
  const within = deck.filter((s,i) => i === 0 || s.year === "NOW" || !/^\d{4}年/.test(s.year) ||
    ((!settings.startDate || s.year.slice(0,4) >= settings.startDate.slice(0,4)) && (!settings.endDate || s.year.slice(0,4) <= settings.endDate.slice(0,4))));
  if(within.length <= settings.slideCount) return within;
  const middle = within.slice(1,-1);
  const take = Math.max(1, settings.slideCount - 2);
  const chosen = Array.from({length:take},(_,i)=>middle[Math.round(i*(middle.length-1)/Math.max(1,take-1))]);
  return [within[0],...chosen,within[within.length-1]];
}

async function generateAiFastForward(){
  const settings = collectFastForwardSettings();
  ffLastSettings = settings;
  const status = document.getElementById("ffGenerateStatus");
  const button = document.getElementById("ffGenerateBtn");
  status.textContent = "Wikiから記録を集めています…"; button.disabled = true;
  try{
    if(location.protocol === "file:") throw new Error("AIバックエンドへ接続するには start_wiki.bat から起動してください");
    status.textContent = "AIが物語を構成しています…";
    const apiBase = location.hostname.endsWith("github.io") ? "https://people-wiki-fast-forward-api.onrender.com" : "";
    const response = await fetch(`${apiBase}/api/generate`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({settings,records:recordsForAi(settings)})});
    const payload = await response.json();
    if(!response.ok || !payload.ok) throw new Error(payload.error || "生成に失敗しました");
    ffDeck = payload.story.slides.map(s => ({year:s.date,title:s.heading,text:s.narration,people:"",sources:s.sourceEvents || []}));
    ffAiGenerated = true; ffStatusNotice = "AIがWikiの記録から生成しました";
  }catch(error){
    ffDeck = localDeckForSettings(settings);
    ffAiGenerated = false; ffStatusNotice = `ローカル構成で表示中: ${error.message}`;
  }finally{
    button.disabled = false; status.textContent = ""; closeFastForwardConfig();
  }
  displayFastForwardDeck();
}

function fastForwardActiveFilter(){
  const btn = document.getElementById("fastForwardFilterBtn");
  const target = btn ? btn.dataset.targetCategory : "";
  if(!target) return;
  openFastForwardConfig("category", target);
}

function showFastForward(kind, value){
  stopFastForward();
  if(kind === "person"){
    const r = byId[value];
    if(!r) return;
    currentArticleId = value;
    ffDeck = r.id === "神谷ハル" ? buildSelfFastForward(r) : buildPersonFastForward(r);
    location.hash = `ff:p:${encodeURIComponent(value)}`;
  } else {
    ffDeck = buildCategoryFastForward(value);
    location.hash = `ff:c:${encodeURIComponent(value)}`;
  }
  ffAiGenerated = false;
  ffStatusNotice = "保存済みの記録からローカル生成しました";
  displayFastForwardDeck();
}

function displayFastForwardDeck(){
  ffIndex = 0;
  document.getElementById("article").innerHTML = `
    <div class="ff-shell">
      <div class="ff-topbar"><div><div class="ff-kicker">≫ TIME COMPRESSION</div><h1 style="margin:4px 0 0;">早送り</h1></div><button class="star-btn" onclick="history.back()">元のページへ戻る</button></div>
      <div id="ffStage" class="ff-stage" onclick="advanceFastForward()" title="クリックして次へ"><div id="ffSlideBody" class="ff-slide-body"></div></div>
      <div id="ffDots" class="ff-dots">${ffDeck.map((_,i)=>`<span class="ff-dot${i===0?' active':''}"></span>`).join("")}</div>
      <div class="ff-notice">${escapeHtml(ffStatusNotice)}</div>
    </div>`;
  document.getElementById("main").scrollTop = 0;
  renderList();
  renderFastForwardSlide();
}

function renderFastForwardSlide(){
  const s = ffDeck[ffIndex];
  if(!s) return;
  const stage = document.getElementById("ffStage");
  const body = document.getElementById("ffSlideBody");
  if(!body) return;
  stage.classList.remove("ff-animate");
  void stage.offsetWidth;
  body.innerHTML = `<div class="ff-year">${escapeHtml(s.year)}</div>${s.title ? `<h2 class="ff-slide-title" ${ffEditing?'contenteditable="true"':''} oninput="updateCurrentSlide('title',this.innerText)">${escapeHtml(s.title)}</h2>` : ""}<div class="ff-slide-text" ${ffEditing?'contenteditable="true"':''} oninput="updateCurrentSlide('text',this.innerText)">${escapeHtml(s.text)}</div>${s.people ? `<div class="ff-people">${escapeHtml(s.people)}</div>` : ""}${s.sources?.length ? `<div class="ff-sources">根拠: ${s.sources.map(escapeHtml).join(" / ")}</div>` : ""}`;
  stage.classList.toggle("ff-editing", ffEditing);
  stage.classList.add("ff-animate");
  document.querySelectorAll("#ffDots .ff-dot").forEach((dot,i) => dot.classList.toggle("active", i === ffIndex));
}

function advanceFastForward(){
  if(!ffDeck.length) return;
  ffIndex = (ffIndex + 1) % ffDeck.length;
  renderFastForwardSlide();
}

function ffMove(delta){
  ffIndex = Math.max(0, Math.min(ffDeck.length - 1, ffIndex + delta));
  renderFastForwardSlide();
  if(ffPlaying){ stopFastForward(); startFastForward(); }
}
function startFastForward(){
  stopFastForward(); ffPlaying = true;
  const btn = document.getElementById("ffPlayBtn"); if(btn) btn.textContent = "一時停止";
  ffTimer = setInterval(() => {
    if(ffIndex >= ffDeck.length - 1){ stopFastForward(); return; }
    ffIndex += 1; renderFastForwardSlide();
  }, 3600);
}
function stopFastForward(){
  if(ffTimer) clearInterval(ffTimer); ffTimer = null; ffPlaying = false;
  const btn = document.getElementById("ffPlayBtn"); if(btn) btn.textContent = "再生";
}
function toggleFastForward(){ ffPlaying ? stopFastForward() : startFastForward(); }
function updateCurrentSlide(field,value){ if(ffDeck[ffIndex]) ffDeck[ffIndex][field] = value.trim(); }
function toggleFastForwardEditing(){
  ffEditing = !ffEditing; stopFastForward(); renderFastForwardSlide();
  const btn = document.getElementById("ffEditBtn"); if(btn) btn.textContent = ffEditing ? "✓ 編集を終了" : "✎ 文章を編集";
}
function regenerateFastForward(){
  if(!ffLastSettings){ openFastForwardConfig(ffContext.kind,ffContext.value,false); return; }
  openFastForwardConfig(ffContext.kind,ffContext.value,true);
}

// 2026-07-26: 検索結果をサイドバーの狭い一覧ではなく、メインの大きな画面に表示する
function showSearchResults(){
  location.hash = "search";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const {filtered, q, fullText, kokuboQ, otaQ} = computeFiltered();
  const condParts = [];
  if(activeFilter !== "全員") condParts.push(`カテゴリ: ${escapeHtml(activeFilter)}`);
  if(kokuboQ) condParts.push(`ユーザーの`);
  if(otaQ) condParts.push(`共同編集者の`);
  if(q) condParts.push(`キーワード: 「${escapeHtml(q)}」`);
  const condText = condParts.length ? condParts.join(" / ") : "絞り込みなし（全員が対象）";
  const rows = filtered.map(r => {
    const nameHit = nameMatches(r, q);
    let snippetHtml = "";
    if(q && fullText && !nameHit){
      const snip = makeSnippet(r.searchText, q);
      if(snip) snippetHtml = `<div class="timeline-text">${escapeHtml(snip)}</div>`;
    }
    return `
    <div class="timeline-item">
      <a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>
      <span class="timeline-date">${escapeHtml(r.category || "")}</span>
      ${snippetHtml}
    </div>`;
  }).join("");
  document.getElementById("article").innerHTML = `
    <div class="badge">検索結果</div>
    <h1>検索結果を大きく表示</h1>
    <div class="summary">${condText}（${filtered.length}件）</div>
    ${rows || '<div style="color:#54595d;">該当ページなし</div>'}
  `;
  document.getElementById("main").scrollTop = 0;
}

function escapeHtml(s){
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// build one regex that matches [[wikilink]] OR any known alias, longest alias first
const aliasPattern = aliasOrder.map(a => a.replace(/[.*+?^${}()|[\]]/g, '\\$&')).join("|");
const combinedRe = new RegExp("\\[\\[([^\\]]+)\\]\\]" + (aliasPattern ? "|(" + aliasPattern + ")" : ""), "g");

function linkify(text){
  if(!text) return "";
  const escaped = escapeHtml(text);
  return escaped.replace(combinedRe, (m, bracketName, aliasName) => {
    if(bracketName){
      const pipeIdx = bracketName.indexOf("|");
      const targetName = pipeIdx >= 0 ? bracketName.slice(0, pipeIdx) : bracketName;
      const displayName = pipeIdx >= 0 ? bracketName.slice(pipeIdx + 1) : bracketName;
      const id = titleToId[targetName];
      if(id) return `<a class="wikilink" onclick="showArticle('${id.replace(/'/g,"\\'")}')">${displayName || dispName(targetName)}</a>`;
      return `<a class="wikilink missing">${displayName || dispName(targetName)}（未作成）</a>`;
    }
    if(aliasName){
      const targetTitle = aliases[aliasName];
      const id = titleToId[targetTitle];
      if(id) return `<a class="wikilink" onclick="showArticle('${id.replace(/'/g,"\\'")}')" title="${aliasName}→${targetTitle}">${aliasName}</a>`;
    }
    return m;
  });
}

// 2026-07-26: 本文中の [^N] 脚注記号を、出典欄の該当項目へジャンプするリンクに変換する。
// linkify() の後（wikilink変換後）にかけるため、"[[" を要求するwikilink正規表現とは衝突しない。
// 2026-08-12: occTracker（{n: 出現回数}のオブジェクト）を渡すと、各出現箇所に一意なid
// （fnref-recId-n-出現順）を振る。これにより出典欄からその出現箇所へ「戻る」リンクを
// 張れるようになる（scrollToFootnoteRef参照）。occTrackerを渡さない呼び出し元（検索結果
// スニペットや「今後の予定」欄など、記事本文の出典欄と対応しない場所）はidなしのまま。
function withFootnoteRefs(html, recId, occTracker){
  if(!html) return html;
  return html.replace(/\[\^\d+\]/g, "");
}

function scrollToFootnote(recId, n){
  const el = document.getElementById('fn-' + recId + '-' + n);
  if(!el) return;
  el.scrollIntoView({behavior:'smooth', block:'center'});
  el.classList.add('fn-highlight');
  setTimeout(() => el.classList.remove('fn-highlight'), 1500);
}

// 2026-08-12: 出典欄の「↩」から、本文中のその脚注が実際に引用されている箇所へ戻る。
function scrollToFootnoteRef(recId, n, idx){
  const el = document.getElementById('fnref-' + recId + '-' + n + '-' + idx);
  if(!el) return;
  el.scrollIntoView({behavior:'smooth', block:'center'});
  el.classList.add('fn-highlight');
  setTimeout(() => el.classList.remove('fn-highlight'), 1500);
}

// 2026-08-08: 「要確認事項一覧」を廃止し、代わりに「AIからの提案」を新設。
// wiki全体の内容（予定・ごぶさたチェック相当の接触状況・各人物の記述）をもとに、AIが
// ユーザー・共同編集者それぞれへの行動案をまとめたものを表示する。原本はexbrainリポジトリ直下の
// AI_SUGGESTIONS.md（Pythonがビルド時に読み込み、JS側のAI_SUGGESTIONS_MDに埋め込む）。
// 週1回程度、スケジュールタスクでAIがこのファイルを書き換えて再ビルドする運用を想定。

// ごく簡易なMarkdown→HTML変換（## 見出し・- 箇条書き・段落のみ対応。
// AI_SUGGESTIONS.md専用。他のページ本文と同じくlinkify()で[[wikilink]]も解決する）。
function renderSuggestionsMarkdown(md){
  const lines = (md || "").split(/\n/);
  let html = "";
  let listBuf = [];
  const flushList = () => {
    if(listBuf.length){
      html += "<ul>" + listBuf.map(l => `<li>${linkify(l)}</li>`).join("") + "</ul>";
      listBuf = [];
    }
  };
  lines.forEach(line => {
    const t = line.trim();
    if(!t){ flushList(); return; }
    if(t.startsWith("最終更新:")) return; // summaryで別途表示するので本文には出さない
    if(t.startsWith("## ")){
      flushList();
      html += `<h3>${linkify(t.slice(3))}</h3>`;
    } else if(t.startsWith("- ")){
      listBuf.push(t.slice(2));
    } else {
      flushList();
      html += `<p>${linkify(t)}</p>`;
    }
  });
  flushList();
  return html;
}

function splitSuggestionsByAudience(md){
  const result = { kokubo: "", ota: "" };
  let current = null;
  (md || "").split(/\r?\n/).forEach(line => {
    if(line.trim() === "## ユーザーへ") { current = "kokubo"; return; }
    if(line.trim() === "## 共同編集者へ") { current = "ota"; return; }
    if(current) result[current] += line + "\n";
  });
  return result;
}

function showAiSuggestions(){
  location.hash = "ai-suggest";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const m = (AI_SUGGESTIONS_MD || "").match(/^最終更新:\s*(.+)$/m);
  const updatedLabel = m ? m[1].trim() : "";
  const audience = splitSuggestionsByAudience(AI_SUGGESTIONS_MD);
  document.getElementById("article").innerHTML = `
    <div class="badge">ダッシュボード</div>
    <h1>AIからの提案</h1>
    <div class="summary">現在のwikiの内容全体をもとに、AIがユーザー・共同編集者それぞれへの行動案をまとめたものです。${updatedLabel ? `最終更新: ${escapeHtml(updatedLabel)}。` : ""}週1回程度を目安に更新されます。あくまで参考であり、強制ではありません。</div>
    <div class="suggestion-tabs" role="tablist" style="display:flex;gap:8px;margin:16px 0 10px;">
      <button id="suggestKokuboBtn" class="star-btn active" role="tab" aria-selected="true" onclick="switchSuggestionAudience('kokubo')">ユーザーへ</button>
      <button id="suggestOtaBtn" class="star-btn" role="tab" aria-selected="false" onclick="switchSuggestionAudience('ota')">共同編集者へ</button>
    </div>
    <div id="suggestionPanel" class="structure-section">${renderSuggestionsMarkdown(audience.kokubo) || '<p style="color:#54595d;">まだ提案がありません。</p>'}</div>
  `;
  window._suggestionAudience = audience;
  document.getElementById("main").scrollTop = 0;
}

function switchSuggestionAudience(audience){
  const data = window._suggestionAudience || splitSuggestionsByAudience(AI_SUGGESTIONS_MD);
  const panel = document.getElementById("suggestionPanel");
  if(!panel) return;
  panel.innerHTML = renderSuggestionsMarkdown(data[audience]) || '<p style="color:#54595d;">まだ提案がありません。</p>';
  const isKokubo = audience === "kokubo";
  const kb = document.getElementById("suggestKokuboBtn");
  const ot = document.getElementById("suggestOtaBtn");
  if(kb){ kb.classList.toggle("active", isKokubo); kb.setAttribute("aria-selected", String(isKokubo)); }
  if(ot){ ot.classList.toggle("active", !isKokubo); ot.setAttribute("aria-selected", String(!isKokubo)); }
}

// 2026-08-08: 関係の近さをノード間の距離に反映する機能。「関係」「ユーザーとの関係」
// 「共同編集者との関係」欄の文章に含まれる語句から簡易的に親密度を推定し、力学レイアウトの
// ばね目標距離（＝落ち着いた時のノード間距離）に反映する。仲がいいと書かれている相手
// ほど近くに、疎遠・気まずいと書かれている相手ほど遠くに表示される。あくまで本文の
// 言い回しからの粗い推定であり、厳密なスコアではない点に注意（凡例に明記）。
const CLOSENESS_KEYWORDS = [
  // 「付き合っ」「彼女」「彼氏」は「相談に付き合う」「彼氏でしょと冷やかされるが否定」等、
  // 恋愛関係ではない/否定文脈でも出現しやすいため、誤検知を避けてtier2以下に置く。
  { dist: 55,  words: ["交際中", "婚約", "結婚を前提", "恋人同士", "本命", "溺愛", "想い人", "一番仲がいい", "最も近しい", "無二の親友", "唯一無二", "主治医", "一心同体"] },
  { dist: 75,  words: ["大切な友達", "親友", "元交際相手", "元カノ", "元カレ", "戦友", "幼馴染", "ずっと仲良し", "家族ぐるみ", "彼女", "彼氏", "付き合っ", "恋人"] },
  { dist: 100, words: ["仲良し", "仲がいい", "仲いい", "気の合う", "相談相手", "仲間", "近しい", "よく話す", "頻繁に"] },
  { dist: 160, words: ["気まずい", "疎遠", "知り合い程度", "一度だけ", "噂で聞いた", "接点はほとんどない", "面識のみ", "あまり話さない"] },
  { dist: 200, words: ["直接の関わりはない", "特に接点なし", "一切関わりがない", "ほぼ関わりがない", "間接的に"] },
];
const CLOSENESS_DEFAULT_LINKED = 130;  // 関係を示す文章が見つからない場合（従来通りの距離）
const CLOSENESS_DEFAULT_TEXTED = 115;  // 関係を示す文章はあるがキーワードに合致しない場合

function closenessDistanceFromText(text){
  if(!text) return null;
  for(const tier of CLOSENESS_KEYWORDS){
    for(const w of tier.words){
      if(text.indexOf(w) >= 0) return tier.dist;
    }
  }
  return CLOSENESS_DEFAULT_TEXTED;
}

// 2026-08-08: [[wikilink]]・脚注記号を取り除いた短いプレーンテキストにする補助関数。
// タイル内の関係紹介文（短文）を作るのに使う。
function stripWikiMarkup(text){
  return (text || "")
    .replace(/\[\^[0-9]+\]/g, "")
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (m, p1, p2) => p2 ? p2 : dispName(p1))
    .replace(/^[\s\-]+/, "")
    .trim();
}

// フィールドの値を検索対象のプレーンテキストに正規化する。「追記」欄（extra）は
// [見出し, 本文]の配列（複数の名前付きサブセクションを持つことがあるため）で、
// 他の欄（relations/traits/current/history等）は単純な文字列。どちらでも動くようにする。
function fieldToText(v){
  if(!v) return "";
  if(typeof v === "string") return v;
  if(Array.isArray(v)){
    return v.map(item => Array.isArray(item) ? item.join("\n") : (typeof item === "string" ? item : "")).join("\n");
  }
  return "";
}

// 2026-08-08: 「関係の記述なし」が多すぎるとの指摘への対応。従来は「関係」「ユーザーとの関係」
// 「共同編集者との関係」欄しか見ていなかったため、履歴や特徴の中でしか触れられていない相手が
// 軒並み「記述なし」になっていた。text（本文の生テキスト、または[見出し,本文]配列）の中から
// targetId を指す [[wikilink]]を含む「文（。！？区切り）」を全て抜き出す、より広く拾うための関数。
function mentionSentences(rawText, targetId){
  const text = fieldToText(rawText);
  if(!text) return [];
  const out = [];
  text.split(/\n/).forEach(line => {
    line.split(/(?<=[。！？])/).forEach(s => {
      const re = /\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g;
      let m;
      while((m = re.exec(s))){
        if(titleToId[m[1].trim()] === targetId){ out.push(s.trim()); return; }
      }
    });
  });
  return out;
}

// 「関係」欄以外にも、特徴・現在の状態・追記・履歴まで幅広く見て、相手について
// 書かれている文章を探す（この順に、関係を直接述べている可能性が高い欄を優先）。
const RELATION_SCAN_FIELDS = ["relations", "traits", "current", "extra", "history"];

// aId・bId間の関係にまつわる文章を、複数の欄から幅広く集める。
// 戻り値: { snippet: タイル表示用の短文, allText: 親密度推定用にまとめた全文 }
function gatherRelationInfo(aId, bId){
  const aRec = byId[aId], bRec = byId[bId];
  if(!aRec || !bRec) return { snippet: "", allText: "" };

  // 「ユーザーとの関係」「共同編集者との関係」欄は、相手を主語にした専用の文章なので最優先で見る
  let primary = "";
  if(bId === "ユーザー") primary = aRec.kokuboRel || "";
  else if(bId === "共同編集者") primary = aRec.otaRel || "";
  else if(aId === "ユーザー") primary = bRec.kokuboRel || "";
  else if(aId === "共同編集者") primary = bRec.otaRel || "";

  let allText = primary;
  let snippet = "";
  if(primary){
    const t = stripWikiMarkup(primary).split(/\n/)[0];
    snippet = t.split(/[。！？]/)[0];
  }

  for(const field of RELATION_SCAN_FIELDS){
    const sentA = mentionSentences(aRec[field], bId);
    const sentB = mentionSentences(bRec[field], aId);
    if(sentA.length || sentB.length){
      allText += " " + sentA.join(" ") + " " + sentB.join(" ");
      if(!snippet){
        snippet = stripWikiMarkup(sentA[0] || sentB[0]);
      }
    }
  }

  if(snippet.length > 30) snippet = snippet.slice(0, 29) + "…";
  return { snippet, allText: allText.trim() };
}

// 2人（aId・bId）の間の関係の近さを、双方のページの記述から推定し、
// タイル配置・力学レイアウトの目標距離（px）として返す。
function edgeCloseness(aId, bId){
  const d = closenessDistanceFromText(gatherRelationInfo(aId, bId).allText);
  return d === null ? CLOSENESS_DEFAULT_LINKED : d;
}

// エッジの近さ（距離）に応じた線の色・太さ。近い＝濃く太く、遠い＝薄く細く。
function edgeStyleForDistance(d){
  if(d <= 60)  return { color: "#c0392b", width: 2.6 };
  if(d <= 80)  return { color: "#d9782f", width: 2.1 };
  if(d <= 105) return { color: "#8a9a4e", width: 1.6 };
  if(d >= 195) return { color: "#e4e6e9", width: 1 };
  if(d >= 155) return { color: "#c8ccd1", width: 1 };
  return { color: "#a9b3bd", width: 1.2 };
}

// 中心(0,0)から外側へ渦巻き状に整数格子座標を生成する（Ulamスパイラル）。
// i=0〜7が中心を囲む8マス（チェビシェフ距離1）、i=8〜23が次の16マス（距離2）…と、
// 生成順が中心からの近さの順になっているので、近い関係順に並べたリストへそのまま
// 割り当てれば「近い人ほど中心付近」のタイル配置が作れる。
function spiralOffsets(n){
  const out = [];
  let x = 0, y = 0, dx = 1, dy = 0;
  let segLen = 1, segPassed = 0, turns = 0;
  for(let i = 0; i < n; i++){
    x += dx; y += dy;
    out.push([x, y]);
    segPassed++;
    if(segPassed === segLen){
      segPassed = 0;
      const ndx = -dy, ndy = dx;
      dx = ndx; dy = ndy;
      turns++;
      if(turns % 2 === 0) segLen++;
    }
  }
  return out;
}

// 2026-08-08: 関係図を、力学レイアウトの線グラフから「正方形タイルを敷き詰めた」
// スタイルに変更（ユーザー要望：線グラフはわかりにくいとのフィードバック）。
// 中心に本人のタイルを置き、渦巻き状に外側へ広げながら、仲がいいと推定される人ほど
// 内側（中心に近い位置）に配置する。各タイルには名前と関係の短い紹介文を表示する。
function showRelationGraph(id){
  location.hash = "g:" + id;
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const center = byId[id];
  if(!center) return;

  const neighborIdSet = new Set();
  (center.links || []).forEach(name => {
    const nid = titleToId[name];
    if(nid && nid !== id) neighborIdSet.add(nid);
  });
  (center.backlinks || []).forEach(nid => { if(nid !== id) neighborIdSet.add(nid); });

  const totalNeighbors = neighborIdSet.size;
  const truncated = totalNeighbors > 80;
  let neighbors = Array.from(neighborIdSet).map(nid => byId[nid]).filter(Boolean);
  if(truncated) neighbors = neighbors.slice(0, 80);

  // 近い関係順（距離が小さい順）に並べ替えてから渦巻きに割り当てる
  const withDist = neighbors.map(n => {
    const info = gatherRelationInfo(id, n.id);
    const d = closenessDistanceFromText(info.allText);
    return { n, dist: d === null ? CLOSENESS_DEFAULT_LINKED : d, snippet: info.snippet };
  });
  withDist.sort((a, b) => a.dist - b.dist);

  document.getElementById("main").scrollTop = 0;

  if(withDist.length === 0){
    document.getElementById("article").innerHTML = `
      <div class="badge">関係図</div>
      <h1>${escapeHtml(dispName(center.title))} の関係図</h1>
      <div class="summary">直接つながっているページが見つかりませんでした。</div>
    `;
    return;
  }

  const TS = 96, GAP = 8;
  const offsets = spiralOffsets(withDist.length);
  let minX = 0, maxX = 0, minY = 0, maxY = 0;
  offsets.forEach(([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  });
  const cellW = TS + GAP;
  const gridW = (maxX - minX + 1) * cellW + GAP;
  const gridH = (maxY - minY + 1) * cellW + GAP;
  const leftFor = gx => GAP + (gx - minX) * cellW;
  const topFor = gy => GAP + (gy - minY) * cellW;

  function tierClass(d){
    if(d <= 60) return "tier-vclose";
    if(d <= 80) return "tier-close";
    if(d <= 105) return "tier-mid";
    if(d >= 195) return "tier-vfar";
    if(d >= 155) return "tier-far";
    return "";
  }

  const centerLeft = leftFor(0), centerTop = topFor(0);
  let tilesHtml = `<div class="closeTile center" data-id="${escapeHtml(center.id)}"
      style="left:${centerLeft}px;top:${centerTop}px;width:${TS}px;height:${TS}px;">
      <div class="ct-name">${escapeHtml(dispName(center.title))}</div>
      <div class="ct-snippet">本人</div>
    </div>`;

  withDist.forEach((item, i) => {
    const [gx, gy] = offsets[i];
    const cls = tierClass(item.dist);
    tilesHtml += `<div class="closeTile ${cls}" data-id="${escapeHtml(item.n.id)}"
        style="left:${leftFor(gx)}px;top:${topFor(gy)}px;width:${TS}px;height:${TS}px;">
        <div class="ct-name">${escapeHtml(dispName(item.n.title))}</div>
        <div class="ct-snippet">${escapeHtml(item.snippet || "（関係の記述なし）")}</div>
      </div>`;
  });

  document.getElementById("article").innerHTML = `
    <div class="badge">関係図</div>
    <h1>${escapeHtml(dispName(center.title))} の関係図</h1>
    <div class="summary">直接つながっている${withDist.length}人${truncated ? `（全${totalNeighbors}件中、表示上限のため80件のみ）` : ""}を表示しています。中心（${escapeHtml(dispName(center.title))}）に近いタイルほど、本文の記述から仲がいいと推定される相手です（赤系＝近い、灰色系＝遠い。厳密な数値ではなく簡易推定です）。タイルをクリックすると該当ページに移動します。</div>
    <div class="closeTileWrap" id="closeTileWrap">
      <div class="closeTileInner" style="width:${gridW}px;height:${gridH}px;">${tilesHtml}</div>
    </div>
  `;

  const wrap = document.getElementById("closeTileWrap");
  wrap.addEventListener("click", function(e){
    const tile = e.target.closest(".closeTile");
    if(tile) showArticle(tile.getAttribute("data-id"));
  });
  // 中心タイルが見える位置まで初期スクロール
  wrap.scrollLeft = Math.max(0, centerLeft - wrap.clientWidth / 2 + TS / 2);
  wrap.scrollTop = Math.max(0, centerTop - wrap.clientHeight / 2 + TS / 2);
}

// 2026-08-03: グループ（サークル・クラス・LINEグループ等、extraCategoriesに「グループ」を
// 持つページ）専用の関係図。showRelationGraph()は「このページ⇔直接の関連先」という
// 中心1点のスポーク型だが、こちらは「グループに属する人達"同士"が互いにどう繋がっているか」
// を見せるためのもの（ユーザー要望）。グループの「関連」欄に列挙されているメンバー（人物ページ
// のみ）をノードとし、メンバーAのページがメンバーBを[[wikilink]]している場合にA-B間へ辺を張る。
function isGroupRecord(r){
  return (r.extraCategories || []).includes("グループ");
}

function groupMemberGraphData(r){
  const seen = new Set();
  const memberIds = [];
  (r.links || []).forEach(name => {
    const mid = titleToId[name];
    if(!mid || seen.has(mid)) return;
    const rec = byId[mid];
    if(!rec || (rec.entityType || "person") !== "person") return;
    seen.add(mid);
    memberIds.push(mid);
  });
  const nodes = memberIds.map(mid => ({ id: mid, title: byId[mid].title }));
  const idxOf = {};
  nodes.forEach((n, i) => { idxOf[n.id] = i; });
  const edgeSeen = new Set();
  const edges = [];
  nodes.forEach((n, i) => {
    const rec = byId[n.id];
    (rec.links || []).forEach(name => {
      const nid = titleToId[name];
      if(nid === undefined || idxOf[nid] === undefined || nid === n.id) return;
      const j = idxOf[nid];
      const key = Math.min(i, j) + "-" + Math.max(i, j);
      if(edgeSeen.has(key)) return;
      edgeSeen.add(key);
      edges.push([i, j, edgeCloseness(n.id, nid)]);
    });
  });
  return { nodes, edges };
}

// 中心固定なしの汎用フォースレイアウト（全ノードが動く点がshowRelationGraphの
// レイアウトと異なる。あちらはノード0＝中心ページを固定する前提のため流用しない）。
function layoutGraphNodes(nodes, edges, W, H){
  const cx = W / 2, cy = H / 2;
  const R = Math.min(W, H) * 0.32;
  const pos = nodes.map((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    return { x: cx + Math.cos(angle) * R, y: cy + Math.sin(angle) * R };
  });
  const vel = nodes.map(() => ({ x: 0, y: 0 }));
  for(let it = 0; it < 220; it++){
    for(let i = 0; i < nodes.length; i++){
      for(let j = i + 1; j < nodes.length; j++){
        const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
        const distSq = dx * dx + dy * dy || 0.01;
        const dist = Math.sqrt(distSq);
        const force = 1800 / distSq;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        vel[i].x += fx; vel[i].y += fy;
        vel[j].x -= fx; vel[j].y -= fy;
      }
    }
    edges.forEach(([a, b, d]) => {
      const target = d !== undefined ? d : 110;
      const dx = pos[b].x - pos[a].x, dy = pos[b].y - pos[a].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = (dist - target) * 0.02;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      vel[a].x += fx; vel[a].y += fy;
      vel[b].x -= fx; vel[b].y -= fy;
    });
    for(let i = 0; i < nodes.length; i++){
      vel[i].x += (cx - pos[i].x) * 0.002;
      vel[i].y += (cy - pos[i].y) * 0.002;
    }
    for(let i = 0; i < nodes.length; i++){
      pos[i].x += vel[i].x * 0.06;
      pos[i].y += vel[i].y * 0.06;
      vel[i].x *= 0.85; vel[i].y *= 0.85;
      pos[i].x = Math.max(20, Math.min(W - 20, pos[i].x));
      pos[i].y = Math.max(20, Math.min(H - 20, pos[i].y));
    }
  }
  return pos;
}

function drawGroupGraph(r){
  const canvas = document.getElementById("groupGraphCanvas");
  if(!canvas) return;
  const { nodes, edges } = groupMemberGraphData(r);
  const note = document.getElementById("groupGraphNote");
  if(nodes.length < 2){
    canvas.style.display = "none";
    if(note) note.textContent = "個別ページを持つメンバーが2人未満のため、関係図を表示できません。";
    return;
  }
  if(note) note.textContent = `メンバー${nodes.length}人のうち、互いに[[wikilink]]で言及し合っている${edges.length}組を線で結んでいます。線が太く赤いほど近い関係と推定され、近くに表示されます（簡易推定）。ノードをクリックすると該当ページに移動します。`;
  const W = canvas.width, H = canvas.height;
  const pos = layoutGraphNodes(nodes, edges, W, H);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, W, H);
  edges.forEach(([a, b, d]) => {
    const s = edgeStyleForDistance(d !== undefined ? d : 110);
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width;
    ctx.beginPath();
    ctx.moveTo(pos[a].x, pos[a].y);
    ctx.lineTo(pos[b].x, pos[b].y);
    ctx.stroke();
  });
  nodes.forEach((n, i) => {
    const p = pos[i];
    ctx.beginPath();
    ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
    ctx.fillStyle = "#7d9dc9";
    ctx.fill();
    ctx.font = "11px sans-serif";
    ctx.fillStyle = "#202122";
    ctx.textAlign = "center";
    ctx.fillText(dispName(n.title), p.x, p.y - 10);
  });
  canvas.onclick = function(e){
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    let best = -1, bestDist = 400;
    pos.forEach((p, i) => {
      const dx = p.x - mx, dy = p.y - my;
      const d = dx * dx + dy * dy;
      if(d < bestDist){ bestDist = d; best = i; }
    });
    if(best >= 0) showArticle(nodes[best].id);
  };
}

// 2026-07-27: 「主要人物」（閲覧者がページ上の★ボタンで追加した人）の一覧ビュー。
// featuredIdsはブラウザのlocalStorageに保存されるため、この一覧も閲覧者ごとに異なる。
function showFeaturedList(){
  location.hash = "featured";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const list = records.filter(r => featuredIds.has(r.id))
    .slice()
    .sort((a, b) => (a.readingSort || a.title).localeCompare(b.readingSort || b.title, 'ja'));
  const rows = list.map(r => `
    <div class="timeline-item">
      <a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>
      <span class="timeline-date" style="margin-left:8px;">${escapeHtml(r.category || "")}</span>
    </div>
  `).join("");
  document.getElementById("article").innerHTML = `
    <div class="badge">主要人物</div>
    <h1>「主要人物」に追加された人一覧</h1>
    <div class="summary">このブラウザでページ上の☆ボタンから「主要人物」に追加した人の一覧です（${list.length}件）。他の端末・ブラウザには反映されません。</div>
    ${rows || '<div style="color:#54595d;">まだ誰も「主要人物」に追加されていません。人物ページの☆ボタンから追加できます。</div>'}
  `;
  document.getElementById("main").scrollTop = 0;
}

// 2026-07-26: 更新日時が新しい順にページを一覧表示する。
// 2026-08-06（修正）: 従来はfrontmatterの「updated: YYYY-MM-DD」（日付のみ・手動記入）に
// 依存しており、分単位の順序が分からない・手動更新を忘れると反映されないという問題が
// あったため、git logの実コミット日時（分単位）＋未コミット分はファイル更新日時を
// 自動的に使う方式に変更（get_file_updated_label、ビルド時にPython側で算出）。
function showRecentUpdates(){
  location.hash = "recent";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const list = records.filter(r => r.updated).slice()
    .sort((a, b) => b.updated.localeCompare(a.updated));
  // 2026-08-19: 従来は上位100件のみ表示していたが、ユーザー要望により全件表示に変更。
  // 単純な文字列連結によるdiv一覧表示のため、現状の全731件規模でも負荷は軽微。
  const shown = list;
  const rows = shown.map(r => `
    <div class="timeline-item">
      <span class="timeline-date">${escapeHtml(r.updated)}</span>
      <a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>
    </div>
  `).join("");
  document.getElementById("article").innerHTML = `
    <div class="badge">最近更新</div>
    <h1>最近更新されたページ</h1>
    <div class="summary">各ページの最終更新日時（分単位）が新しい順に並べています（${shown.length}/${list.length}件を表示）。</div>
    ${rows || '<div style="color:#54595d;">更新日時を持つページがありません</div>'}
  `;
  document.getElementById("main").scrollTop = 0;
}

// 2026-07-26: 「ごぶさたチェック」— 履歴に記録された最後の出来事の日付が古い順に並べ、
// しばらく連絡を取っていない（かもしれない）人に気づけるようにする。
function daysSince(dateStr){
  const d = new Date(dateStr + "T00:00:00");
  const now = new Date();
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.floor((today0 - d) / (1000 * 60 * 60 * 24));
}
// 2026-07-26（拡充）: 視点（ユーザー／共同編集者）切り替え・並び順切り替え・名前検索を追加。
let dormantPerspective = "kokubo";
let dormantOrder = "oldest";
function showDormantCheck(){
  location.hash = "dormant";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  document.getElementById("article").innerHTML = `
    <div class="badge">ごぶさたチェック</div>
    <h1>最後に会った・接触した日</h1>
    <div class="summary">基本情報に「最終接触」が明記されているページのみを対象にしています。話題に上がっただけの出来事は含みません。</div>
    <div class="dormant-controls">
      <div class="dormant-toggle-group">
        <button id="dormantPerspBtnKokubo" class="dormant-toggle-btn" onclick="setDormantPerspective('kokubo')">ユーザー視点</button>
        <button id="dormantPerspBtnOta" class="dormant-toggle-btn" onclick="setDormantPerspective('ota')">共同編集者視点</button>
      </div>
      <div class="dormant-toggle-group">
        <button id="dormantOrderBtnOldest" class="dormant-toggle-btn" onclick="setDormantOrder('oldest')">古い順</button>
        <button id="dormantOrderBtnNewest" class="dormant-toggle-btn" onclick="setDormantOrder('newest')">新しい順</button>
      </div>
      <input id="dormantSearch" placeholder="名前で絞り込み..." oninput="renderDormantList()">
      <span id="dormantCount" class="dormant-count"></span>
    </div>
    <div id="dormantChart"></div>
    <div id="dormantList" style="display:none;"></div>
    <div id="dormantTiles"></div>
  `;
  updateDormantToggleUI();
  renderDormantList();
  document.getElementById("main").scrollTop = 0;
}
// 2026-08-04: ユーザー視点/共同編集者視点・古い順/新しい順の切り替えを、プルダウン（select）から
// ワンタップのボタン切り替えに変更（ユーザー要望）。状態はdormantPerspective/dormantOrderの
// モジュール変数にそのまま保持し、ボタンのactive表示をupdateDormantToggleUI()で同期する。
function setDormantPerspective(v){
  dormantPerspective = v;
  updateDormantToggleUI();
  renderDormantList();
}
function setDormantOrder(v){
  dormantOrder = v;
  updateDormantToggleUI();
  renderDormantList();
}
function updateDormantToggleUI(){
  const pk = document.getElementById("dormantPerspBtnKokubo");
  const po = document.getElementById("dormantPerspBtnOta");
  if(pk) pk.classList.toggle("active", dormantPerspective === "kokubo");
  if(po) po.classList.toggle("active", dormantPerspective === "ota");
  const oo = document.getElementById("dormantOrderBtnOldest");
  const on = document.getElementById("dormantOrderBtnNewest");
  if(oo) oo.classList.toggle("active", dormantOrder === "oldest");
  if(on) on.classList.toggle("active", dormantOrder === "newest");
}
// 2026-08-02: ユーザー視点では、古すぎる最終接触は実用的な「ごぶさた」の目安にならないため、
// ユーザー指示により表示対象から除外する（共同編集者視点には適用しない）。
// 2026-08-13: ユーザー指示によりしきい値を2026-04-01→2026-02-18に変更（より古い最終接触まで表示）。
const DORMANT_KOKUBO_MIN_DATE = "2026-02-18";
// 2026-08-04: ごぶさたチェック（通常モードの棒グラフ）に重ねる目安ライン。
// 「今日」は都度 new Date() から計算されるため、日付が進むほど自動的に該当する
// バーが増えていく（この配列自体は固定の日数を保持するだけでよい）。
const DORMANT_THRESHOLDS = [
  { days: 60, label: "2ヶ月" },
  { days: 182, label: "半年" },
  { days: 365, label: "1年" },
];
function renderDormantList(){
  const searchEl = document.getElementById("dormantSearch");
  const query = searchEl ? searchEl.value.trim() : "";
  const useOta = dormantPerspective === "ota";
  let items = records.filter(r => useOta ? r.lastContactDateOta : r.lastContactDate);
  if(!useOta){
    items = items.filter(r => r.lastContactDate >= DORMANT_KOKUBO_MIN_DATE);
  }
  if(query){
    items = items.filter(r => dispName(r.title).toLowerCase().includes(dispName(query).toLowerCase()));
  }
  items = items.map(r => {
    const date = useOta ? r.lastContactDateOta : r.lastContactDate;
    const raw = (useOta ? r.lastContactRawOta : r.lastContactRaw) || date;
    // 2026-08-05: 「前々回接触」（薄い青バー用）。最終接触と同じ視点（ユーザー/共同編集者）の
    // 前々回接触フィールドが明示的に書かれている場合のみ、そこからの経過日数を計算する。
    const prevDate = useOta ? r.prevContactDateOta : r.prevContactDate;
    const prevRaw = (useOta ? r.prevContactRawOta : r.prevContactRaw) || prevDate;
    return { r, raw, days: daysSince(date), prevRaw, prevDays: prevDate ? daysSince(prevDate) : null };
  });
  items.sort((a, b) => dormantOrder === "oldest" ? (b.days - a.days) : (a.days - b.days));
  const rows = items.map(({r, raw, days}) => `
    <div class="timeline-item">
      <span class="timeline-date">${escapeHtml(raw)}（約${days}日前）</span>
      <a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>
    </div>
  `).join("");
  document.getElementById("dormantList").innerHTML = rows || '<div style="color:#54595d;">該当ページなし</div>';
  // 2026-08-01: 横軸=人・バー長=経過日数の棒グラフ（多い順が既定表示）。
  // 2026-08-05: 「前々回接触」がある人は、薄い青バー（前々回接触からの経過日数）を
  // 下に敷き、その上に濃い青バー（最終接触からの経過日数）を重ねて表示する
  // （ユーザー要望：前回・前々回の間隔が一目でわかるようにしたい）。
  // maxDaysは薄い青バーがはみ出さないよう、prevDaysも含めて計算する。
  // 2026-08-05: 最長のバーが枠の右端ぴったりに突き当たって見切れて見える、という指摘への
  // 対応で、100%ではなく96%を上限にスケールし、右側に常に少し余白を残す。
  const maxDays = items.reduce((m, {days, prevDays}) => Math.max(m, days, prevDays || 0), 0) || 1;
  const barPct = (d) => Math.min(96, Math.max(2, Math.round((d / maxDays) * 96)));
  const chartRows = items.map(({r, raw, days, prevRaw, prevDays}) => {
    const pct = barPct(days);
    const prevBarHtml = (prevDays != null)
      ? `<div class="dormant-chart-bar-prev" style="width:${barPct(prevDays)}%;" title="前々回接触: ${escapeHtml(prevRaw)}（約${prevDays}日前）"></div>`
      : "";
    return `
    <div class="dormant-chart-row" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')" title="${escapeHtml(raw)}">
      <span class="dormant-chart-name">${escapeHtml(dispName(r.title))}</span>
      <div class="dormant-chart-bar-wrap">${prevBarHtml}<div class="dormant-chart-bar" style="width:${pct}%;"></div></div>
      <span class="dormant-chart-days">約${days}日前</span>
    </div>`;
  }).join("");
  // 2026-08-04: 「2ヶ月前」「半年前」「1年前」の目安ライン。thresholdの日数は固定だが、
  // 起点となる「今日」は毎回 daysSince() 経由で new Date() から算出しているため、
  // 日付が進めば自動的にライン位置（＝どのバーがそのラインを越えるか）も後ろへずれていく。
  const thresholdHtml = DORMANT_THRESHOLDS.filter(t => t.days <= maxDays).map(t => {
    const pct = Math.min(100, Math.round((t.days / maxDays) * 100));
    return `<div class="dormant-threshold-line" style="left:${pct}%;" title="${t.label}前（約${t.days}日）"><span class="dormant-threshold-label">${t.label}</span></div>`;
  }).join("");
  document.getElementById("dormantChart").innerHTML =
    `<div class="dormant-threshold-overlay">${thresholdHtml}</div>` +
    (chartRows || '<div style="color:#54595d;">該当ページなし</div>');
  const tiles = items.map(({r, raw, days}) => `
    <div class="dormant-tile" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">
      <span class="dormant-tile-days" title="約${days}日前">${days}</span>
      <div class="dormant-tile-name">${escapeHtml(dispName(r.title))}</div>
      <div class="dormant-tile-desc">${escapeHtml(dormantSnippet(raw))}</div>
    </div>
  `).join("");
  document.getElementById("dormantTiles").innerHTML = tiles || '<div style="color:#54595d;">該当ページなし</div>';
  const countEl = document.getElementById("dormantCount");
  if(countEl) countEl.textContent = items.length + "件";
}
// 2026-07-31: 「最終接触」の生テキスト（例:「2026-07-02（活動後に駅前の
// サイゼリヤで6人で食事）」）から、日付部分を除いた「何をしたか」の説明だけを
// タイル表示用に取り出す。括弧が入れ子になっているケースも考慮し、最初の開き括弧から
// 最後の閉じ括弧までを丸ごと拾う（欲張りマッチ）。括弧が無ければ空文字を返す。
function dormantSnippet(raw){
  if(!raw) return "";
  const m = /[（(]([\s\S]*)[）)]/.exec(raw);
  return m ? m[1].trim() : "";
}

// 2026-07-26: 誕生日一覧。基本情報の「誕生日」フィールドを持つページを、次の誕生日が
// 近い順に並べる。現状データがあるページはごく少数だが、増えるほど活きる仕組み。
function daysUntilBirthday(sortMD){
  const [mo, d] = sortMD.split("-").map(Number);
  const now = new Date();
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let next = new Date(now.getFullYear(), mo - 1, d);
  if(next < today0) next = new Date(now.getFullYear() + 1, mo - 1, d);
  return Math.round((next - today0) / (1000 * 60 * 60 * 24));
}
function showBirthdays(){
  location.hash = "birthdays";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const withBday = records.filter(r => r.birthdaySort).map(r => ({
    r, days: daysUntilBirthday(r.birthdaySort)
  })).sort((a, b) => a.days - b.days);
  const rows = withBday.map(({r, days}) => `
    <div class="timeline-item">
      <span class="timeline-date">${escapeHtml(r.birthdayRaw)}（${days === 0 ? "今日！" : `あと${days}日`}）</span>
      <a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>
    </div>
  `).join("");
  document.getElementById("article").innerHTML = `
    <div class="badge">誕生日一覧</div>
    <h1>誕生日一覧</h1>
    <div class="summary">基本情報に「誕生日」が記録されているページを、次の誕生日が近い順に並べています（${withBday.length}件）。</div>
    ${rows || '<div style="color:#54595d;">誕生日が記録されているページがまだありません</div>'}
  `;
  document.getElementById("main").scrollTop = 0;
}

// 2026-08-05: 「予定」ダッシュボード。各ページの「## 予定」「## ユーザーの予定」
// 「## 共同編集者の予定」欄（「履歴」と同じ「- YYYY-MM(-DD): 本文」形式）に書かれた今後の
// 予定を、全ページ横断で日付順にまとめて一覧表示する。旧「矛盾自動検出」を置き換える
// 形で追加（ユーザー要望）。データ構造化はPython側のparse_history_entries()を
// 「予定」系セクションにも流用したもの（各レコードのr.schedule配列。各エントリは
// "person"（both/kokubo/ota）を持ち、「## 予定」＝both・「## ユーザーの予定」＝kokubo・
// 「## 共同編集者の予定」＝otaとしてタグ付けされている）。
// 2026-08-05（拡張）: 「予定をユーザーと共同編集者で分けてほしい」というユーザー要望に対応し、
// ごぶさたチェックと同じ視点トグル（.dormant-toggle-group）を流用して、ユーザー視点/
// 共同編集者視点で表示を絞り込めるようにした。"both"タグの予定はどちらの視点にも出す。
let upcomingPerspective = "kokubo";
function showUpcomingEvents(){
  location.hash = "upcoming";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  document.getElementById("article").innerHTML = `
    <div class="badge">予定</div>
    <h1>今後の予定</h1>
    <div class="summary">各ページの「予定」「ユーザーの予定」「共同編集者の予定」欄に書いた今後の予定をまとめた一覧です。「ユーザーの予定」「共同編集者の予定」に書いた予定は該当する視点にのみ表示され、「予定」に書いた予定は両方の視点に表示されます。<br><b>注意:</b> 「ユーザーの予定」に載っている内容の大半は、ユーザー本人が考えている・計画しているだけのもので、相手との間で確定した約束になっているとは限りません（本人の意向・妄想を多く含みます）。</div>
    <div class="dormant-controls">
      <div class="dormant-toggle-group">
        <button id="upcomingPerspBtnKokubo" class="dormant-toggle-btn" onclick="setUpcomingPerspective('kokubo')">ユーザー視点</button>
        <button id="upcomingPerspBtnOta" class="dormant-toggle-btn" onclick="setUpcomingPerspective('ota')">共同編集者視点</button>
      </div>
    </div>
    <div id="upcomingListWrap"></div>
  `;
  updateUpcomingToggleUI();
  renderUpcomingList();
  document.getElementById("main").scrollTop = 0;
}
function setUpcomingPerspective(v){
  upcomingPerspective = v;
  updateUpcomingToggleUI();
  renderUpcomingList();
}
function updateUpcomingToggleUI(){
  const pk = document.getElementById("upcomingPerspBtnKokubo");
  const po = document.getElementById("upcomingPerspBtnOta");
  if(pk) pk.classList.toggle("active", upcomingPerspective === "kokubo");
  if(po) po.classList.toggle("active", upcomingPerspective === "ota");
}
function renderUpcomingList(){
  const todayStr = new Date().toISOString().slice(0, 10);
  const all = [];
  records.forEach(r => (r.schedule || []).forEach(e => {
    if(e.person === "both" || e.person === upcomingPerspective) all.push({r, e});
  }));
  all.sort((a, b) => a.e.date.localeCompare(b.e.date));
  const future = all.filter(x => x.e.date >= todayStr);
  const past = all.filter(x => x.e.date < todayStr).slice().reverse();
  const row = x => {
    const isConfirmed = x.e.status === "確";
    const statusBadge = `<span class="sched-status ${isConfirmed ? 'sched-confirmed' : 'sched-tentative'}" title="${isConfirmed ? '相手に伝えて確定した予定' : 'まだユーザーが考えているだけで、相手と確定していない予定'}">${x.e.status || "予"}</span>`;
    return `
    <div class="timeline-item">
      ${statusBadge}<span class="timeline-date">${escapeHtml(x.e.label)}</span>
      ${withFootnoteRefs(linkify(x.e.text), x.r.id)}
      <span style="margin-left:8px;">（<a class="wikilink" onclick="showArticle('${x.r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(x.r.title))}</a>）</span>
    </div>`;
  };
  document.getElementById("upcomingListWrap").innerHTML = `
    <h3>今後の予定（${future.length}件）</h3>
    ${future.length ? future.map(row).join("") : '<div style="color:#54595d;">今後の予定はまだ登録されていません</div>'}
    ${past.length ? `<h3 style="margin-top:22px;">過去の予定（${past.length}件）</h3>${past.map(row).join("")}` : ""}
  `;
}

// 2026-08-06:「定款」ページ。このwikiを編集するAI（Claude）が毎回の作業前に
// 守るべきルールをまとめたもの（exbrainリポジトリ直下の定款.mdと同内容。
// 「AIはMDファイルの編集主体であり、ページとして表示しない限りユーザーの目に触れにくい」
// という指摘を受け、予定ページの隣にトグルで確認できるようにした）。
// 内容を変更する場合はexbrain/定款.mdとこの関数の両方を更新すること。
function showCharter(){
  location.hash = "charter";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  document.getElementById("article").innerHTML = `
    <div class="badge">このWikiについて</div>
    <h1>定款</h1>
    <div class="summary">この人物Wikiを編集する担当者が、指示を受けるたびに必ず確認・順守するルール集です。原本はexbrainリポジトリ直下の 定款.md。</div>

    <div class="structure-section">
      <h3>第1条（目的）</h3>
      <p>高校時代から現在までの人物と出来事を蓄積し、関係の変化を振り返る人物データベース。</p>

      <h3>第2条（事実の扱い・絶対に守ること）</h3>
      <ul>
        <li>事実を捏造しない。わからないことは「わからない」と書くか、ユーザーに確認する</li>
        <li>推測・伝聞・未確認情報は、そのように明記する（「推測」「伝聞」「要確認」等）</li>
        <li>年月日を本人記憶や部活年次等から逆算・類推した場合は「頃」を付けて、推定であることを見た目でもわかるようにする</li>
        <li>人物の同定に確信が持てない場合、勝手に決め打ちせず、調査するかユーザーに確認してから編集する</li>
        <li>自傷・希死念慮に類する発言の扱いは特に慎重に。文脈上ジョーク・誇張表現と判断される場合でも、その旨を明記しつつ記録するか、ユーザーの意向を確認して文言をぼかす</li>
        <li>新しい事実が複数人の関係に及ぶ場合、両方のページに反映する（片方だけに書いて終わりにしない）。関連セクションの相互リンクも両側に追加する</li>
        <li>依頼された範囲を超えて勝手に手を広げない。既存記述を「ついでに」変更しない</li>
      </ul>

      <h3>第3条（編集の手順 — 毎回この順で行う）</h3>
      <ul>
        <li>対象人物のページを特定する（読みが曖昧な場合は調査、または本人に確認）</li>
        <li>編集前に必ず現状のファイルを読み、既存の見出し構成を壊さない（見出しの重複を作らない）</li>
        <li>編集後、footnote整合性・見出し重複・不要な連続空行がないかを機械的にチェックする</li>
        <li>build_people_wiki.pyを実行し、重複候補・前々回接触候補の検出件数が既知のベースラインから増えていないか確認する</li>
        <li>ビルドされたHTMLの&lt;script&gt;ブロックをnode --checkで構文チェックする</li>
        <li>新しいJS機能を追加・変更した場合はNode.jsハーネスで実際のレンダリング結果を検証する</li>
        <li>chibako-wiki-mobile/index.htmlにコピーし、verify_sync.shで全項目OKを確認する</li>
        <li>exbrain・chibako-wiki-mobileそれぞれでgit commit（日本語で具体的なメッセージ）</li>
        <li>ユーザーにPowerShellでのgit pull → git push手順を案内する（サンドボックスはネットワーク非接続のため）</li>
      </ul>

      <h3>第4条（データスキーマ・命名規則）</h3>
      <p>詳細は本ページの隣にある「🧭 このWikiの構造」ページの「運用ルール・しきい値一覧」を正とする。特に忘れやすいものだけ再掲:</p>
      <ul>
        <li>「高校時部活」は高校の同級生の人専用。他校の部活動と絶対に混ぜない（他校は「高校時代の部活」を使う）</li>
        <li>「最終接触」「前々回接触」は「YYYY-MM-DD（内容）」の明示フィールド。前々回接触は履歴からの自動推測をしない</li>
        <li>「予定」「ユーザーの予定」「共同編集者の予定」は「- [確|予] YYYY-MM(-DD): 本文」の箇条書き形式。冒頭の「[確]」は相手に伝えて確定した約束、「[予]」は本人が考えているだけの意向。タグを省略すると自動で「予」扱いになる</li>
        <li>各ページの更新日時は2026-08-06〜、git logの実コミット日時（分単位）を正とする自動方式。frontmatterのupdated欄を手動で書き換える必要はない</li>
      </ul>

      <h3>第5条（判断に迷ったら）</h3>
      <ul>
        <li>人物の同定・事実の解釈に複数の可能性があり自分で判断できない場合は、ユーザーに確認してから進める</li>
        <li>既存記述と矛盾する新情報が来た場合、古い記述を無言で上書きせず、矛盾点を確認するか両方を併記して経緯を残す</li>
      </ul>

      <h3>第6条（ユーザー・共同編集者が直接編集する場合のツール）</h3>
      <p>AIを介さず直接.mdを編集した場合も、以下のツール（exbrainフォルダ直下）を使えば手順の大半を自動化できる。</p>
      <ul>
        <li><code>new_person.bat</code> — 新規人物ページを、正しい見出し構成のテンプレートで対話形式で作成する</li>
        <li><code>build_and_push.bat</code> — ビルド→mobile側へコピー→同期チェック→両リポジトリのコミット→pull/pushまで一気に行う</li>
      </ul>
      <p>それぞれの<code>.bat</code>ファイルをダブルクリックするだけで使える（Pythonが必要。未導入の場合はスクリプトが案内する）。</p>

      <p style="color:#54595d;font-size:13px;margin-top:16px;">※このページは自動的にAIへ読み込まれる仕組みではありません。会話のたびにユーザーから参照を促すか、AI自身が意識して定款.mdを読む必要があります。</p>
    </div>
  `;
  document.getElementById("main").scrollTop = 0;
}

// 2026-08-05: 「このWikiの構造」ページ。旧「raw/LINE読了状況マップ」を置き換える形で追加
// （ユーザー要望：どういう仕組みで成り立っているか・どういう条件でデータを読み込むか・
// どのようなLINE会話データを保持しているか・どういう仕組みでできているかを確認できる場所が欲しい）。
// raw/フォルダの生データ一覧（file/lineCount/dateRange）はscan_raw_line_files()の集計結果を
// そのまま流用している。
function showWikiStructure(){
  location.hash = "structure";
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  const catList = Array.from(categorySet).sort((a, b) => (catCounts[b] || 0) - (catCounts[a] || 0));
  const catBadges = catList.map(c => `<span class="structure-stat" style="padding:4px 10px;">${escapeHtml(c)}: ${catCounts[c] || 0}件</span>`).join(" ");
  const rawRows = rawFiles.length ? rawFiles.map(f => `
    <div class="rawfile-item">
      <div class="rf-name">${escapeHtml(f.file)}</div>
      <div class="rf-meta">行数: ${f.lineCount}行 / ファイル内の日付範囲: ${escapeHtml(f.dateRange || "検出できず")}</div>
    </div>`).join("") : '<div style="color:#54595d;">raw/フォルダにファイルが見つかりません</div>';
  document.getElementById("article").innerHTML = `
    <div class="badge">このWikiについて</div>
    <h1>このWikiの構造</h1>
    <div class="summary">このページ自体がどういう仕組みで成り立っているかをまとめたものです。</div>

    <div class="structure-stat-grid">
      <div class="structure-stat"><b>${records.length}</b>ページ</div>
      <div class="structure-stat"><b>${catList.length}</b>カテゴリ</div>
      <div class="structure-stat"><b>${rawFiles.length}</b>raw生データファイル</div>
    </div>

    <div class="structure-section">
      <h3>全体の仕組み</h3>
      <p>Markdownデータフォルダ「exbrain」（entities/people/ 以下、人物・グループごとに1ファイル、現在${records.length}件）を情報源として、ビルドスクリプト（scripts/build_people_wiki.py）が全ファイルを読み込み・解析し、1個の静的HTMLファイル（このページ自体）にデータをまるごと埋め込んで生成しています。サーバーやデータベースは使わず、ビルド時に生成されたJSONをブラウザ上のJavaScriptが読み込んで画面を組み立てる方式です。</p>
      <p>ビルド結果は2つのGitリポジトリに反映されます。「chibako-exbrain」がMarkdown原本を管理する非公開リポジトリ、「chibako-wiki-mobile」がビルド済みの単一HTMLファイル（index.html＝このページ）だけを公開するリポジトリです。データを直したいときはexbrain側の.mdファイルを編集してビルドし直します。</p>
    </div>

    <div class="structure-section">
      <h3>データの読み込み条件</h3>
      <p>ビルドのたびに entities/people/ 以下の全 *.md ファイルをスキャンします。各ファイルのfrontmatter（type: entity、entity: person または glossary 等）とタイトル行（# 名前）・1行サマリ（&gt; 1行サマリ:）を読み取り、さらに本文中の決まった見出しを構造化データとして取り込みます。</p>
      <ul>
        <li>## 基本情報 — 「- キー: 値」形式の一覧（大学・出身高校・ひらがな等）</li>
        <li>## 特徴 / ## 価値観 / ## 現在の状態 — 自由記述</li>
        <li>## ユーザーとの関係 / ## 共同編集者との関係 / ## 関係 — 関係性の記述（「位置づけ」「最終接触」等のキーも解釈）</li>
        <li>## 履歴 — 「- YYYY-MM-DD: 本文」形式の過去の出来事（自動で時系列順に並べ替え）</li>
        <li>## 予定 / ## ユーザーの予定 / ## 共同編集者の予定 — 履歴と同形式で書く今後の予定（「今後の予定」ページに横断集約。「予定」は両視点、「ユーザーの予定」「共同編集者の予定」は該当視点にのみ表示）。本文の先頭に「[確]」（相手に伝えて確定した予定）または「[予]」（まだ本人が考えているだけの意向）のタグを付けると、一覧でその区別がバッジ表示される（タグを省略した場合は安全側に倒して「予」扱い）</li>
        <li>## 関連 — [[wikilink]]形式の関連ページ一覧</li>
        <li>## 出典 — [^N]: 説明文 形式の脚注定義（本文中の[^N]からジャンプできる）</li>
      </ul>
      <p>上記に該当しない独自の見出し（例:「## ○○からの補強情報」）も内容は削除せず「その他のセクション」として保持・表示されます。</p>
    </div>

    <div class="structure-section">
      <h3>保持しているLINE会話データ</h3>
      <p>raw/フォルダに置かれた生データファイル（LINEエクスポート等）の一覧です。行数・ファイル内で検出できた日付範囲を機械的に集計しています。各人物ページの「出典」欄がこれらのファイルを参照します。</p>
      ${rawRows}
    </div>

    <div class="structure-section">
      <h3>カテゴリ内訳</h3>
      <p>${catBadges}</p>
    </div>

    <div class="structure-section">
      <h3>その他の主な仕組み</h3>
      <ul>
        <li>[[wikilink]]記法や本文中の名前の言及を自動でページへのリンクに変換</li>
        <li>基本情報の「大学」「高校時部活」は自動でタグ化され、同じ大学・部活の人を一覧できるページにリンクする（星雲大学のみ在籍者が多いため所属カテゴリ別にグループ表示する特例あり）</li>
        <li>「ごぶさたチェック」— 「最終接触」欄の日付をもとに、しばらく接触のない人を検出</li>
        <li>「AIからの提案」— wiki全体の内容をもとにAIがまとめた、ユーザー・共同編集者への行動案（週1回程度を目安に更新。原本はexbrainリポジトリ直下の AI_SUGGESTIONS.md）</li>
        <li>重複候補の自動検出 — 読みが完全一致する別ページをビルド時に警告（誤って同一人物を2ページ作ってしまうミスの検知用）</li>
        <li>前々回接触の書き忘れ候補の自動検出 — 「最終接触」はあるが「前々回接触」が無いページについて、履歴から候補を機械的に拾いビルドのたび警告（memory/reference/reference_prev_contact_candidates.mdに一覧出力。自動反映はせず手動確認・追記が必要）</li>
        <li>検索は「名前の完全一致 &gt; タイトルの部分一致 &gt; 本文一致」の順で優先表示</li>
      </ul>
    </div>

    <div class="structure-section">
      <h3>運用ルール・しきい値一覧</h3>
      <p>「なぜこの数値・この色になっているか」を後から見返せるように、これまでに決めた細かいルールをまとめました。値を変えたいときはこの一覧を更新の起点にしてください（実装はscripts/build_people_wiki.py、コード内の日付コメントに決めた経緯があります）。</p>

      <h4>フィールドの書式ルール</h4>
      <ul>
        <li>大学: 「大学名（詳細）」の形式に統一（例:「星雲大学（文科二類）」）</li>
        <li>高校時部活: 高校の同級生の人専用のフィールド名。他校の部活動と絶対に混ぜない（同じキー名にすると、部活ごとの一覧ページに他校の同名部活が混入してしまうため）</li>
        <li>高校時代の部活: 高校の同級生以外の人の部活動フィールド名（上記と意図的に別名にしている）</li>
        <li>出身地 / 出身高校: 別フィールドとして分離（「出身地・出身高校」のようにまとめない）</li>
        <li>ひらがな: 読みの正規フィールド名（「読み」「ふりがな」表記は自動的に「ひらがな」として扱われ、値中のカタカナも自動でひらがなに変換される）</li>
        <li>最終接触 / 前々回接触: どちらも「YYYY-MM-DD（内容）」の明示フィールドとして書く。前々回接触は最終接触と違い履歴からの自動推測はしない（誤検知リスクが高いため）。書き忘れは自動検出のみ行い、反映は手動</li>
        <li>履歴 / 予定 / ユーザーの予定 / 共同編集者の予定: いずれも「- YYYY-MM(-DD): 本文」の箇条書き形式。「予定」はユーザー・共同編集者どちらの視点にも表示、「ユーザーの予定」「共同編集者の予定」は該当視点にのみ表示。予定系のみ本文冒頭に「[確]」「[予]」タグで確定状況を明示する（省略時は「予」扱い）</li>
      </ul>

      <h4>ごぶさたチェックのルール</h4>
      <ul>
        <li>対象は「最終接触」が明記されているページのみ（履歴に会った記録があるだけでは対象にならない）</li>
        <li>ユーザー視点は2026-04-01以降の最終接触のみ対象（それ以前は古すぎて実用的な目安にならないため除外）。共同編集者視点はこの制限なし</li>
        <li>サイドバーのバッジ件数は「90日以上ごぶさた」の人数</li>
        <li>目安ライン: 2ヶ月・半年・1年の3本（「今日」基準で毎回自動計算し直すため、日付が進むと自動で位置がずれる）</li>
        <li>バーの色: 濃い青＝最終接触からの経過日数、薄い青（#d9e7f8、濃い青の下に同じ左端から重ねる）＝前々回接触からの経過日数</li>
        <li>バーの最大幅は96%まで（100%だと枠の右端に隙間なく突き当たって見切れて見えるため、常に右に少し余白を残す）</li>
        <li>視点（ユーザー／共同編集者）・並び順（古い順／新しい順）はワンタップのボタン切り替え（プルダウンは廃止）</li>
      </ul>

      <h4>最近更新のルール</h4>
      <ul>
        <li>各ページの更新日時は手動記入のfrontmatterに頼らず、git logの実コミット日時（分単位）を正とする。まだコミットされていない直近の編集は、ファイルの更新日時（mtime）と比較していずれか新しい方を採用する（2026-08-06〜）</li>
        <li>これにより、同日中に複数ページを更新した場合も分単位で正しい順序になり、更新欄の記入漏れによる「実際は更新したのに古いまま」という不具合も起きなくなる</li>
      </ul>

      <h4>検索・並び替えのルール</h4>
      <ul>
        <li>検索結果は「名前の完全一致 &gt; タイトルの部分一致 &gt; 本文一致」の3段階で優先表示し、各段階の中はあいうえお順</li>
        <li>一覧・検索結果のあいうえお順は「ひらがな」フィールドの読みを正規化したキーで判定（読みが無いページはタイトル自体で代用）</li>
      </ul>

      <h4>カテゴリ・サイドバーのルール</h4>
      <ul>
        <li>カテゴリはentities/people/内のフォルダ名がそのまま使われる（直下ファイルは「その他」）</li>
        <li>サイドバーの絞り込みボタンで最初から見えるのは10件以上あるカテゴリのみ。それ未満は「もっと見る」に格納</li>
        <li>「その他」「前提知識」「幼馴染」の3カテゴリは絞り込みボタン自体を出さない（表示件数が少ない・単体では意味を持ちにくいため）</li>
        <li>星雲大学ページのみ特例: 在籍者が129人と突出して多いため、他大学のような単純な一覧ではなく所属カテゴリ（フォルダ）別にグループ化して表示する</li>
      </ul>
    </div>
  `;
  document.getElementById("main").scrollTop = 0;
}

function showUnivArticle(name){
  location.hash = "u:" + encodeURIComponent(name);
  const people = records.filter(r => (r.univTags || []).includes(name))
    .sort((a,b) => a.title.localeCompare(b.title, 'ja'));
  const overview = univOverviews[name] || "概要情報なし。";
  const personLink = r =>
    `<a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>`;
  let bodyHtml;
  // 2026-08-05: 星雲大学だけ在籍者・進学者が突出して多く（100名超）、他大学と同じ
  // フラットな一覧では探しづらいため特例でカテゴリ（人物が属するフォルダ）別に
  // グルーピングして表示する。他の大学はこれまで通りのフラット一覧のまま。
  if(name === "星雲大学" && people.length){
    const groups = {};
    people.forEach(r => {
      const cat = r.category || "その他";
      (groups[cat] = groups[cat] || []).push(r);
    });
    const catNames = Object.keys(groups).sort((a,b) => {
      if(a === "その他" && b !== "その他") return 1;
      if(b === "その他" && a !== "その他") return -1;
      return (groups[b].length - groups[a].length) || a.localeCompare(b, 'ja');
    });
    bodyHtml = catNames.map(cat => `
      <h4 style="margin:18px 0 6px;color:#54595d;font-size:14px;">${escapeHtml(cat)}（${groups[cat].length}人）</h4>
      <div class="related-group">${groups[cat].map(personLink).join("")}</div>
    `).join("");
  } else {
    const list = people.length ? people.map(personLink).join("") : `<span style="color:#54595d;">該当者なし</span>`;
    bodyHtml = `<div id="related">${list}</div>`;
  }
  document.getElementById("article").innerHTML = `
    <div class="badge">大学</div>
    <h1>${escapeHtml(name)}</h1>
    <div class="summary">${escapeHtml(overview)}</div>
    <h3>在籍者・進学者一覧（${people.length}人）</h3>
    ${bodyHtml}
  `;
  document.getElementById("main").scrollTop = 0;
}

// 2026-08-05: 基本情報の値の末尾についている脚注記号（例:「星雲大学[^7]」）を、大学名/部活名の
// 照合前に切り離すためのヘルパー。切り離した記号は捨てず、呼び出し側で末尾に脚注ジャンプ
// リンクとして付け直す（withFootnoteRefs参照）。これが無いと、末尾の"[^7]"が邪魔をして
// 括弧マッチ・完全一致判定が失敗し、「星雲大学[^7]」のような壊れた表示になっていた。
function splitTrailingFootnotes(v){
  const m = /^([\s\S]*?)((?:\[\^\d+\])+)\s*$/.exec(v || "");
  return m ? { base: m[1], marker: m[2] } : { base: v || "", marker: "" };
}
function univLinkCell(v, univTags, recId, occTracker){
  const {base, marker} = splitTrailingFootnotes(v);
  const fnHtml = marker ? withFootnoteRefs(marker, recId, occTracker) : "";
  const tag = (univTags && univTags[0]) || "";
  if(!tag || NON_UNIV_TAGS.has(tag)) return linkify(base) + fnHtml;
  const m = base.match(/^(.*?)（([^（）]*)）$/);
  const detail = m ? `（${escapeHtml(m[2])}）` : (base === tag ? "" : escapeHtml(base.slice(tag.length)));
  return `<a class="wikilink" onclick="showUnivArticle('${tag.replace(/'/g,"\\'")}')">${escapeHtml(tag)}</a>${detail}${fnHtml}`;
}

function showClubArticle(name){
  location.hash = "c:" + encodeURIComponent(name);
  const people = records.filter(r => (r.clubTags || []).includes(name))
    .sort((a,b) => a.title.localeCompare(b.title, 'ja'));
  const list = people.length ? people.map(r =>
    `<a class="wikilink" onclick="showArticle('${r.id.replace(/'/g,"\\'")}')">${escapeHtml(dispName(r.title))}</a>`
  ).join("") : `<span style="color:#54595d;">該当者なし</span>`;
  document.getElementById("article").innerHTML = `
    <div class="badge">高校時部活</div>
    <h1>${escapeHtml(name)}</h1>
    <div class="summary">高校の同級生における高校時代の部活動「${escapeHtml(name)}」の所属者一覧。</div>
    <h3>所属者一覧（${people.length}人）</h3>
    <div id="related">${list}</div>
  `;
  document.getElementById("main").scrollTop = 0;
}

function clubLinkCell(v, clubTags, recId, occTracker){
  const {base, marker} = splitTrailingFootnotes(v);
  const fnHtml = marker ? withFootnoteRefs(marker, recId, occTracker) : "";
  if(!clubTags || !clubTags.length) return linkify(base) + fnHtml;
  const m = base.match(/^(.*?)（([^（）]*)）$/);
  const detail = m ? `（${escapeHtml(m[2])}）` : "";
  const links = clubTags.map(t =>
    `<a class="wikilink" onclick="showClubArticle('${t.replace(/'/g,"\\'")}')">${escapeHtml(t)}</a>`
  ).join("・");
  return links + detail + fnHtml;
}

function showArticle(id){
  const r = byId[id];
  if(!r) return;
  currentArticleId = id;
  location.hash = id;
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  // 2026-08-12: このページ内で[^N]が出現するたびにoccTrackerへ{n: 出現回数}を記録し、
  // 各出現箇所に一意なidを振る（withFootnoteRefs参照）。出典欄の「↩」からここへ戻れるようにする。
  const footnoteOcc = {};
  const fn = (t) => withFootnoteRefs(linkify(t), id, footnoteOcc);
  let infobox = "";
  if(r.basic.length){
    const catColor = categoryColor(r.category);
    const initial = dispName(r.title).charAt(0) || "?";
    infobox = `<div class="infobox">
      <div class="ib-title">${escapeHtml(dispName(r.title))}</div>
      <div class="ib-avatar-row">
        <div class="ib-avatar" style="background:${catColor.bg};color:${catColor.text};">${escapeHtml(initial)}</div>
        <div>
          <div class="ib-avatar-name">${escapeHtml(dispName(r.title))}</div>
          <span class="ib-cat-badge" style="background:${catColor.bg};color:${catColor.text};">${escapeHtml(r.category || "")}</span>
        </div>
      </div>
      <table>` +
      r.basic.map(([k,v]) => {
        let cell;
        if(k === "大学" && v) cell = univLinkCell(v, r.univTags, r.id, footnoteOcc);
        else if(k === "高校時部活" && v) cell = clubLinkCell(v, r.clubTags, r.id, footnoteOcc);
        else cell = withFootnoteRefs(linkify(v), r.id, footnoteOcc);
        return `<tr><td class="k">${escapeHtml(k)}</td><td>${cell}</td></tr>`;
      }).join("") +
      `</table></div>`;
  }
  const related = r.links.length ? r.links.map(name => {
    const id2 = titleToId[name];
    return id2 ? `<a class="wikilink" onclick="showArticle('${id2.replace(/'/g,"\\'")}')">${escapeHtml(dispName(name))}</a>` : "";
  }).filter(Boolean).join("") : "";

  const isFeatured = featuredIds.has(id);
  // 本文セクションはlinkify()（wikilink変換）の後にwithFootnoteRefs()（[^N]ジャンプリンク化）をかける。
  // footnoteOcc（上でinfobox構築時にも共有済み）にこの時点で各[^N]の出現箇所idが記録されるので、
  // 出典欄のHTML（footnotesHtml）はbodyHtmlを組み立てた後に生成する。
  const bodyHtml = `
    ${r.traits ? `<h3>特徴</h3><div class="section-text">${fn(r.traits)}</div>` : ""}
    ${r.values ? `<h3>価値観</h3><div class="section-text">${fn(r.values)}</div>` : ""}
    ${r.kokuboRel ? `<h3>ユーザーとの関係</h3><div class="section-text">${fn(r.kokuboRel)}</div>` : ""}
    ${r.otaRel ? `<h3>共同編集者との関係</h3><div class="section-text">${fn(r.otaRel)}</div>` : ""}
    ${r.relations ? `<h3>関係</h3><div class="section-text">${fn(r.relations)}</div>` : ""}
    ${r.current ? `<h3>現在の状態</h3><div class="section-text">${fn(r.current)}</div>` : ""}
    ${(r.extra||[]).map(([h,t]) => `<h3>${escapeHtml(h)}</h3><div class="section-text">${fn(t)}</div>`).join("")}
    ${r.history ? `<h3>履歴</h3>${renderHistoryBlock(r.history, fn)}` : ""}
  `;
  // 2026-08-12: 出典欄の各項目に、本文中でその[^N]が実際に出現した箇所へ戻る「↩」リンクを付ける。
  // 同じ脚注が複数箇所で引用されている場合は↩1・↩2…と出現順に番号を振る。
  const footnotesHtml = "";
  document.getElementById("article").innerHTML = `
    <div class="badge">${escapeHtml(r.category)}</div>
    <button class="star-btn ${isFeatured ? 'active' : ''}" onclick="toggleFeatured('${id.replace(/'/g,"\\'")}')">${isFeatured ? '★ 主要人物' : '☆ 主要人物に追加'}</button>
    <button class="fast-forward-btn" style="margin-left:6px;" onclick="openFastForwardConfig('person','${id.replace(/'/g,"\\'")}')">≫ ${r.id === '神谷ハル' ? '自分の今までを早送り' : (isGroupRecord(r) ? 'このコミュニティを早送り' : 'この人との関係を早送り')}</button>
    ${infobox}
    <h1>${escapeHtml(dispName(r.title))}</h1>
    <div class="summary">${linkify(r.summary)}</div>
    ${bodyHtml}
    ${related ? `<h3>関連</h3><div id="related">${related}</div>` : ""}
    ${footnotesHtml}
  `;
  document.getElementById("main").scrollTop = 0;
  renderList();
}

function showHome(){
  stopFastForward();
  const selfRecord = byId["神谷ハル"] || records.find(r => (r.entityType || "person") === "person");
  const selfId = selfRecord ? selfRecord.id.replace(/'/g,"\\'") : "";
  document.getElementById("article").innerHTML = `
    <section class="home-hero">
      <div class="home-kicker">PEOPLE WIKI</div>
      <h1 class="home-title">人との記憶を、<br>時間ごと残していく。</h1>
      <p class="home-lead">人物Wikiは、出会った人、交わした会話、一緒に過ごした出来事をつなぎ、関係の変化まで振り返れる個人のためのWikiです。</p>
      <div class="home-actions">
        ${selfRecord ? `<button class="home-primary" onclick="showArticle('${selfId}')">自分のページを見る →</button>` : ""}
        <button class="home-secondary" onclick="openFastForwardConfig('category','高校コミュニティ')">高校3年間を早送り ≫</button>
      </div>
      <div class="home-features">
        <div class="home-feature"><div class="home-feature-icon">👤</div><h3>人を覚えておく</h3><p>人物ごとに特徴、出来事、現在の関係を一つのページへ蓄積します。</p></div>
        <div class="home-feature"><div class="home-feature-icon">⌕</div><h3>人を探す</h3><p>名前、所属、出来事から人物を検索し、関連する人のページへたどれます。</p></div>
        <div class="home-feature"><div class="home-feature-icon">≫</div><h3>時間を早送りする</h3><p>期間とグループを選ぶと、蓄積した記録をAIが3枚の物語にまとめます。</p></div>
      </div>
      <div class="home-demo-route"><strong>おすすめの体験順</strong><br>「自分のページを見る」→ 気になる人物との関係を開く →「早送り」で時間の変化を振り返る</div>
    </section>`;
  document.getElementById("main").scrollTop = 0;
  renderList();
}

function goHome(){
  if(location.hash === "#home") showHome();
  else location.hash = "home";
}

function init(){
  renderFilters();
  renderList();
  // 2026-07-26: サイドバー圧縮のため、フルテキストではなく件数バッジ＋title(ホバー説明)に変更
  const aiSuggestBtn = document.getElementById("aiSuggestBtn");
  if(aiSuggestBtn){
    const m = (AI_SUGGESTIONS_MD || "").match(/^最終更新:\s*(.+)$/m);
    aiSuggestBtn.title = m ? `AIからの提案（最終更新: ${m[1].trim()}）` : "AIからの提案";
  }
  const featuredBtn = document.getElementById("featuredBtn");
  if(featuredBtn){
    featuredBtn.title = `「主要人物」に追加された人一覧（${featuredIds.size}件）`;
  }
  const recentBtn = document.getElementById("recentBtn");
  if(recentBtn){
    const recentTotal = records.filter(r => r.updated).length;
    recentBtn.title = `最近更新（${recentTotal}件）`;
  }
  const dormantBtn = document.getElementById("dormantBtn");
  if(dormantBtn){
    const dormantTotal = records.filter(r => r.lastContactDate && r.lastContactDate >= DORMANT_KOKUBO_MIN_DATE && daysSince(r.lastContactDate) >= 90).length;
    dormantBtn.title = `ごぶさたチェック（90日以上:${dormantTotal}件）`;
  }
  const birthdayBtn = document.getElementById("birthdayBtn");
  if(birthdayBtn){
    const birthdayTotal = records.filter(r => r.birthdaySort).length;
    birthdayBtn.title = `誕生日一覧（${birthdayTotal}件）`;
  }
  const upcomingBtn = document.getElementById("upcomingBtn");
  if(upcomingBtn){
    const todayStr = new Date().toISOString().slice(0, 10);
    const upcomingTotal = records.reduce((sum, r) => sum + (r.schedule || []).filter(e => e.date >= todayStr).length, 0);
    upcomingBtn.title = `今後の予定（${upcomingTotal}件）`;
  }
  const charterBtn = document.getElementById("charterBtn");
  if(charterBtn){
    charterBtn.title = "定款";
  }
  const structureBtn = document.getElementById("structureBtn");
  if(structureBtn){
    structureBtn.title = `このWikiの構造（${records.length}ページ / raw生データ${rawFiles.length}件）`;
  }
  if(window.innerWidth <= 760){
    document.getElementById("sidebar").classList.add("collapsed");
  }
  // 審査時に古いURLのハッシュが残っていても、起動直後は必ず入口画面を表示する。
  history.replaceState(null, "", location.pathname + location.search + "#home");
  showHome();
}

// 2026-07-27: ブラウザの「戻る/進む」でハッシュが変わっても表示が更新されなかったため、
// ルーティング処理を route() として切り出し、hashchange イベントでも呼び出すようにした。
function route(){
  const rawHash = location.hash.replace('#','');
  if(!rawHash || rawHash === 'home'){
    showHome();
    return;
  }
  if(rawHash.startsWith('ff:p:')){
    showFastForward('person', decodeURIComponent(rawHash.slice(5)));
    return;
  }
  if(rawHash.startsWith('ff:c:')){
    showFastForward('category', decodeURIComponent(rawHash.slice(5)));
    return;
  }
  if(rawHash.startsWith('u:')){
    showUnivArticle(decodeURIComponent(rawHash.slice(2)));
    return;
  }
  if(rawHash.startsWith('c:')){
    showClubArticle(decodeURIComponent(rawHash.slice(2)));
    return;
  }
  if(rawHash.startsWith('g:')){
    showHome();
    return;
  }
  if(rawHash === 'ai-suggest'){
    showHome();
    return;
  }
  if(rawHash === 'featured'){
    showFeaturedList();
    return;
  }
  if(rawHash === 'recent'){
    showRecentUpdates();
    return;
  }
  if(rawHash === 'charter'){
    showHome();
    return;
  }
  if(rawHash === 'dormant'){
    showDormantCheck();
    return;
  }
  if(rawHash === 'birthdays'){
    showBirthdays();
    return;
  }
  if(rawHash === 'upcoming'){
    showUpcomingEvents();
    return;
  }
  if(rawHash === 'structure'){
    showHome();
    return;
  }
  if(rawHash === 'search'){
    showSearchResults();
    return;
  }
  const hash = decodeURIComponent(rawHash);
  if(hash && byId[hash]) showArticle(hash);
  else showHome();
}
window.addEventListener('hashchange', route);
init();
</script>
</body>
</html>
"""

html = html.replace("__DATA_JSON__", data_json)
html = html.replace("__AI_SUGGESTIONS_JSON__", json.dumps(load_ai_suggestions(), ensure_ascii=False))
html = html.replace("__LAST_UPDATE_BADGE__", LAST_UPDATE_BADGE_HTML)

# ビルド時の壊れチェック: 検証に失敗した場合は一切ファイルを書き換えず異常終了する
# （vault-sync.ps1側は既にexit code!=0を「wiki html rebuild FAILED」としてログする仕組みがある）。
build_errors = validate_wiki_html(html)
if build_errors:
    print("[BUILD FAILED] 生成されたwiki HTMLが検証に失敗したため出力しません（既存ファイルは変更なし）:", file=sys.stderr)
    for e in build_errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

# 2026-07-22: exbrainリポジトリの1つ上のフォルダ(OUTER_DIR)にある人物wiki/(WIKI_DIR)へ出力。
# クローン先のフォルダ名や実行時のカレントディレクトリに依存しない。
# 出力先は人物wiki/直下の現行ファイル（people_wiki_ver*.html）を自動検出して上書きする。
# 古いバージョン/配下は対象外（globはサブフォルダを再帰しないため自動的に除外される）。
# ファイルが見つからない場合のみ新規に people_wiki_ver1.0.html を作成する。
def version_key(path):
    m = re.search(r'ver(\d+)\.(\d+)', os.path.basename(path))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

candidates = glob.glob(os.path.join(WIKI_DIR, "people_wiki_ver*.html"))
if candidates:
    outpath = max(candidates, key=version_key)
else:
    os.makedirs(WIKI_DIR, exist_ok=True)
    outpath = os.path.join(WIKI_DIR, "people_wiki_ver1.0.html")

with open(outpath, "w", encoding="utf-8") as f:
    f.write(html)
print("written", outpath, len(html), "chars,", len(records), "records")

# 2026-07-23: けいさん側のモバイル公開パイプライン（push_mobile_wiki.ps1）が
# $OUTER\chibako_wiki.html を固定パスとして参照しているため、互換性維持のため
# 同じ内容をこちらにも書き出す（people_wiki_ver*.html と二重出力）。
# 双方のスクリプトがどちらのファイル名を前提にしていても壊れないようにするための措置。
compat_path = os.path.join(OUTER_DIR, "chibako_wiki.html")
with open(compat_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written (compat)", compat_path, len(html), "chars")

# GitHub Pagesはリポジトリ直下のindex.htmlを入口として配信する。
pages_path = os.path.join(OUTER_DIR, "index.html")
with open(pages_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written (GitHub Pages)", pages_path, len(html), "chars")
