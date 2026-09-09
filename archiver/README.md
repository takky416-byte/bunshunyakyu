# yakyu.bunshun.jp 記事アーカイブツール

`https://yakyu.bunshun.jp/` のブログ記事を、本文・画像・(可能であれば)コメントとともに
ローカルに個人アーカイブするためのスクリプトです。

**注意:** この環境(Claude Code on the web のサンドボックス)からは
`yakyu.bunshun.jp` へのネットワークアクセスがポリシーでブロックされているため、
このスクリプトはユーザー自身のPC(またはアクセス制限のない環境)で実行してください。

## セットアップ

```bash
cd archiver
python3 -m venv .venv
source .venv/bin/activate  # Windows は .venv\Scripts\activate
pip install -r requirements.txt
```

コメント欄がJavaScriptで後から描画されるタイプ(Disqus、Facebookコメント等)の場合は、
`--render` オプション用に Playwright も入れてください。

```bash
pip install playwright
playwright install chromium
```

## 使い方

### 1本だけ保存する

```bash
python archive_site.py --url https://yakyu.bunshun.jp/blogs/af9f98c6821b
```

### サイト全体(一覧ページを起点にページネーションを辿って)保存する

```bash
python archive_site.py --list-url https://yakyu.bunshun.jp/ --max-pages 50
```

- 途中で止めても `archive/index.json` に保存済みURLが記録されるので、再実行すると続きから取得します(`--force` で再取得も可能)。
- 記事の保存件数を絞りたい場合は `--max-articles 10` のように指定してください。

### コメントがJSで描画される場合

```bash
python archive_site.py --url https://yakyu.bunshun.jp/blogs/af9f98c6821b --render
```

## 出力

```
archive/
  index.json                       アーカイブ済み記事の一覧(重複防止用)
  2024-05-01-xxxxxx/
    article.md                     本文(Markdown、フロントマター付き)
    article.html                   取得時点の生HTML(バックアップ用)
    images/                        本文中の画像
    comments.json                  取得できたコメント(取得できない場合は空配列)
```

## 会員限定記事(ログインが必要な記事)を保存する

自分のアカウントで正規に閲覧できる会員限定記事を、私的複製の範囲で保存したい場合はログイン機能を使えます。

```bash
export BUNSHUN_USERNAME="あなたのID"
export BUNSHUN_PASSWORD="あなたのパスワード"   # 未設定なら実行時にプロンプトで安全に入力できます
python archive_site.py --login-url https://yakyu.bunshun.jp/login \
    --url https://yakyu.bunshun.jp/blogs/xxxxxxxx
```

- ID/パスワードは**コマンドライン引数に直接書かない**でください(シェル履歴に残ります)。
  環境変数 `BUNSHUN_USERNAME` / `BUNSHUN_PASSWORD` を使うか、両方省略すると実行時に
  `input()` / `getpass` で安全に入力を求められます。
- ログインフォームは `<input type="password">` を含む `<form>` を自動検出し、
  ユーザーID欄はフィールド名に `email` / `login` / `user` / `id` などを含むものを推測して使います。
  自動検出がうまくいかない場合は `--username-field` / `--password-field` でフォームの
  `name` 属性を直接指定してください(ブラウザの開発者ツールでログインフォームのHTMLを見れば分かります)。
- ログイン後のページに再びログインフォームが検出された場合は失敗とみなし、処理を中断します。
- 認証情報や取得した会員限定記事は、自分の契約範囲内での個人利用にとどめ、共有・再配布しないでください。

## うまく本文/画像/コメントが取れないとき

サイトのHTML構造が事前に確認できない状態で作っているため、抽出ロジックは
「よくあるパターン」を複数試す汎用実装になっています。もし特定の記事で

- 本文が正しく取れない
- 画像が抜け落ちる
- コメントが空になる

場合は、該当ページの `article.html`(保存された生HTML)の該当箇所を見せてもらえれば、
`archive_site.py` 内の以下の定数を調整して精度を上げられます。

- `CONTENT_SELECTORS`: 本文を囲むタグのCSSセレクタ候補
- `COMMENT_CONTAINER_SELECTORS`: コメント欄を囲むタグのCSSセレクタ候補

## Facebookコメントプラグインについて

コメント欄が Facebook の `fb-comments` プラグインの場合、コメント本文はFacebook側の
iframe内にあり、`--render` を使っても外部サイトから正規のAPI無しに全件を確実に
取得できない場合があります(表示されている分のみ拾えることがありますが保証はできません)。

## 利用上の注意

- このツールは**個人的な保存(私的複製)**を目的としています。取得した記事や画像を
  再配布・再公開しないでください。
- サーバーに負荷をかけないよう、デフォルトでリクエスト間に1.5秒の間隔を空けています
  (`--delay` で調整可能)。大量ページを一気に取得するのは避けてください。
- サイトの利用規約を確認の上、自己責任でご利用ください。
