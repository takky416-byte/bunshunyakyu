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

    - 太字: **太字にしたい部分**
    - 斜体: *斜体にしたい部分* または _斜体にしたい部分_
    - 下線: __下線を引きたい部分__
    - 組み合わせ可(例: **__太字+下線の見出し__**)
    - 引用(blockquote): 段落の先頭を "> " にする
    - 本文途中に画像を差し込む: 画像だけの行(前後を空行で区切った1行)に
      ![説明](画像ファイルのパス) と書く(説明部分は空でも可: ![](img.jpg))

    例:
        **__IT野球選手名鑑 #017__**

        ルーター

        ![完投したエースの写真](images/ace.jpg)

        今日は完封勝利でした。**エースの好投**が光った試合でした。

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


def get_credentials(args) -> tuple[str, str]:
    username = args.username or os.environ.get("BUNSHUN_USERNAME")
    password = args.password or os.environ.get("BUNSHUN_PASSWORD")
    if not username:
        username = input("ユーザーID/メールアドレス: ").strip()
    if not password:
        password = getpass.getpass("パスワード: ")
    return username, password


INLINE_IMAGE_LINE_RE = re.compile(r'^!\[[^\]]*\]\(([^)]+)\)$')
INLINE_TOKEN_RE = re.compile(r'(\*\*|__|\*|_)')


def parse_inline_runs(text: str) -> list[tuple[str, bool, bool, bool]]:
    """段落中の **太字** / *斜体*・_斜体_ / __下線__ 記法を、トグル方式で
    (テキスト, bold, italic, underline) の並びに分解する。**__組み合わせ__** のような
    入れ子(太字+下線など)も、単純なトグルの積み重ねとして扱えるので対応できる。"""
    bold = italic = underline = False
    runs: list[tuple[str, bool, bool, bool]] = []
    for tok in INLINE_TOKEN_RE.split(text):
        if tok == "":
            continue
        if tok == "**":
            bold = not bold
        elif tok == "__":
            underline = not underline
        elif tok in ("*", "_"):
            italic = not italic
        else:
            runs.append((tok, bold, italic, underline))
    return runs


def read_body_blocks(body_file: Path) -> list[dict]:
    """本文ファイルを、段落(文字装飾つき)と画像挿入指示のブロック列に変換する。
    - 空行区切りの段落: **太字** / *斜体*・_斜体_ / __下線__ をサポート(組み合わせ可)
    - 画像だけの行(1行が丸ごと ![alt](path) の形): その位置に画像を挿入する指示として扱う
    - 行頭が "> " の段落: 引用(blockquote)として扱う
    """
    text = body_file.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[dict] = []
    for para in paragraphs:
        m = INLINE_IMAGE_LINE_RE.match(para)
        if m:
            blocks.append({"type": "image", "path": m.group(1)})
            continue
        quote = para.startswith("> ")
        if quote:
            para = para[2:]
        blocks.append({"type": "text", "runs": parse_inline_runs(para), "quote": quote})
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
    html = escape(text)
    if underline:
        html = f"<u>{html}</u>"
    if italic:
        html = f"<em>{html}</em>"
    if bold:
        html = f"<strong>{html}</strong>"
    return html


def render_block_html(block: dict) -> str:
    inner = "".join(render_run_html(t, b, i, u) for t, b, i, u in block["runs"])
    if block.get("quote"):
        return f"<blockquote>{inner}</blockquote>"
    return inner


def insert_text_block(page, block: dict) -> None:
    """1つの段落ブロックを、Trix本体のJS API editor.insertHTML() で挿入する。
    Ctrl+B/Ctrl+I/Ctrl+Uをトグルしながら実際にキー入力する方式は、Trixの内部状態
    への反映タイミングと合わずに文字の欠落・移動や書式の混線が実際に発生することが
    判明したため、この方式に統一した(以前「JS側から直接書き換えると送信が効かなく
    なる」という仮説でキー入力方式に切り替えたが、その後の検証でキー入力方式に
    変えても送信の問題は直らなかったため、この仮説は誤りだったと判断している。
    insertHTML方式で生成されるHTML自体は、実際の送信データ(hidden inputのvalue)で
    正しい内容になっていることを確認済み)。"""
    html = render_block_html(block)
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
    wait_for_uploads_to_finish(page)
    fig_count = page.evaluate(
        "() => document.querySelectorAll('trix-editor figure[data-trix-attachment]').length"
    )
    print(f"  [診断] 画像挿入直後のfigure要素数: {fig_count}")


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
        if i > 0:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
        if block["type"] == "image":
            insert_inline_image(page, block["path"])
        else:
            insert_text_block(page, block)
    fig_count = page.evaluate(
        "() => document.querySelectorAll('trix-editor figure[data-trix-attachment]').length"
    )
    print(f"  [診断] 本文入力完了時点のfigure要素数: {fig_count}")
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
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSetter.call(hiddenInput, editorEl.innerHTML);
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


