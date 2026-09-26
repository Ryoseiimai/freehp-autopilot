#!/usr/bin/env python3
"""Claude Code の OAuth トークンを発行し、画面にもログにも出さずに GitHub の secret（CLAUDE_CODE_OAUTH_TOKEN）へ入れる。

  python3 tools/setup_claude_token.py [--repo Ryoseiimai/freehp-autopilot] [--profile "Profile 10"] [--wait 1200]

流れ（memory reference_claude_setup_token_unattended の手順）:
  1. `claude setup-token` を pty で起動する（端末の幅を1000にして URL が折り返さないようにする）
  2. PATH の先頭に「URL をファイルに書くだけの偽 open」を置き、既定のプロファイルで勝手に開かせない
  3. 受け取った URL（承認後に localhost の CLI へ自動で戻る形）を、Chrome の指定プロファイルの新しいウィンドウで開く
  4. 本人が「承認」を1回押す → CLI が受け取って sk-ant-oat01-… を出す → 正規表現で拾う
  5. そのトークンで `claude -p` が動くか試し、動いたら `ghp secret set` の標準入力へ渡す
トークンは標準出力・ファイル・引数のどこにも書かない。進み具合だけを1行ずつ出す。

取り違え対策（2026-09-27 に実際に起きた: 別のセッションの承認画面が押され、こちらは待ったまま時間切れ）:
  - 開いた画面の見分け方（アドレスの localhost の番号と、state の先頭6文字）を最初に出す
  - Chrome の履歴を15秒ごとに読み、localhost の callback が「この CLI の番号と state」で開かれたかを確かめる。
    別の番号の callback が開かれたら「別の承認画面が押された」と出す（こちらはまだ未承認）
  - 貼り付け式（コードを表示してコピーさせる画面）に落ちたときのために、クリップボードを1秒ごとに見て、
    「コード#この CLI の state」の形の文字列が来たらそれだけを CLI に流し込む（本人の操作はコピーだけ）
"""
import argparse
import datetime
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import termios
import time
import urllib.parse
from pathlib import Path

TOKEN_RE = re.compile(rb"sk-ant-oat01-[A-Za-z0-9_\-]+")
URL_RE = re.compile(rb"https://(?:claude\.ai|claude\.com|platform\.claude\.com)/[^\s\x1b\"']+")
ANSI_RE = re.compile(rb"\x1b\[[0-9;?<>=]*[A-Za-z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?")
SUCCESS_RE = re.compile(rb"Long-?lived\s*authentication\s*token\s*created", re.I)
ERROR_RE = re.compile(rb"(OAuth\s*error|Invalid\s*code|Sign-?in\s*timed\s*out|does\s*not\s*permit)[^\n]{0,120}", re.I)
PASTED_CODE_RE = re.compile(r"^\s*([A-Za-z0-9_\-]{16,})#([A-Za-z0-9_\-]{16,})\s*$")
CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
CHROME_EPOCH = datetime.datetime(1601, 1, 1)

TERM_ROWS, TERM_COLS = 50, 1000
READ_CHUNK = 65536
POLL_SEC = 0.5
TOKEN_SETTLE_SEC = 1.0
STOP_WAIT_SEC = 3.0
HISTORY_EVERY_SEC = 15
CLIPBOARD_EVERY_SEC = 1
SUCCESS_WITHOUT_TOKEN_SEC = 8
VERIFY_TIMEOUT_SEC = 120
VERIFY_MODEL = "claude-sonnet-5"
FAKE_OPEN = '#!/bin/sh\nfor a in "$@"; do case "$a" in http*) printf "%s\\n" "$a" >> "$URL_FILE";; esac; done\n'


def say(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def find_bin(name):
    for cand in (str(Path.home() / f".local/bin/{name}"), shutil.which(name)):
        if cand and Path(cand).exists():
            return cand
    sys.exit(f"{name} が見つかりません（~/.local/bin を確認）")


def url_identity(url):
    """URL から (localhost の番号, state) を取り出す。"""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    redirect = urllib.parse.urlparse(q.get("redirect_uri", [""])[0])
    return (redirect.port if redirect.hostname == "localhost" else None), q.get("state", [""])[0]


def open_in_chrome(url, profile):
    # 新しいウィンドウで開き、Chrome を最前面にする（Chrome を終了させる操作はしない）
    subprocess.run(["/usr/bin/open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", "--new-window", url], check=True)
    subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True)


