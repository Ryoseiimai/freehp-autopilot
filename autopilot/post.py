"""post（1日8回・日本時間8〜22時）: 材料から投稿を作り、品質の関所を通ったものだけを X に出す。

- 型（tip/check/rewrite/law/part/mihon/photo/offer）は直近3本と重ならないように選ぶ。画像を付けられる型を優先
- URL 付きは1日1本まで（url_post_hours の回にだけ offer 型で出す）。URL 付きは X の料金が13倍のため
- 書き方: 1回目は通常のモデルで3案 → 落ちたら一番よい案を指摘つきで難所用のモデルに直させる（最大2回）
- 既定は DRY_RUN=1（投稿予定文をログと Summary に出して state/posts.jsonl に記録するだけ）
- 本番（DRY_RUN=0）では、投稿前に GET /2/users/me のハンドルが freehp3000 かを照合し、違えば1文字も出さず失敗にする
- 手動実行で型を試すときは環境変数 POST_TYPE（例: offer）で型を固定できる
"""
import os
import random
from datetime import timedelta

from . import fetch, gate, llm, materials, xapi
from .common import STATE_DIR, SkipJob, append_jsonl, is_dry_run, jst_date, load_config, log, main_guard, now_jst, parse_iso, read_jsonl, summary

POSTS_PATH = STATE_DIR / "posts.jsonl"
IMAGE_TYPES = {"part", "mihon", "photo", "offer"}
IMAGE_WEIGHT = 2
REVISE_ROUNDS = 2
REVISE_VARIANTS = 2

TYPE_GUIDE = {
    "tip": "一言ノウハウ。材料の要点から1つだけ選び、店主が今日すぐ自分のホームページやお店の情報で試せる形にする。",
    "check": "3点チェック。材料をもとに「自分のHPで確かめること」を短い箇条（・）で3つ並べる。",
    "rewrite": "書き換え例。お店のホームページによくある、ぼんやりした一文を「前」、見本の書き方を手本に具体的に直したものを「後」として並べる。前後とも架空の例で、実在の店の文ではない。",
    "law": "表示の注意。材料（公的機関の一次情報）にあるルールを1つ取り上げ、店主のHPやチラシでやりがちなことに当てはめる。断定は材料に書いてある範囲だけ。",
    "part": "部品の紹介。どんな悩みの店に向くかを先に言い、その部品で何ができるかを1〜2文で。",
    "mihon": "見本の見どころ。架空のお店の見本ページで、その業種だから効いている工夫を1つだけ紹介し、読んだ店主が自分の店に当てはめる一歩を添える。架空の店であることがわかるように書く。",
    "photo": "写真のコツ。HP に載せる写真を AI で作るときや撮るときに効く「粗の指定」などのコツを1つ紹介する。写真は架空の店のものだとわかるように書く。",
    "offer": "案内（URL 付き）。3000円ホームページのこと、または部品・見本・写真素材のページを、押し売りせず紹介する。最後の行に指定の URL をそのまま1つだけ置く。",
}

SYSTEM = (
    "あなたは「3000円ホームページ（@freehp3000）」の中の人です。読むのは小さなお店の店主です。"
    "店主が読んで今日すぐ役立つことを、落ち着いた短い日本語で書きます。売り込みより役に立つことを優先します。JSON 以外は出力しません。"
)


def recent_posts(rows, mode, days):
    since = now_jst() - timedelta(days=days)
    return [r for r in rows if r.get("mode") == mode and parse_iso(r["ts"]) >= since]


def choose(rows, mode, cfg, url_slot, rng):
    """(型, 材料) を選ぶ。材料が無ければ (None, None)。"""
    pc = cfg["post"]
    recent = recent_posts(rows, mode, pc["similarity_days"])
    recent_types = [r["type"] for r in recent[-pc["recent_type_window"]:]]
    cooled = {r["material_key"] for r in recent_posts(rows, mode, pc["material_cooldown_days"])}
    forced = os.environ.get("POST_TYPE", "").strip()
    if forced in pc["types"]:
        types = [forced]
    elif url_slot:
        types = ["offer"]
    else:
        types = [t for t in pc["types"] if t != "offer" and t not in recent_types]
    pool = []
    for t in types:
        mats = [m for m in materials.for_type(t) if m["key"] not in cooled]
        if mats:
            pool.append((t, mats))
    if not pool:
        return None, None
    weights = [IMAGE_WEIGHT if t in IMAGE_TYPES else 1 for t, _ in pool]
    post_type, mats = rng.choices(pool, weights=weights, k=1)[0]
    return post_type, rng.choice(mats)