def wait_for_uploads_to_finish(page, timeout_ms: int = 10000) -> None:
    """本文中の画像(Trixの添付ファイル)のアップロードが完了するまで待つ。
    Trixは添付ファイルの属性(url)が実際のサーバーURLに更新されても、すでに
    描画済みの<img>要素のsrc属性は自動的には再描画しない(見た目上はblob:の
    プレビューのままに見えても、実際に保存されるHTML(data-trix-attachmentの
    JSON)側はすでに更新されている、というTrix特有の挙動)。そのため<img src>
    ではなく、実際に保存に使われる data-trix-attachment のJSON中のurlを見て
    判定する(以前は<img src^="blob:">を見ていたため、実際はとっくに完了して
    いても永遠に未完了と誤判定していた)。
    なお、drop相当のイベントを発火した直後はTrixがまだ添付要素を作成していない
    ことがあるため、「blobの添付が0件」であることそのものは完了の証拠にならない
    (=まだ挿入すらされていない場合も0件になり、即座に完了扱いされてしまい、次の
    操作が割り込んで画像そのものが失われる不具合が実際に発生した)。そのため、
    最後に挿入された添付が実際に存在し、かつそのurlが(空/未設定ではなく)
    実際の値として設定されていて、それがblob:でもない、という条件で判定する
    (「urlがblob:で始まらない」だけを条件にすると、urlキー自体がまだ存在しない
    ＝本当は何も終わっていない状態まで「完了」と誤判定してしまい、src/hrefが
    欠けた不完全な添付情報が保存されてしまう不具合が実際に発生した)。"""
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
    except Exception:
        print("  [警告] 画像のアップロードが完了しないまま既定の待ち時間"
              f"({timeout_ms}ms)を超えました。このまま送信すると失敗する"
              "(反応がないまま何も保存されない)可能性があります。", file=sys.stderr)


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
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
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

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--publish-at", type=parse_publish_at,
                        help="予約投稿する日時(例: '2026-09-15 21:00')")
    group.add_argument("--publish-now", action="store_true", help="予約せず今すぐ公開する")
    group.add_argument("--draft", action="store_true",
                        help="公開/予約はせず、下書き保存ボタンを押すだけにする"
                             "(予約投稿がうまくいかない場合の切り分け・代替手段用)")

    args = parser.parse_args()

    if not args.inspect:
        if not args.title:
            parser.error("--title を指定してください")
        if not args.body_file:
            parser.error("--body-file を指定してください")
        if not args.publish_at and not args.publish_now and not args.draft:
            parser.error("--publish-at か --publish-now か --draft のいずれかを指定してください")

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

        print(f"[新規投稿フォームを開く] {args.new_post_url}")
        page.goto(args.new_post_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)

        try:
            blocks = read_body_blocks(Path(args.body_file))
            for image_path in args.image:
                blocks.append({"type": "image", "path": image_path})

            fill_title(page, args.title)
            if args.header_image:
                set_header_image(page, args.header_image)
            fill_body(page, blocks)
            if args.draft:
                submit_draft(page)
                print("[完了] 下書き保存しました。サイト側で内容をご確認ください。")
            elif args.publish_at:
                set_schedule(page, args.publish_at)
                submit_post(page)
                print(f"[完了] 予約投稿を送信しました(予約日時: {args.publish_at})。"
                      "サイト側の予約投稿一覧で内容をご確認ください。")
            else:
                submit_post(page)
                print("[完了] 投稿を送信しました。サイト側で公開状態をご確認ください。")
            if args.headed:
                input("  ブラウザで結果を確認してください。Enterキーを押すと閉じます... ")
        except Exception as e:
            fail_dir = Path(args.inspect_out)
            fail_dir.mkdir(parents=True, exist_ok=True)
            (fail_dir / "error_form.html").write_text(page.content(), encoding="utf-8")
            try:
                page.screenshot(path=str(fail_dir / "error_form.png"), full_page=True)
            except Exception:
                pass
            print(f"[エラー] {e}", file=sys.stderr)
            print(f"  失敗時点のHTML/スクリーンショットを {fail_dir} に保存しました。"
                  "共有してもらえればセレクタを調整します。", file=sys.stderr)
            if args.headed:
                input("  ブラウザで状況を確認してください。Enterキーを押すと閉じます... ")
            browser.close()
            sys.exit(1)

        browser.close()


if __name__ == "__main__":
    main()
