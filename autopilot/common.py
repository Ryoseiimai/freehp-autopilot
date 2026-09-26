"""全ジョブ共通: 置き場所・日本時間・フラグ・状態ファイルの読み書き・ログ。"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "kb"
STATE_DIR = ROOT / "state"
MATERIALS_DIR = ROOT / "materials"
DESIGN_DIR = ROOT / "design"
REPORTS_DIR = ROOT / "reports"
OUT_DIR = ROOT / "out"
STOP_FILE = ROOT / "STOP"

JST = timezone(timedelta(hours=9))


class SkipJob(Exception):
    """設定不足などで「何もせず正常終了」にするときに投げる。理由はログに出す。"""


def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def now_jst():
    return datetime.now(JST)


def jst_date(dt=None):
    return (dt or now_jst()).astimezone(JST).strftime("%Y-%m-%d")


def parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def env_flag(name, default=False):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_dry_run():
    # 既定は dry-run。DRY_RUN=0 を明示したときだけ本番動作にする
    return os.environ.get("DRY_RUN", "1").strip() not in ("0", "false", "no", "off")


def has_env(*names):
    return all(os.environ.get(n, "").strip() for n in names)


def stop_requested():
    return STOP_FILE.exists()


def log(msg):
    print(msg, flush=True)


def summary(md):
    """Actions の実行結果ページ（Summary）に Markdown を足す。手元では標準出力へ。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md.rstrip() + "\n\n")
    else:
        log(md)


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


SECRET_LIKE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]+|Bearer\s+\S+|x-access-token:[^@\s]+@)"
)


def redact(text):
    """ログや Issue に貼る前に、鍵らしい文字列を伏せる。"""
    return SECRET_LIKE.sub("[REDACTED]", text or "")


def run_main(job_name, fn):
    """各ジョブの入口。STOP・スキップ・失敗の扱いをそろえる。"""
    if stop_requested():
        log(f"[{job_name}] STOP ファイルがあるので何もしません")
        summary(f"### {job_name}\nSTOP ファイルがあるので停止中（何もしていません）")
        return 0
    try:
        fn()
    except SkipJob as e:
        log(f"[{job_name}] スキップ: {e}")
        summary(f"### {job_name}\nスキップ: {e}")
        return 0
    return 0


def main_guard(job_name, fn):
    sys.exit(run_main(job_name, fn))
