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
タイトル欄(#title)・本文エディタ(Trixエディタ)・予約投稿の切り替え
(#editContents_reservation / #reservation_post_time)・送信ボタン
(.subHeader__buttons button.btnFill--medium)はそのHTML構造に基づいて実装している。
ただし**ヘッダー画像アップロード後に出る可能性があるクロップ(トリミング)確認モーダルの
確定ボタンの文言だけは未確認で、`HEADER_IMAGE_CROP_CONFIRM_TEXT` は推測**。
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
    - 本文途中に画像を差し込む: 画像だけの行(前後を空行で区切った1行)に
      ![説明](画像ファイルのパス) と書く(説明部分は空でも可: ![](img.jpg))

    例:
        今日は完封勝利でした。**エースの好投**が光った試合でした。

        ![完投したエースの写真](images/ace.jpg)

        次戦も*期待*しています。

    太字/斜体はエディタの Ctrl+B / Ctrl+I ショートカットを使って切り替えながら
    入力する仕組みのため、投稿先のエディタがこれらのショートカットに対応して
    いない場合は反映されません。画像挿入もエディタのツールバーの「画像」ボタンを
    推測でクリックする仕組みのため、ボタンが見つからない場合はエラーになります
    (--inspect で保存したHTMLを見せてもらえれば調整します)。

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
from pathlib import Path

DEFAULT_LOGIN_URL = "https://yakyu.bunshun.jp/login"
DEFAULT_NEW_POST_URL = "https://yakyu.bunshun.jp/blogs/new"
DEFAULT_INSPECT_DIR = Path("archive/_new_post_inspect")

USER_AGENT = (
    "Mozilla/5.0 (compatible; PersonalArchiveBot/1.0; "
    "+for-personal-use-only)"
)

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
# 選択ダイアログが開く、という2段階の構造。クロップ(トリミング)モーダルが後に出る
# 可能性があるが、その確定ボタンの文言は未確認のため推測(要調整)。
HEADER_IMAGE_WRAPPER_SELECTOR = ".editMainImageWrapper"
HEADER_IMAGE_UPLOAD_MENU_TEXT = "画像をアップロード"
HEADER_IMAGE_CROP_CONFIRM_TEXT = ["完了", "適用する", "この内容で保存", "トリミングして保存", "保存する", "OK", "適用"]

# 「予約投稿」トグルは type=checkbox の #editContents_reservation。
# チェックを入れると type=datetime-local の #reservation_post_time が現れる。
SCHEDULE_TOGGLE_LABEL_SELECTOR = 'label[for="editContents_reservation"]'
SCHEDULE_DATETIME_SELECTOR = "#reservation_post_time"

# 送信(公開/予約)ボタンは .subHeader__buttons 内の button.btnFill--medium
# (下書き保存ボタンは btnOutline--medium で別物なので触らない)。
# 予約投稿・即時公開のどちらでも同じボタンで、表示文言だけが動的に変わる構造のため、
# クラスセレクタを優先し、文言候補はフォールバックとして残す。
SUBMIT_BUTTON_SELECTOR = ".subHeader__buttons button.btnFill--medium"
SUBMIT_BUTTON_TEXT_FALLBACK = ["予約投稿する", "予約する", "投稿する", "公開する", "公開", "投稿"]


def get_credentials(args) -> tuple[str, str]:
    username = args.username or os.environ.get("BUNSHUN_USERNAME")
    password = args.password or os.environ.get("BUNSHUN_PASSWORD")
    if not username:
        username = input("ユーザーID/メールアドレス: ").strip()
    if not password:
        password = getpass.getpass("パスワード: ")
    return username, password


INLINE_IMAGE_LINE_RE = re.compile(r'^!\[[^\]]*\]\(([^)]+)\)$')
INLINE_STYLE_RE = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_', re.DOTALL)


def parse_inline_runs(text: str) -> list[tuple[str, bool, bool]]:
    """段落中の **太字** / *斜体* / _斜体_ 記法を (テキスト, bold, italic) の並びに分解する。"""
    runs: list[tuple[str, bool, bool]] = []
    last_end = 0
    for m in INLINE_STYLE_RE.finditer(text):
        if m.start() > last_end:
            runs.append((text[last_end:m.start()], False, False))
        if m.group(1) is not None:
            runs.append((m.group(1), True, False))
        else:
            runs.append((m.group(2) or m.group(3), False, True))
        last_end = m.end()
    if last_end < len(text):
        runs.append((text[last_end:], False, False))
    return [r for r in runs if r[0]]


def read_body_blocks(body_file: Path) -> list[dict]:
    """本文ファイルを、段落(文字装飾つき)と画像挿入指示のブロック列に変換する。
    - 空行区切りの段落: **太字** / *斜体* / _斜体_ をサポート
    - 画像だけの行(1行が丸ごと ![alt](path) の形): その位置に画像を挿入する指示として扱う
    """
    text = body_file.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[dict] = []
    for para in paragraphs:
        m = INLINE_IMAGE_LINE_RE.match(para)
        if m:
            blocks.append({"type": "image", "path": m.group(1)})
        else:
            blocks.append({"type": "text", "runs": parse_inline_runs(para)})
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
            loc.click()
            return True
        except Exception:
            continue
    # ボタン/ラベルとしても試す
    for text in texts:
        try:
            loc = page.get_by_role("button", name=re.compile(re.escape(text))).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click()
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
    loc.click()
    loc.fill(title)
    print(f"  タイトル入力欄: {selector}")


def type_run(page, text: str, bold: bool, italic: bool) -> None:
    """1つのテキスト区間を、必要ならCtrl+B / Ctrl+Iでトグルしながら入力する。
    エディタがこれらのショートカットに対応していない場合、装飾は反映されない。"""
    if bold:
        page.keyboard.press("Control+b")
    if italic:
        page.keyboard.press("Control+i")
    page.keyboard.type(text, delay=5)
    if italic:
        page.keyboard.press("Control+i")
    if bold:
        page.keyboard.press("Control+b")


TRIX_INSERT_FILE_JS = """
([b64, filename, mime]) => {
    const el = document.querySelector('trix-editor');
    if (!el || !el.editor) return false;
    const byteChars = atob(b64);
    const bytes = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
    const file = new File([bytes], filename, { type: mime });
    el.editor.insertFile(file);
    return true;
}
"""


def insert_inline_image(page, image_path: str) -> None:
    """本文エディタ(Trix)のカーソル位置に画像を挿入する。
    Trixエディタが公式に提供している editor.insertFile(file) API を、実際に
    ドラッグ&ドロップ/ペーストで画像を貼り付けたのと同じ経路で呼び出す
    (ツールバーのボタンを推測でクリックするより確実)。"""
    import base64
    import mimetypes

    resolved = Path(image_path).resolve()
    if not resolved.is_file():
        raise RuntimeError(f"画像ファイルが見つかりません: {resolved}")

    data = resolved.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"

    ok = page.evaluate(TRIX_INSERT_FILE_JS, [b64, resolved.name, mime])
    if not ok:
        raise RuntimeError(
            f"本文エディタ(trix-editor)が見つからず、画像を挿入できませんでした({resolved})。"
            "本文入力欄の構造がTrixエディタでなくなっている可能性があります。"
        )
    # アップロード(非同期処理)が始まるのを少し待つ
    page.wait_for_timeout(1500)
    print(f"  本文中に画像を挿入しました(Trixエディタ insertFile): {resolved}")


def fill_body(page, blocks: list[dict]) -> None:
    loc, selector = find_first_locator(page, NEW_POST_BODY_SELECTORS)
    if not loc:
        raise RuntimeError(
            "本文入力欄が見つかりませんでした。NEW_POST_BODY_SELECTORS を"
            "実際のフォーム構造(--inspect の form.html)に合わせて調整してください。"
        )
    loc.click()
    for i, block in enumerate(blocks):
        if i > 0:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
        if block["type"] == "image":
            insert_inline_image(page, block["path"])
        else:
            for run_text, bold, italic in block["runs"]:
                type_run(page, run_text, bold, italic)
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
    wrapper.click()

    upload_item = page.get_by_text(HEADER_IMAGE_UPLOAD_MENU_TEXT, exact=False).first
    upload_item.wait_for(state="visible", timeout=3000)
    with page.expect_file_chooser(timeout=5000) as fc_info:
        upload_item.click()
    fc_info.value.set_files(resolved)
    page.wait_for_timeout(1000)
    print(f"  ヘッダー画像をアップロードしました: {resolved}")

    for text in HEADER_IMAGE_CROP_CONFIRM_TEXT:
        try:
            btn = page.get_by_role("button", name=re.compile(re.escape(text))).first
            btn.wait_for(state="visible", timeout=2000)
            btn.click()
            print(f"  トリミング確認ダイアログを確定しました({text})")
            page.wait_for_timeout(500)
            return
        except Exception:
            continue
    print("  [警告] トリミング確認ダイアログの確定ボタンが見つかりませんでした"
          "(そもそも出ていない可能性もあります)。ヘッダー画像が正しく設定されたか、"
          "投稿完了後に手動でご確認ください。HEADER_IMAGE_CROP_CONFIRM_TEXT を実際の"
          "文言に合わせて調整できます。", file=sys.stderr)


def set_schedule(page, publish_at: datetime) -> None:
    """予約投稿を有効化して日時を設定する。
    #editContents_reservation (checkbox) をオンにすると #reservation_post_time
    (type=datetime-local) が出現する構造(実際のHTML構造から確認済み)。"""
    toggle_label = page.locator(SCHEDULE_TOGGLE_LABEL_SELECTOR).first
    toggle_label.wait_for(state="visible", timeout=5000)
    toggle_label.click()

    dt_loc = page.locator(SCHEDULE_DATETIME_SELECTOR).first
    dt_loc.wait_for(state="visible", timeout=3000)
    dt_loc.fill(publish_at.strftime("%Y-%m-%dT%H:%M"))
    print(f"  予約日時({SCHEDULE_DATETIME_SELECTOR}): {publish_at}")


def submit_post(page) -> None:
    """公開/予約ボタンを押す(予約投稿・即時公開のどちらでも同じボタン)。"""
    try:
        btn = page.locator(SUBMIT_BUTTON_SELECTOR).first
        btn.wait_for(state="visible", timeout=3000)
        btn.click()
    except Exception:
        if not click_by_text_candidates(page, SUBMIT_BUTTON_TEXT_FALLBACK):
            raise RuntimeError(
                "送信ボタンが見つかりませんでした。SUBMIT_BUTTON_SELECTOR / "
                "SUBMIT_BUTTON_TEXT_FALLBACK を実際の構造に合わせて調整してください。"
            )
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


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

    args = parser.parse_args()

    if not args.inspect:
        if not args.title:
            parser.error("--title を指定してください")
        if not args.body_file:
            parser.error("--body-file を指定してください")
        if not args.publish_at and not args.publish_now:
            parser.error("--publish-at か --publish-now のどちらかを指定してください")

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
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

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
            if args.publish_at:
                set_schedule(page, args.publish_at)
                submit_post(page)
                print(f"[完了] 予約投稿を送信しました(予約日時: {args.publish_at})。"
                      "サイト側の予約投稿一覧で内容をご確認ください。")
            else:
                submit_post(page)
                print("[完了] 投稿を送信しました。サイト側で公開状態をご確認ください。")
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
            browser.close()
            sys.exit(1)

        browser.close()


if __name__ == "__main__":
    main()
