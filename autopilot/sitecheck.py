"""見本 HTML の自動検品。

静的な検品（HTML の文字列だけで分かること）: 必須タグ・禁止語・仮の値・外部スクリプト・送信フォーム・使ってよい写真か
実画面の検品（Playwright・幅390/1440）: 横スクロール・画像の読み込み・コンソールエラー・デザイン5原則の機械判定
  （グラデーション・影・12px 未満の文字・差し色の面積5%未満。大きな箱の角丸は注意だけ）
"""
import functools
import http.server
import re
import threading
from pathlib import Path

WIDTHS = (390, 1440)
MIN_FONT_PX = 12
MAX_ACCENT_AREA = 0.05
ALLOWED_SCRIPT_SRC = re.compile(r"^/mihon/parts/[a-z]+/[a-z]+\.js$")
ALLOWED_STYLESHEET = re.compile(r"^(/mihon/parts/[a-z/]+\.css|https://fonts\.googleapis\.com/css2\?.*)$")
ALLOWED_IFRAME = re.compile(r"^https://(www\.)?google\.com/maps")
PLACEHOLDERS = ("000-0000-0000", "example.com", "lorem", "ダミー", "TODO", "xxx", "〇", "○", "□□", "＿＿")
MIHON_BANNED = ("無料", "タダ", "業界最安", "誰でも")
THIRD_PARTY = ("google.com", "gstatic.com", "googleapis.com")


def static_problems(html, allowed_images):
    problems = []
    low = html.lower()
    required = {
        "<!doctype html>": "doctype が無い",
        '<html lang="ja"': 'html lang="ja" が無い',
        'name="viewport"': "viewport が無い",
        'content="noindex"': "noindex が無い",
        'class="notice"': "見本の断り書き（p.notice）が無い",
        "https://freehp.jp/": "フッターの freehp.jp へのリンクが無い",
    }
    problems += [msg for key, msg in required.items() if key.lower() not in low]
    body = re.sub(r"(?s)<!-- budoux:start -->.*?<!-- budoux:end -->", "", html)
    body = re.sub(r"(?s)<!--.*?-->", "", body)
    for word in PLACEHOLDERS:
        if word.lower() in body.lower():
            problems.append(f"仮の値が残っている: {word}")
    for word in MIHON_BANNED:
        if word in body:
            problems.append(f"禁止語: {word}")
    for src in re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)", body, re.I):
        if not ALLOWED_SCRIPT_SRC.match(src):
            problems.append(f"許可していないスクリプト: {src}")
    for href in re.findall(r"<link\b[^>]*rel=[\"']stylesheet[\"'][^>]*href=[\"']([^\"']+)", body, re.I):
        if not ALLOWED_STYLESHEET.match(href.replace("&amp;", "&")):
            problems.append(f"許可していない CSS: {href}")
    for src in re.findall(r"<iframe\b[^>]*\bsrc=[\"']([^\"']+)", body, re.I):
        if not ALLOWED_IFRAME.match(src):
            problems.append(f"許可していない iframe: {src}")
    if re.search(r"<form\b", body, re.I):
        problems.append("送信フォームがある")
    if re.search(r"javascript:|document\.cookie|XMLHttpRequest|fetch\(|localStorage|\beval\(", body, re.I):
        problems.append("使わない JavaScript（通信・保存・eval）がある")
    if re.search(r"\son[a-z]+\s*=", body, re.I):
        problems.append("インラインのイベント属性（onclick など）がある")
    for src in re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", body, re.I):
        if src not in allowed_images:
            problems.append(f"使ってよい写真の一覧に無い画像: {src}")
    return problems


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(root):
    handler = functools.partial(_Quiet, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


PAGE_JS = """(args) => {
  const [minFont] = args;
  const vw = document.documentElement.clientWidth;
  const out = { scroll: document.documentElement.scrollWidth, client: vw, wide: [], broken: [], tiny: [],
                gradients: [], shadows: [], rounded: [], accentArea: 0 };
  const pageArea = Math.max(1, document.documentElement.scrollWidth * document.documentElement.scrollHeight);
  const chroma = (c) => {
    const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return 0;
    const p = m[1].split(',').map(s => parseFloat(s)); if (p.length > 3 && p[3] < 0.5) return 0;
    return (Math.max(p[0], p[1], p[2]) - Math.min(p[0], p[1], p[2])) / 255;
  };
  const label = (el) => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '');
  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = el.getBoundingClientRect();
    if (r.width && r.right > vw + 0.5 && !el.closest('dialog:not([open])')) out.wide.push(label(el));
    if (cs.backgroundImage.includes('gradient')) out.gradients.push(label(el));
    if (cs.boxShadow && cs.boxShadow !== 'none') out.shadows.push(label(el));
    if (r.width > 48 && r.height > 48 && parseFloat(cs.borderTopLeftRadius) > 4) out.rounded.push(label(el));
    if (chroma(cs.backgroundColor) > 0.15) out.accentArea += (r.width * r.height) / pageArea;
    const ownText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (ownText && r.width && parseFloat(cs.fontSize) < minFont && !el.closest('[aria-hidden="true"]')) out.tiny.push(label(el) + ' ' + cs.fontSize);
  });
  document.querySelectorAll('img').forEach(img => { if (!img.complete || !img.naturalWidth) out.broken.push(img.getAttribute('src')); });
  for (const k of ['wide', 'gradients', 'shadows', 'rounded', 'tiny']) out[k] = [...new Set(out[k])].slice(0, 8);
  return out;
}"""


def browser_check(site_root, path, shots_dir):
    """実画面で検品し、(問題, 注意, 撮った画像のパス) を返す。"""
    from playwright.sync_api import sync_playwright

    problems, notes, shots = [], [], []
    shots_dir = Path(shots_dir)
    shots_dir.mkdir(parents=True, exist_ok=True)
    httpd, base = serve(site_root)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors = []
                page.on("console", lambda m: errors.append(m) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(e))
                page.goto(base + path, wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(400)
                got = page.evaluate(PAGE_JS, [MIN_FONT_PX])
                if got["scroll"] > got["client"]:
                    problems.append(f"{width}px で横スクロールが出る（{got['scroll']}>{got['client']}）: {got['wide']}")
                if got["broken"]:
                    problems.append(f"{width}px で読み込めない画像: {got['broken']}")
                if got["gradients"]:
                    problems.append(f"グラデーションを使っている: {got['gradients']}")
                if got["shadows"]:
                    problems.append(f"影を使っている: {got['shadows']}")
                if got["tiny"]:
                    problems.append(f"{width}px で {MIN_FONT_PX}px 未満の文字: {got['tiny']}")
                if got["accentArea"] >= MAX_ACCENT_AREA:
                    problems.append(f"{width}px で差し色の面積が {got['accentArea']:.1%}（5%未満にする）")
                if got["rounded"]:
                    notes.append(f"{width}px で大きな箱に角丸: {got['rounded']}")
                own = [str(getattr(e, "text", e)) for e in errors if not any(d in str(getattr(e, "location", {}) or "") for d in THIRD_PARTY)]
                if own:
                    problems.append(f"{width}px でコンソールエラー: {own[:3]}")
                shot = shots_dir / f"shot-{width}.png"
                page.screenshot(path=str(shot), full_page=True)
                shots.append(shot)
                page.close()
            browser.close()
    finally:
        httpd.shutdown()
    return list(dict.fromkeys(problems)), list(dict.fromkeys(notes)), shots
