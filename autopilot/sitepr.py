"""free-hp-site へ見本を出す: ブランチを切って PR を開く・（フラグがあれば）マージ・公開の確認。

鍵: FREEHP_SITE_TOKEN（free-hp-site に contents と pull requests の書き込みができる fine-grained token）。
自動マージは既定でしない（AUTO_MERGE=1 のときだけ squash でマージする）。
"""
import subprocess
import time
import urllib.error
import urllib.request

from . import github
from .common import load_config, log, redact

TOKEN_ENV = "FREEHP_SITE_TOKEN"
LIVE_WAIT_SEC = 300
LIVE_POLL_SEC = 20


def _git(site_dir, *args):
    proc = subprocess.run(["git", "-C", str(site_dir), *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(redact(f"git {args[0]} 失敗: {(proc.stderr or proc.stdout)[-300:]}"))
    return proc.stdout.strip()


def open_pr(site_dir, slug, title, body, token):
    repo = load_config()["intake"]["site_repo"]
    publish_dir = load_config()["intake"]["publish_dir"]
    branch = f"autopilot/r-{slug}"
    _git(site_dir, "checkout", "-B", branch)
    _git(site_dir, "add", f"{publish_dir}/{slug}/index.html")
    _git(site_dir, "-c", "user.name=freehp-autopilot", "-c", "user.email=freehp-autopilot@users.noreply.github.com",
         "commit", "-m", title)
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    _git(site_dir, "push", "--force", remote, f"HEAD:refs/heads/{branch}")
    _git(site_dir, "checkout", "-")
    pr = github.api("POST", f"/repos/{repo}/pulls", {"title": title, "head": branch, "base": "main", "body": body}, token_env=TOKEN_ENV)
    log(f"  PR を開きました: {pr['html_url']}")
    return pr["number"], pr["html_url"]


def merge_pr(number):
    repo = load_config()["intake"]["site_repo"]
    github.api("PUT", f"/repos/{repo}/pulls/{number}/merge", {"merge_method": "squash"}, token_env=TOKEN_ENV)


def is_merged(number):
    repo = load_config()["intake"]["site_repo"]
    return bool(github.api("GET", f"/repos/{repo}/pulls/{number}", token_env=TOKEN_ENV).get("merged"))


def is_live(url):
    req = urllib.request.Request(url, headers={"User-Agent": "freehp-autopilot"}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def wait_live(url):
    deadline = time.time() + LIVE_WAIT_SEC
    while time.time() < deadline:
        if is_live(url):
            return True
        time.sleep(LIVE_POLL_SEC)
    return False
