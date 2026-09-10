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

## 差分取得(2回目以降、新規・更新分だけ取得する)

最初に `--infinite-scroll` で全件取得したあとは、2回目以降は `--update` を使うと、
毎回全件をスクロールし直さずに、**新規記事**と**コメント/リアクション数が前回から
変わった記事**だけを確認・保存できます。

yakyu.bunshun.jp の `/blogs` には「更新順(コメントが新しい順)」の並び替えがあり、
そのURLは以下のようになります(サイト上で並び替えを選択した時のURLを使ってください)。

```bash
python archive_site.py --login-url https://yakyu.bunshun.jp/login --browser-login --render \
    --list-url "https://yakyu.bunshun.jp/blogs?order_type=comment_update_desc" \
    --infinite-scroll --update
```

仕組み:

- 「更新順」の一覧を上から順に実際に開いていき、`archive/index.json` に記録された
  前回の保存内容(本文の内容・コメント数・リアクション数)と比較します。
- 新規記事、または本文/コメント/リアクションのいずれかが変わっている記事だけを
  保存し直します。
- 変化なしの記事が既定で**5件連続**続いたら、それより下は既に最新のはずとみなして
  そこで巡回を打ち切ります(`--update-stop-after` で件数を変更可能)。
- ただし、コメントが一切付かないまま本文だけこっそり編集された記事は、この
  「コメント更新順」の一覧に上がってこない可能性があり、その場合は `--update` では
  検知できません。それを漏れなく確認したい場合は、下記の `--recheck` を使ってください。

### 本文の編集も漏れなく確認したい場合(全件チェック)

`--update` は「コメント更新順」の一覧に頼っているため、コメントの付かない本文編集を
見逃す可能性があります。それを避けたい場合は `--recheck` を使うと、一覧の**全記事**を
実際に開いて、新規記事・本文編集・コメント/リアクションの変化のどれかがあった記事だけを
保存し直します(早期打ち切りをしないので、時間は初回のフル取得と同程度かかりますが、
変化のなかった記事のファイルは上書きされません)。

```bash
python archive_site.py --login-url https://yakyu.bunshun.jp/login --browser-login --render \
    --list-url https://yakyu.bunshun.jp/blogs --infinite-scroll --recheck
```

普段は `--update` で素早く新着を拾い、月イチ程度で `--recheck` を回して
本文編集の見逃しがないか確認する、という使い分けがおすすめです。

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
python archive_site.py --login-url https://yakyu.bunshun.jp/login \
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

## ブログ記事の自動投稿(予約投稿)

`post_blog.py` を使うと、タイトル・本文・画像を指定して、yakyu.bunshun.jp の
マイページに新規ブログ記事を自動投稿(予約投稿)できます。タイトル・本文・画像は
ChatGPTなどで作成したものをファイル/ファイルパスとして渡す想定です。

**注意:** 新規投稿ページ(`https://yakyu.bunshun.jp/blogs/new`)の実際のHTML構造は
確認済みで、タイトル欄・本文エディタ(Trixエディタ)・予約投稿の切り替え・送信ボタンは
その構造に基づいて実装しています。**ヘッダー画像アップロード後に出る可能性がある
クロップ(トリミング)確認ボタンの文言だけは未確認で推測**のため、うまくいかない場合は
`--inspect` から試してください。

### セットアップ

```bash
pip install playwright
playwright install chromium
```

### 0. まずはフォームの構造を確認する(初回・うまくいかないときに)

```bash
python post_blog.py --login-url https://yakyu.bunshun.jp/login --inspect
```

- ログイン後に新規投稿ページを開き、`archive/_new_post_inspect/form.html` と
  `form.png` に保存します。
- 自動入力がうまくいかない場合は、この `form.html` を見せてもらえれば、
  実際の構造に合わせて `post_blog.py` 内のセレクタを調整します。
  (投稿失敗時にも同じフォルダに `error_form.html` / `error_form.png` として
  失敗時点の状態が自動保存されます。)

### 1. 本文ファイルを用意する

プレーンテキスト/Markdown風の記法で、段落は空行で区切ってください(`post_body.txt` など)。

- 太字: `**太字にしたい部分**`
- 斜体: `*斜体にしたい部分*` または `_斜体にしたい部分_`
- 下線: `__下線を引きたい部分__`
- 組み合わせ可: `**__太字+下線の見出し__**`
- 引用(blockquote): 段落の先頭を `> ` にする
- 本文途中に画像を差し込む: 画像だけの行(前後を空行で区切る)に
  `![説明](画像ファイルのパス)` と書く(説明は空でも可: `![](img.jpg)`)

