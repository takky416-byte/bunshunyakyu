#!/usr/bin/env python3
"""
yakyu.bunshun.jp のブログ記事を個人アーカイブ用にローカル保存するスクリプト。

使い方:
    # 1本だけ保存
    python archive_site.py --url https://yakyu.bunshun.jp/blogs/af9f98c6821b

    # 一覧ページを起点にページネーションを辿って全記事を保存
    python archive_site.py --list-url https://yakyu.bunshun.jp/ --max-pages 20

    # 一覧ページがボタン無しの無限スクロール方式で追加読み込みされる場合
    # (Playwrightが必要: pip install playwright && playwright install chromium)
    python archive_site.py --list-url https://yakyu.bunshun.jp/blogs --infinite-scroll

    # JS で描画されるコメント(Disqus / Facebookコメント等)も取りたい場合
    python archive_site.py --url https://yakyu.bunshun.jp/blogs/af9f98c6821b --render

    # 会員限定記事の場合(先にログインしてからセッションを使って取得)
    export BUNSHUN_USERNAME="your_id"
    export BUNSHUN_PASSWORD="your_password"   # 未設定なら実行時にプロンプトで入力
    python archive_site.py --login-url https://yakyu.bunshun.jp/login \
        --url https://yakyu.bunshun.jp/blogs/af9f98c6821b

必要ライブラリ:
    pip install -r requirements.txt
    (--render を使う場合は追加で `pip install playwright && playwright install chromium`)

出力:
    archive/<YYYY-MM-DD>-<slug>/article.md   本文(Markdown, フロントマター付き)
    archive/<YYYY-MM-DD>-<slug>/article.html 取得した生HTML(バックアップ)
    archive/<YYYY-MM-DD>-<slug>/images/...   本文中の画像
    archive/<YYYY-MM-DD>-<slug>/comments.json コメント(取得できた場合)
    archive/index.json                       これまでにアーカイブした記事の一覧(重複防止)

注意:
    このスクリプトは個人的な保存(私的複製の範囲)を目的としています。
    取得した記事を再配布・公開しないでください。サイトの利用規約・著作権に従って使用してください。
    サーバーに過度な負荷をかけないよう、リクエスト間には --delay 秒の間隔を空けます。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown

USER_AGENT = (
    "Mozilla/5.0 (compatible; PersonalArchiveBot/1.0; "
    "+for-personal-use-only)"
)

DEFAULT_OUT_DIR = Path("archive")

# 本文らしき要素を探すための候補セレクタ(サイト固有の構造が不明なため、
# よくあるパターンを優先度順に並べている。うまく取れない場合はここを調整する)
CONTENT_SELECTORS = [
    "article",
    "main article",
    ".entry-content",
    ".post-content",
    ".article-body",
    ".p-article__body",
    ".p-entry__body",
    "#article-body",
    ".l-article__body",
    "main",
    "#content",
    ".content",
]

# コメント欄らしき要素を探すための候補セレクタ
COMMENT_CONTAINER_SELECTORS = [
    "#comments",
    ".comments",
    ".comment-list",
    "ul.comment-list",
    "#disqus_thread",
    ".fb-comments",
    "#fb-comments",
]

NAV_TAGS_TO_STRIP = ["nav", "header", "footer", "aside", "script", "style", "form", "noscript"]


@dataclass
class Article:
    url: str
    title: str = ""
    date: str = ""
    author: str = ""
    body_html: str = ""
    images: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    raw_html: str = ""


def slugify(text: str, fallback: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE).strip("-")
    text = text[:60] if text else fallback
    return text or fallback


def fetch_html(url: str, session: requests.Session, render: bool = False, timeout: int = 20) -> str:
    if render:
        return fetch_html_rendered(url)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def fetch_html_rendered(url: str) -> str:
    """Playwright でページを開き、JS 実行後の HTML を取得する(コメント欄が JS 描画の場合用)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "--render を使うには playwright が必要です: pip install playwright && playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle", timeout=30000)
        # 遅延読み込み画像・コメントウィジェットの読み込みを待つため少し待機
        page.wait_for_timeout(2000)
        html = page.content()
        browser.close()
        return html


def session_cookies_for_playwright(session: requests.Session) -> list[dict]:
    cookies = []
    for c in session.cookies:
        if not c.domain:
            continue
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
        })
    return cookies


