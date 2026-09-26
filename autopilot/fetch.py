"""Web から取ってくる: ページ本文の文字化・RSS/RDF/Atom の読み取り・画像のダウンロード。"""
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .common import parse_iso

USER_AGENT = "Mozilla/5.0 (compatible; freehp-autopilot/1.0; +https://freehp.jp/)"
TIMEOUT_SEC = 25
MAX_BYTES = 4_000_000
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "template"}
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "div", "section", "article", "br", "dt", "dd", "table"}


class FetchError(RuntimeError):
    pass


def get(url, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            data = resp.read(MAX_BYTES + 1)
            ctype = resp.headers.get("Content-Type", "")
            final_url = resp.geturl()
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(f"{url} を取得できません: {e}") from e
    if len(data) > MAX_BYTES:
        raise FetchError(f"{url} が大きすぎます")
    return data, ctype, final_url


def decode(data, ctype):
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    if not m:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', data[:4000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main_depth = 0
        self.all_parts = []
        self.main_parts = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        if tag in ("main", "article"):
            self.main_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self._push("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        if tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._push("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self.skip:
            self._push(data)

    def _push(self, s):
        self.all_parts.append(s)
        if self.main_depth:
            self.main_parts.append(s)


def page_text(url, limit=12000):
    """ページの本文を文字にして (題名, 本文) を返す。<main>/<article> があればそこだけ使う。"""
    data, ctype, _ = get(url, "text/html,application/xhtml+xml")
    ex = _TextExtractor()
    ex.feed(decode(data, ctype))
    parts = ex.main_parts if len("".join(ex.main_parts).strip()) > 400 else ex.all_parts
    text = re.sub(r"[ \t　]+", " ", "".join(parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return ex.title.strip(), text[:limit]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(el, name):
    for c in el:
        if _local(c.tag) == name:
            if name == "link" and c.get("href"):
                return c.get("href")
            return (c.text or "").strip()
    return ""


def _parse_date(text):
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        return parse_iso(text)
    except ValueError:
        return None


def feed_items(url):
    """RSS 2.0 / RSS 1.0(RDF) / Atom を読んで [{title, link, published(datetime|None), summary}] を返す。"""
    data, _, _ = get(url, "application/rss+xml,application/xml,text/xml")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FetchError(f"{url} のフィードを読めません: {e}") from e
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = re.sub(r"\s+", " ", _child_text(el, "title"))
        link = _child_text(el, "link") or el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        date_text = _child_text(el, "pubDate") or _child_text(el, "date") or _child_text(el, "published") or _child_text(el, "updated")
        summary = _child_text(el, "description") or _child_text(el, "summary")
        summary = re.sub(r"<[^>]+>", " ", summary)
        if title and link:
            items.append({"title": title, "link": link.strip(), "published": _parse_date(date_text), "summary": re.sub(r"\s+", " ", summary)[:500]})
    return items


def image_bytes(url, allowed_hosts):
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if host not in allowed_hosts:
        raise FetchError(f"画像の取得先 {host} は許可していません")
    data, ctype, _ = get(url, "image/*")
    if not ctype.startswith("image/"):
        raise FetchError(f"{url} は画像ではありません（{ctype}）")
    return ctype.split(";")[0], data
