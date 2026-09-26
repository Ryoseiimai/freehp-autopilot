"""report（毎朝8時 JST）: 前日の投稿・反応・要望・作り直し・失敗を1枚（5行＋絵＋リンク）にまとめる。

出し先: この repo の Issue「毎朝の報告」へのコメント（無ければ作る）＋ reports/<日付>.md と .svg。
メールは MAIL_SMTP_HOST / MAIL_SMTP_USER / MAIL_SMTP_PASS / MAIL_TO（任意で MAIL_SMTP_PORT、既定465）が
そろっているときだけ送る。
"""
import os
import smtplib
from collections import Counter
from datetime import timedelta
from email.message import EmailMessage

from . import github, kb, xapi
from .common import REPORTS_DIR, STATE_DIR, is_dry_run, load_config, log, main_guard, now_jst, parse_iso, read_json, read_jsonl, summary, JST

WORKFLOWS = ("collect.yml", "post.yml", "intake-rebuild.yml", "report.yml")
BAR_UNIT = 1
BAR_MAX = 20
SVG_W, SVG_ROW, SVG_LABEL_W = 560, 34, 150
DEFAULT_SMTP_PORT = 465


def day_of(ts):
    return parse_iso(ts).astimezone(JST).strftime("%Y-%m-%d")


def post_stats(day):
    rows = [r for r in read_jsonl(STATE_DIR / "posts.jsonl") if r.get("date") == day]
    by_mode = Counter(r["mode"] for r in rows)
    types = Counter(r["type"] for r in rows if r["mode"] != "rejected")
    urls = sum(1 for r in rows if r.get("url") and r["mode"] != "rejected")
    return rows, by_mode, types, urls


def reactions(rows):
    ids = [r["tweet_id"] for r in rows if r.get("mode") == "posted" and r.get("tweet_id")]
    if not ids:
        return None
    if not xapi.credentials_present():
        return "X の鍵が未設定なので反応は取得していません"
    got = xapi.metrics(ids)
    total = Counter()
    for m in got.values():
        total.update({k: v for k, v in m.items() if isinstance(v, int)})
    return total


def request_stats(day):
    events = Counter()
    for req in read_json(STATE_DIR / "requests.json", {}).values():
        for h in req.get("history", []):
            if day_of(h["ts"]) == day:
                events[h["status"]] += 1
    return events


def kb_stats(day):
    entries = kb.load_all()
    new = sum(1 for e in entries if e.meta.get("collected_at", "").startswith(day))
    stale = sum(1 for e in entries if not e.current)
    return new, len(entries) - stale, stale


def run_stats(day):
    if not github.available():
        return None, []
    fails = Counter()
    for wf in WORKFLOWS:
        try:
            runs = github.workflow_runs(wf, per_page=100)
        except github.GitHubError as e:
            log(f"  {wf} の履歴を読めません: {e}")
            continue
        for run in runs:
            if day_of(run["created_at"]) == day and run.get("conclusion") == "failure":
                fails[wf.replace(".yml", "")] += 1
    open_issues = github.list_issues(load_config()["failures"]["label"])
    return fails, open_issues