def count_matching_links(html: str, base_url: str, pattern: re.Pattern) -> int:
    soup = BeautifulSoup(html, "html.parser")
    count = 0
    for a in soup.find_all("a", href=True):
        abs_url = urljoin(base_url, a["href"])
        if urlparse(abs_url).netloc == urlparse(base_url).netloc and pattern.search(urlparse(abs_url).path):
            count += 1
    return count


def fetch_list_html_infinite_scroll(
    url: str,
    link_pattern: str | None,
    max_scrolls: int,
    scroll_pause_ms: int,
    session: requests.Session | None = None,
) -> str:
    """無限スクロール(ボタンなし・URLも変わらず、スクロールで追加読み込みされる)方式の
    一覧ページを、実際にブラウザでスクロールさせながら記事リンクが増えなくなるまで読み込む。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "--infinite-scroll を使うには playwright が必要です: "
            "pip install playwright && playwright install chromium"
        ) from e

    pattern = re.compile(link_pattern) if link_pattern else re.compile(r"/blogs/[0-9a-f]{8,}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT)
        if session is not None:
            cookies = session_cookies_for_playwright(session)
            if cookies:
                context.add_cookies(cookies)
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)

        last_count = count_matching_links(page.content(), url, pattern)
        print(f"  現在 {last_count} 件のリンクを検出(スクロールして追加読み込みします)")
        stable_rounds = 0
        for i in range(max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(scroll_pause_ms)
            new_count = count_matching_links(page.content(), url, pattern)
            if new_count > last_count:
                print(f"  スクロール {i + 1}回目: {new_count} 件に増加")
                last_count = new_count
                stable_rounds = 0
            else:
                stable_rounds += 1
                if stable_rounds >= 3:
                    print(f"  3回連続で増加なし。読み込み完了とみなします(合計 {last_count} 件)")
                    break

        html = page.content()
        browser.close()
        return html


def get_credentials(args) -> tuple[str, str]:
    """認証情報をコマンドライン引数 > 環境変数 > 対話入力 の優先順位で取得する。
    パスワードをコマンド履歴に残さないため、コマンドライン引数での指定は推奨しない。"""
    username = args.username or os.environ.get("BUNSHUN_USERNAME")
    password = args.password or os.environ.get("BUNSHUN_PASSWORD")
    if not username:
        username = input("ユーザーID/メールアドレス: ").strip()
    if not password:
        password = getpass.getpass("パスワード: ")
    return username, password


def browser_login(
    login_url: str, username: str, password: str,
    username_field: str | None, password_field: str | None,
) -> tuple[list[dict], bool, str]:
    """Vue/Reactなど、ログインボタンの送信処理がJavaScriptで実装されているサイト向けに、
    実際にヘッドレスブラウザでフォームへ入力・送信ボタンをクリックしてログインする。
    戻り値は (取得したCookie一覧, ログイン成功と思われるか, 最終的なページURL)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "--browser-login を使うには playwright が必要です: "
            "pip install playwright && playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
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

        final_url = page.url
        still_has_password = page.locator('input[type="password"]').count() > 0
        cookies = context.cookies()
        browser.close()

    success = not still_has_password
    return cookies, success, final_url


def apply_cookies_to_session(session: requests.Session, cookies: list[dict]) -> None:
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path") or "/")


def detect_login_form(soup: BeautifulSoup, username_field: str | None, password_field: str | None):
    """type=password の <input> を含む <form> をログインフォームとみなして返す。"""
    for form in soup.find_all("form"):
        pw_input = form.find("input", attrs={"type": "password"})
        if password_field:
            pw_input = form.find("input", attrs={"name": password_field}) or pw_input
        if pw_input:
            return form, pw_input
    return None, None


def guess_username_field_name(form) -> str | None:
    candidates_attrs = ["name", "id"]
    candidate_keywords = ["email", "mail", "login", "user", "account", "id"]
    for inp in form.find_all("input"):
        itype = (inp.get("type") or "text").lower()
        if itype in {"password", "hidden", "submit", "checkbox", "radio"}:
            continue
        for attr in candidates_attrs:
            val = (inp.get(attr) or "").lower()
            if any(kw in val for kw in candidate_keywords):
                return inp.get("name")
    # フォールバック: password 以外の最初のテキスト系 input
    for inp in form.find_all("input"):
        itype = (inp.get("type") or "text").lower()
        if itype in {"text", "email"}:
            return inp.get("name")
    return None


