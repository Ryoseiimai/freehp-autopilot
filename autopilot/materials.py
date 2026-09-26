"""投稿の材料を集める。材料は「key・型・本文（事実の根拠）・URL・画像」の dict にそろえる。

- kb/ の現役の項目（出典URLつき）
- materials/parts.json（部品8種）・mihon.json（見本5件）・photos.json（架空の商店街の写真）
画像は freehp.jp にあるものだけを使う（photos.json の画像も freehp.jp に置いてある同じ写真を指す）。
"""
import json

from . import kb
from .common import MATERIALS_DIR, load_config

KB_TYPES = {
    "tip": ("seo", "web", "shukyaku", "hojokin"),
    "check": ("seo", "web", "shukyaku"),
    "law": ("law",),
}


def _load(name, default):
    path = MATERIALS_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def from_kb(post_type):
    cats = KB_TYPES[post_type]
    rows = []
    for e in kb.load_all():
        if not e.current or e.meta.get("category") not in cats:
            continue
        rows.append({
            "key": e.key,
            "text": f"題名: {e.meta.get('title')}\n出典: {e.meta.get('publisher')} {e.meta.get('source_url')}\n{e.body}",
            "source_url": e.meta.get("source_url"),
            "url": None,
            "image": None,
        })
    return rows


def image_alt(url):
    for img in _load("images.json", []):
        if img["url"] == url:
            return img["alt"]
    return "部品カタログのページの見出し画像" if url.endswith("ogp.png") else ""


def parts():
    rows = []
    for p in _load("parts.json", []):
        rows.append({
            "key": f"part:{p['id']}",
            "text": f"部品「{p['name']}」（こんな要望に: 「{p['need']}」）\n{p['desc']}\nカタログ: {p['url']}\n"
                    "部品はコピーして貼るだけで動く。ライセンスは MIT-0 で、お店のホームページに自由に貼って使える。",
            "source_url": p["url"],
            "url": p["url"],
            "image": p["image"],
            "image_note": f"部品カタログの写真（{image_alt(p['image'])}）",
        })
    return rows


def parts_digest():
    return "\n".join(f"- {p['name']}: 「{p['need']}」" for p in _load("parts.json", []))


def mihon():
    rows = []
    for m in _load("mihon.json", []):
        rows.append({
            "key": f"mihon:{m['slug']}",
            "text": f"見本「{m['title']}」（架空のお店）{m['url']}\nリード: {m['lead']}\n"
                    f"このページだけの工夫: {m['signature']}\nデザインの考え方: {m['design']}\nページの文章: {m['page_text'][:800]}",
            "source_url": m["url"],
            "url": m["url"],
            "image": m["image"],
            "image_note": f"見本ページの写真・AI で作った架空の店のイメージ（{m['image_alt']}）",
        })
    return rows


def photos():
    data = _load("photos.json", {})
    tips = "\n".join(f"- {t}" for t in data.get("tips", []))
    rows = []
    for ph in data.get("photos", []):
        rows.append({
            "key": f"photo:{ph['key']}",
            "text": f"{data['summary']}\nこの写真: {ph['name']}（{ph['industry']}向け）。実写に見せるために指定した粗: "
                    f"{'・'.join(ph['roughness'])}\n作り方のコツ:\n{tips}\n写真の置き場: {data['source_url']}",
            "source_url": data["source_url"],
            "url": data["source_url"],
            "image": ph["image"],
            "image_note": f"架空の商店街の写真「{ph['name']}」（AI で作った架空の店の写真）",
        })
    return rows


def offers():
    brand = load_config()["brand"]
    base = f"{brand['product']}: {brand['price_line']}。サイト {brand['site_url']}"
    rows = [{"key": "offer:site", "text": base, "source_url": brand["site_url"], "url": brand["site_url"], "image": None}]
    catalog = "https://freehp.jp/mihon/parts/"
    rows.append({"key": "offer:parts", "text": base + f"\nHP に貼れる部品8種を並べたカタログ: {catalog}\n" + parts_digest(),
                 "source_url": catalog, "url": catalog, "image": "https://freehp.jp/mihon/parts/ogp.png",
                 "image_note": "部品カタログのページの見出し画像"})
    for m in mihon():
        rows.append({**m, "key": "offer:" + m["key"], "text": base + "\n" + m["text"]})
    for p in photos()[:1]:
        rows.append({**p, "key": "offer:photos", "text": base + "\n" + p["text"]})
    return rows


def for_type(post_type):
    if post_type in KB_TYPES:
        return from_kb(post_type)
    if post_type == "rewrite":
        # 書き換え例は見本の文章を手本にする（画像は付けない）
        return [{**m, "key": "rewrite:" + m["key"], "image": None, "url": None} for m in mihon()]
    return {"part": parts, "mihon": mihon, "photo": photos, "offer": offers}[post_type]()
