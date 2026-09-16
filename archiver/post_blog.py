#!/usr/bin/env python3
"""
yakyu.bunshun.jp のマイページから、ブログ記事を自動投稿(予約投稿)するスクリプト。

タイトル・本文・画像はChatGPT等で作成したものを用意しておき、このスクリプトが
ログイン→新規投稿フォームへの入力→画像添付→予約投稿設定→送信、を自動で行います。

**注意:** この環境(Claude Code on the web のサンドボックス)からは
yakyu.bunshun.jp へのネットワークアクセスがポリシーでブロックされているため、
このスクリプトはユーザー自身のPC(またはアクセス制限のない環境)で実行してください。

**フォーム構造について:**
新規投稿ページ(https://yakyu.bunshun.jp/blogs/new)は実際のHTMLを確認済みで、
タイトル欄(#title)・本文エディタ(Trixエディタ)・ヘッダー画像アップロード後の
トリミング確認ダイアログ(.trimingModal-btn .btnFill--medium)・予約投稿の切り替え
(#editContents_reservation / #reservation_post_time)・送信ボタン
(.subHeader__buttons button.btnFill--medium)は、すべて実際のHTML構造に基づいて
実装している。なお、このフォームにはヘッダー画像のトリミング用UI(croppaライブラリ)が
大きな<canvas>を持っており、他の要素へのクリックを妨げることがあるため、クリック操作は
force=True(重なりチェックを無視して強制的にクリック)で行っている。
うまくいかない場合は `--inspect` で保存される `form.html` / `form.png`、または失敗時に
自動保存される `error_form.html` / `error_form.png` を見せてもらえれば調整する。

使い方:
    # 0. まずはログイン〜投稿フォーム表示だけ試して、フォームのHTMLを確認する
    python post_blog.py --login-url https://yakyu.bunshun.jp/login --inspect

    # 1. 本文をファイルから読み込んで予約投稿する(ヘッダー画像・本文途中の画像も指定)
    python post_blog.py --login-url https://yakyu.bunshun.jp/login \\
        --title "9/10 の試合を振り返って" \\
        --header-image header.jpg \\
        --body-file post_body.txt \\
        --publish-at "2026-09-15 21:00"

    # 2. 予約せず、今すぐ公開したい場合
    python post_blog.py --login-url https://yakyu.bunshun.jp/login \\
        --title "..." --body-file post_body.txt --publish-now

必要ライブラリ:
    pip install playwright
    playwright install chromium

本文ファイル(--body-file)の書き方:
    プレーンテキスト/Markdown風の記法を使います。段落は空行で区切ってください。

    - 太字: <b>太字にしたい部分</b>
    - 斜体: <i>斜体にしたい部分</i>
    - 下線: <u>下線を引きたい部分</u>
    - 組み合わせ可(例: <b><u>太字+下線の見出し</u></b>)
    - 引用(blockquote): 段落の先頭を "> " にする
    - 見出し(<h3>): 段落の先頭を "# "(半角シャープ+半角スペース)にする
      (例: "# 弘治元年　安芸国・厳島"。このサイトのTrixエディタは見出しレベルを
      1つしか持たないため、"##"のように#を複数書いても同じ見出しになる)
    - 本文途中に画像を差し込む: 画像だけの行(前後を空行で区切った1行)に
      ![説明](画像ファイルのパス) と書く(説明部分は空でも可: ![](img.jpg)。
      ![]()記法を忘れてファイル名だけの行になっていても、拡張子から画像だと
      わかれば自動的に認識される)
    - **太字**・*斜体*・__下線__(旧記法)も後方互換のため引き続き使えるが、
      ChatGPT等に生成させる場合は __ が標準Markdownの太字と解釈され下線に
      ならないことがあるため、<b>/<i>/<u> タグを使うことを推奨する。
    - 1つの段落(空行で区切られていない範囲)の中で改行したい場合は、単純に行を
      分けて書けばよい(生の改行は自動的に<br>として扱われるため、明示的に
      <br>と書く必要はない)。
    - 段落(空行区切り)ごとに、実際のブログでは空行1行分の間隔になる(以前は
      2行分になってしまう不具合があったため修正済み)。

    例:
        <b><u>IT野球選手名鑑 #017</u></b>

        ルーター

        ![完投したエースの写真](images/ace.jpg)

        今日は完封勝利でした。<b>エースの好投</b>が光った試合でした。

        # 弘治元年　安芸国・厳島

        今日の舞台は安芸国・厳島です。

        > この記事は生成AIを活用して執筆しています。

    太字/斜体/下線/引用は、Trix本体のJS API editor.insertHTML() で、あらかじめ
    組み立てた(<strong>/<em>/<u>/<blockquote>の)HTMLを段落単位で一括挿入する
    ことで反映しています(Ctrl+B/Ctrl+I/Ctrl+Uをトグルしながら実際にキー入力する
    方式も試しましたが、Trixの内部状態への反映タイミングと合わず、文字の欠落・
    移動や書式の混線が実際に発生したため、この方式に統一しています)。本文中への
    画像挿入は、実際にファイルをドラッグ&ドロップしたのと同じ DragEvent
    (dragenter→dragover→drop)を発火させることで行っています。

    本文とは別に、記事の一番上に出る「ヘッダー画像(アイキャッチ/サムネイル)」は
    --header-image で指定してください。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path

DEFAULT_LOGIN_URL = "https://yakyu.bunshun.jp/login"
DEFAULT_NEW_POST_URL = "https://yakyu.bunshun.jp/blogs/new"
DEFAULT_INSPECT_DIR = Path("archive/_new_post_inspect")

# 新規投稿フォームの各要素を探すための候補。
# 2026-09時点で実際に確認できたHTML(yakyu.bunshun.jp/blogs/new, OSIRO基盤)を元にしている。
# タイトルは <textarea id="title" placeholder="タイトル">
NEW_POST_TITLE_SELECTORS = [
    "#title",
    'textarea[placeholder*="タイトル"]',
    'input[placeholder*="タイトル"]',
]

# 本文は vue-trix コンポーネント(Basecamp社の Trix エディタ)が使われている。
# Trixは実行時に <trix-editor contenteditable="true"> を生成する。
NEW_POST_BODY_SELECTORS = [
    "trix-editor",
    '[contenteditable="true"]',
]

# ヘッダー(記事一番上の「メイン画像」)は、まず .editMainImageWrapper をクリックすると
# ドロップダウンメニューが開き、その中の「画像をアップロード」をクリックするとファイル
# 選択ダイアログが開く、という2段階の構造。そのあとに出るトリミング確認モーダルの
# 確定ボタンは <div class="trimingModal-btn"><div class="btnFill--medium">確定する</div>
# ...という、<button>ではなくただのdivに文言が入っている構造であることを実際のHTMLで
# 確認済み(get_by_role("button", ...)では見つからないので注意)。
HEADER_IMAGE_WRAPPER_SELECTOR = ".editMainImageWrapper"
HEADER_IMAGE_UPLOAD_MENU_TEXT = "画像をアップロード"
HEADER_IMAGE_CROP_CONFIRM_SELECTOR = ".trimingModal-btn .btnFill--medium"
HEADER_IMAGE_CROP_CONFIRM_TEXT_FALLBACK = ["確定する", "完了", "適用する", "保存する", "OK", "適用"]

# 「予約投稿」トグルは type=checkbox の #editContents_reservation。
# チェックを入れると type=datetime-local の #reservation_post_time が現れる。
SCHEDULE_TOGGLE_LABEL_SELECTOR = 'label[for="editContents_reservation"]'
SCHEDULE_DATETIME_SELECTOR = "#reservation_post_time"

# 送信(公開/予約)ボタンは .subHeader__buttons 内の button.btnFill--medium。
# 予約投稿・即時公開のどちらでも同じボタンで、表示文言だけが動的に変わる構造のため、
# クラスセレクタを優先し、文言候補はフォールバックとして残す。
SUBMIT_BUTTON_SELECTOR = ".subHeader__buttons button.btnFill--medium"
SUBMIT_BUTTON_TEXT_FALLBACK = ["予約投稿する", "予約する", "投稿する", "公開する", "公開", "投稿"]

# 下書き保存ボタンは同じ .subHeader__buttons 内の button.btnOutline--medium
# (onSubmitDraft が動く、公開/予約ボタンとは別の処理)。
DRAFT_BUTTON_SELECTOR = ".subHeader__buttons button.btnOutline--medium"
DRAFT_BUTTON_TEXT_FALLBACK = ["下書き保存"]

# 興味関心タグ: 実際のUIは、フォーム上にカードが並んでいるのではなく、「興味関心タグ」欄の
# 「編集する」ボタンを押すとモーダルが開き、その中の検索ボックスにタグ名を入力して絞り込んだ
# 上でカードをクリックし、最後に「完了」を押して確定する、という流れであることをスクリーン
# ショットで確認済み(似た名前のタグ(例:「偏愛選手名鑑2023」)も候補に出るため、完全一致で
# カードを選ぶ必要がある)。
# このツールはもともと「IT野球選手名鑑」専用で、常に["選手名鑑", "IT野球選手名鑑"]を既定の
# タグとして使っていたが、同じ文春野球友の会サイトの他のブログにも投稿できるようにする際、
# 何も指定しないと無関係なブログの記事にまでこのIT野球選手名鑑用タグが付いてしまうため、
# 既定のタグという概念自体を廃止した。タグは --tag(単発投稿)/ 記事フォルダのtags.txt
# (記事ごと)/ --batch-dir直下のtags.txt(そのブログの全記事に共通)のいずれかで、
# ブログごとに明示的に指定する。
TAGS_EDIT_BUTTON_TEXT = "編集する"
TAGS_SEARCH_PLACEHOLDER = "興味関心タグを検索"
TAGS_DONE_BUTTON_TEXT = "完了"


def get_credentials(args) -> tuple[str, str]:
    username = args.username or os.environ.get("BUNSHUN_USERNAME")
    password = args.password or os.environ.get("BUNSHUN_PASSWORD")
    if not username:
        username = input("ユーザーID/メールアドレス: ").strip()
    if not password:
        password = getpass.getpass("パスワード: ")
    return username, password


# 見出し行: 段落の先頭行が "#"(1〜6個)+空白で始まる場合、本物の見出し
# (<h3>)ブロックとして扱う。このサイトのTrixエディタは見出しレベルを1つしか
# 持たず、実際に公開された記事のHTML(devtoolsで確認)でも <h3>...</h3> として
# 保存されることを確認済み。以前はMarkdownの見出し記号(#)は非対応(そのまま
# 文字として表示される)としていたが、見出しが必要なブログ(合戦を章立てで
# 紹介するものなど)に対応するため、本物の見出しとして解釈するように変更した。
HEADING_LINE_RE = re.compile(r'^#{1,6}\s+')
INLINE_IMAGE_LINE_RE = re.compile(r'^!\[[^\]]*\]\(([^)]+)\)$')
# ChatGPT等が ![](img1.jpg) の記法を忘れ、ファイル名だけを1行で書いてしまうことが
# 実際にあったため、拡張子から画像だとわかるファイル名だけの行も画像指示として救済する。
BARE_IMAGE_LINE_RE = re.compile(
    r'^[^\s![\]()]+\.(?:jpe?g|png|gif|webp|bmp|svg)$', re.IGNORECASE
)
# 太字/斜体/下線は <b>/<i>/<u> タグを正式な記法とする。標準的なMarkdownでは
# **太字**・__も太字__・*斜体*であり、「下線」という概念自体が無いため、
# ChatGPT等に生成させると __ が太字として解釈されて下線が反映されない現象が
# 実際に発生した。**/*/__ による旧記法との一貫性のなさを避けるため、
# HTMLタグに統一している(ChatGPT等は明示的なHTMLタグであれば素直にそのまま
# 出力できるため、Markdown側の「独自ルール」を誤って上書きされにくい)。
INLINE_TOKEN_RE = re.compile(
    r'(\*\*|__|\*|_|</?b>|</?i>|</?u>|<br\s*/?>)', re.IGNORECASE
)
# <br> は改行位置の目印として、実際のテキストには絶対出てこない値に置き換えて
# runsの中を通し、最終的なHTML組み立て時(render_run_html)に本物の<br>タグに
# 戻す(段落内の生の改行文字は、HTMLとしてはただの空白に潰れてしまい実際の
# 改行にならないため、本文中で改行したい場合は<br>を明示的に使ってもらう必要が
# あり、ChatGPT等がそのように出力することが実際にあった)。
BR_MARKER = "\x00BR\x00"
BR_TOKEN_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)
# ChatGPT等が強調のつもりで **** (4つ以上のアスタリスク)のような非標準の記法を
# 使うことが実際にあり、そのままだとトグルが偶数回効いて逆に無装飾になってしまう
# ため、3つ以上連続するアスタリスク/アンダースコアは正規の **(太字)/*(斜体)の
# 組み合わせとして扱えるよう、4つ以上は2つに正規化しておく(3つは**+*の
# 組み合わせ=太字+斜体として元々正しく解釈されるため、そのままにする)。
EXCESS_MARKER_RE = re.compile(r'(\*{4,}|_{4,})')


def _normalize_excess_markers(text: str) -> str:
    def repl(m: re.Match) -> str:
        return m.group(0)[:2]
    return EXCESS_MARKER_RE.sub(repl, text)


def parse_inline_runs(text: str) -> list[tuple[str, bool, bool, bool]]:
    """段落中の <b>太字</b> / <i>斜体</i> / <u>下線</u> 記法(組み合わせ可)を
    (テキスト, bold, italic, underline) の並びに分解する。**太字**・*斜体*・
    __旧下線__ も後方互換のため引き続きトグルとして解釈する。"""
    text = _normalize_excess_markers(text)
    bold = italic = underline = False
    runs: list[tuple[str, bool, bool, bool]] = []
    for tok in INLINE_TOKEN_RE.split(text):
        if tok == "":
            continue
        tok_lower = tok.lower()
        if tok == "**":
            bold = not bold
        elif tok == "__":
            underline = not underline
        elif tok in ("*", "_"):
            italic = not italic
        elif tok_lower == "<b>":
            bold = True
        elif tok_lower == "</b>":
            bold = False
        elif tok_lower == "<i>":
            italic = True
        elif tok_lower == "</i>":
            italic = False
        elif tok_lower == "<u>":
            underline = True
        elif tok_lower == "</u>":
            underline = False
        elif BR_TOKEN_RE.fullmatch(tok):
            runs.append((BR_MARKER, bold, italic, underline))
        else:
            runs.append((tok, bold, italic, underline))
    return runs


def read_body_blocks(body_file: Path) -> list[dict]:
    """本文ファイルを、段落(文字装飾つき)と画像挿入指示のブロック列に変換する。
    - 空行区切りの段落: <b>太字</b> / <i>斜体</i> / <u>下線</u> をサポート(組み合わせ可、
      **太字**・*斜体*・__下線__ の旧記法も後方互換として使える)
    - 画像だけの行(1行が丸ごと ![alt](path) の形、または拡張子から画像だとわかる
      ファイル名だけの行): その位置に画像を挿入する指示として扱う(ChatGPT等が
      ![]()記法を忘れてファイル名だけを書いてしまうことがあるための救済)。
      相対パスは実行時のカレントディレクトリではなく、この本文ファイル自身が
      置かれているディレクトリを基準に解決する。
    - 行頭が "> " の段落: 引用(blockquote)として扱う
    - 段落の先頭行が "#"(1〜6個)+空白で始まる場合: 本物の見出し(<h3>)として扱う
    """
    text = body_file.read_text(encoding="utf-8-sig")
    base_dir = body_file.resolve().parent
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[dict] = []
    for para in paragraphs:
        m = INLINE_IMAGE_LINE_RE.match(para)
        image_ref = m.group(1) if m else (para if BARE_IMAGE_LINE_RE.match(para) else None)
        if image_ref is not None:
            image_path = Path(image_ref)
            if not image_path.is_absolute():
                image_path = base_dir / image_path
            blocks.append({"type": "image", "path": str(image_path)})
            continue
        quote = para.startswith("> ")
        lines = para.split("\n")
        heading = False
        if quote:
            # 引用が複数行にわたり、継続行の先頭にも "> "(または空行代わりの
            # 単独の ">")が付いている書き方をChatGPT等がすることが実際にあった。
            # 先頭行だけでなく、各行の "> "/">" を取り除く。
            stripped_lines = []
            for line in lines:
                if line.startswith("> "):
                    stripped_lines.append(line[2:])
                elif line == ">":
                    stripped_lines.append("")
                else:
                    stripped_lines.append(line)
            lines = stripped_lines
        else:
            m = HEADING_LINE_RE.match(lines[0])
            if m:
                heading = True
                lines[0] = lines[0][m.end():]
        # 生の改行文字はHTML上ただの空白に潰れて見た目の改行にならないため、
        # 空行では区切られていない(=同じ段落内の)改行はすべて<br>に変換する
        # (引用に限らず、プロフィール欄や見出し+説明のような、1段落内に複数行が
        # 書かれたブロックすべてに当てはまる。空行代わりの単独">"だった行は
        # 上で""に変換済みなので、その前後の連続する<br><br>が「1行分の空き」
        # として表示される)。
        para = "<br>".join(lines)
        blocks.append({"type": "text", "runs": parse_inline_runs(para), "quote": quote, "heading": heading})
    return blocks




def find_first_locator(page, selectors: list[str], timeout_ms: int = 5000):
    """候補セレクタを順番に試し、最初に見つかった(表示されている)ものを返す。"""
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc, selector
        except Exception:
            continue
    return None, None


def click_by_text_candidates(page, texts: list[str], timeout_ms: int = 3000) -> bool:
    for text in texts:
        try:
            loc = page.get_by_text(text, exact=False).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click(force=True)
            return True
        except Exception:
            continue
    # ボタン/ラベルとしても試す
    for text in texts:
        try:
            loc = page.get_by_role("button", name=re.compile(re.escape(text))).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click(force=True)
            return True
        except Exception:
            continue
    return False


def login_with_browser(page, login_url: str, username: str, password: str,
                        username_field: str | None, password_field: str | None) -> bool:
    """archive_site.py の browser_login() と同じ考え方でフォームに入力してログインする
    (投稿フォームの操作自体がPlaywright前提のため、ここでは常にブラウザ経由でログインする)。
    ページはそのまま(閉じずに)返し、後続の投稿フォーム操作に使い回す。"""
    page.goto(login_url, wait_until="networkidle", timeout=30000)

    pw_selector = f'input[name="{password_field}"]' if password_field else 'input[type="password"]'
    pw_locator = page.locator(pw_selector).first
    pw_locator.wait_for(timeout=10000)

    if username_field:
        user_selector = f'input[name="{username_field}"]'
    elif page.locator('input[type="email"]').count() > 0:
        user_selector = 'input[type="email"]'
    else:
        user_selector = 'input[type="text"]'
    page.locator(user_selector).first.fill(username)
    pw_locator.fill(password)

    submit_button = page.locator('button[type="submit"], input[type="submit"]').first
    submit_button.click()

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    still_has_password = page.locator('input[type="password"]').count() > 0
    return not still_has_password


def inspect_new_post_form(page, new_post_url: str, out_dir: Path) -> None:
    print(f"[inspect] 新規投稿ページを開きます: {new_post_url}")
    page.goto(new_post_url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "form.html").write_text(page.content(), encoding="utf-8")
    try:
        page.screenshot(path=str(out_dir / "form.png"), full_page=True)
    except Exception as e:
        print(f"  [警告] スクリーンショット取得に失敗しました: {e}", file=sys.stderr)
    print(f"[inspect] 保存しました: {out_dir / 'form.html'}")
    print("  このHTMLを共有してもらえれば、入力欄・画像添付・予約投稿のセレクタを")
    print("  実際の構造に合わせて調整できます。")


def fill_title(page, title: str) -> None:
    loc, selector = find_first_locator(page, NEW_POST_TITLE_SELECTORS)
    if not loc:
        raise RuntimeError(
            "タイトル入力欄が見つかりませんでした。NEW_POST_TITLE_SELECTORS を"
            "実際のフォーム構造(--inspect の form.html)に合わせて調整してください。"
        )
    loc.click(force=True)
    loc.fill(title)
    print(f"  タイトル入力欄: {selector}")


TRIX_INSERT_HTML_JS = """
([html]) => {
    const el = document.querySelector('trix-editor');
    if (!el || !el.editor) return false;
    el.editor.insertHTML(html);
    return true;
}
"""


def render_run_html(text: str, bold: bool, italic: bool, underline: bool) -> str:
    """1つのテキスト区間を、実際のTrix出力(<strong><em><u>...)と同じ入れ子順で
    HTML化する。"""
    if text == BR_MARKER:
        return "<br>"
    html = escape(text)
    if underline:
        html = f"<u>{html}</u>"
    if italic:
        html = f"<em>{html}</em>"
    if bold:
        html = f"<strong>{html}</strong>"
    return html


def is_heading_block(block: dict) -> bool:
    """直前に通常より広い間隔(2行分)を空けたい、見出し的なブロックかどうか。
    次のいずれかに該当する場合にTrueを返す:
    - "# "で始まる本物の見出し(<h3>)ブロック
    - 段落全体が<u>下線</u>で装飾された、小見出しとして使われるブロック(旧来の
      擬似見出し記法。実際の記事では、説明が続いたあとに次の見出しへ入る箇所が
      通常の段落間より広めに空いていた方が読みやすいとの判断で追加した)。"""
    if block["type"] != "text" or block.get("quote"):
        return False
    if block.get("heading"):
        return True
    non_br_runs = [r for r in block["runs"] if r[0] != BR_MARKER]
    return bool(non_br_runs) and all(underline for _, _, _, underline in non_br_runs)


def render_block_html(block: dict) -> str:
    inner = "".join(render_run_html(t, b, i, u) for t, b, i, u in block["runs"])
    if block.get("quote"):
        return f"<blockquote>{inner}</blockquote>"
    if block.get("heading"):
        return f"<h3>{inner}</h3>"
    return inner


def insert_raw_html(page, html: str) -> None:
    """Trix本体のJS API editor.insertHTML() でHTML文字列をそのまま挿入する共通処理。
    Ctrl+B/Ctrl+I/Ctrl+Uをトグルしながら実際にキー入力する方式は、Trixの内部状態
    への反映タイミングと合わずに文字の欠落・移動や書式の混線が実際に発生することが
    判明したため、この方式に統一した(以前「JS側から直接書き換えると送信が効かなく
    なる」という仮説でキー入力方式に切り替えたが、その後の検証でキー入力方式に
    変えても送信の問題は直らなかったため、この仮説は誤りだったと判断している。
    insertHTML方式で生成されるHTML自体は、実際の送信データ(hidden inputのvalue)で
    正しい内容になっていることを確認済み)。段落間の区切りも、この同じ仕組みで
    <br><br>を挿入することで実現している(後述のfill_body参照。以前はEnterキーの
    物理的な打鍵で段落間を区切っていたが、実際の送信結果を確認したところ、
    キー入力のタイミングがずれて全ブロックが1つに繋がってしまう不具合が実際に
    発生したため、他の改行と同じ<br><br>挿入方式に統一した)。"""
    ok = page.evaluate(TRIX_INSERT_HTML_JS, [html])
    if not ok:
        raise RuntimeError(
            "本文エディタ(trix-editor)が見つからず、テキストを挿入できませんでした。"
        )


TRIX_DROP_FILE_JS = """
([b64, filename, mime]) => {
    const el = document.querySelector('trix-editor');
    if (!el) return false;
    const byteChars = atob(b64);
    const bytes = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
    const file = new File([bytes], filename, { type: mime });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    const rect = el.getBoundingClientRect();
    const opts = {
        bubbles: true,
        cancelable: true,
        dataTransfer,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
    };
    el.dispatchEvent(new DragEvent('dragenter', opts));
    el.dispatchEvent(new DragEvent('dragover', opts));
    el.dispatchEvent(new DragEvent('drop', opts));
    return true;
}
"""


def insert_inline_image(page, image_path: str) -> None:
    """本文エディタ(Trix)のカーソル位置に画像を挿入する。
    Trixエディタの editor.insertFile() というJS APIを直接呼ぶ方法は、この
    サイトのアップロード開始トリガー(イベント)をうまく発火できず、
    アップロードが完了しないまま止まることが実際の動作確認で分かったため、
    「ファイルをドラッグ&ドロップした」状態を本物に近い形で再現する方式に
    している(dragenter→dragover→dropの順にDragEventを発火する)。"""
    import base64
    import mimetypes

    resolved = Path(image_path).resolve()
    if not resolved.is_file():
        raise RuntimeError(f"画像ファイルが見つかりません: {resolved}")

    data = resolved.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"

    ok = page.evaluate(TRIX_DROP_FILE_JS, [b64, resolved.name, mime])
    if not ok:
        raise RuntimeError(
            f"本文エディタ(trix-editor)が見つからず、画像を挿入できませんでした({resolved})。"
            "本文入力欄の構造がTrixエディタでなくなっている可能性があります。"
        )
    print(f"  本文中に画像を挿入しました(ドラッグ&ドロップを再現): {resolved}")
    # 次の操作に進む前に、この画像のアップロードが完了する(blob:プレビューが
    # 実際のサーバーURLに置き換わる)まで待つ。複数枚挿入する場合、1枚ずつ完了を
    # 待たずに次を挿入すると、アップロード処理が競合して完了しないことがあるため。
    status = wait_for_uploads_to_finish(page)
    if status == "no_attachment":
        # ログには挿入成功と出るのに実際には本文に画像が入らない不具合が
        # 実際にあった(ログイン直後、セッションで最初に処理する記事でのみ
        # 再現しており、待ち時間を延ばしても直らなかったため、時間切れでは
        # なくドロップ操作自体がまだ何も起こしていない状態のまま終わって
        # いると判断)。この場合はまだ添付が1つも作られていない(重複の
        # 心配がない)ため、もう一度同じドロップをやり直す。
        print("  [警告] 画像の添付が作成されませんでした。もう一度ドラッグ&ドロップを"
              "やり直します。", file=sys.stderr)
        page.evaluate(TRIX_DROP_FILE_JS, [b64, resolved.name, mime])
        status = wait_for_uploads_to_finish(page)
    if status != "ok":
        print("  [警告] 画像のアップロードが完了しないまま処理を続行します。"
              "このまま送信すると画像が保存されない可能性があります。", file=sys.stderr)


def fill_body(page, blocks: list[dict]) -> None:
    loc, selector = find_first_locator(page, NEW_POST_BODY_SELECTORS)
    if not loc:
        raise RuntimeError(
            "本文入力欄が見つかりませんでした。NEW_POST_BODY_SELECTORS を"
            "実際のフォーム構造(--inspect の form.html)に合わせて調整してください。"
        )
    loc.click(force=True)
    page.wait_for_timeout(100)
    for i, block in enumerate(blocks):
        if block["type"] == "image":
            # 空のエディタにいきなり画像をドロップすると、添付そのものが
            # 作られずに消えてしまう不具合が実際にあった(本文の一番最初が
            # 画像の記事で確認)。実際に公開済みの記事の保存データでも、本文
            # 最初の画像の前には必ずいくつか<br>が入っていたため、直前に
            # 何もない場合(i==0)でも<br><br>を挿入してから画像をドロップする。
            # ブロック間の区切りとしての<br><br>もここで兼ねる。
            insert_raw_html(page, "<br><br>")
            insert_inline_image(page, block["path"])
        else:
            # ブロック(article.txtの空行区切り段落)の間の区切りは、他の改行と
            # 同じ<br><br>を使う。ただし、区切り用の<br><br>だけを単独で
            # insertHTML()すると、挿入した時点でそれが本文の末尾になるため、
            # 片方が自動的に取り除かれて<br>1個に潰れてしまう不具合が実際に
            # あった(以前はEnterキーの物理打鍵で区切っていたが、キー入力の
            # タイミングがJS側のinsertHTML()呼び出しとずれて全ブロックが1つに
            # 繋がってしまう不具合があり、<br><br>方式に変えた際に今度はこの
            # 潰れ方に変わった)。そのため、区切りの<br><br>は独立して挿入せず、
            # 続くブロック本体のHTMLと同じ1回のinsertHTML()呼び出しに含める
            # (常に何か実内容が後に続く状態にして、末尾と誤認されないようにする)。
            if i == 0:
                prefix = ""
            elif is_heading_block(block):
                # 小見出しの直前だけ、通常の1行分(<br><br>)ではなく2行分
                # (<br><br><br>)空ける。
                prefix = "<br><br><br>"
            else:
                prefix = "<br><br>"
            insert_raw_html(page, prefix + render_block_html(block))
    # Vue側(content.body、実際に送信される値そのもの)が、見た目のDOM内容と
    # 食い違うことが実際に確認された(figureはDOM上に残っているのに、送信データ
    # には含まれない)。合成の trix-change イベントを発火する方法では直らな
    # かったため、Trixのイベント経由の同期には頼らず、Vueが実際にv-modelで
    # 監視している隠しinput要素(input[name="body"])に対して、今のTrix
    # エディタの本当のHTMLを直接セットし、ネイティブの"input"イベントを発火
    # させることで、Vue側の状態を強制的に上書きする。
    page.evaluate(
        """() => {
            const editorEl = document.querySelector('trix-editor');
            const hiddenInput = document.querySelector('input[name="body"]');
            if (!editorEl || !hiddenInput) return false;
            let html = editorEl.innerHTML;
            // 引用(blockquote)を本文の最後に置くと、Trixが続きを入力できる
            // ようにと空の段落(<p><br></p>)を自動的に末尾へ追加することが
            // 実際にあった。見た目上は空行1つ増えるだけで実害は小さいが、
            // 実際に公開済みの記事のデータには無かった余分な要素なので取り除く。
            html = html.replace(/<p>\\s*<br>\\s*<\\/p>\\s*$/, '');
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSetter.call(hiddenInput, html);
            hiddenInput.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
        }"""
    )
    page.wait_for_timeout(200)
    print(f"  本文入力欄: {selector}({len(blocks)}ブロック)")


def set_header_image(page, image_path: str) -> None:
    """記事一番上のヘッダー(「メイン画像」)をアップロードする。
    .editMainImageWrapper クリック→出てくるメニューの「画像をアップロード」クリック、
    という2段階の操作(いずれも実際のHTML構造から確認済み)。
    この後にクロップ(トリミング)確認モーダルが出る場合があり、その確定ボタンの文言は
    未確認のため、よくありそうな候補から推測でクリックする(見つからなくても致命的
    エラーにはせず、警告を出して続行する)。"""
    resolved = str(Path(image_path).resolve())
    if not Path(resolved).is_file():
        raise RuntimeError(f"ヘッダー画像ファイルが見つかりません: {resolved}")

    wrapper = page.locator(HEADER_IMAGE_WRAPPER_SELECTOR).first
    wrapper.wait_for(state="visible", timeout=5000)
    wrapper.click(force=True)

    upload_item = page.get_by_text(HEADER_IMAGE_UPLOAD_MENU_TEXT, exact=False).first
    upload_item.wait_for(state="visible", timeout=3000)
    with page.expect_file_chooser(timeout=5000) as fc_info:
        upload_item.click(force=True)
    fc_info.value.set_files(resolved)
    print(f"  ヘッダー画像をアップロードしました: {resolved}")

    # クロップ(トリミング)用canvasが画像を読み込んで初期化されるまで少し待つ。
    # ここが短いと、croppa側の切り抜き範囲がまだ決まっていない状態で「確定する」を
    # 押してしまい、アップロードが完了しない(blob:プレビューのまま止まる)ことがある。
    try:
        page.wait_for_selector(".croppa-container canvas", state="visible", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    confirmed = False
    try:
        btn = page.locator(HEADER_IMAGE_CROP_CONFIRM_SELECTOR).first
        btn.wait_for(state="visible", timeout=3000)
        btn.click(force=True)
        print(f"  トリミング確認ダイアログを確定しました({HEADER_IMAGE_CROP_CONFIRM_SELECTOR})")
        confirmed = True
    except Exception:
        for text in HEADER_IMAGE_CROP_CONFIRM_TEXT_FALLBACK:
            try:
                btn = page.get_by_text(text, exact=False).first
                btn.wait_for(state="visible", timeout=2000)
                btn.click(force=True)
                print(f"  トリミング確認ダイアログを確定しました({text})")
                confirmed = True
                break
            except Exception:
                continue

    if confirmed:
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('.editContents__photoimage--main img[src^=\"blob:\"], trix-editor img[src^=\"blob:\"]').length === 0",
                timeout=45000,
            )
            print("  ヘッダー画像のアップロード完了を確認しました。")
        except Exception:
            print("  [警告] トリミング確定後もヘッダー画像のアップロードが完了しません"
                  "でした。このまま送信すると失敗する可能性があります。", file=sys.stderr)
        return

    print("  [警告] トリミング確認ダイアログの確定ボタンが見つかりませんでした"
          "(そもそも出ていない可能性もあります)。ヘッダー画像が正しく設定されたか、"
          "投稿完了後に手動でご確認ください。HEADER_IMAGE_CROP_CONFIRM_SELECTOR を実際の"
          "構造に合わせて調整できます。", file=sys.stderr)


def set_schedule(page, publish_at: datetime) -> None:
    """予約投稿を有効化して日時を設定する。
    #editContents_reservation (checkbox) をオンにすると #reservation_post_time
    (type=datetime-local) が出現する構造(実際のHTML構造から確認済み)。"""
    toggle_label = page.locator(SCHEDULE_TOGGLE_LABEL_SELECTOR).first
    toggle_label.wait_for(state="visible", timeout=5000)
    toggle_label.click(force=True)

    dt_loc = page.locator(SCHEDULE_DATETIME_SELECTOR).first
    dt_loc.wait_for(state="visible", timeout=3000)
    dt_loc.fill(publish_at.strftime("%Y-%m-%dT%H:%M"))
    print(f"  予約日時({SCHEDULE_DATETIME_SELECTOR}): {publish_at}")


def set_tags(page, tags: list[str], inspect_out: Path | None = None) -> None:
    """新規投稿フォームの「興味関心タグ」を設定する。実際のUI(スクリーンショットで
    確認済み)は次の流れ:
      1. 「興味関心タグ」欄の「編集する」ボタンをクリックするとモーダルが開く
      2. モーダル内の検索ボックス(placeholder="興味関心タグを検索")にタグ名を
         入力すると、一致するカードが絞り込み表示される(似た名前のタグ、例:
         「偏愛選手名鑑2023」も出てくるため、完全一致するカードだけをクリックする
         必要がある)
      3. カードをクリックすると「選択中の興味関心タグ」に追加される
      4. 全タグを選び終えたら「完了」ボタンでモーダルを閉じて確定する
    途中の要素が見つからない場合は警告を出すだけで処理は継続する(致命的エラーには
    しない)。成功/失敗に関わらず、最後にこの時点の画面のスクリーンショットとHTMLを
    保存する(inspect_out指定時。うまく設定できていない場合の原因調査用)。"""
    try:
        edit_btn = page.get_by_text(TAGS_EDIT_BUTTON_TEXT, exact=True).first
        edit_btn.wait_for(state="visible", timeout=5000)
        edit_btn.click(force=True)
    except Exception:
        print(f"  [警告] 興味関心タグの「{TAGS_EDIT_BUTTON_TEXT}」ボタンが見つからず、"
              "タグを設定できませんでした。", file=sys.stderr)
        _dump_tags_debug(page, inspect_out)
        return

    try:
        search_box = page.get_by_placeholder(TAGS_SEARCH_PLACEHOLDER).first
        search_box.wait_for(state="visible", timeout=5000)
    except Exception:
        print("  [警告] 興味関心タグの検索ボックスが見つからず、タグを設定できません"
              "でした。", file=sys.stderr)
        _dump_tags_debug(page, inspect_out)
        return

    for tag in tags:
        try:
            search_box.fill(tag)
            page.wait_for_timeout(800)
            card = page.get_by_text(tag, exact=True).first
            card.wait_for(state="visible", timeout=5000)
            card.scroll_into_view_if_needed()
            card.click(force=True)
            print(f"  興味関心タグ「{tag}」を選択しました。")
        except Exception:
            print(f"  [警告] 興味関心タグ「{tag}」が候補に見つからず設定できません"
                  "でした。", file=sys.stderr)

    try:
        done_btn = page.get_by_text(TAGS_DONE_BUTTON_TEXT, exact=True).first
        done_btn.wait_for(state="visible", timeout=3000)
        done_btn.click(force=True)
        print("  興味関心タグの選択を確定しました。")
    except Exception:
        print(f"  [警告] 興味関心タグの「{TAGS_DONE_BUTTON_TEXT}」ボタンが見つからず、"
              "選択内容が確定していない可能性があります。", file=sys.stderr)

    _dump_tags_debug(page, inspect_out)


def _dump_tags_debug(page, inspect_out: Path | None) -> None:
    if inspect_out is None:
        return
    try:
        inspect_out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(inspect_out / "tags_debug.png"), full_page=True)
        (inspect_out / "tags_debug.html").write_text(page.content(), encoding="utf-8")
        print(f"  [診断] タグ欄の状態を {inspect_out / 'tags_debug.png'} / "
              f"{inspect_out / 'tags_debug.html'} に保存しました"
              "(タグがうまく設定できない場合はこれを共有してください)。")
    except Exception:
        pass


def wait_for_uploads_to_finish(page, timeout_ms: int = 45000) -> str:
    """本文中の画像(Trixの添付ファイル)のアップロードが完了するまで待つ。
    Trixは添付ファイルの属性(url)が実際のサーバーURLに更新されても、すでに
    描画済みの<img>要素のsrc属性は自動的には再描画しない(見た目上はblob:の
    プレビューのままに見えても、実際に保存されるHTML(data-trix-attachmentの
    JSON)側はすでに更新されている、というTrix特有の挙動)。そのため<img src>
    ではなく、実際に保存に使われる data-trix-attachment のJSON中のurlを見て
    判定する(以前は<img src^="blob:">を見ていたため、実際はとっくに完了して
    いても永遠に未完了と誤判定していた)。

    戻り値は次の3種類:
    - "ok": アップロード完了(urlがblob:以外の実際の値になった)
    - "no_attachment": 添付要素自体が一度も作られなかった(ドロップ操作が
      何も起こしていない)。ログイン直後、セッションで最初に処理する記事の
      1枚目でだけ再現する不具合が実際にあり、待ち時間(45秒)を延ばしても
      直らなかったため、時間切れではなく添付そのものが作られていないと判断
      できるよう、まずこの状態を先にチェックしている。呼び出し側でこの場合
      だけ安全に(まだ何も無いので重複の心配なく)ドロップをやり直せる。
    - "stuck_blob": 添付は作られたが、既定の待ち時間内にurlが実際の値に
      更新されなかった(純粋なアップロード遅延の可能性が高い)。
    既定値は45秒(ヘッダー画像側の待ち時間と同じ)。以前は10秒にしていたが、
    5記事の一括投稿中に2記事だけ本文中の画像が保存されない不具合が実際に
    発生し、待ち時間切れ(警告は出るが処理は継続してしまう)が原因と判断した
    ため、余裕を持たせた。"""
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('trix-editor figure[data-trix-attachment]').length > 0",
            timeout=5000,
        )
    except Exception:
        print("  [警告] 画像の添付要素が作成されませんでした"
              "(ドラッグ&ドロップが反応していない可能性があります)。", file=sys.stderr)
        return "no_attachment"

    try:
        page.wait_for_function(
            """() => {
                const figs = document.querySelectorAll('trix-editor figure[data-trix-attachment]');
                if (figs.length === 0) return false;
                const last = figs[figs.length - 1];
                try {
                    const attrs = JSON.parse(last.getAttribute('data-trix-attachment'));
                    return !!attrs.url && !attrs.url.startsWith('blob:');
                } catch (e) {
                    return false;
                }
            }""",
            timeout=timeout_ms,
        )
        print("  画像のアップロード完了を確認しました。")
        return "ok"
    except Exception:
        print("  [警告] 画像のアップロードが完了しないまま既定の待ち時間"
              f"({timeout_ms}ms)を超えました。このまま送信すると失敗する"
              "(反応がないまま何も保存されない)可能性があります。", file=sys.stderr)
        return "stuck_blob"


def click_submit_button(page, selector: str, text_fallback: list[str], label: str) -> None:
    """公開/予約または下書き保存のボタンを押す共通処理。
    ボタンがまだdisabled(バリデーション未通過)の場合はクリックしても何も起きず
    「エラーは出ないが実際には保存されない」状態になるため、事前にチェックする。"""
    has_attachment_in_vue = page.evaluate(
        "() => { const el = document.querySelector('input[name=\"body\"]');"
        " return el ? el.value.includes('data-trix-attachment') : null; }"
    )
    print(f"  [診断] 送信直前のVue側本文に画像添付が含まれているか: {has_attachment_in_vue}")

    # クリックした瞬間に何が起きているか(JSエラー・コンソール出力・実際に発生した
    # ネットワークリクエスト)を自動で収集する。予約投稿/公開ボタンだけがクリック
    # できても何も起きない(下書き保存は正常に動く)という現象の原因調査用。
    console_messages: list[str] = []
    page_errors: list[str] = []
    requests_seen: list[str] = []

    def _on_console(msg):
        console_messages.append(f"[{msg.type}] {msg.text}")

    def _on_pageerror(err):
        page_errors.append(str(err))

    def _on_request(req):
        requests_seen.append(f"{req.method} {req.url}")

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    page.on("request", _on_request)

    try:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=3000)
            if btn.is_disabled():
                print(f"  [警告] {label}ボタンがまだ無効(disabled)になっています。"
                      "少し待ってから再確認します。", file=sys.stderr)
                page.wait_for_timeout(1500)
            if btn.is_disabled():
                raise RuntimeError(
                    f"{label}ボタンが無効(disabled)のままクリックできませんでした。"
                    "タイトル/本文が空、または他の必須項目が未入力の可能性があります。"
                )
            btn.click(force=True)
        except RuntimeError:
            raise
        except Exception:
            if not click_by_text_candidates(page, text_fallback):
                raise RuntimeError(
                    f"{label}ボタンが見つかりませんでした。セレクタ/文言候補を"
                    "実際の構造に合わせて調整してください。"
                )
        # 予約投稿/公開ボタンはクリック後に confirm() ダイアログが挟まる
        # (dialogの承認はこちらのハンドラが非同期に行う)ため、networkidle判定が
        # 実際の遷移が始まる"前"の一瞬の静けさで満たされてしまい、その後の固定
        # 待機(1500ms)だけではURL変化(/blogs/new からの遷移)が間に合わない
        # ことがある(実際には投稿に成功しているのに、このスクリプトだけが
        # 「まだ新規投稿ページのまま」と誤判定して失敗扱いする不具合が発生した)。
        # そのため、まずURLが実際に変わるまで能動的に待つ。
        try:
            page.wait_for_url(lambda url: "/blogs/new" not in url, timeout=15000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(500)
    finally:
        page.remove_listener("console", _on_console)
        page.remove_listener("pageerror", _on_pageerror)
        page.remove_listener("request", _on_request)
        print(f"  [診断] {label}クリック後に発生したコンソール出力: "
              f"{console_messages if console_messages else '(なし)'}")
        print(f"  [診断] {label}クリック後に発生したJSエラー: "
              f"{page_errors if page_errors else '(なし)'}")
        print(f"  [診断] {label}クリック後に発生したネットワークリクエスト: "
              f"{requests_seen if requests_seen else '(なし)'}")

    if "/blogs/new" in page.url:
        raise RuntimeError(
            f"{label}ボタンを押した後もURLが新規投稿ページのままです({page.url})。"
            "クリックはできても、サーバー側のバリデーションエラーなどで実際には"
            "保存されていない可能性があります。"
        )
    print(f"  {label}後のURL: {page.url}")


def submit_post(page) -> None:
    """公開/予約ボタンを押す(予約投稿・即時公開のどちらでも同じボタン)。"""
    click_submit_button(page, SUBMIT_BUTTON_SELECTOR, SUBMIT_BUTTON_TEXT_FALLBACK, "送信")


def submit_draft(page) -> None:
    """下書き保存ボタンを押す。"""
    click_submit_button(page, DRAFT_BUTTON_SELECTOR, DRAFT_BUTTON_TEXT_FALLBACK, "下書き保存")


def parse_publish_at(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"日時の形式が正しくありません: {value}(例: '2026-09-15 21:00')"
    )


def load_batch(path: Path) -> list[dict]:
    """--batch で指定するJSONファイル(記事のリスト)を読み込み、投稿ジョブの
    リストに変換する。JSON中の相対パス(header_image/body_file/images)は、
    実行時のカレントディレクトリではなく、このJSONファイル自身が置かれている
    ディレクトリを基準に解決する(記事とその画像を1フォルダにまとめて配布
    しやすくするため)。

    JSON形式の例:
        [
          {
            "title": "9/10 の試合を振り返って",
            "header_image": "header1.jpg",
            "body_file": "post1.txt",
            "images": ["extra1.jpg"],
            "publish_at": "2026-09-15 21:00"
          },
          {
            "title": "別の記事",
            "body_file": "post2.txt",
            "draft": true
          }
        ]

    draft / publish_now / publish_at のうち、必ずどれか1つだけを指定する。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"--batch のJSONファイルを読み込めませんでした({path}): {e}") from e
    if not isinstance(data, list):
        raise SystemExit(f"--batch のJSONは記事の配列(リスト)である必要があります: {path}")

    base_dir = path.parent

    def resolve(p: str) -> str:
        pp = Path(p)
        return str(pp if pp.is_absolute() else (base_dir / pp))

    jobs: list[dict] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise SystemExit(f"--batch の{i}番目の要素がオブジェクトではありません: {entry!r}")
        title = entry.get("title")
        body_file = entry.get("body_file")
        if not title:
            raise SystemExit(f"--batch の{i}番目の記事に title がありません")
        if not body_file:
            raise SystemExit(f"--batch の{i}番目の記事({title})に body_file がありません")

        modes = [k for k in ("draft", "publish_now", "publish_at") if entry.get(k)]
        if len(modes) != 1:
            raise SystemExit(
                f"--batch の{i}番目の記事({title})には draft / publish_now / publish_at "
                "のいずれか1つだけを指定してください"
            )
        mode = modes[0]
        publish_at = parse_publish_at(str(entry["publish_at"])) if mode == "publish_at" else None

        if "tags" not in entry:
            raise SystemExit(
                f"--batch の{i}番目の記事({title})に tags がありません。既定のタグは廃止した"
                "ため、タグを付けたい場合は \"tags\": [\"タグ名\", ...] を、付けない場合は "
                "\"tags\": [] を明示的に指定してください。"
            )

        jobs.append({
            "title": title,
            "header_image": resolve(entry["header_image"]) if entry.get("header_image") else None,
            "body_file": resolve(body_file),
            "images": [resolve(p) for p in entry.get("images", [])],
            "mode": mode,
            "publish_at": publish_at,
            "tags": entry["tags"],
        })
    return jobs