def profile_label(profile_dir):
    try:
        import json
        info = json.loads((CHROME_DIR / "Local State").read_text())["profile"]["info_cache"].get(profile_dir, {})
        return f"{profile_dir}（{info.get('user_name', '?')}）"
    except (OSError, ValueError, KeyError):
        return profile_dir


def callback_visits(since):
    """全プロファイルの履歴から、since 以降に開かれた localhost の callback を (プロファイル, 番号, state) で返す。"""
    since_us = int((since - CHROME_EPOCH).total_seconds() * 1e6)
    found = []
    for hist in CHROME_DIR.glob("*/History"):
        try:
            con = sqlite3.connect(f"file:{hist}?mode=ro&immutable=1", uri=True)
            rows = con.execute(
                "select u.url from visits v join urls u on u.id = v.url "
                "where v.visit_time > ? and u.url like 'http://localhost:%/callback%'", (since_us,)).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for (url,) in rows:
            parsed = urllib.parse.urlparse(url)
            state = urllib.parse.parse_qs(parsed.query).get("state", [""])[0]
            found.append((hist.parent.name, parsed.port, state))
    return found


def clipboard():
    try:
        return subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def stop_child(pid, fd):
    """claude を確実に止める。先に pty を閉じる（読まれない端末への書き込みで固まって SIGTERM が効かないことがあるため）。"""
    try:
        os.close(fd)
    except OSError:
        pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        waited = 0.0
        while waited < STOP_WAIT_SEC:
            try:
                done, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if done:
                return
            time.sleep(POLL_SEC)
            waited += POLL_SEC


