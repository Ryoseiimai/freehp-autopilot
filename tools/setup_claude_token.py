#!/usr/bin/env python3
"""Claude Code の OAuth トークンを発行し、画面にもログにも出さずに GitHub の secret（CLAUDE_CODE_OAUTH_TOKEN）へ入れる。

  python3 tools/setup_claude_token.py [--repo Ryoseiimai/freehp-autopilot] [--profile "Profile 10"] [--wait 900]

流れ（memory reference_claude_setup_token_unattended の手順）:
  1. `claude setup-token` を pty で起動する（端末の幅を1000にして URL が折り返さないようにする）
  2. PATH の先頭に「URL をファイルに書くだけの偽 open」を置き、既定のプロファイルで勝手に開かせない
  3. URL を Chrome の指定プロファイル（claude.ai にログイン済みのもの）で開く → 本人が承認を1クリック
  4. 出力に出る sk-ant-oat01-… を正規表現で拾い、そのまま `ghp secret set` の標準入力へ渡す
トークンは標準出力・ファイル・引数のどこにも書かない。進み具合だけを1行ずつ出す
（「承認画面を開きました」が出たら本人の承認待ち。--wait 秒を過ぎたら諦めて終了コード2）。
"""
import argparse
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

TOKEN_RE = re.compile(rb"sk-ant-oat01-[A-Za-z0-9_\-]+")
URL_RE = re.compile(rb"https://(?:claude\.ai|claude\.com|console\.anthropic\.com)/[^\s\x1b\"']+")
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
TERM_ROWS, TERM_COLS = 50, 1000
READ_CHUNK = 65536
POLL_SEC = 0.5
TOKEN_SETTLE_SEC = 1.0
STOP_WAIT_SEC = 3.0
FAKE_OPEN = '#!/bin/sh\nfor a in "$@"; do case "$a" in http*) printf "%s\\n" "$a" >> "$URL_FILE";; esac; done\n'


def say(msg):
    print(msg, flush=True)


def find_ghp():
    for cand in (shutil.which("ghp"), str(Path.home() / ".local/bin/ghp")):
        if cand and Path(cand).exists():
            return cand
    sys.exit("ghp が見つかりません（~/.local/bin/ghp を確認）")


def find_claude():
    for cand in (str(Path.home() / ".local/bin/claude"), shutil.which("claude")):
        if cand and Path(cand).exists():
            return cand
    sys.exit("claude が見つかりません")


def open_in_chrome(url, profile):
    subprocess.run(["/usr/bin/open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", url], check=True)


def set_secret(ghp, repo, token):
    proc = subprocess.run([ghp, "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", repo],
                          input=token, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        # gh のエラー文にトークンは含まれないが、念のため伏せてから出す
        err = TOKEN_RE.sub(b"[REDACTED]", (proc.stderr or proc.stdout).encode()).decode(errors="replace")
        sys.exit(f"secret の設定に失敗しました: {err[:300]}")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Ryoseiimai/freehp-autopilot")
    ap.add_argument("--profile", default="Profile 10")
    ap.add_argument("--wait", type=int, default=900, help="本人の承認を待つ最大秒数")
    ap.add_argument("--debug-log", help="伏せ字にした claude の出力を書く先（原因調べ用）")
    ap.add_argument("--no-open", action="store_true", help="ブラウザを開かない（起動と片付けだけ試す）")
    args = ap.parse_args()
    ghp, claude = find_ghp(), find_claude()

    work = Path(tempfile.mkdtemp(prefix="setup-token-"))
    url_file = work / "url.txt"
    fake_open = work / "open"
    fake_open.write_text(FAKE_OPEN)
    fake_open.chmod(0o700)
    env = dict(os.environ, URL_FILE=str(url_file), PATH=f"{work}:{os.environ.get('PATH', '')}",
               BROWSER=str(fake_open), COLUMNS=str(TERM_COLS), LINES=str(TERM_ROWS))

    pid, fd = pty.fork()
    if pid == 0:
        os.execve(claude, [claude, "setup-token"], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", TERM_ROWS, TERM_COLS, 0, 0))
    say("claude setup-token を起動しました")

    buf = b""
    opened = False
    token = None
    token_seen_at = None
    deadline = time.time() + args.wait
    debug = open(args.debug_log, "ab") if args.debug_log else None
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], POLL_SEC)
            if ready:
                try:
                    chunk = os.read(fd, READ_CHUNK)
                except OSError:
                    chunk = b""
                if not chunk:
                    # claude が終わった。出きった分からトークンを探して抜ける
                    m = TOKEN_RE.search(ANSI_RE.sub(b"", buf))
                    token = m.group(0).decode() if m else None
                    break
                buf += chunk
                if debug:
                    debug.write(TOKEN_RE.sub(b"[REDACTED]", chunk))
                    debug.flush()
            clean = ANSI_RE.sub(b"", buf)
            if not opened:
                url = url_file.read_text().strip().splitlines()[0] if url_file.exists() and url_file.read_text().strip() else None
                if not url:
                    m = URL_RE.search(clean)
                    url = m.group(0).decode() if m else None
                if url and args.no_open:
                    opened = True
                    say("承認画面の URL を受け取りました（--no-open なので開いていません）")
                elif url:
                    open_in_chrome(url, args.profile)
                    opened = True
                    say(f"承認画面を開きました（Chrome {args.profile}）。承認のクリックを待っています（最大{args.wait // 60}分）")
            m = TOKEN_RE.search(clean)
            if m:
                # 最後まで出きってから拾う（途中で切れた文字列を拾わないため）
                if token_seen_at is None:
                    token_seen_at = time.time()
                elif time.time() - token_seen_at >= TOKEN_SETTLE_SEC:
                    token = m.group(0).decode()
                    break
        else:
            say("待ち時間を過ぎたので止めます（トークンは発行されていません）")
            return 2
    finally:
        if debug:
            debug.close()
        stop_child(pid, fd)
        shutil.rmtree(work, ignore_errors=True)
        buf = b""

    if not token:
        say("claude setup-token がトークンを出さずに終わりました（--debug-log で原因を確認）")
        return 1
    set_secret(ghp, args.repo, token)
    token = None
    say(f"CLAUDE_CODE_OAUTH_TOKEN を {args.repo} の secret に入れました（値は表示していません）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