def material_text(material):
    """採点と生成に渡す材料。添える画像も中身の一部として見せる。"""
    note = f"\n添える画像: {material['image_note']}" if material.get("image") else "\n添える画像: なし（文字だけの投稿）"
    return material["text"] + note


def rules_text(post_type, material, recent_texts, cfg):
    q = cfg["quality"]
    rubric = "\n".join(f"- {r}" for r in gate.RUBRIC)
    url_rule = (
        f"最後の行に次の URL をそのまま1つだけ置く: {material['url']}" if post_type == "offer"
        else "URL は1つも入れない"
    )
    recent = "\n".join(f"- {t}" for t in recent_texts[-12:]) or "（まだ無し）"
    return (
        f"型: {TYPE_GUIDE[post_type]}\n\n材料（事実はここに書いてあることだけ使う）:\n{material_text(material)}\n\n"
        f"直近の投稿（話も言い回しも重ねない）:\n{recent}\n\n"
        "決まり:\n"
        "- 1投稿に話は1つ。全角で60〜130字くらい（X の上限は日本語140字）\n"
        f"- {url_rule}\n"
        f"- 次の言葉は使わない: {'・'.join(q['banned_words'])}・0円\n"
        "- 数字は材料にあるものだけ。材料に無い割合・件数・順位は書かない\n"
        "- 事実として書くことは材料にあることだけ。店主への提案は材料から素直に言える範囲にする\n"
        "- 競合のホームページ制作サービス・実在の店・個人の名前は出さない（出典としての公的機関や Google の仕組みの名前は可）\n"
        "- @ で人に呼びかけない。DM に誘わない。ハッシュタグは付けない。絵文字は使わない\n"
        "- 1行目で「何の話か」と「読む得」がわかるようにする\n"
        "- 専門用語（英語の項目名・技術の名前）は使わず、店主の言葉に言い換える\n\n"
        f"採点の基準（各10点・90点未満は出さない）:\n{rubric}\n"
    )


def generate(post_type, material, recent_texts, cfg):
    prompt = (
        rules_text(post_type, material, recent_texts, cfg)
        + f"\n違う切り口の候補を{cfg['post']['candidates']}つ作ってください。\n"
        + '出力は JSON だけ: [{"text": "投稿文（改行は \\n）", "angle": "切り口をひとことで"}]'
    )
    return _candidates(llm.ask_json(prompt, SYSTEM, kind="default", max_tokens=2000))


def revise(post_type, material, recent_texts, cfg, draft, problems):
    prompt = (
        rules_text(post_type, material, recent_texts, cfg)
        + f"\n次の案は採点で落ちました。\n案:\n{draft}\n\n指摘:\n"
        + "\n".join(f"- {p}" for p in problems)
        + f"\n\n指摘をすべて直した書き直しを{REVISE_VARIANTS}つ作ってください。材料に無いことは足さず、削って直すことを優先します。\n"
        + '出力は JSON だけ: [{"text": "投稿文（改行は \\n）", "angle": "何を直したか"}]'
    )
    return _candidates(llm.ask_json(prompt, SYSTEM, kind="hard", max_tokens=2000))


def _candidates(got):
    return [c for c in (got if isinstance(got, list) else []) if isinstance(c, dict) and c.get("text")]


def best_failed(records):
    """落ちた候補のうち、次に直す元にする1件と、その指摘を返す。"""
    scored = [r for r in records if not r["hard_problems"] and r.get("score")]
    pick = max(scored, key=lambda r: r["score"]["total"]) if scored else records[0]
    problems = pick["hard_problems"] + pick.get("score", {}).get("problems", [])
    judge_notes = [f"判定係: {k} が No 寄り（{v['prob']:.2f}）{v.get('reason') or ''}" for k, v in (pick.get("judge") or {}).items() if not v["yes"]]
    return pick["text"], problems + judge_notes


def write_until_pass(post_type, material, recent_texts, cfg, url_slot):
    mtext = material_text(material)
    records = []
    cands = generate(post_type, material, recent_texts, cfg)
    for round_no in range(REVISE_ROUNDS + 1):
        chosen, recs = gate.evaluate(cands, mtext, recent_texts, cfg, url_allowed=url_slot)
        records += recs
        for r in recs:
            log(f"  [{round_no}] 候補{r['index']}: {r.get('score', {}).get('total', '-')}点 {r['hard_problems'] or ''} {r['text'][:40]}…")
        if chosen or round_no == REVISE_ROUNDS or not recs:
            return chosen, records
        draft, problems = best_failed(recs)
        log(f"  直しに回す: {draft[:40]}… 指摘 {len(problems)}件")
        cands = revise(post_type, material, recent_texts, cfg, draft, problems)
    return None, records