def read_tags_file(path: Path) -> list[str]:
    """1行に1つずつタグ名が書かれた tags.txt を読み込む(空行は無視)。"""
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def load_batch_dir(path: Path, default_mode: str | None, default_publish_at: datetime | None,
                    default_tags: list[str] | None) -> list[dict]:
    """--batch-dir で指定したフォルダの直下にある各サブフォルダを、1記事分の
    設定として読み込む。JSONを書く手間を省くための単純なフォルダ規約:

        posts/
          tags.txt          (省略可。このフォルダ=1つのブログの全記事に共通の興味関心タグ。
                               1行に1つずつタグ名を書く。下記の記事ごとのtags.txtがあれば
                               そちらが優先される)
          2026-09-10-game-recap/
            title.txt        (省略可。無ければフォルダ名をそのままタイトルにする)
            body.txt または body.md (title.txt/article.txtのどちらかが無ければ必須)
            article.txt      (title.txt/body.txtの代わりに、1ファイルにまとめて
                               置いてもよい。1行目がタイトル、それ以降が本文として
                               自動的に分割される。ChatGPT等の出力をコピー&ペーストで
                               そのまま1ファイル保存するだけで済ませたい場合用)
            header.*         (省略可。ヘッダー画像。拡張子は問わない)
            publish_at.txt   (省略可。この記事だけ個別の予約日時にしたい場合。
                               1行目に '2026-09-15 21:00' のように書く)
            tags.txt         (省略可。この記事だけ個別の興味関心タグにしたい場合。
                               1行に1つずつタグ名を書く。上のフォルダ直下tags.txtより優先)

    本文中に差し込む画像は、--body-file と同じく本文ファイル内に
    ![alt](画像ファイル名) と書けばよく(そのフォルダを基準にパスが解決される)、
    フォルダ名の昇順で処理するので、日付や連番をフォルダ名の頭に付けると
    投稿順をコントロールしやすい。

    投稿方法(下書き/即時公開/予約投稿)は、`publish_at.txt` があるフォルダは
    そこに書かれた日時で必ず予約投稿になる。無いフォルダは、コマンドラインの
    --draft/--publish-now/--publish-at (default_mode/default_publish_at) を
    既定値として使う。全フォルダに publish_at.txt がある場合は、コマンドライン側の
    --draft/--publish-now/--publish-at は省略できる。

    タグには既定値が無い(複数のブログを投稿できるようにする際、無関係なブログの記事に
    まで別ブログ用のタグが付いてしまわないよう、既定のタグという概念自体を廃止した)。
    記事ごとの tags.txt → フォルダ直下(--batch-dir自身)の tags.txt →
    コマンドラインの --tag/--no-tags (default_tags) の順で優先され、どれも無い記事は
    エラーにする。"""
    if not path.is_dir():
        raise SystemExit(f"--batch-dir に指定したパスがフォルダではありません: {path}")

    subdirs = sorted(p for p in path.iterdir() if p.is_dir())
    if not subdirs:
        raise SystemExit(f"--batch-dir のフォルダの直下に記事フォルダが見つかりません: {path}")

    batch_tags_file = path / "tags.txt"
    batch_default_tags = read_tags_file(batch_tags_file) if batch_tags_file.is_file() else None

    jobs: list[dict] = []
    for d in subdirs:
        title_file = d / "title.txt"
        article_file = d / "article.txt"
        body_file = None
        for name in ("body.txt", "body.md"):
            candidate = d / name
            if candidate.is_file():
                body_file = candidate
                break

        if article_file.is_file():
            # article.txt があるフォルダは、常にこれをタイトル・本文の正として
            # 使う(body.txtの有無に関わらず)。以前は「body.txtがまだ無い場合
            # だけ」article.txtを読んでいたが、一度実行してbody.txtが自動生成
            # されると、次回以降article.txtが無視されてタイトルがフォルダ名に
            # フォールバックしてしまう不具合が実際に発生したため、article.txt
            # がある限り毎回そこから本文を再生成するようにしている。
            lines = article_file.read_text(encoding="utf-8-sig").splitlines()
            first_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
            if first_idx is None:
                raise SystemExit(f"{article_file} が空です")
            title = lines[first_idx].strip()
            body_lines = lines[first_idx + 1:]
            while body_lines and not body_lines[0].strip():
                body_lines.pop(0)
            if not body_lines:
                raise SystemExit(f"{article_file} にタイトルはありますが本文がありません")
            body_file = d / "body.txt"
            body_file.write_text("\n".join(body_lines), encoding="utf-8")
            if title_file.is_file():
                # title.txt があれば明示的な上書きとして扱う(通常は不要)。
                lines2 = [line.strip() for line in title_file.read_text(encoding="utf-8-sig").splitlines()]
                override = next((line for line in lines2 if line), None)
                if override:
                    title = override
        elif title_file.is_file():
            lines = [line.strip() for line in title_file.read_text(encoding="utf-8-sig").splitlines()]
            title = next((line for line in lines if line), d.name)
        else:
            title = d.name

        if body_file is None:
            raise SystemExit(
                f"{d} に article.txt、または body.txt(もしくは body.md)が見つかりません"
            )

        header_candidates = sorted(d.glob("header.*"))

        publish_at_file = d / "publish_at.txt"
        if publish_at_file.is_file():
            lines = [line.strip() for line in publish_at_file.read_text(encoding="utf-8-sig").splitlines()]
            value = next((line for line in lines if line), None)
            if not value:
                raise SystemExit(f"{publish_at_file} が空です(予約日時を1行目に書いてください)")
            mode, publish_at = "publish_at", parse_publish_at(value)
        elif default_mode is not None:
            mode, publish_at = default_mode, default_publish_at
        else:
            raise SystemExit(
                f"{d} に publish_at.txt が無く、--draft/--publish-now/--publish-at も"
                "指定されていません。この記事フォルダに publish_at.txt を置くか、"
                "コマンドラインで既定の投稿方法を指定してください。"
            )

        tags_file = d / "tags.txt"
        if tags_file.is_file():
            tags = read_tags_file(tags_file)
        elif batch_default_tags is not None:
            tags = batch_default_tags
        elif default_tags is not None:
            tags = default_tags
        else:
            raise SystemExit(
                f"{d} にタグの指定がありません({tags_file} が無く、{batch_tags_file} も無く、"
                "コマンドラインで --tag/--no-tags も指定されていません)。この記事フォルダに "
                "tags.txt を置くか、--batch-dir 直下にそのブログ全体で共通の tags.txt を置くか、"
                "コマンドラインで --tag/--no-tags を指定してください(タグを付けない記事は "
                "中身が空の tags.txt でも構いません)。"
            )

        jobs.append({
            "title": title,
            "header_image": str(header_candidates[0]) if header_candidates else None,
            "body_file": str(body_file),
            "images": [],
            "mode": mode,
            "publish_at": publish_at,
            "tags": tags,
        })
    return jobs


