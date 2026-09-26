# freehp-autopilot

3000円ホームページ（https://freehp.jp/ ・X @freehp3000）を、Mac が閉じていても GitHub Actions だけで回すオートパイロット。
目的と本人の言葉は `GOAL.md`。ここには「何が・いつ・どう動くか」と「本番にする手順」だけを書く。

## 4本の定期ジョブ

| ワークフロー | いつ | すること | 書き込むもの |
|---|---|---|---|
| `collect.yml` | 6時間ごと（JST 3:07 9:07 15:07 21:07） | `sources.json` の公式サイト（Google 検索セントラル・web.dev・中小企業庁・ミラサポplus・消費者庁・個人情報保護委員会など）から新着と更新を集め、Claude で店主向けに要約。材料（`materials/`）も free-hp-site から作り直す | `kb/<分類>/<日付>-<slug>.md`・`kb/INDEX.md`・`state/collect_seen.json`・`materials/*.json` |
| `post.yml` | 1日8回（JST 8:13〜21:43） | kb・部品・見本・写真素材から投稿を作り、品質の関所を通ったものだけ X へ。既定は DRY_RUN（予定文を記録するだけ） | `state/posts.jsonl` |
| `intake-rebuild.yml` | Issue フォーム・repository_dispatch で即時／15分ごとの巡回は変数で有効化 | 要望 → 見本 HTML を作り直し → 自動検品 → free-hp-site へ PR → 公開されたら返事 | `state/requests.json`・`state/replies.jsonl`・成果物（HTML とスクリーンショット） |
| `report.yml` | 毎朝 8:00 JST | 前日の投稿・反応・要望・作り直し・失敗を5行＋絵＋リンクにまとめ、Issue「毎朝の報告」へコメント（メール設定があればメールも） | `reports/<日付>.md`・`.svg` |

`test.yml` はコードを変えたときだけ単体テストを流す（鍵は使わない）。

## 品質の関所（`autopilot/gate.py`）

X に出す文章は全部ここを通る。どれか1つでも落ちたら出さない。

1. 機械の決まり: 禁止語（`config.json` の `quality.banned_words`・「0円」も不可）、材料に無い数字（10以下の数と 3,000 は可）、@での呼びかけ、URL は1つまで・行き先は freehp.jp と ryoseiimai.github.io だけ・今日すでに URL 付きを出していたら不可、X の長さ（日本語140字）、直近30日の投稿との似すぎ（文字3-gram の Jaccard 0.4 以上）
2. Claude の採点: 10項目×10点で90点未満は不可。1項目でも7点未満なら不可。競合サービス・実在の店・個人の名前があれば不可（出典としての公的機関や Google の仕組みの名前は可）
3. 判定係の Yes/No 2問:「読んだ店主は得をするか」「誤り・誇張・根拠のない断定が無いか」。`TYPESAFE_API_KEY` があれば Jev、無ければ Claude が代わりに答える

書き方は、1回目に通常モデル（claude-sonnet-5）で3案 → 全部落ちたら一番よい案を指摘つきで claude-opus-5-5 に直させる（最大2回）。それでも落ちたら今回は出さずに記録だけ残す。

見本 HTML は `design/DESIGN_RULES.md`（ai-design-brief の5原則・frontend-design・free-hp-site の mihon/ と mihon/parts/ の決まりの要約）を渡して claude-opus-5-5 が作り、`autopilot/sitecheck.py` で検品する:
必須タグ（noindex・viewport・断り書き・フッター）、禁止語、仮の値（000-0000-0000 など）、外部スクリプト・送信フォーム・イベント属性の禁止、使ってよい写真だけか、幅390/1440 の横スクロール、画像の読み込み、コンソールエラー、グラデーション・影・12px 未満の文字・差し色の面積5%未満。最後に Claude が10項目で採点（85点未満・7点未満の項目・依頼に無い事実の作文は不合格）。落ちたら指摘つきで1回だけ作り直す。

## 安全弁

- **STOP**: repo の直下に `STOP` という名前のファイルを置くと、全ジョブが最初の段で止まる（何もせず成功で終わる）
- **DRY_RUN**: 既定は 1。X への投稿・返信、free-hp-site への PR は `DRY_RUN=0` のときだけ
- **アカウント照合**: X に出す直前に毎回 `GET /2/users/me` のハンドルが `freehp3000` かを確かめ、違えば1文字も出さずに失敗にする
- **上限**: 投稿は1日10本・URL 付きは1日1本、作り直しは1日5件、返信は1日10件（URL 付き3件）・同じ相手へは1日1回（`config.json`）
- **失敗**: ジョブが失敗すると Issue「[失敗] <ジョブ名>」に記録（ラベル `failure`）。3回連続で失敗したらそのワークフローを自動で無効化してコメントする。次に成功したら Issue は自動で閉じる
- 投稿（`POST /2/tweets`）は自動で再試行しない（タイムアウトでも X 側では出ていることがあるため）

## 鍵（Settings → Secrets and variables → Actions）

鍵が無い部分は「未設定なのでスキップ」と Summary に出して成功で終わる。