def bar(n):
    return "█" * min(n // BAR_UNIT, BAR_MAX) + (f" {n}" if n else " 0")


def svg_chart(day, items):
    height = SVG_ROW * len(items) + 50
    top = max([v for _, v in items] + [1])
    rows = []
    for i, (label, value) in enumerate(items):
        y = 40 + i * SVG_ROW
        w = int((SVG_W - SVG_LABEL_W - 60) * value / top)
        rows.append(
            f'<text x="12" y="{y + 16}" font-size="14" fill="#2A2C30">{label}</text>'
            f'<rect x="{SVG_LABEL_W}" y="{y + 2}" width="{w}" height="18" fill="#111114"/>'
            f'<text x="{SVG_LABEL_W + w + 8}" y="{y + 16}" font-size="14" fill="#111114">{value}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{height}" viewBox="0 0 {SVG_W} {height}" '
        f'font-family="Noto Sans JP, Hiragino Sans, sans-serif"><rect width="100%" height="100%" fill="#FFFFFF"/>'
        f'<text x="12" y="26" font-size="16" font-weight="700" fill="#111114">{day} のオートパイロット</text>'
        + "".join(rows) + "</svg>\n"
    )


def build(day):
    cfg = load_config()
    rows, by_mode, types, urls = post_stats(day)
    react = reactions(rows)
    req = request_stats(day)
    kb_new, kb_live, kb_stale = kb_stats(day)
    fails, open_issues = run_stats(day)
    mode_note = "（DRY_RUN 中＝予定文だけ）" if is_dry_run() else ""
    type_text = "・".join(f"{t}{n}" for t, n in types.most_common()) or "なし"
    if react is None:
        react_text = "本番の投稿が無いので反応はまだありません"
    elif isinstance(react, str):
        react_text = react
    else:
        react_text = f"いいね {react['like_count']}・リポスト {react['retweet_count']}・返信 {react['reply_count']}・表示 {react.get('impression_count', 0)}"
    fail_text = "なし" if not fails else "・".join(f"{k} {v}回" for k, v in fails.items())
    if fails is None:
        fail_text = "（GitHub の履歴を読めない環境）"
    lines = [
        f"1. 投稿: 本番 {by_mode['posted']}本・予定文 {by_mode['dry_run']}本・見送り {by_mode['rejected']}本{mode_note}（URL付き {urls}本／型 {type_text}）",
        f"2. 反応: {react_text}",
        f"3. 要望: 受付 {req['new']}・作成 {req['built'] + req['pr_open']}・不合格 {req['failed']}・PR {req['pr_open']}・公開 {req['published']}・返事 {req['replied']}",
        f"4. ナレッジ: 新しく {kb_new}件（使える {kb_live}件・古い印 {kb_stale}件）",
        f"5. 失敗: {fail_text}" + (f"（開いている失敗 Issue: {', '.join('#' + str(i['number']) for i in open_issues)}）" if open_issues else ""),
    ]
    chart_items = [("投稿（本番）", by_mode["posted"]), ("投稿（予定文）", by_mode["dry_run"]), ("見送り", by_mode["rejected"]),
                   ("要望の受付", req["new"]), ("ナレッジ追加", kb_new), ("ジョブ失敗", sum((fails or {}).values()))]
    pictures = "\n".join(f"{label:　<7}{bar(v)}" for label, v in chart_items)
    repo = os.environ.get("GITHUB_REPOSITORY", "Ryoseiimai/freehp-autopilot")
    links = [
        f"- 投稿の記録: https://github.com/{repo}/blob/main/state/posts.jsonl",
        f"- ナレッジ目次: https://github.com/{repo}/blob/main/kb/INDEX.md",
        f"- 実行の一覧: https://github.com/{repo}/actions",
        f"- X: https://x.com/{cfg['brand']['x_handle']}",
    ]
    md = (
        f"## {day} の報告\n\n" + "\n".join(lines) + "\n\n```\n" + pictures + "\n```\n\n"
        f"![{day} のグラフ](https://github.com/{repo}/blob/main/reports/{day}.svg?raw=true)\n\n" + "\n".join(links) + "\n"
    )
    return md, svg_chart(day, chart_items)


def send_mail(subject, body):
    needed = ("MAIL_SMTP_HOST", "MAIL_SMTP_USER", "MAIL_SMTP_PASS", "MAIL_TO")
    if not all(os.environ.get(k, "").strip() for k in needed):
        return "メールの設定が無いので送っていません"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["MAIL_SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(body)
    port = int(os.environ.get("MAIL_SMTP_PORT", DEFAULT_SMTP_PORT))
    if port == DEFAULT_SMTP_PORT:
        with smtplib.SMTP_SSL(os.environ["MAIL_SMTP_HOST"], port, timeout=30) as s:
            s.login(os.environ["MAIL_SMTP_USER"], os.environ["MAIL_SMTP_PASS"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(os.environ["MAIL_SMTP_HOST"], port, timeout=30) as s:
            s.starttls()
            s.login(os.environ["MAIL_SMTP_USER"], os.environ["MAIL_SMTP_PASS"])
            s.send_message(msg)
    return f"メールを {os.environ['MAIL_TO'].split('@')[0][:2]}***@… に送りました"


def run():
    cfg = load_config()["report"]
    day = os.environ.get("REPORT_DAY") or (now_jst() - timedelta(days=1)).strftime("%Y-%m-%d")
    md, svg = build(day)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{day}.md").write_text(md, encoding="utf-8")
    (REPORTS_DIR / f"{day}.svg").write_text(svg, encoding="utf-8")
    where = "Issue なし（GH_TOKEN が無い環境）"
    if github.available():
        github.ensure_label(cfg["label"], "0e8a16", "毎朝の報告")
        issue = github.find_open_issue(cfg["issue_title"], cfg["label"]) or github.create_issue(
            cfg["issue_title"], "毎朝8時（日本時間）に前日の結果をコメントで積みます。", [cfg["label"]])
        github.comment(issue["number"], md)
        where = f"Issue #{issue['number']} にコメント"
    mail = send_mail(f"[FreeHP] {day} の報告", md)
    summary(md + f"\n\n- 出し先: {where}\n- {mail}")


if __name__ == "__main__":
    main_guard("report", run)