def verify_token(claude, token):
    """そのトークンで claude -p が1回答えられるかを試す。トークンは環境変数でだけ渡す。"""
    env = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=token)
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [claude, "-p", "--model", VERIFY_MODEL, "--tools", "", "--output-format", "json", "--no-session-persistence",
           "--setting-sources", "project", "--strict-mcp-config"]
    try:
        proc = subprocess.run(cmd, input="「OK」とだけ返してください。", capture_output=True, text=True,
                              timeout=VERIFY_TIMEOUT_SEC, env=env, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0 and '"is_error":false' in proc.stdout.replace(" ", "")


def set_secret(ghp, repo, token):
    proc = subprocess.run([ghp, "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", repo],
                          input=token, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        # gh のエラー文にトークンは含まれないが、念のため伏せてから出す
        err = TOKEN_RE.sub(b"[REDACTED]", (proc.stderr or proc.stdout).encode()).decode(errors="replace")
        sys.exit(f"secret の設定に失敗しました: {err[:300]}")


def spawn(claude, env):
    pid, fd = pty.fork()
    if pid == 0:
        # exec の前に端末の幅を決める（親が後から変えると、起動時の幅で描かれて URL やトークンが折り返すことがある）
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", TERM_ROWS, TERM_COLS, 0, 0))
        os.execve(claude, [claude, "setup-token"], env)
    return pid, fd


def run(args):
    ghp, claude = find_bin("ghp"), find_bin("claude")
    work = Path(tempfile.mkdtemp(prefix="setup-token-"))
    url_file = work / "url.txt"
    fake_open = work / "open"
    fake_open.write_text(FAKE_OPEN)
    fake_open.chmod(0o700)
    env = dict(os.environ, URL_FILE=str(url_file), PATH=f"{work}:{os.environ.get('PATH', '')}",
               BROWSER=str(fake_open), COLUMNS=str(TERM_COLS), LINES=str(TERM_ROWS))
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    others = subprocess.run(["/usr/bin/pgrep", "-f", "claude setup-token"], capture_output=True, text=True).stdout.split()
    if others:
        say(f"注意: ほかに claude setup-token が {len(others)}本動いています（承認画面の取り違えのもと。終わってから押してください）")
    started = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    pid, fd = spawn(claude, env)
    say("claude setup-token を起動しました")
    debug = open(args.debug_log, "ab") if args.debug_log else None

    buf, token = b"", None
    port = state = None
    opened = pasted = approved = manual_seen = False
    states = set()
    warned_ports, reported_errors = set(), set()
    token_seen_at = success_at = None
    next_history = next_clip = 0.0
    deadline = time.time() + args.wait
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], POLL_SEC)
            if ready:
                try:
                    chunk = os.read(fd, READ_CHUNK)
                except OSError:
                    chunk = b""
                if not chunk:
                    m = TOKEN_RE.search(ANSI_RE.sub(b"", buf))
                    token = m.group(0).decode() if m else None
                    say("claude setup-token が終了しました")
                    break
                buf += chunk
                if debug:
                    debug.write(TOKEN_RE.sub(b"[REDACTED]", chunk))
                    debug.flush()
            clean = ANSI_RE.sub(b"", buf)
            now = time.time()

            if not opened:
                auto = url_file.read_text().split()[0] if url_file.exists() and url_file.read_text().strip() else None
                if auto:
                    port, state = url_identity(auto)
                    states.add(state)
                    kind = f"localhost:{port} へ自動で戻る形" if port else "コードを表示して貼らせる形"
                    say(f"承認画面の URL を受け取りました（{kind}・state {state[:6]}…）")
                    if args.no_open:
                        say("  --no-open なので開いていません")
                    else:
                        open_in_chrome(auto, args.profile)
                        say(f"承認画面を開きました: Chrome {profile_label(args.profile)} の新しいウィンドウ。"
                            f"アドレスに localhost%3A{port} が入っている画面の「承認」を押してください（最大{args.wait // 60}分待ちます）")
                    opened = True

            if not manual_seen:
                manual = [u.decode() for u in URL_RE.findall(clean) if b"localhost" not in u and b"state=" in u]
                if manual:
                    manual_seen = True
                    _, mstate = url_identity(manual[0])
                    states.add(mstate)
                    form = "貼り付け式" if "oauth/code/callback" in urllib.parse.unquote(manual[0]) else "別の形"
                    say(f"  画面に出ている予備の URL は{form}（state {mstate[:6]}…・こちらは開きません）")

            if opened and not args.no_open and not approved and now >= next_history:
                next_history = now + HISTORY_EVERY_SEC
                for prof, p, st in callback_visits(started):
                    if p == port and st == state:
                        approved = True
                        say(f"承認を確認しました（{profile_label(prof)}・この CLI の画面）。トークンの受け取りを待っています")
                    elif p not in warned_ports:
                        warned_ports.add(p)
                        say(f"注意: 別の承認画面（{profile_label(prof)}・localhost:{p}）が押されました。"
                            f"こちら（{profile_label(args.profile)}・localhost:{port}）はまだ未承認です")

            if states and not pasted and now >= next_clip:
                next_clip = now + CLIPBOARD_EVERY_SEC
                m = PASTED_CODE_RE.match(clipboard())
                if m and m.group(2) in states:
                    os.write(fd, m.group(0).strip().encode() + b"\r")
                    pasted = True
                    say("クリップボードのコード（この CLI の state と一致）を CLI に貼りました")

            for err in ERROR_RE.findall(clean):
                text = err.decode(errors="replace")
                if text not in reported_errors:
                    reported_errors.add(text)
                    say(f"CLI の表示: {text}")

            if success_at is None and SUCCESS_RE.search(clean):
                success_at = now
                say("CLI がトークンの発行に成功したと表示しました")
            m = TOKEN_RE.search(clean)
            if m:
                # 最後まで出きってから拾う（途中で切れた文字列を拾わないため）
                if token_seen_at is None:
                    token_seen_at = now
                elif now - token_seen_at >= TOKEN_SETTLE_SEC:
                    token = m.group(0).decode()
                    break
            elif success_at and now - success_at > SUCCESS_WITHOUT_TOKEN_SEC:
                say("成功の表示は出たのにトークンの文字列を拾えませんでした（--debug-log の伏せ字ログで形を確認）")
                return 1
        else:
            say("待ち時間を過ぎたので止めます（トークンは発行されていません）")
            return 2
    finally:
        if debug:
            debug.close()
        stop_child(pid, fd)
        shutil.rmtree(work, ignore_errors=True)

    if not token:
        say("トークンが出ないまま終わりました（--debug-log で原因を確認）")
        return 1
    say(f"トークンを受け取りました（{len(token)}文字）。claude -p で動くか試しています")
    if not verify_token(claude, token):
        say("そのトークンで claude -p が動かなかったので、secret には入れません")
        return 1
    set_secret(ghp, args.repo, token)
    token = None
    say(f"CLAUDE_CODE_OAUTH_TOKEN を {args.repo} の secret に入れました（値は表示していません）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Ryoseiimai/freehp-autopilot")
    ap.add_argument("--profile", default="Profile 10", help="claude.ai にログイン済みの Chrome プロファイルのフォルダ名")
    ap.add_argument("--wait", type=int, default=1200, help="本人の承認を待つ最大秒数")
    ap.add_argument("--debug-log", help="伏せ字にした claude の出力を書く先（原因調べ用）")
    ap.add_argument("--no-open", action="store_true", help="ブラウザを開かない（起動・URL の形・片付けだけ試す）")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