def publish(text, image_url, cfg):
    """本番だけ呼ぶ。ハンドル照合 → 画像アップロード → 投稿。投稿は再試行しない。"""
    xapi.assert_handle(cfg["brand"]["x_handle"])
    media_ids = []
    if image_url:
        _, data = fetch.image_bytes(image_url, cfg["quality"]["allowed_url_hosts"])
        media_ids.append(xapi.upload_image(data))
    return xapi.create_post(text, media_ids)


def run():
    cfg = load_config()
    pc = cfg["post"]
    dry = is_dry_run()
    mode = "dry_run" if dry else "posted"
    label = "dry-run" if dry else "本番"
    now = now_jst()
    if not dry and not (pc["jst_start_hour"] <= now.hour < pc["jst_end_hour"]):
        raise SkipJob(f"投稿の時間外（{now:%H:%M} JST・{pc['jst_start_hour']}〜{pc['jst_end_hour']}時だけ出す）")
    if not dry and not xapi.credentials_present():
        raise SkipJob("X の鍵（X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）が未設定なので投稿はスキップ")

    rows = read_jsonl(POSTS_PATH)
    today = [r for r in rows if r.get("mode") == mode and r.get("date") == jst_date()]
    if len(today) >= pc["max_posts_per_day"]:
        raise SkipJob(f"今日はもう {len(today)}本出したので上限（{pc['max_posts_per_day']}本）")
    url_used = sum(1 for r in today if r.get("url"))
    url_slot = url_used < pc["max_url_posts_per_day"] and now.hour in pc["url_post_hours"]
    if os.environ.get("POST_TYPE", "").strip() == "offer":
        # 手動実行で型を offer に指定したときは、URL の上限だけ守って時刻の条件は外す
        url_slot = url_used < pc["max_url_posts_per_day"]

    rng = random.Random(now.strftime("%Y%m%d%H%M"))
    post_type, material = choose(rows, mode, cfg, url_slot, rng)
    if not material:
        raise SkipJob("使える材料がありません（kb が空か、全部が冷却期間中）")
    log(f"型: {post_type} / 材料: {material['key']}")
    if not llm.available():
        summary(f"### post（{label}）\n- 選んだ型: {post_type}\n- 材料: {material['key']}")
        raise SkipJob("Claude の鍵が未設定なので文章づくりと採点はスキップ")

    recent_texts = [r["text"] for r in recent_posts(rows, mode, pc["similarity_days"])]
    chosen, records = write_until_pass(post_type, material, recent_texts, cfg, url_slot)
    base = {"ts": now.isoformat(timespec="seconds"), "date": jst_date(), "type": post_type, "material_key": material["key"]}
    if not chosen:
        reason = " / ".join(best_failed(records)[1])[:600] if records else "候補が作れなかった"
        append_jsonl(POSTS_PATH, {**base, "mode": "rejected", "text": "", "reason": reason})
        summary(f"### post（{label}）\n- 型 {post_type}・材料 {material['key']}\n- 関所で全部落ちたので今回は出しません: {reason}")
        return

    text = chosen["text"]
    rec = next(r for r in records if r["text"] == text)
    judge_detail = rec.get("judge") or {}
    image = material.get("image")
    tweet_id = publish(text, image, cfg) if not dry else None
    append_jsonl(POSTS_PATH, {
        **base, "mode": mode, "text": text, "image": image, "url": bool(xapi.URL_RE.search(text)), "tweet_id": tweet_id,
        "score": rec["score"]["total"], "judge": {k: round(v["prob"], 3) for k, v in judge_detail.items()},
        "judge_source": next(iter(judge_detail.values()))["source"] if judge_detail else None,
        "source_url": material.get("source_url"),
    })
    head = "本番で投稿しました" if not dry else "DRY_RUN なので投稿していません（予定文）"
    link = f"- https://x.com/{cfg['brand']['x_handle']}/status/{tweet_id}\n" if tweet_id else ""
    probs = {k: round(v["prob"], 2) for k, v in judge_detail.items()}
    summary(
        f"### post: {head}\n- 型 {post_type}・材料 {material['key']}・採点 {rec['score']['total']}点・判定 {probs}\n"
        f"- 画像: {image or 'なし'}\n{link}\n```\n{text}\n```"
    )


if __name__ == "__main__":
    main_guard("post", run)
