"""品質の関所。X に出す文章（投稿・返信）は全部ここを通す。

順番: ①機械の決まり（禁止語・数字・URL・長さ・似すぎ）→ ②Claude の10項目採点（90点未満は落とす）
→ ③Jev の Yes/No 2問（読んだ人が得をするか／誤りや誇張がないか）。どれかで落ちたら出さない。
"""
import re
import unicodedata

from . import judge, llm, xapi
from .common import log

NUM_RE = re.compile(r"[0-9][0-9,.]*")
MENTION_RE = re.compile(r"@[A-Za-z0-9_]{1,15}")
ZERO_YEN_RE = re.compile(r"(?<![0-9,])0円")
ITEM_FLOOR = 7

RUBRIC = [
    "店主が読んで今日すぐ使える具体的な中身がある",
    "事実として書いたことは材料にあり、誤りがない（店主への提案は材料から素直に言える範囲ならよい）",
    "誇張・煽り・根拠のない断定がない",
    "専門用語を使わず、短い文で読みやすい",
    "話が1つに絞れている",
    "直近の投稿と話も言い回しも重ならない",
    "売り込みが強すぎず、3000円ホームページの落ち着いた語り口に合う",
    "1行目で読む理由がわかる",
    "画像や URL の使い方が中身に合っている（無いなら無いで自然）",
    "禁止事項（無料・タダ・業界最安・必ず・誰でも・DM への誘導・競合や実在の店や個人の名前）を守っている",
]

JEV_QUESTIONS = {
    "benefit": "この投稿を読んだ小さなお店の店主は得をするか（今日すぐ役立つ知識や行動が得られるか）",
    "accurate": "この投稿には誤り・誇張・根拠のない断定が含まれていないか（含まれていなければ Yes）",
}


def normalize(text):
    return unicodedata.normalize("NFKC", text or "")


def numbers_in(text):
    text = xapi.URL_RE.sub(" ", normalize(text))
    return {m.group(0).replace(",", "").rstrip(".") for m in NUM_RE.finditer(text)}


def hard_problems(text, material_text, cfg, url_allowed):
    """機械で判定できる決まりを確かめ、問題のリストを返す（空なら合格）。"""
    q = cfg["quality"]
    problems = []
    norm = normalize(text)
    for word in q["banned_words"]:
        w = normalize(word)
        if w.isascii():
            if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", norm, re.I):
                problems.append(f"禁止語「{word}」")
        elif w in norm:
            problems.append(f"禁止語「{word}」")
    if ZERO_YEN_RE.search(norm):
        problems.append("「0円」は無料と同じ意味になるので使わない")
    if MENTION_RE.search(xapi.URL_RE.sub(" ", norm)):
        problems.append("@ で人に呼びかけている")

    urls = xapi.URL_RE.findall(norm)
    if len(urls) > 1:
        problems.append("URL が2つ以上ある")
    if urls and not url_allowed:
        problems.append("今日はもう URL 付きを出したので URL は付けられない")
    for u in urls:
        host = re.sub(r"^https?://", "", u).split("/")[0].lower()
        if host not in q["allowed_url_hosts"]:
            problems.append(f"許可していない URL の行き先: {host}")

    allowed = numbers_in(material_text) | {n.replace(",", "") for n in q["always_allowed_numbers"]}
    for n in numbers_in(norm):
        try:
            small = float(n) <= q["small_number_max"]
        except ValueError:
            small = False
        if not small and n not in allowed:
            problems.append(f"材料に無い数字「{n}」")

    length = xapi.weighted_length(text)
    if length > xapi.MAX_WEIGHTED_LENGTH:
        problems.append(f"長すぎる（X の数え方で {length}/280）")
    if not text.strip():
        problems.append("空")
    return problems


def ngrams(text, n=3):
    t = re.sub(r"\s+", "", normalize(xapi.URL_RE.sub("", text)))
    return {t[i:i + n] for i in range(max(len(t) - n + 1, 1))}


def similarity(a, b):
    ga, gb = ngrams(a), ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def most_similar(text, recent_texts):
    best = (0.0, "")
    for r in recent_texts:
        s = similarity(text, r)
        if s > best[0]:
            best = (s, r)
    return best