例(`post_body.txt`):

```
**__IT野球選手名鑑 #017__**

ルーター

![完投したエースの写真](images/ace.jpg)

今日は完封勝利でした。**エースの好投**が光った試合でした。

> この記事は生成AIを活用して執筆しています。
```

これらの記法は、実際に公開済みの記事の保存後HTML(`<strong>`/`<em>`/`<u>`/`<blockquote>`)を
確認したうえでその構造どおりにHTMLを組み立て、Trixエディタが公式に提供する
`editor.insertHTML()`(リッチテキストを貼り付けたのと同じ処理)で挿入しているため、
キーボードショートカットの推測に頼らず確実に反映されます。画像挿入も同様に、
ツールバーのボタンではなく `editor.insertFile()`(ドラッグ&ドロップ/貼り付けと同じ経路)
を使っています。

### 2. 予約投稿する

```bash
python post_blog.py --login-url https://yakyu.bunshun.jp/login \
    --title "9/10 の試合を振り返って" \
    --header-image header.jpg \
    --body-file post_body.txt \
    --publish-at "2026-09-15 21:00"
```

- `--header-image` は記事の一番上に出るヘッダー画像(アイキャッチ/サムネイル)用です。
  本文中に差し込む画像とは別物なので、本文中の画像は `--body-file` 内の
  `![](path)` 記法で指定してください。
- 本文の最後にまとめて画像を追加したいだけの場合は `--image path.png`(複数指定可)も
  使えます。
- `--publish-at` の代わりに `--publish-now` を指定すると、予約せずすぐに公開します。
- うまく動かない場合はブラウザ画面を見ながら確認できるよう `--headed` を付けてください。
- ログイン情報は `archive_site.py` と同様、環境変数 `BUNSHUN_USERNAME` /
  `BUNSHUN_PASSWORD` か対話入力で渡せます(コマンドライン引数への直書きは非推奨)。

### 既知の制限

- 太字/斜体/下線/引用は `editor.insertHTML()` で確実に反映されます(公開済み記事の
  保存後HTMLで実際の出力形式を確認済み)。ただし単一の `*` や `_` を装飾以外の目的
  (地の文中の記号として)で使いたい場合は、意図せずイタリックの開始/終了と
  解釈されてしまう可能性があります。
- 本文途中の画像挿入は、本文エディタが Trix エディタであることを利用し、
  Trixが公式に提供している `editor.insertFile(file)` API を直接呼び出しています
  (ドラッグ&ドロップ/貼り付けと同じ経路のため、ツールバーのボタンを推測でクリック
  するより確実です)。
- ヘッダー画像(「メイン画像」)は `.editMainImageWrapper` クリック→
  「画像をアップロード」クリックの2段階操作までは実際の構造を確認済みです。
  ただしその後に出る可能性のあるクロップ(トリミング)確認モーダルの確定ボタンの
  文言(`HEADER_IMAGE_CROP_CONFIRM_TEXT`)は未確認のため推測です。見つからない場合は
  警告を出して処理を続けるので、投稿完了後にヘッダー画像が意図通り設定されたか
  手動でご確認ください。
- 見出し(`#`)・箇条書き・番号リストなど、上記以外のMarkdown記法はサポートして
  いません(そのまま文字として入力されます)。
- 予約投稿の切り替え(`#editContents_reservation`)・日時入力
  (`#reservation_post_time`)・送信ボタン(`.subHeader__buttons button.btnFill--medium`)
  は実際のHTML構造から確認済みのセレクタです。

うまくいかない箇所があれば、失敗時に自動保存される `error_form.html` / `error_form.png`
(`--inspect-out` で指定したフォルダ、既定は `archive/_new_post_inspect/`)を
見せてもらえれば、実際の構造に合わせて調整します。

## 利用上の注意

- このツールは**個人的な保存(私的複製)**を目的としています。取得した記事や画像を
  再配布・再公開しないでください。
- サーバーに負荷をかけないよう、デフォルトでリクエスト間に1.5秒の間隔を空けています
  (`--delay` で調整可能)。大量ページを一気に取得するのは避けてください。
- サイトの利用規約を確認の上、自己責任でご利用ください。