| 名前 | 必須 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` または `CLAUDE_CODE_OAUTH_TOKEN` | どちらか1つ | Claude。API キーなら Messages API を直接、OAuth トークン（`claude setup-token`）なら Claude Code CLI 経由で呼ぶ。API キーのときだけ見本の採点で画面の画像も見る |
| `TYPESAFE_API_KEY` | 任意 | Jev（判定係）。無ければ Claude が代わりに判定 |
| `X_API_KEY` `X_API_SECRET` `X_ACCESS_TOKEN` `X_ACCESS_SECRET` | 本番の投稿に必要 | X API v2（OAuth 1.0a ユーザーコンテキスト・@freehp3000 で認可したもの） |
| `FREEHP_SITE_TOKEN` | 本番の PR に必要 | Ryoseiimai/free-hp-site だけに Contents と Pull requests の書き込み権限を付けた fine-grained token |
| `MAIL_SMTP_HOST` `MAIL_SMTP_USER` `MAIL_SMTP_PASS` `MAIL_TO`（`MAIL_SMTP_PORT` は任意・既定465） | 任意 | 毎朝の報告をメールでも送る |

変数（Variables）:

| 名前 | 既定 | 意味 |
|---|---|---|
| `DRY_RUN` | 1 | 0 で本番（投稿・返信・PR） |
| `AUTO_MERGE` | 0 | 1 で見本の PR を自動マージして公開まで進める |
| `X_MENTIONS_ENABLED` | 0 | 1 で @freehp3000 へのメンションを読んで要望として受け付ける（読み取りも従量課金） |
| `INTAKE_POLL_ENABLED` | 0 | 1 で intake-rebuild を15分ごとに巡回させる（メンションの読み取り・マージ済み PR の公開確認） |

## 本番にする手順（DRY_RUN=0）

1. 鍵を入れる: `ghp secret set ANTHROPIC_API_KEY -R Ryoseiimai/freehp-autopilot`（値は聞かれたら貼る）。X の4つと `FREEHP_SITE_TOKEN` も同じように
2. dry-run で1日回し、`state/posts.jsonl` の予定文と、Issue「毎朝の報告」を読む
3. X の鍵を確かめる（投稿しない）: Actions → post → Run workflow で `check_account` にチェック → ログに「鍵のアカウントは @freehp3000」と出れば OK
4. X の開発者コンソールでクレジット残高を確認（切れていると 402 で失敗する）
5. 本番へ: `ghp variable set DRY_RUN --body 0 -R Ryoseiimai/freehp-autopilot`
6. 要望の作り直しを公開まで自動にするなら `AUTO_MERGE=1`、メンションも受け付けるなら `X_MENTIONS_ENABLED=1` と `INTAKE_POLL_ENABLED=1`

止めるとき: `DRY_RUN` を 1 に戻す（投稿だけ止まる）か、`STOP` ファイルを置く（全部止まる）。1本だけなら `ghp workflow disable post.yml -R Ryoseiimai/freehp-autopilot`。

## 要望の受け口

- この repo の Issue フォーム「見本の作り直しの依頼」（店名・業種・載せたいこと・雰囲気・参考URL）。作り直したいときは Issue に `rebuild` ラベル
- repository_dispatch: `POST https://api.github.com/repos/Ryoseiimai/freehp-autopilot/dispatches` に `{"event_type": "freehp-request", "client_payload": {"shop_name": "", "industry": "", "wants": "", "mood": "", "reference_urls": ""}}`。この repo は private なので外の人は Issue を開けない。freehp.jp の依頼フォーム（Worker）からはこちらを呼ぶ
- X のメンション（`X_MENTIONS_ENABLED=1` のとき）。初回は位置だけ記録し、過去のメンションには返事をしない

見本は `https://freehp.jp/mihon/r/<slug>/` に出る（free-hp-site の `mihon/r/<slug>/index.html`・noindex）。

## Actions の利用時間（private repo の無料枠は月2,000分）

見込み: post 8回×約2分＋collect 4回×約3分＋report 1分 ≒ 1日30分 ≒ 月900分。intake はイベントのときだけ（1回3〜8分）。
`INTAKE_POLL_ENABLED=1` にすると15分ごとに最低1分ずつ動き、それだけで月約2,900分になって無料枠を超える。常時巡回が要るなら repo を public にするか、GitHub の有料プランにする。

## 手元で試す

```bash
python3 -m unittest discover -s tests -v                    # 鍵もネットも使わない
AUTOPILOT_LLM=cli python3 -m autopilot.collect              # ログイン済みの claude -p で要約まで
AUTOPILOT_LLM=cli POST_TYPE=part python3 -m autopilot.post  # DRY_RUN 既定なので投稿はしない
git clone --depth 1 https://github.com/Ryoseiimai/free-hp-site.git .site
AUTOPILOT_LLM=cli SITE_DIR=.site python3 -m autopilot.intake # state/requests.json の new を作り直す
python3 tools/build_materials.py .site                      # 材料を作り直す
```

## 置き場所

- `autopilot/` ジョブ本体（`collect` `post` `intake` `rebuild` `sitecheck` `sitepr` `report` `failures`）と共通部品（`llm` `judge` `gate` `xapi` `fetch` `kb` `materials` `github` `common`）
- `config.json` 上限・禁止語・モデル名など / `sources.json` 集める出典
- `design/DESIGN_RULES.md` 見本づくりのデザインルールの要約（元の文書が変わったら手で直す）
- `materials/` 投稿の材料（parts・mihon・images は collect が自動で作り直す。photos.json は架空の商店街のページを手で要約したもの）
- `kb/` ナレッジ / `state/` 各ジョブの記録 / `reports/` 毎朝の報告

## 意図的に簡略化したところ

- 投稿の型の選び方は「直近3本と重ならない＋画像つきを2倍の重み」の単純な抽選。反応の良し悪しでの重みづけはまだ無い（入口: `post.choose`）
- 要望の参考URLは中身を取りに行かない（依頼の文章に混ざった命令を増やさないため）。取りに行くなら `rebuild.make_brief` に足す
- 見本の採点で画面の画像を見るのは `ANTHROPIC_API_KEY` のときだけ。OAuth トークン（CLI 経路）では HTML から判断する
- メールは SMTP だけ（Gmail ならアプリパスワード）
