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

### 一覧ページが「無限スクロール」方式の場合

ページ番号リンクや「もっと見る」ボタンが無く、スクロールするだけで記事が
自動的に追加読み込みされるタイプの一覧ページには `--infinite-scroll` を使います
(要 Playwright。上のセットアップ手順でインストールしてください)。

```bash
python archive_site.py --list-url https://yakyu.bunshun.jp/blogs --infinite-scroll
```

- ブラウザを裏側で実際に動かして一番下までスクロールを繰り返し、記事リンクが
  3回連続で増えなくなった時点で「読み込み完了」とみなします。
- 読み込みに時間がかかるサイトでは `--scroll-pause-ms 3000` のように待ち時間を延ばしてください。
- 通常のページネーション(`--max-pages` / 「次へ」リンクを辿る方式)とは併用しません。
  `--infinite-scroll` を指定した場合はこちらが優先されます。

## 出力

```
archive/
  index.json                       アーカイブ済み記事の一覧(重複防止用)
  2024/
    05/
      2024-05-01-xxxxxx-記事タイトル/
        article.md                 本文(Markdown、フロントマター付き)
        article.html               取得時点の生HTML(バックアップ用)
        images/                    本文中の画像
        comments.json              取得できたコメント(取得できない場合は空配列)
        reactions.json             取得できたリアクション/いいね(取得できない場合は空配列)
```

記事数が多くなることを想定し、投稿日の「年/月」ごとにフォルダを分けて保存します
(投稿日が取得できない記事は、取得した日の年月に保存されます)。

## コメント・リアクションがJavaScriptで後から読み込まれる場合

yakyu.bunshun.jp のようなコミュニティサイトでは、記事本文は最初のHTMLに含まれていても、
コメントやリアクション(いいね・スタンプ等)はページを開いた後にJavaScriptが追加で読み込む
実装になっていることがあります。この場合は `--render` を付けてください(要 Playwright)。

```bash
python archive_site.py --login-url https://yakyu.bunshun.jp/login --browser-login --render \
    --url https://yakyu.bunshun.jp/blogs/xxxxxxxx
```

- `--render` はログイン中のセッション(Cookie)を引き継いだ状態でブラウザを開くので、
  会員限定記事でもログインしたまま取得できます(`--login-url`と一緒に使った場合)。
- `--list-url --infinite-scroll` と組み合わせて全件保存する場合も同様に `--render` を追加できますが、
  1記事ごとに実際にブラウザを起動するため、通常よりかなり時間がかかります。
- コメント・リアクションを拾うためのCSS選択パターン(`COMMENT_CONTAINER_SELECTORS` /
  `REACTION_CONTAINER_SELECTORS`)は実際のサイト構造を確認できない状態での推測です。
  `--render` を付けても `comments.json` / `reactions.json` が空のままの場合は、保存された
  `article.html`(`--render` 時はJS実行後のHTMLが保存されます)を見せてもらえれば、
  実際の構造に合わせて調整します。

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

### ログインボタンを押してもログインできない(JavaScriptでログイン処理している)場合

サイトによっては、ログインフォームの送信処理がVue.jsやReactなどのJavaScriptで実装されており、
単純にフォームの内容をそのままPOST送信するだけではログインできないことがあります
(送信ボタンを押すとJavaScript側で別のAPIにリクエストを送るなど、フォームのHTML構造だけでは
再現できない処理が行われているケース)。

この場合は `--browser-login` を付けてください。実際にヘッドレスブラウザでフォームに入力し、
送信ボタンをクリックすることでログインします(要 Playwright)。

```bash
python archive_site.py --login-url https://yakyu.bunshun.jp/login --browser-login \
    --list-url https://yakyu.bunshun.jp/blogs --infinite-scroll
```

- 保存された記事の本文が「ログイン」という案内ページの内容になってしまっている場合は、
  ほぼこのパターンです。`--browser-login` を試してください。

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
