"""ナレッジ（kb/<分類>/<日付>-<slug>.md）の読み書き。前付け（---で囲んだ key: value）を持つ Markdown。"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .common import KB_DIR, jst_date

CATEGORIES = {
    "seo": "検索（Google 検索セントラルなど）",
    "web": "表示速度・使いやすさ（web.dev など）",
    "shukyaku": "集客（Google ビジネスプロフィール・業種ごとの知見）",
    "hojokin": "補助金・支援（中小企業庁・ミラサポplus など）",
    "law": "表示のルール（景品表示法・特定商取引法・個人情報）",
    "other": "その他",
}
FRONT_KEYS = [
    "title", "category", "source_url", "source_title", "publisher", "published", "expires",
    "collected_at", "checked_at", "content_hash", "relevance", "status", "stale_reason",
]


@dataclass
class Entry:
    path: Path
    meta: dict
    body: str = field(repr=False)

    @property
    def current(self):
        return self.meta.get("status", "current") == "current"

    @property
    def key(self):
        return "kb:" + self.path.relative_to(KB_DIR).as_posix()


def parse(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ": " in line or line.endswith(":"):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def render(meta, body):
    lines = [f"{k}: {meta.get(k, '')}".rstrip() for k in FRONT_KEYS]
    return "---\n" + "\n".join(lines) + "\n---\n" + body.lstrip("\n")


def load_all():
    entries = []
    for p in sorted(KB_DIR.glob("*/*.md")):
        meta, body = parse(p.read_text(encoding="utf-8"))
        if meta:
            entries.append(Entry(p, meta, body))
    return entries


def save(entry):
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    entry.path.write_text(render(entry.meta, entry.body), encoding="utf-8")


def unique_path(category, slug, day=None):
    day = day or jst_date()
    slug = re.sub(r"[^a-z0-9\-]+", "-", slug.lower()).strip("-")[:60] or "note"
    base = KB_DIR / category / f"{day}-{slug}.md"
    n = 2
    path = base
    while path.exists():
        path = base.with_name(f"{day}-{slug}-{n}.md")
        n += 1
    return path


def mark_stale(entries, stale_after_days, today=None):
    """期限切れ・古すぎる項目に印を付ける。印を付けた件数を返す。"""
    today = today or date.fromisoformat(jst_date())
    changed = 0
    for e in entries:
        if not e.current:
            continue
        reason = ""
        expires = e.meta.get("expires", "")
        published = e.meta.get("published", "")
        if re.match(r"\d{4}-\d{2}-\d{2}$", expires) and date.fromisoformat(expires) < today:
            reason = f"期限（{expires}）を過ぎた"
        elif re.match(r"\d{4}-\d{2}-\d{2}$", published) and date.fromisoformat(published) < today - timedelta(days=stale_after_days):
            reason = f"公開から{stale_after_days}日を超えた"
        if reason:
            e.meta["status"] = "stale"
            e.meta["stale_reason"] = reason
            save(e)
            changed += 1
    return changed


def write_index(entries):
    lines = [
        "# ナレッジ目次（自動生成・手で直さない）",
        "",
        "collect ジョブが6時間ごとに一次情報を集めて要約したもの。【古い】は期限切れ・更新済み・古すぎる印で、投稿の材料には使わない。",
        "",
    ]
    for cat, label in CATEGORIES.items():
        rows = [e for e in entries if e.meta.get("category") == cat]
        if not rows:
            continue
        lines += [f"## {label}", ""]
        for e in sorted(rows, key=lambda x: x.path.name, reverse=True):
            mark = "" if e.current else "【古い】"
            rel = e.path.relative_to(KB_DIR).as_posix()
            lines.append(f"- {mark}[{e.meta.get('title', rel)}]({rel})（{e.meta.get('publisher', '')}・{e.meta.get('published') or '日付不明'}）")
        lines.append("")
    (KB_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