def login(session: requests.Session, login_url: str, username: str, password: str,
          username_field: str | None, password_field: str | None) -> bool:
    resp = session.get(login_url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form, pw_input = detect_login_form(soup, username_field, password_field)
    if not form or not pw_input:
        print("[エラー] ログインフォームが見つかりませんでした。"
              "--username-field / --password-field で明示指定してください。", file=sys.stderr)
        return False

    pw_name = pw_input.get("name")
    user_name = username_field or guess_username_field_name(form)
    if not user_name:
        print("[エラー] ユーザーID欄が見つかりませんでした。--username-field で指定してください。", file=sys.stderr)
        return False

    payload = {}
    for inp in form.find_all(["input", "textarea"]):
        name = inp.get("name")
        if not name:
            continue
        payload[name] = inp.get("value", "")
    payload[user_name] = username
    payload[pw_name] = password

    action = form.get("action") or login_url
    action_url = urljoin(login_url, action)
    method = (form.get("method") or "post").lower()

    if method == "get":
        result = session.get(action_url, params=payload, timeout=20)
    else:
        result = session.post(action_url, data=payload, timeout=20)
    result.raise_for_status()

    result_soup = BeautifulSoup(result.text, "html.parser")
    still_has_login_form, _ = detect_login_form(result_soup, username_field, password_field)
    if still_has_login_form:
        print("[警告] ログイン後も再度ログインフォームが検出されました。"
              "ID/パスワードが誤っているか、フォーム構造の自動検出に失敗している可能性があります。", file=sys.stderr)
        return False

    print("[ログイン成功と思われます] ログイン後のページにフォームが見当たりませんでした。"
          "念のため保存された記事が会員限定部分まで含んでいるか確認してください。")
    return True


def pick_content_element(soup: BeautifulSoup):
    for selector in CONTENT_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 200:
            return el
    # フォールバック: body 直下で最も文字数が多いブロックを採用
    candidates = soup.find_all(["div", "section", "article"])
    best = None
    best_len = 0
    for c in candidates:
        length = len(c.get_text(strip=True))
        if length > best_len:
            best = c
            best_len = length
    return best or soup.body or soup


def extract_title(soup: BeautifulSoup) -> str:
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        return og["content"].strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    if soup.title:
        return soup.title.get_text(strip=True)
    return ""


def extract_date(soup: BeautifulSoup) -> str:
    meta = soup.select_one('meta[property="article:published_time"]')
    if meta and meta.get("content"):
        return meta["content"][:10]
    time_tag = soup.find("time")
    if time_tag:
        return (time_tag.get("datetime") or time_tag.get_text(strip=True))[:10]
    return ""


def extract_comments(soup: BeautifulSoup) -> list[dict]:
    comments = []
    for selector in COMMENT_CONTAINER_SELECTORS:
        container = soup.select_one(selector)
        if not container:
            continue
        # よくあるコメント1件ぶんの構造を推測して拾う
        items = container.select("li, .comment, .comment-body, article")
        if not items:
            text = container.get_text(strip=True)
            if text:
                comments.append({"author": "", "date": "", "text": text})
            continue
        for item in items:
            text = item.get_text(" ", strip=True)
            if text and len(text) > 1:
                comments.append({"author": "", "date": "", "text": text})
        if comments:
            break
    return comments


def download_images(content_el, base_url: str, out_dir: Path, session: requests.Session, delay: float) -> None:
    img_dir = out_dir / "images"
    for i, img in enumerate(content_el.find_all("img")):
        src = img.get("data-src") or img.get("src")
        if not src:
            continue
        abs_url = urljoin(base_url, src)
        ext = Path(urlparse(abs_url).path).suffix or ".jpg"
        local_name = f"img_{i:03d}{ext}"
        local_path = img_dir / local_name
        try:
            img_dir.mkdir(parents=True, exist_ok=True)
            resp = session.get(abs_url, timeout=20)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            img["src"] = f"images/{local_name}"
            time.sleep(delay)
        except requests.RequestException as e:
            print(f"  [警告] 画像取得失敗: {abs_url} ({e})", file=sys.stderr)


def clean_content(content_el):
    for tag in content_el.select(", ".join(NAV_TAGS_TO_STRIP)):
        tag.decompose()
    return content_el


def parse_article(url: str, html: str) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup)
    date = extract_date(soup)
    content_el = pick_content_element(BeautifulSoup(html, "html.parser"))
    content_el = clean_content(content_el)
    comments = extract_comments(soup)
    return Article(
        url=url,
        title=title,
        date=date,
        body_html=str(content_el),
        comments=comments,
        raw_html=html,
    )


