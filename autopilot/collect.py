"""collect（6時間ごと）: HP づくりに使える一次情報を集め、Claude で要約して kb/ に積む。

- 出典は sources.json の公式サイトだけ（フィードは新着、ページは30日ごとに中身が変わったか確かめる）
- 要約に入れる「出典の言葉」は、原文にその文字列が本当にあるものだけ残す。1つも確かめられなければ捨てる
- 同じ URL・似た題名は重複として捨てる。期限切れ・古いものには【古い】の印を付ける
- Claude の鍵が無いときは、古い印と目次の更新だけして正常終了する
"""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

from . import fetch, gate, kb, llm
from .common import ROOT, STATE_DIR, SkipJob, load_config, log, main_guard, now_jst, read_json, summary, write_json

SEEN_PATH = STATE_DIR / "collect_seen.json"
TITLE_DUP_MIN = 0.6

SYSTEM = (
    "あなたは小さなお店のホームページづくりを手伝う編集者です。"
    "渡された一次情報の本文だけを根拠に、店主に役立つ要点をまとめます。本文に無いことは書きません。JSON 以外は出力しません。"
)


def load_sources():
    return json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))


def due_page(seen_row, recheck_days, now):
    if not seen_row:
        return True
    checked = seen_row.get("checked_at")
    return not checked or datetime.fromisoformat(checked) < now - timedelta(days=recheck_days)


