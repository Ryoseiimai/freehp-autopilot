"""失敗の自動起票と、3回連続失敗での自動停止。

  python -m autopilot.failures fail --job post --workflow post.yml --log out/post.log
  python -m autopilot.failures ok   --job post

連続回数は状態ファイルを持たず、GitHub のワークフロー実行履歴から数える
（スキップ・キャンセルは数えずに飛ばし、成功が出たところで止める）。
"""
import argparse
from pathlib import Path

from . import github
from .common import load_config, log, now_jst, redact

LOG_TAIL_LINES = 40
NEUTRAL = {"skipped", "cancelled", "neutral", None}


def issue_title(job):
    return f"[失敗] {job}"


def consecutive_failures(workflow_file):
    current = github.run_url().rsplit("/", 1)[-1]
    count = 1
    for run in github.workflow_runs(workflow_file, per_page=15):
        if str(run["id"]) == current or run.get("status") != "completed":
            continue
        conclusion = run.get("conclusion")
        if conclusion == "failure":
            count += 1
        elif conclusion in NEUTRAL:
            continue
        else:
            break
    return count


def log_tail(path):
    p = Path(path) if path else None
    if not p or not p.exists():
        return "（ログファイルなし。Actions の実行ページを見てください）"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
    return redact("\n".join(lines))


def record_failure(job, workflow_file, log_path):
    cfg = load_config()["failures"]
    github.ensure_label(cfg["label"], "d73a4a", "オートパイロットの失敗")
    count = consecutive_failures(workflow_file)
    body = (
        f"- ジョブ: `{job}`（{workflow_file}）\n"
        f"- 時刻: {now_jst():%Y-%m-%d %H:%M} JST\n"
        f"- 連続失敗: {count}回\n"
        f"- 実行: {github.run_url()}\n\n"
        f"<details><summary>ログの最後</summary>\n\n```\n{log_tail(log_path)}\n```\n</details>\n"
    )
    existing = github.find_open_issue(issue_title(job), cfg["label"])
    if existing:
        github.comment(existing["number"], body)
        number = existing["number"]
    else:
        number = github.create_issue(issue_title(job), body, [cfg["label"]])["number"]
    log(f"失敗を Issue #{number} に記録しました（連続{count}回）")
    if count >= cfg["stop_after_consecutive"]:
        github.disable_workflow(workflow_file)
        github.comment(
            number,
            f"{count}回連続で失敗したので `{workflow_file}` を止めました。\n"
            f"直したら `gh workflow enable {workflow_file}` で再開します。",
        )
        log(f"{count}回連続失敗のため {workflow_file} を無効化しました")


def record_ok(job):
    cfg = load_config()["failures"]
    existing = github.find_open_issue(issue_title(job), cfg["label"])
    if existing:
        github.comment(existing["number"], f"直りました（{now_jst():%Y-%m-%d %H:%M} JST・{github.run_url()}）")
        github.close_issue(existing["number"])
        log(f"Issue #{existing['number']} を閉じました")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["fail", "ok"])
    p.add_argument("--job", required=True)
    p.add_argument("--workflow")
    p.add_argument("--log")
    args = p.parse_args()
    if not github.available():
        log("GH_TOKEN / GITHUB_REPOSITORY が無いので記録をスキップします")
        return
    if args.mode == "fail":
        record_failure(args.job, args.workflow, args.log)
    else:
        record_ok(args.job)


if __name__ == "__main__":
    main()
