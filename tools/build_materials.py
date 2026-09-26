#!/usr/bin/env python3
"""free-hp-site の作業コピーから、投稿と見本づくりの材料（materials/*.json）を作り直す。

  python3 tools/build_materials.py <free-hp-site のパス>

出すもの:
  materials/parts.json   部品8種（要望・名前・説明・カタログURL・画像URL）
  materials/mihon.json   見本5件（店名・URL・画像・リード文・signature・デザインの考え方）
  materials/images.json  freehp.jp にある写真の一覧（パス・幅・高さ・代替テキスト）
photos.json（架空の商店街の写真素材）は外部ページの内容を手で要約したもので、ここでは作らない。
"""
import html
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_URL = "https://freehp.jp/"
PART_FALLBACK_IMAGE = "mihon/parts/ogp.png"


def text_of(fragment):
    fragment = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def jpeg_size(path):
    data = path.read_bytes()
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg_len
    return None, None


def alt_map(site):
    alts = {}
    for page in site.glob("mihon/**/index.html"):
        src = page.read_text(encoding="utf-8")
        for m in re.finditer(r'<img\b[^>]*>', src, re.S):
            tag = m.group(0)
            s = re.search(r'\bsrc="([^"]+)"', tag)
            a = re.search(r'\balt="([^"]*)"', tag)
            if s and a and a.group(1):
                full = (page.parent / s.group(1)).resolve().relative_to(site.resolve()).as_posix()
                alts.setdefault(full, html.unescape(a.group(1)))
                # 部品カタログは小さい版（-s）を src に書き、大きい版（-l）は srcset にだけある
                alts.setdefault(re.sub(r"-s\.jpg$", "-l.jpg", full), html.unescape(a.group(1)))
    return alts


def build_images(site):
    alts = alt_map(site)
    rows = []
    for jpg in sorted(site.glob("mihon/**/*.jpg")):
        rel = jpg.relative_to(site).as_posix()
        # 小さい版（-s / -640 / -800）は一覧から外し、大きい版だけ載せる
        if re.search(r"-(s|640|800)\.jpg$", rel):
            continue
        w, h = jpeg_size(jpg)
        rows.append({"path": "/" + rel, "url": SITE_URL + rel, "width": w, "height": h, "alt": alts.get(rel, "")})
    return rows


def build_parts(site):
    src = (site / "mihon/parts/index.html").read_text(encoding="utf-8")
    parts = []
    for m in re.finditer(r'<section class="part" id="(\w+)"(.*?)</section>', src, re.S):
        pid, body = m.group(1), m.group(2)
        head = text_of(body.split('class="stage"')[0].split(">", 1)[-1])
        need = re.search(r"「([^」]+)」", head)
        after_need = head.split("」", 1)[-1].strip()
        name = after_need.split(" ", 1)[0]
        desc = after_need[len(name):].split(" ファイル ")[0].split(" コードをコピー")[0].strip()
        img = re.search(r'src="img/([a-z\-]+)-s\.jpg"', body)
        image = f"mihon/parts/img/{img.group(1)}-l.jpg" if img else PART_FALLBACK_IMAGE
        parts.append({
            "id": pid,
            "need": need.group(1) if need else "",
            "name": name,
            "desc": desc,
            "url": f"{SITE_URL}mihon/parts/#{pid}",
            "image": SITE_URL + image,
        })
    return parts


def build_mihon(site):
    rows = []
    for page in sorted(site.glob("mihon/*/index.html")):
        slug = page.parent.name
        if slug == "parts":
            continue
        src = page.read_text(encoding="utf-8")
        title = re.search(r"<title>([^<]+)</title>", src).group(1)
        comment = re.search(r"<!--(.*?)-->", src, re.S).group(1)
        sig = re.search(r"signature（記憶に残る1要素）(.*?)デザインシステム(.*?)(?:■|$)", comment, re.S)
        img = re.search(r'<img\b[^>]*\bsrc="([^"]+\.jpg)"[^>]*>', src, re.S)
        alt = re.search(r'\balt="([^"]*)"', img.group(0)) if img else None
        body = src.split("<body>", 1)[-1].split("<!-- budoux:start -->")[0]
        leads = [text_of(p) for p in re.findall(r'<p class="lead[^"]*">(.*?)</p>', body, re.S)]
        rows.append({
            "slug": slug,
            "title": title.replace("（架空のお店の見本）", ""),
            "url": f"{SITE_URL}mihon/{slug}/",
            "image": f"{SITE_URL}mihon/{slug}/{img.group(1)}" if img else "",
            "image_alt": html.unescape(alt.group(1)) if alt else "",
            "lead": " ".join(leads)[:300],
            "signature": re.sub(r"\s+", " ", sig.group(1)).strip() if sig else "",
            "design": re.sub(r"\s+", " ", sig.group(2)).strip()[:300] if sig else "",
            "page_text": text_of(body)[:1500],
        })
    return rows


def write(name, data):
    path = ROOT / "materials" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if new != old:
        path.write_text(new, encoding="utf-8")
        print(f"更新: materials/{name}（{len(data)}件）")
    else:
        print(f"変化なし: materials/{name}")


def main():
    if len(sys.argv) != 2:
        sys.exit("使い方: python3 tools/build_materials.py <free-hp-site のパス>")
    site = Path(sys.argv[1])
    write("parts.json", build_parts(site))
    write("mihon.json", build_mihon(site))
    write("images.json", build_images(site))


if __name__ == "__main__":
    main()