def gather(sources, seen, cfg):
    now = now_jst()
    feeds, pages, errors = [], [], []
    for src in sources:
        if src["kind"] == "page":
            if due_page(seen.get(src["url"]), cfg["page_recheck_days"], now):
                pages.append({"source": src, "url": src["url"], "title": "", "published": None})
            continue
        try:
            items = fetch.feed_items(src["url"])
        except fetch.FetchError as e:
            errors.append(str(e))
            continue
        oldest = datetime.now(timezone.utc) - timedelta(days=cfg["feed_item_max_age_days"])
        for it in items:
            if it["link"] in seen:
                continue
            if it["published"] and it["published"] < oldest:
                continue
            words = src.get("include") or []
            if words and not any(w in it["title"] + it["summary"] for w in words):
                continue
            feeds.append({"source": src, "url": it["link"], "title": it["title"], "published": it["published"]})
    feeds.sort(key=lambda c: c["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    # ページの見直しとフィードの新着を半分ずつ（片方が足りなければもう片方で埋める）
    slots = cfg["max_new_per_run"]
    page_take = min(len(pages), max(math.ceil(slots / 2), slots - len(feeds)))
    return pages[:page_take] + feeds + pages[page_take:], errors


def summarize(cand, text):
    cats = "\n".join(f"- {k}: {v}" for k, v in kb.CATEGORIES.items())
    prompt = (
        f"出典: {cand['source']['publisher']}\nURL: {cand['url']}\n題名: {cand['title']}\n\n"
        f"本文（ここだけを根拠にする）:\n{text}\n\n"
        "この情報を、小さなお店（飲食・美容・整体・小売・教室など）の店主が自分のホームページや集客に"
        "役立てる観点で整理してください。\n"
        f"分類は次のどれか1つ:\n{cats}\n\n"
        "出力は JSON だけ:\n"
        '{"relevance": 0〜10（店主のHPづくり・集客にどれだけ役立つか。関係なければ0）,\n'
        ' "category": "上の分類キー", "title": "日本語の題名（40字以内）", "slug": "英小文字とハイフンの短い名前",\n'
        ' "points": ["要点（日本語・1文ずつ・3〜5個）"], "actions": ["店主が今日できること（1〜3個）"],\n'
        ' "facts": [{"claim": "事実を日本語1文で", "quote": "その claim を直接裏づける、本文からそのまま抜き出した一節（40字以内・一字一句そのまま）"}],\n'
        ' "published": "本文にある公開日・更新日 YYYY-MM-DD（無ければ null）",\n'
        ' "expires": "締切や期限があれば YYYY-MM-DD（無ければ null）", "caution": "誤解しやすい点や注意（無ければ空文字）"}'
    )
    return llm.ask_json(prompt, SYSTEM, max_tokens=3000)


def squash(s):
    return re.sub(r"\s+", "", gate.normalize(s))


def verified_facts(facts, text):
    body = squash(text)
    return [f for f in facts if f.get("quote") and squash(f["quote"]) in body]


def to_body(data, cand, page_title, facts):
    lines = [f"# {data['title']}", "", "## 要点"]
    lines += [f"- {p}" for p in data.get("points", [])]
    lines += ["", "## 店主が今日できること"]
    lines += [f"- {a}" for a in data.get("actions", [])]
    lines += ["", "## 出典の言葉（原文から引用）"]
    lines += [f"- {f['claim']}: 「{f['quote']}」" for f in facts]
    if data.get("caution"):
        lines += ["", "## 注意", data["caution"]]
    title = clean_title(cand["title"] or page_title) or cand["url"]
    lines += ["", f"出典: [{title}]({cand['url']})（{cand['source']['publisher']}）", ""]
    return "\n".join(lines)


def clean_title(title):
    # ページの <title> は「題名 | サイト名 | …」の形が多いので先頭だけ使う
    return re.split(r"\s+[|｜]\s+", title.replace("\n", " ").strip())[0][:120]


def date_or_blank(value):
    return value if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}$", value) else ""


def process(cand, seen, entries, cfg):
    """1件を処理して結果の1行（表に出す文）を返す。LLM を呼んだら True も返す。"""
    url = cand["url"]
    page_title, text = fetch.page_text(url, cfg["text_limit"])
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    prev = seen.get(url, {})
    now = now_jst().isoformat(timespec="seconds")
    if prev.get("hash") == digest:
        prev["checked_at"] = now
        return f"変化なし: {url}", False
    if len(text) < 200:
        seen[url] = {"status": "rejected", "reason": "本文が短すぎる", "hash": digest, "checked_at": now}
        return f"捨てた（本文が短い）: {url}", False

    data = summarize(cand, text)
    facts = verified_facts(data.get("facts", []), text)
    reason = ""
    if int(data.get("relevance", 0)) < cfg["min_relevance"]:
        reason = f"関連が低い（{data.get('relevance')}）"
    elif not facts:
        reason = "引用を原文で確かめられない"
    else:
        for e in entries:
            if e.current and e.meta.get("source_url") != url and gate.similarity(e.meta.get("title", ""), data["title"]) >= TITLE_DUP_MIN:
                reason = f"既存と重複: {e.meta.get('title')}"
                break
    if reason:
        seen[url] = {"status": "rejected", "reason": reason, "hash": digest, "checked_at": now}
        return f"捨てた（{reason}）: {data.get('title') or url}", True

    category = data.get("category") if data.get("category") in kb.CATEGORIES else cand["source"]["category"]
    published = date_or_blank(data.get("published")) or (
        cand["published"].astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d") if cand["published"] else ""
    )
    for e in entries:
        if e.meta.get("source_url") == url and e.current:
            e.meta["status"] = "stale"
            e.meta["stale_reason"] = "出典ページが更新された（新しい要約に置き換え）"
            kb.save(e)
    entry = kb.Entry(
        kb.unique_path(category, data.get("slug", "note")),
        {
            "title": data["title"],
            "category": category,
            "source_url": url,
            "source_title": clean_title(cand["title"] or page_title),
            "publisher": cand["source"]["publisher"],
            "published": published,
            "expires": date_or_blank(data.get("expires")),
            "collected_at": now,
            "checked_at": now[:10],
            "content_hash": digest,
            "relevance": str(data.get("relevance")),
            "status": "current",
            "stale_reason": "",
        },
        to_body(data, cand, page_title, facts),
    )
    kb.save(entry)
    entries.append(entry)
    seen[url] = {"status": "kept", "kb_path": entry.key, "hash": digest, "checked_at": now}
    return f"追加: [{data['title']}]({entry.path.relative_to(ROOT).as_posix()})", True


def run():
    cfg = load_config()["collect"]
    entries = kb.load_all()
    staled = kb.mark_stale(entries, cfg["stale_after_days"])
    if not llm.available():
        kb.write_index(entries)
        raise SkipJob(f"Claude の鍵が未設定なので要約はスキップ（古い印 {staled}件・目次だけ更新）")

    seen = read_json(SEEN_PATH, {})
    candidates, errors = gather(load_sources(), seen, cfg)
    lines, llm_calls, fetch_errors = [], 0, list(errors)
    for cand in candidates:
        if llm_calls >= cfg["max_new_per_run"]:
            break
        try:
            line, called = process(cand, seen, entries, cfg)
        except fetch.FetchError as e:
            fetch_errors.append(str(e))
            if cand["source"]["kind"] == "feed":
                # 記事が取れないものは何度も試さない（ページの出典は次の見直しでまた試す）
                seen[cand["url"]] = {"status": "rejected", "reason": "取得できない", "checked_at": now_jst().isoformat(timespec="seconds")}
            continue
        llm_calls += int(called)
        lines.append(line)
        log(line)
        write_json(SEEN_PATH, seen)
    kb.write_index(entries)
    write_json(SEEN_PATH, seen)

    if fetch_errors and not lines:
        raise RuntimeError("出典をひとつも取得できませんでした: " + " / ".join(fetch_errors[:3]))
    md = [f"### collect（{now_jst():%Y-%m-%d %H:%M} JST）", f"- 候補 {len(candidates)}件・要約 {llm_calls}回・古い印 {staled}件"]
    md += [f"- {l}" for l in lines] or ["- 新しい情報はありませんでした"]
    md += [f"- 取得できなかった出典: {e}" for e in fetch_errors[:5]]
    summary("\n".join(md))


if __name__ == "__main__":
    main_guard("collect", run)
