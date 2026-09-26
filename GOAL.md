完了条件: 下の全項目にチェックが入り、@freehp3000 が Mac なしで毎日投稿・要望を受けて見本を作り直す状態になり、本人に5行以内＋画像で報告済み。

# FreeHP オートパイロット（2026-09-27 本人指示・irai R2125）

本人の言葉: 「AIが24時間365日みんなを幸せにしてくれ」「毎日めっちゃ投稿」「HPに使えそうな情報をどんどん収集してナレッジを作る仕組みから」「相手からの要望を受けたら作り直す仕組みもPCがなくても動く」「kaeruのGitHubで常に」「ClaudeのAPIを使ってもいい」「品質が高いもの・喜ばれるものを。120点」「3000円ホームページ」→（04:58更新）「制作0円・運用費として年間3,000円」「年3,000円以上払いたい人は払える、ドネーションでもいい」「プロフィールとか全部お任せ」。

## チェックリスト（正本）
- [x] G0 @freehp3000 の表示名・自己紹介・サイト欄・アイコン・ヘッダー（Codex・実Chrome Profile 8。スクショ ~/dev/2026-09-25-freehp-parts-x/shots/codex_freehp3000_brand.png）
- [x] G1 部品8種カタログ公開 https://freehp.jp/mihon/parts/ （本番検証PASS）
- [x] G2 （G12 に置き換え。04:58 に料金が「制作0円・運用費 年3,000円」へ再変更されたため）
- [ ] G3 （進捗: 決済定義を新料金に書き換え済み d63d1e1・テスト7件OK。残り=本人がStripe鍵を ~/.freehp-stripe/.env に再設置→stripe_setup.py→apply_links.py）支払い導線: 3,000円の支払いリンクと任意の応援リンクを用意（既存の Stripe 経路 memory reference_freehp_stripe_pipeline を確認。本番の決済設定は本人に1行確認してから）
- [ ] G4 Ryoseiimai/freehp-autopilot の4ワークフロー（collect/post/intake-rebuild/report）が dry-run で全 success（autopilot-builder）
- [ ] G5 （進捗: X鍵=~/.x_api_tokens_horiemonbiz.zsh が @freehp3000 と照合済み・Jev鍵あり→builderがrepo作成後に投入。Claude鍵は本人承認1クリック待ち。Stripe鍵 ~/.freehp-stripe/.env が消えている＝G3で本人に再設置を依頼）secrets 投入: CLAUDE_CODE_OAUTH_TOKEN（setup-token・Profile 10）／TYPESAFE_API_KEY／X API（@freehp3000 の認可トークン。旧 @horiemonAI_biz 用に @ryoseichan3160 の開発者アプリで認可済みの可能性）
- [ ] G6 X API のクレジット残高を確認し、不足なら本人にチャージを1行依頼（楽天デビット・理由と月額目安つき）
- [ ] G7 DRY_RUN=0 で本番化し、初日の投稿を司令塔が全件検品（誤り・禁止語・重複なし）
- [ ] G8 要望→作り直しの実地テスト（テスト用 Issue→見本生成→検品→PR）
- [ ] G9 翌朝8時の報告が届くことを確認
- [x] G11 （完了: https://claude.ai/artifact/UprYv6GXdsgyoSPFTYm5kb 1件あたり残り約2,747円・損益分岐29件・月100万円に約4,627件）採算計算（本人 04:58「世界中のHPを制作0円で作って運用費年3,000円。採算がとれるか計算して。薄利多売」）: 原価データ収集（unit-cost-researcher）→ 1サイトあたり原価・損益分岐・規模別シナリオ（1千/1万/10万/100万サイト）→ 見える化ページ（5行＋グラフ＋リンク）
- [ ] G12 料金表記を「制作0円・運用費 年3,000円（税込）・ドメイン原価・任意の応援」に改定（price-builder に指示済み）→ G11 で採算が取れると確認してから公開
- [ ] G10 本人へ5行以内＋画像で報告・Daily Note 追記・irai done R2125/R2118

## 仕組み（4本の定期ジョブ）
1. collect（6時間ごと）: HPづくりに使える一次情報（Google 検索セントラル・web.dev・中小企業庁/補助金・景表法/特商法の公式・業種ごとの集客）→ Claude で要約 → `kb/<分類>/<日付>-<slug>.md`（出典URL必須・重複除外）
2. post（1日6〜10本・8〜22時）: kb と部品・見本から投稿 → 品質の関所 → X。URL付きは1日1本まで
3. intake → rebuild（15分ごと）: Issue フォーム・@freehp3000 メンション → 見本を作り直し → 自動検品 → freehp.jp/mihon/r/<slug>/ → 返信（上限あり）
4. report（毎朝8時）: 前日の結果を5行＋リンクで本人へ

## 品質の関所（120点）
- Claude の採点（100点満点で90点未満は出さない）＋ Jev の Yes/No（「読んだ人が得をするか」「誤りや誇張がないか」）
- 禁止: 「無料」「タダ」「業界最安」「必ず」「誰でも」、実在の他社・個人名、根拠のない数字、直近30日と似た投稿、DM
- 事実は出典URLを持つものだけ

## 安全弁
- repo の `STOP` があれば全停止／1日の投稿・返信上限／同じ相手への返信は1日1回
- 失敗は Issue に自動起票、3回連続失敗で停止＋報告