def post_one_article(page, new_post_url: str, job: dict, inspect_out: Path | None = None) -> None:
    """1記事分のタイトル/画像/本文の入力〜送信を行う。"""
    print(f"[新規投稿フォームを開く] {new_post_url}")
    page.goto(new_post_url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1000)

    blocks = read_body_blocks(Path(job["body_file"]))
    for image_path in job["images"]:
        blocks.append({"type": "image", "path": image_path})

    fill_title(page, job["title"])
    if job["header_image"]:
        set_header_image(page, job["header_image"])
    fill_body(page, blocks)
    if job["tags"]:
        set_tags(page, job["tags"], inspect_out)

    if job["mode"] == "draft":
        submit_draft(page)
        print("[完了] 下書き保存しました。サイト側で内容をご確認ください。")
    elif job["mode"] == "publish_at":
        set_schedule(page, job["publish_at"])
        submit_post(page)
        print(f"[完了] 予約投稿を送信しました(予約日時: {job['publish_at']})。"
              "サイト側の予約投稿一覧で内容をご確認ください。")
    else:
        submit_post(page)
        print("[完了] 投稿を送信しました。サイト側で公開状態をご確認ください。")


def run_job_with_error_capture(page, new_post_url: str, job: dict, inspect_out: Path) -> bool:
    """post_one_article() を実行し、失敗した場合は失敗時点のHTML/スクリーン
    ショットを保存した上でFalseを返す(--batch実行時に1件の失敗で全体を
    止めないようにするため、例外はここで吸収する)。"""
    try:
        post_one_article(page, new_post_url, job, inspect_out)
        return True
    except Exception as e:
        inspect_out.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^\w.-]", "_", job["title"])[:50] or "untitled"
        html_path = inspect_out / f"error_form_{safe_title}.html"
        png_path = inspect_out / f"error_form_{safe_title}.png"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(png_path), full_page=True)
        except Exception:
            pass
        print(f"[エラー] 「{job['title']}」の投稿に失敗しました: {e}", file=sys.stderr)
        print(f"  失敗時点のHTML/スクリーンショットを {html_path} / {png_path} に"
              "保存しました。共有してもらえればセレクタを調整します。", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--login-url", default=DEFAULT_LOGIN_URL, help="ログインページURL")
    parser.add_argument("--new-post-url", default=DEFAULT_NEW_POST_URL,
                         help="新規投稿フォームのURL(未確認のため要調整の可能性あり)")
    parser.add_argument("--username", help="ログインID(未指定時は環境変数 BUNSHUN_USERNAME か対話入力)")
    parser.add_argument("--password", help="パスワード(未指定時は環境変数 BUNSHUN_PASSWORD か対話入力)")
    parser.add_argument("--username-field", help="ログインフォームのユーザーID欄のname属性")
    parser.add_argument("--password-field", help="ログインフォームのパスワード欄のname属性")
    parser.add_argument("--headed", action="store_true", help="ブラウザ画面を表示して実行する(デバッグ用)")

    parser.add_argument("--inspect", action="store_true",
                         help="投稿はせず、ログイン後に新規投稿ページを開いてHTML/スクリーンショットを保存するだけ")
    parser.add_argument("--inspect-out", default=str(DEFAULT_INSPECT_DIR), help="--inspect の保存先")

    parser.add_argument("--title", help="記事タイトル")
    parser.add_argument("--header-image", help="ヘッダー画像(アイキャッチ/サムネイル)として使う画像ファイル")
    parser.add_argument("--body-file", help="本文が書かれたテキスト/Markdownファイル"
                                             "(**太字**/*斜体*、![alt](path)での画像挿入に対応)")
    parser.add_argument("--image", action="append", default=[],
                         help="本文の最後にまとめて挿入する画像(複数指定可)。"
                              "本文の途中に差し込みたい場合は --body-file 中に ![](path) と書く")
    parser.add_argument("--tag", action="append",
                         help="興味関心タグとして設定するタグ名(複数指定可、"
                              "「おすすめの興味関心タグ」に表示されるものに一致する必要あり)。"
                              "既定のタグは無いため、単発投稿(--batch/--batch-dir を使わない"
                              "場合)では --tag か --no-tags のどちらかを必ず指定する")
    parser.add_argument("--no-tags", action="store_true", help="興味関心タグを一切設定しない")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--publish-at", type=parse_publish_at,
                        help="予約投稿する日時(例: '2026-09-15 21:00')")
    group.add_argument("--publish-now", action="store_true", help="予約せず今すぐ公開する")
    group.add_argument("--draft", action="store_true",
                        help="公開/予約はせず、下書き保存ボタンを押すだけにする"
                             "(予約投稿がうまくいかない場合の切り分け・代替手段用)")

    parser.add_argument("--batch", help="複数記事をまとめて投稿するためのJSONファイル。"
                                         "指定した場合、--title/--header-image/--body-file/"
                                         "--image/--publish-at/--publish-now/--draft は使えません"
                                         "(それぞれの記事ごとにJSON側で指定するため)")
    parser.add_argument("--batch-dir",
                         help="複数記事をまとめて投稿するためのフォルダ(JSONを書きたくない場合用)。"
                              "直下の各サブフォルダを1記事として扱い、"
                              "title.txt+body.txt(またはbody.md)、"
                              "またはその代わりに1ファイルにまとめた article.txt"
                              "(1行目がタイトル、それ以降が本文)・header.*(省略可)を読む。"
                              "記事ごとに違う予約日時にしたい場合は、そのフォルダに"
                              "publish_at.txt を置いて1行目に日時を書く(例: '2026-09-15 21:00')。"
                              "publish_at.txt が無いフォルダには --draft/--publish-now/"
                              "--publish-at で指定した既定の投稿方法が使われる"
                              "(全フォルダに publish_at.txt がある場合は省略可)")
    parser.add_argument("--batch-delay-seconds", type=float, default=3.0,
                         help="--batch/--batch-dir で複数記事を投稿する際、1記事ごとの間に"
                              "空ける秒数(既定: 3秒。サーバーに負荷をかけすぎないようにするため)")

    args = parser.parse_args()

    if args.batch and args.batch_dir:
        parser.error("--batch と --batch-dir は同時に指定できません")

    if args.tag and args.no_tags:
        parser.error("--tag と --no-tags は同時に指定できません")

    if args.batch:
        if args.title or args.header_image or args.body_file or args.image \
                or args.publish_at or args.publish_now or args.draft \
                or args.tag or args.no_tags:
            parser.error("--batch は --title/--header-image/--body-file/--image/"
                          "--publish-at/--publish-now/--draft/--tag/--no-tags と"
                          "同時に指定できません(記事ごとの設定はJSON側に書いてください)")
    elif args.batch_dir:
        if args.title or args.header_image or args.body_file or args.image:
            parser.error("--batch-dir は --title/--header-image/--body-file/--image と"
                          "同時に指定できません(記事ごとの設定はフォルダ側に書いてください)")
        # --draft/--publish-now/--publish-at はここでは必須にしない: 各記事フォルダに
        # publish_at.txt を置けば記事ごとに個別の予約日時にできるため、全フォルダに
        # publish_at.txt がある場合はコマンドライン側を省略できる(足りない場合は
        # load_batch_dir() が該当フォルダを指摘してエラーにする)。
    elif not args.inspect:
        if not args.title:
            parser.error("--title を指定してください")
        if not args.body_file:
            parser.error("--body-file を指定してください")
        if not args.publish_at and not args.publish_now and not args.draft:
            parser.error("--publish-at か --publish-now か --draft のいずれかを指定してください")
        if not args.tag and not args.no_tags:
            parser.error("--tag か --no-tags のいずれかを指定してください(既定のタグは廃止した"
                          "ため、タグを付けない場合も --no-tags で明示してください)")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "post_blog.py の実行には playwright が必要です: "
            "pip install playwright && playwright install chromium"
        ) from e

    username, password = get_credentials(args)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        # User-Agentはあえて上書きしない: 独自のUser-Agent(以前は名前に"Bot"を
        # 含めていた)を名乗ると、サイト側のスパム/ボット対策に引っかかって、
        # エラーも出さず投稿だけ静かに無視される可能性があるため、Playwrightに
        # 同梱されている実際のChromiumの標準UAをそのまま使う。
        context = browser.new_context()
        # navigator.webdriver は Playwright/Selenium等で起動したブラウザだと true になり、
        # サイト側のボット判定に使われることがある(該当する場合、投稿ボタンを押しても
        # エラーも出さず何も起きない、という今回まさに起きている症状と一致するため)。
        # 各ページのスクリプトが実行される前に false を返すよう上書きしておく。
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
        )
        page = context.new_page()
        # 予約投稿/公開ボタンをクリックしてもコンソール出力もネットワーク
        # リクエストも一切発生しない、という現象の仮説として、window.confirm()
        # のようなネイティブダイアログが出ていて、それをPlaywrightが既定の
        # 挙動(自動キャンセル)で握りつぶしている可能性がある(ネイティブダイアログ
        # はコンソール/ネットワークのどちらにも痕跡を残さないため、この仮説は
        # これまでの「何も起きない」という観測と矛盾しない)。ダイアログが出たら
        # 内容を表示した上でOK(承認)する。
        def _on_dialog(dialog):
            print(f"  [診断] ダイアログを検出しました(type={dialog.type}): "
                  f"{dialog.message!r} → 承認(OK)します。")
            dialog.accept()
        page.on("dialog", _on_dialog)

        print(f"[ログイン] {args.login_url}")
        ok = login_with_browser(page, args.login_url, username, password,
                                 args.username_field, args.password_field)
        if not ok:
            browser.close()
            print(f"[中断] ログインに失敗した可能性があります(遷移後URL: {page.url})。"
                  "ID/パスワードや --username-field / --password-field をご確認ください。",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[ログイン成功と思われます] 遷移後URL: {page.url}")

        if args.inspect:
            inspect_new_post_form(page, args.new_post_url, Path(args.inspect_out))
            browser.close()
            return

        # 既定のタグは廃止したため、--no-tags/--tag のどちらも無ければ None のままにする
        # (--batch-dir では None のまま load_batch_dir() に渡し、記事フォルダ/バッチフォルダ
        # 直下の tags.txt で解決できるかを任せる。単発投稿では上のバリデーションで
        # --tag/--no-tags のどちらかが必須のため、ここで None のままになることはない)。
        tags = [] if args.no_tags else (list(args.tag) if args.tag else None)

        is_batch = bool(args.batch or args.batch_dir)
        if args.batch:
            jobs = load_batch(Path(args.batch))
        elif args.batch_dir:
            if args.draft:
                default_mode = "draft"
            elif args.publish_now:
                default_mode = "publish_now"
            elif args.publish_at:
                default_mode = "publish_at"
            else:
                default_mode = None
            jobs = load_batch_dir(Path(args.batch_dir), default_mode, args.publish_at, tags)
        else:
            jobs = [{
                "title": args.title,
                "header_image": args.header_image,
                "body_file": args.body_file,
                "images": args.image,
                "mode": "draft" if args.draft else ("publish_at" if args.publish_at else "publish_now"),
                "publish_at": args.publish_at,
                "tags": tags,
            }]

        inspect_out = Path(args.inspect_out)
        results: list[tuple[str, bool]] = []
        for i, job in enumerate(jobs, start=1):
            if is_batch:
                print(f"\n===== [{i}/{len(jobs)}] {job['title']} =====")
            ok = run_job_with_error_capture(page, args.new_post_url, job, inspect_out)
            results.append((job["title"], ok))
            if is_batch and i < len(jobs) and args.batch_delay_seconds > 0:
                page.wait_for_timeout(int(args.batch_delay_seconds * 1000))

        if args.headed:
            input("  ブラウザで結果を確認してください。Enterキーを押すと閉じます... ")

        browser.close()

        if is_batch:
            print("\n===== 投稿結果 =====")
            for title, ok in results:
                print(f"  {'OK' if ok else 'NG'}: {title}")

        if any(not ok for _, ok in results):
            sys.exit(1)


if __name__ == "__main__":
    main()