def save_article(article: Article, out_root: Path, session: requests.Session, delay: float) -> Path:
    date_part = article.date or datetime.now().strftime("%Y-%m-%d")
    url_id = Path(urlparse(article.url).path).name or "article"
    title_slug = slugify(article.title, fallback="")
    dir_name = f"{date_part}-{url_id}" + (f"-{title_slug}" if title_slug else "")
    out_dir = out_root / dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    content_soup = BeautifulSoup(article.body_html, "html.parser")
    download_images(content_soup, article.url, out_dir, session, delay)

    markdown_body = html_to_markdown(str(content_soup), heading_style="ATX")
    frontmatter = (
        "---\n"
        f"title: {json.dumps(article.title, ensure_ascii=False)}\n"
        f"url: {article.url}\n"
        f"date: {article.date}\n"
        f"archived_at: {datetime.now().isoformat(timespec='seconds')}\n"
        "---\n\n"
    )
    (out_dir / "article.md").write_text(frontmatter + markdown_body, encoding="utf-8")
    (out_dir / "article.html").write_text(article.raw_html, encoding="utf-8")
    (out_dir / "comments.json").write_text(
        json.dumps(article.comments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir


def load_index(out_root: Path) -> dict:
    index_path = out_root / "index.json"
    if index_path.exists():
        return json.loads(index_path.read_text(encoding="utf-8"))
    return {}


def save_index(out_root: Path, index: dict) -> None:
    (out_root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def discover_article_links(list_html: str, base_url: str, link_pattern: str | None) -> list[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    for tag in soup.select(", ".join(NAV_TAGS_TO_STRIP)):
        tag.decompose()
    pattern = re.compile(link_pattern) if link_pattern else re.compile(r"/blogs/[0-9a-f]{8,}")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        abs_url = urljoin(base_url, a["href"])
        parsed = urlparse(abs_url)
        if parsed.netloc != urlparse(base_url).netloc:
            continue
        if pattern.search(parsed.path) and abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links


def find_next_page(list_html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(list_html, "html.parser")
    next_link = soup.select_one('a[rel="next"]')
    if next_link and next_link.get("href"):
        return urljoin(base_url, next_link["href"])
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text in {"次へ", "次のページ", "Next", ">"}:
            return urljoin(base_url, a["href"])
    return None


def archive_one(url: str, out_root: Path, session: requests.Session, index: dict, args) -> None:
    if url in index and not args.force:
        print(f"[スキップ] 既に保存済み: {url}")
        return
    print(f"[取得中] {url}")
    try:
        html = fetch_html(url, session, render=args.render)
    except requests.RequestException as e:
        print(f"  [エラー] 取得失敗: {e}", file=sys.stderr)
        return
    article = parse_article(url, html)
    out_dir = save_article(article, out_root, session, args.delay)
    index[url] = {
        "title": article.title,
        "date": article.date,
        "dir": str(out_dir.relative_to(out_root)),
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "comments_count": len(article.comments),
    }
    save_index(out_root, index)
    print(f"  → 保存先: {out_dir} (コメント {len(article.comments)} 件)")
    time.sleep(args.delay)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="単一記事のURL")
    parser.add_argument("--list-url", help="一覧ページのURL(ここから記事リンクを辿って全件保存)")
    parser.add_argument("--link-pattern", help="記事URLを判定する正規表現(未指定時は /blogs/<英数字ID> を想定)")
    parser.add_argument("--max-pages", type=int, default=10, help="一覧ページを辿る最大ページ数")
    parser.add_argument("--max-articles", type=int, default=0, help="保存する記事数の上限(0=無制限)")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="保存先ディレクトリ")
    parser.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔(秒)")
    parser.add_argument("--render", action="store_true", help="Playwright でJS実行後のHTMLを取得(コメント欄がJS描画の場合)")
    parser.add_argument("--force", action="store_true", help="既に保存済みでも再取得する")
    parser.add_argument("--login-url", help="会員限定記事を取得する場合のログインページURL")
    parser.add_argument("--username", help="ログインID(未指定時は環境変数 BUNSHUN_USERNAME か対話入力)")
    parser.add_argument("--password", help="パスワード(未指定時は環境変数 BUNSHUN_PASSWORD か対話入力。"
                                            "コマンド履歴に残るため指定は非推奨)")
    parser.add_argument("--username-field", help="ログインフォームのユーザーID欄のname属性(自動検出できない場合に指定)")
    parser.add_argument("--password-field", help="ログインフォームのパスワード欄のname属性(自動検出できない場合に指定)")
    parser.add_argument("--browser-login", action="store_true",
                         help="ログインフォームの送信処理がJavaScriptで実装されているサイト向けに、"
                              "Playwrightで実際にブラウザ操作してログインする(単純なPOST送信でログインできない場合に指定)")
    parser.add_argument("--infinite-scroll", action="store_true",
                         help="一覧ページがボタン無し・URL変化無しのスクロールで追加読み込みされる場合に指定"
                              "(Playwrightで実際にスクロールして記事リンクを集める)")
    parser.add_argument("--max-scrolls", type=int, default=50, help="--infinite-scroll 使用時の最大スクロール回数")
    parser.add_argument("--scroll-pause-ms", type=int, default=1500,
                         help="--infinite-scroll 使用時、1回のスクロール後に読み込みを待つ時間(ミリ秒)")
    args = parser.parse_args()

    if not args.url and not args.list_url:
        parser.error("--url か --list-url のどちらかを指定してください")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    index = load_index(out_root)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    if args.login_url:
        username, password = get_credentials(args)
        if args.browser_login:
            cookies, ok, final_url = browser_login(
                args.login_url, username, password, args.username_field, args.password_field
            )
            if not ok:
                print(f"[中断] ブラウザ経由のログインに失敗した可能性があります(遷移後URL: {final_url})。"
                      "ID/パスワードをご確認ください。", file=sys.stderr)
                sys.exit(1)
            apply_cookies_to_session(session, cookies)
            print(f"[ログイン成功と思われます] ブラウザ経由でログインしました(遷移後URL: {final_url})")
        elif not login(session, args.login_url, username, password, args.username_field, args.password_field):
            print("[中断] ログインに失敗した可能性があるため処理を中止します。"
                  "--force で無視して続行することはできません。フォーム構造をご確認ください。", file=sys.stderr)
            sys.exit(1)

    if args.url:
        archive_one(args.url, out_root, session, index, args)
        return

    saved_count = 0

    if args.infinite_scroll:
        # 無限スクロール方式: ブラウザで実際にスクロールしながら記事リンクを集める
        print(f"[無限スクロール取得] {args.list_url}")
        list_html = fetch_list_html_infinite_scroll(
            args.list_url, args.link_pattern, args.max_scrolls, args.scroll_pause_ms, session=session
        )
        links = discover_article_links(list_html, args.list_url, args.link_pattern)
        print(f"  記事リンク {len(links)} 件見つかりました")
        for link in links:
            if args.max_articles and saved_count >= args.max_articles:
                break
            archive_one(link, out_root, session, index, args)
            saved_count += 1
        return

    # --list-url モード(通常のページネーション): 「次へ」リンクを辿って記事リンクを収集しつつ都度保存
    page_url = args.list_url
    for page_num in range(1, args.max_pages + 1):
        print(f"[一覧ページ {page_num}] {page_url}")
        try:
            list_html = fetch_html(page_url, session, render=args.render)
        except requests.RequestException as e:
            print(f"  [エラー] 一覧ページ取得失敗: {e}", file=sys.stderr)
            break

        links = discover_article_links(list_html, page_url, args.link_pattern)
        print(f"  記事リンク {len(links)} 件見つかりました")
        for link in links:
            if args.max_articles and saved_count >= args.max_articles:
                break
            archive_one(link, out_root, session, index, args)
            saved_count += 1

        if args.max_articles and saved_count >= args.max_articles:
            break

        next_url = find_next_page(list_html, page_url)
        if not next_url or next_url == page_url:
            print("次のページが見つからないため終了します")
            break
        page_url = next_url
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