def llm_scores(candidates, material_text, recent_texts):
    """Claude に10項目×10点で採点させる。戻り値は候補と同じ順の dict のリスト。"""
    listing = "\n".join(f"[{i}] {c['text']}" for i, c in enumerate(candidates))
    rubric = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(RUBRIC))
    recent = "\n".join(f"- {t}" for t in recent_texts[-15:]) or "（まだ無し）"
    prompt = (
        "X に出す投稿の候補を採点してください。読むのは小さなお店の店主です。\n"
        f"採点項目（各0〜10点、合計100点）:\n{rubric}\n\n"
        f"材料（事実として使ってよいのはここに書いてあることだけ）:\n{material_text}\n\n"
        f"直近の投稿:\n{recent}\n\n候補:\n{listing}\n\n"
        "点の目安: 10＝直す必要がない／9＝好みの範囲の小さな直しで良くなる／7〜8＝直した方がよい問題がある／"
        "6以下＝直さないと出せない（誤り・誇張・役に立たない・伝わらない）。甘くせず、この目安どおりに付けてください。\n"
        "problems には8点以下にした項目の理由を書きます（9点の好みの範囲の指摘は書かない）。\n"
        "競合のホームページ制作サービス名・実在の店名・個人名が入っていれば real_names を true にします"
        "（出典としての公的機関名や Google などの仕組みの名前は可）。\n"
        '出力は JSON だけ: [{"index": 0, "scores": [10個の整数], "real_names": false, "problems": ["…"]}, ...]'
    )
    parsed = llm.ask_json(prompt, "あなたは SNS 投稿の厳しい編集者です。JSON 以外は出力しません。")
    by_index = {int(p["index"]): p for p in parsed}
    results = []
    for i in range(len(candidates)):
        p = by_index.get(i, {"scores": [0] * len(RUBRIC), "real_names": True, "problems": ["採点が返らなかった"]})
        scores = [int(s) for s in p.get("scores", [])][: len(RUBRIC)]
        results.append({
            "total": sum(scores),
            "scores": scores,
            "real_names": bool(p.get("real_names")),
            "problems": p.get("problems", []),
        })
    return results


def jev_check(text, material_text, min_prob):
    state = {"post": text, "material": material_text[:4000]}
    answers = judge.yes_no(state, JEV_QUESTIONS)
    ok = all(a["prob"] >= min_prob for a in answers.values())
    return ok, answers


def evaluate(candidates, material_text, recent_texts, cfg, url_allowed):
    """候補を全部の関所に通し、(合格した1件 or None, 候補ごとの記録) を返す。"""
    pc = cfg["post"]
    records = []
    alive = []
    for i, c in enumerate(candidates):
        problems = hard_problems(c["text"], material_text, cfg, url_allowed)
        sim, sim_text = most_similar(c["text"], recent_texts)
        if sim >= pc["similarity_max"]:
            problems.append(f"直近の投稿と似すぎ（{sim:.2f}）: {sim_text[:40]}")
        rec = {"index": i, "text": c["text"], "hard_problems": problems, "similarity": round(sim, 3)}
        records.append(rec)
        if not problems:
            alive.append(i)
    if not alive:
        return None, records

    scores = llm_scores([candidates[i] for i in alive], material_text, recent_texts)
    ranked = []
    for i, s in zip(alive, scores):
        records[i]["score"] = s
        low = [RUBRIC[k] for k, v in enumerate(s["scores"]) if v < ITEM_FLOOR]
        if s["real_names"]:
            records[i]["hard_problems"].append("実在の他社・店・個人の名前がある")
        elif low:
            records[i]["hard_problems"].append(f"{ITEM_FLOOR}点未満の項目: {' / '.join(low)}")
        elif s["total"] >= pc["min_score"]:
            ranked.append((s["total"], i))
    for _, i in sorted(ranked, reverse=True):
        ok, answers = jev_check(candidates[i]["text"], material_text, pc["jev_min_prob"])
        records[i]["judge"] = answers
        if ok:
            return candidates[i], records
        log(f"  候補{i}は判定係で落ちました: {answers}")
    return None, records
