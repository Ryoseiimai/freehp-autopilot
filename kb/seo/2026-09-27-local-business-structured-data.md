---
title: ローカルビジネスの構造化データで営業時間や住所をGoogleに伝える
category: seo
source_url: https://developers.google.com/search/docs/appearance/structured-data/local-business?hl=ja
source_title: ローカル ビジネス（LocalBusiness）の構造化データ
publisher: Google 検索セントラル
published: 2026-09-12
expires:
collected_at: 2026-09-27T05:04:00+09:00
checked_at: 2026-09-27
content_hash: 20c0e78d11a23f9b
relevance: 9
status: current
stale_reason:
---
# ローカルビジネスの構造化データで営業時間や住所をGoogleに伝える

## 要点
- LocalBusiness の構造化データを使うと、営業時間・住所・電話番号・メニューなどをGoogleに正しく伝えられ、検索結果やGoogleマップの表示に役立つ。
- 必須プロパティは address（住所）と name（店名）の2つ。営業時間や電話番号、価格帯、geo（緯度・経度）などは推奨プロパティ。
- 種類は Restaurant、DaySpa、HealthClub などできるだけ具体的なサブタイプを選ぶ。複数のサービスがある場合は配列で指定する。
- 営業時間は openingHoursSpecification で書く。深夜0時をまたぐ営業、24時間営業、終日休業、年末年始などの季節休業も指定できる。
- 書いた後はリッチリザルト テストで検証し、URL検査ツールでGoogleが見られる状態か確認する。掲載されるまで数日かかることもあり、必ず表示されるわけではない。

## 店主が今日できること
- 自店の業種に合う具体的なタイプ（例: Restaurant）を選び、店名・住所・電話番号・営業時間を JSON-LD で書き出す。
- 作った構造化データをリッチリザルト テストに貼って、重大なエラーがないか確認する。
- ホームページの制作会社やCMSの担当者がいれば、この構造化データの追加を依頼する。

## 出典の言葉（原文から引用）
- LocalBusiness の必須プロパティは address（住所）と name（ビジネスの名前）である。: 「ビジネスの物理的な場所。できるだけ多くのプロパティを指定します。」
- 営業時間が深夜0時をまたぐ場合は、1つの OpeningHoursSpecification で開始時間と終了時間を定義する。: 「営業時間が深夜 0 時をまたぐ場合は、1 つの OpeningHoursSpecification プロパティで開始時間と終了時間を定義します。」
- 終日休業する場合は、opens と closes の両方を「00:00」に設定する。: 「終日休業する場合は、opens プロパティと closes プロパティを両方とも「00:00」に設定します。」
- priceRange は100文字未満で指定しないと、価格帯が表示されない。: 「このフィールドは 100 文字未満で指定してください。」
- 電話番号には国コードと市外局番を含める必要がある。: 「電話番号には、必ず国コードと市外局番を含めてください。」
- 構造化データを使ったコンテンツが必ず検索結果に表示されるとは限らない。: 「構造化データを使用するコンテンツが必ず検索結果に表示されるとは限りません。」

## 注意
レストランカルーセルは表示されるレストランが限定されており、利用にはフォームからの登録が必要。また aggregateRating と review は、他のローカルビジネスを収集するサイト向けの推奨で、自店のクチコミを自分で書き込む用途ではない。本文の例は米国の住所・電話番号なので、日本の店では国コード（+81）や日本の住所形式に置き換える必要がある。本文の営業時間の書式説明には hh:mm:ss とあるが、例では hh:mm が使われている。

出典: [ローカル ビジネス（LocalBusiness）の構造化データ](https://developers.google.com/search/docs/appearance/structured-data/local-business?hl=ja)（Google 検索セントラル）
