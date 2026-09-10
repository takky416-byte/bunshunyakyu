#!/usr/bin/env python3
"""
yakyu.bunshun.jp のマイページから、ブログ記事を自動投稿(予約投稿)するスクリプト。

タイトル・本文・画像はChatGPT等で作成したものを用意しておき、このスクリプトが
ログイン→新規投稿フォームへの入力→画像添付→予約投稿設定→送信、を自動で行います。

**注意:** この環境(Claude Code on the web のサンドボックス)からは
yakyu.bunshun.jp へのネットワークアクセスがポリシーでブロックされているため、
このスクリプトはユーザー自身のPC(またはアクセス制限のない環境)で実行してください。

**重要 - 実際のフォーム構造が未確認です:**
新規投稿フォームの実際のHTML構造(入力欄のname属性やクラス名)を確認できない状態で
作成しているため、`NEW_POST_*_SELECTORS` は「よくあるパターン」からの推測です。
初回は必ず `--inspect` を付けて実行し、フォームが正しく開けているか
(`archive/_new_post_inspect/` に保存される `form.html` / `form.png`)を確認してください。
自動入力がうまくいかない場合は、その `form.html` を見せてもらえれば、実際の構造に
合わせて `NEW_POST_*_SELECTORS` を調整します(archive_site.py の CONTENT_SELECTORS
などを調整してきたのと同じ流れです)。

使い方:
    # 0. まずはログイン〜投稿フォーム表示だけ試して、フォームのHTMLを確認する
    python post_blog.py --login-url https://yakyu.bunshun.jp/login --inspect

    # 1. 本文をファイルから読み込んで予約投稿する(画像は複数指定可)
    python post_blog.py --login-url https://yakyu.bunshun.jp/login \\
        --title "9/10 の試合を振り返って" \\
        --body-file post_body.txt \\
        --image image1.png --image image2.png \\
        --publish-at "2026-09-15 21:00"

    # 2. 予約せず、今すぐ公開したい場合
    python post_blog.py --login-url https://yakyu.bunshun.jp/login \\
        --title "..." --body-file post_body.txt --publish-now

必要ライブラリ:
    pip install playwright
    playwright install chromium

本文ファイルについて:
    プレーンテキスト/Markdownを想定しています。空行で段落を区切ってください。
    リッチテキストエディタに対して段落ごとに Enter で改行しながら入力するため、
    見出しや太字などのMarkdown記法はそのままの文字として入力されます
    (エディタ側で自動変換されない限り装飾は反映されません)。
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
DEFAULT_NEW_POST_URL = "https://yakyu.bunshun.jp/mypage/blogs/new"
DEFAULT_INSPECT_DIR = Path("archive/_new_post_inspect")

USER_AGENT = (
    "Mozilla/5.0 (compatible; PersonalArchiveBot/1.0; "
    "+for-personal-use-only)"
)

# 新規投稿フォームの各要素を探すための候補(推測)。
# 実際のフォームHTML(--inspect で保存される form.html)を見ながら調整してください。
NEW_POST_TITLE_SELECTORS = [
    'input[name="title"]',
    'input[placeholder*="タイトル"]',
    'textarea[name="title"]',
    'textarea[placeholder*="タイトル"]',
]

NEW_POST_BODY_SELECTORS = [
    '[contenteditable="true"]',
    ".ProseMirror",
    ".ql-editor",
    'textarea[name="body"]',
    'textarea[placeholder*="本文"]',
]

NEW_POST_IMAGE_INPUT_SELECTORS = [
    'input[type="file"]',
]

# 「予約投稿」への切り替え(トグル/チェックボックス/ラジオ)候補
NEW_POST_SCHEDULE_TOGGLE_TEXT = ["予約投稿", "予約する", "公開日時を指定"]

NEW_POST_SCHEDULE_DATE_SELECTORS = [
    'input[type="date"]',
    'input[name*="date"]',
]
NEW_POST_SCHEDULE_TIME_SELECTORS = [
    'input[type="time"]',
    'input[name*="time"]',
]
NEW_POST_SCHEDULE_DATETIME_SELECTORS = [
    'input[type="datetime-local"]',
]

# 送信ボタンの文言候補(予約投稿 / 即時公開 それぞれ)
SUBMIT_BUTTON_TEXT_SCHEDULE = ["予約投稿する", "予約する", "予約して投稿", "この内容で予約する"]
SUBMIT_BUTTON_TEXT_PUBLISH = ["投稿する", "公開する", "公開", "投稿"]


def get_credentials(args) -> tuple[str, str]:
    username = args.username or os.environ.get("BUNSHUN_USERNAME")
    password = args.password or os.environ.get("BUNSHUN_PASSWORD")
    if not username:
        username = input("ユーザーID/メールアドレス: ").strip()
    if not password:
        password = getpass.getpass("パスワード: ")
    return username, password


def read_body_paragraphs(body_file: Path) -> list[str]:
    text = body_file.read_text(encoding="utf-8")
    # 空行区切りで段落に分割(前後の空白は除去)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paragraphs if p]


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


def fill_body(page, paragraphs: list[str]) -> None:
    loc, selector = find_first_locator(page, NEW_POST_BODY_SELECTORS)
    if not loc:
        raise RuntimeError(
            "本文入力欄が見つかりませんでした。NEW_POST_BODY_SELECTORS を"
            "実際のフォーム構造(--inspect の form.html)に合わせて調整してください。"
        )
    loc.click()
    for i, para in enumerate(paragraphs):
        if i > 0:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
        page.keyboard.type(para, delay=5)
    print(f"  本文入力欄: {selector}({len(paragraphs)}段落)")


def attach_images(page, image_paths: list[str]) -> None:
    if not image_paths:
        return
    resolved = [str(Path(p).resolve()) for p in image_paths]
    for p in resolved:
        if not Path(p).is_file():
            raise RuntimeError(f"画像ファイルが見つかりません: {p}")

    file_inputs = page.locator(", ".join(NEW_POST_IMAGE_INPUT_SELECTORS))
    count = file_inputs.count()
    if count == 0:
        raise RuntimeError(
            "画像アップロード用の input[type=file] が見つかりませんでした。"
            "エディタのツールバーから画像追加ボタンを押さないと input が現れない"
            "構造の可能性があります。--inspect の form.html / form.png を確認してください。"
        )
    # 最初に見つかった file input にまとめて設定する(複数画像対応のものを想定)。
    # 1枚ずつしか受け付けない構造の場合は、うまく添付できない可能性があるため
    # form.html を見ながら要調整。
    file_inputs.first.set_input_files(resolved)
    print(f"  画像 {len(resolved)} 枚を添付しました: {', '.join(resolved)}")


def set_schedule(page, publish_at: datetime) -> None:
    if not click_by_text_candidates(page, NEW_POST_SCHEDULE_TOGGLE_TEXT):
        raise RuntimeError(
            "「予約投稿」への切り替えが見つかりませんでした。"
            "NEW_POST_SCHEDULE_TOGGLE_TEXT を実際の文言に合わせて調整してください。"
        )
    page.wait_for_timeout(500)

    dt_loc, dt_selector = find_first_locator(page, NEW_POST_SCHEDULE_DATETIME_SELECTORS, timeout_ms=2000)
    if dt_loc:
        dt_loc.fill(publish_at.strftime("%Y-%m-%dT%H:%M"))
        print(f"  予約日時({dt_selector}): {publish_at}")
        return

    date_loc, date_selector = find_first_locator(page, NEW_POST_SCHEDULE_DATE_SELECTORS, timeout_ms=2000)
    time_loc, time_selector = find_first_locator(page, NEW_POST_SCHEDULE_TIME_SELECTORS, timeout_ms=2000)
    if date_loc and time_loc:
        date_loc.fill(publish_at.strftime("%Y-%m-%d"))
        time_loc.fill(publish_at.strftime("%H:%M"))
        print(f"  予約日時({date_selector} / {time_selector}): {publish_at}")
        return

    raise RuntimeError(
        "予約日時の入力欄が見つかりませんでした。NEW_POST_SCHEDULE_*_SELECTORS を"
        "実際のフォーム構造に合わせて調整してください。"
    )


def submit_post(page, schedule: bool) -> None:
    texts = SUBMIT_BUTTON_TEXT_SCHEDULE if schedule else SUBMIT_BUTTON_TEXT_PUBLISH
    if not click_by_text_candidates(page, texts):
        raise RuntimeError(
            "送信ボタンが見つかりませんでした。SUBMIT_BUTTON_TEXT_SCHEDULE / "
            "SUBMIT_BUTTON_TEXT_PUBLISH を実際のボタン文言に合わせて調整してください。"
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
    parser.add_argument("--body-file", help="本文が書かれたテキスト/Markdownファイル")
    parser.add_argument("--image", action="append", default=[], help="添付する画像ファイル(複数指定可)")

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
            paragraphs = read_body_paragraphs(Path(args.body_file))
            fill_title(page, args.title)
            fill_body(page, paragraphs)
            attach_images(page, args.image)
            if args.publish_at:
                set_schedule(page, args.publish_at)
                submit_post(page, schedule=True)
                print(f"[完了] 予約投稿を送信しました(予約日時: {args.publish_at})。"
                      "サイト側の予約投稿一覧で内容をご確認ください。")
            else:
                submit_post(page, schedule=False)
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
