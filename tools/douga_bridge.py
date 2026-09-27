#!/usr/bin/env python3
"""Free動画 受け渡しの橋渡しスクリプト（Mac常駐で動かす）。

役割は2つだけ:
1. GitHub の video-request ラベル付き Issue を読み、
   ~/dev/2026-09-27-free-douga/jobs/<job_id>/request.json を作る
   （動画のダウンロードはしない。リンクを記録するだけ）。
2. jobs/<job_id>/output/report.json ができていたら、
   Issue への「完成しました」コメントの下書きを
   state/douga_drafts/<job_id>.md に書く（投稿はしない）。

形式の正: ~/dev/2026-09-27-free-douga/INTERFACE.md
GitHub操作は個人アカウント(Ryoseiimai)。gh の設定ディレクトリを ghp と同じものに切り替えて呼ぶ。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

DEFAULT_REPO = "Ryoseiimai/freehp-autopilot"
DEFAULT_JOBS_DIR = Path.home() / "dev" / "2026-09-27-free-douga" / "jobs"
DEFAULT_STATE_DIR = Path.home() / "dev" / "2026-09-27-freehp-autopilot" / "state" / "douga_drafts"
LABEL = "video-request"
DURATION_LIMIT_S = 1800  # 30分。エンジン側もこの秒数で止まる(INTERFACE.md参照)。

# Issue Forms の見出し(video.yml の label)→ request.json のキー対応。
# video.yml 側の文言を変えたら、ここも合わせて直すこと。
FIELD_HEADINGS = {
    "お名前・ハンドル": "name",
    "連絡先": "contact_text",
    "動画の共有リンク": "video_url",
    "動画の長さ": "duration_text",
    "やってほしいこと": "tasks_raw",
    "雰囲気": "mood",
    "納期の希望": "due_date_text",
    "その他の希望・固有名詞など": "notes",
    "個人情報の扱いへの同意": "consent_raw",
}

NO_RESPONSE = "_no response_"


def gh_env() -> dict:
    """個人(Ryoseiimai)アカウント用の gh 環境変数(ghpと同じ切り替え方)。"""
    env = os.environ.copy()
    env["GH_CONFIG_DIR"] = str(Path.home() / ".config" / "gh-personal")
    return env


def gh_json(args: list[str]) -> object:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
        env=gh_env(),
    )
    return json.loads(result.stdout)


def parse_issue_body(body: str) -> dict:
    """GitHub Issue Forms が生成する `### 見出し\n\n回答` 形式をパースする。"""
    sections: dict[str, str] = {}
    # "### " で分割。先頭のmarkdown説明文(見出しなし部分)は無視する。
    parts = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
    # parts = [先頭の余り, 見出し1, 本文1, 見出し2, 本文2, ...]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections[heading] = content

    parsed: dict[str, str] = {}
    for heading, key in FIELD_HEADINGS.items():
        raw = sections.get(heading, "")
        parsed[key] = "" if raw.strip().lower() == NO_RESPONSE else raw
    return parsed


def checked_labels(raw: str) -> list[str]:
    """`- [x] ラベル` 形式のチェックボックスから、チェック済みのラベルだけを返す。"""
    checked = []
    for line in raw.splitlines():
        m = re.match(r"^- \[(x|X)\]\s*(.+)$", line.strip())
        if m:
            checked.append(m.group(2).strip())
    return checked


def extract_minutes(text: str) -> int | None:
    """「12分」「約1時間」などから分数を大まかに取り出す。読めなければ None。"""
    if not text:
        return None
    hour_m = re.search(r"(\d+)\s*時間", text)
    minute_m = re.search(r"(\d+)\s*分", text)
    minutes = 0
    found = False
    if hour_m:
        minutes += int(hour_m.group(1)) * 60
        found = True
    if minute_m:
        minutes += int(minute_m.group(1))
        found = True
    return minutes if found else None


# ぼかしの既定セット(INTERFACE.mdのoptions.maskの例に合わせる)。
# 簡略化: フォームは「個人情報をぼかす」の1チェックのみで、項目別の選択はさせていない。
# チェックがあれば全項目を対象にする(過検出は許容・見落としは許容しない、という向き)。
DEFAULT_MASK_TARGETS = [
    "faces_non_speaker", "names", "email", "phone",
    "address", "customer_company", "money", "internal_docs", "credentials",
]


def build_request(issue: dict, parsed: dict) -> tuple[dict, list[str]]:
    """request.json の中身と、受付側で確認すべき警告リストを作る。"""
    warnings: list[str] = []

    task_labels = checked_labels(parsed.get("tasks_raw", ""))
    consent_labels = checked_labels(parsed.get("consent_raw", ""))
    if not consent_labels:
        warnings.append("個人情報の扱いへの同意チェックが確認できません。申込者に確認してください。")

    minutes = extract_minutes(parsed.get("duration_text", ""))
    if minutes is None:
        warnings.append(
            f"動画の長さが数値として読み取れません: {parsed.get('duration_text', '')!r}"
        )
    elif minutes > DURATION_LIMIT_S // 60:
        warnings.append(
            f"動画の長さが30分を超えています({minutes}分)。分割をご案内するか、お断りしてください。"
        )

    notes_parts = [p for p in (parsed.get("mood"), parsed.get("notes")) if p]
    notes = "\n".join(notes_parts)

    due_at = None
    due_text = parsed.get("due_date_text", "")
    if due_text:
        # 自由記述なので厳密な日時変換はしない。テキストのままnotesにも残す。
        notes = f"{notes}\n納期希望: {due_text}".strip()

    request = {
        "job_id": None,  # 呼び出し側で埋める
        "source_video": "",
        "source_url": parsed.get("video_url", ""),
        "duration_limit_s": DURATION_LIMIT_S,
        "due_at": due_at,
        "options": {
            "subtitles": "字幕を付ける" in task_labels,
            "telop_style": "テロップを入れる" in task_labels,
            "cut_fillers": "言い直し・待ち時間をカットする" in task_labels,
            "cut_dead_air": "言い直し・待ち時間をカットする" in task_labels,
            "zoom": False,
            "mask": DEFAULT_MASK_TARGETS
            if any("ぼかす" in t for t in task_labels)
            else [],
        },
        "speakers": [],
        "dictionary": [],
        "notes": (
            f"申込者: {parsed.get('name', '')} / 連絡先: {parsed.get('contact_text', '')}\n"
            + notes
        ).strip(),
        "contact": {"issue": issue["url"]},
    }
    return request, warnings


def job_id_for_issue(issue: dict) -> str:
    created_at = datetime.strptime(issue["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    jst = created_at.astimezone(JST)
    return f"{jst:%Y%m%d-%H%M}-{issue['number']}"


def existing_job_dir_for_issue(jobs_dir: Path, issue_number: int) -> Path | None:
    if not jobs_dir.is_dir():
        return None
    suffix = f"-{issue_number}"
    for child in jobs_dir.iterdir():
        if child.is_dir() and child.name.endswith(suffix):
            return child
    return None


def process_new_requests(repo: str, jobs_dir: Path, dry_run: bool) -> None:
    issues = gh_json(
        [
            "issue", "list",
            "--repo", repo,
            "--label", LABEL,
            "--state", "open",
            "--json", "number,title,body,url,createdAt",
        ]
    )
    if not issues:
        print(f"[douga_bridge] {LABEL} の新規Issueはありません。")
        return

    for issue in issues:
        existing = existing_job_dir_for_issue(jobs_dir, issue["number"])
        if existing is not None:
            print(f"[douga_bridge] Issue #{issue['number']} は既に {existing.name} を作成済み。スキップ。")
            continue

        job_id = job_id_for_issue(issue)
        parsed = parse_issue_body(issue.get("body") or "")
        request, warnings = build_request(issue, parsed)
        request["job_id"] = job_id

        job_dir = jobs_dir / job_id
        request_path = job_dir / "request.json"
        if dry_run:
            print(f"[dry-run] Issue #{issue['number']} -> {request_path} を作成する予定")
            print(json.dumps(request, ensure_ascii=False, indent=2))
        else:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "input").mkdir(exist_ok=True)
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[douga_bridge] 作成: {request_path}")

        for warning in warnings:
            print(f"[douga_bridge][要確認] Issue #{issue['number']}: {warning}")


def process_completed_jobs(jobs_dir: Path, state_dir: Path, dry_run: bool) -> None:
    if not jobs_dir.is_dir():
        print("[douga_bridge] jobs/ がまだありません。完了ジョブなし。")
        return

    found_any = False
    for job_dir in sorted(jobs_dir.iterdir()):
        report_path = job_dir / "output" / "report.json"
        if not report_path.is_file():
            continue
        found_any = True
        draft_path = state_dir / f"{job_dir.name}.md"
        if draft_path.exists():
            continue

        report = json.loads(report_path.read_text(encoding="utf-8"))
        request_path = job_dir / "request.json"
        issue_url = ""
        if request_path.is_file():
            request = json.loads(request_path.read_text(encoding="utf-8"))
            issue_url = request.get("contact", {}).get("issue", "")

        if report.get("status") != "done":
            print(
                f"[douga_bridge] {job_dir.name}: status={report.get('status')} のため下書きは作らない"
                " (needs_human/failed は司令塔が判断)"
            )
            continue

        draft = (
            f"完成しました。\n\n"
            f"- 元の長さ: {report.get('duration_in_s', '?')}秒 → 仕上がり: {report.get('duration_out_s', '?')}秒\n"
            f"- 完成動画・字幕・ぼかし監査などは {job_dir / 'output'} にあります(受け渡し方法は司令塔が決めます)。\n\n"
            f"直し依頼は1回まで対応します。ご希望があればこのIssueに書いてください。\n\n"
            f"(対象Issue: {issue_url})\n"
        )
        if dry_run:
            print(f"[dry-run] {draft_path} を作成する予定:\n{draft}")
        else:
            state_dir.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(draft, encoding="utf-8")
            print(f"[douga_bridge] 下書き作成: {draft_path}")

    if not found_any:
        print("[douga_bridge] report.json ができているジョブはまだありません。")


SELF_TEST_BODY = """書いてもらった内容だけで見本を作ります。

### お名前・ハンドル

田中（Xは @tanaka_shop）

### 連絡先

tanaka@example.com

### 動画の共有リンク

https://drive.example.com/abc

### 動画の長さ

12分

### やってほしいこと

- [x] 字幕を付ける
- [x] テロップを入れる
- [ ] 言い直し・待ち時間をカットする
- [x] 個人情報（顔・名前・連絡先・画面の鍵など）をぼかす

### 雰囲気

落ち着いた感じ

### 納期の希望

_No response_

### その他の希望・固有名詞など

「こむぎ工房」という店名を正しく認識してほしい

### 個人情報の扱いへの同意

- [x] 動画に映る第三者の個人情報（顔・名前・メールアドレス・電話番号・画面に映る鍵情報など）はぼかして納品することに同意します。
"""


def self_test() -> None:
    parsed = parse_issue_body(SELF_TEST_BODY)
    assert parsed["name"] == "田中（Xは @tanaka_shop）", parsed["name"]
    assert parsed["contact_text"] == "tanaka@example.com"
    assert parsed["video_url"] == "https://drive.example.com/abc"
    assert parsed["duration_text"] == "12分"
    assert parsed["mood"] == "落ち着いた感じ"
    assert parsed["due_date_text"] == "", repr(parsed["due_date_text"])
    assert "こむぎ工房" in parsed["notes"]

    fake_issue = {
        "number": 999,
        "url": "https://github.com/Ryoseiimai/freehp-autopilot/issues/999",
        "createdAt": "2026-10-01T01:30:00Z",
    }
    request, warnings = build_request(fake_issue, parsed)
    assert request["source_url"] == "https://drive.example.com/abc"
    assert request["options"]["subtitles"] is True
    assert request["options"]["cut_fillers"] is False
    assert request["options"]["mask"], "ぼかしチェック済みなのにmaskが空"
    assert warnings == [], warnings

    job_id = job_id_for_issue(fake_issue)
    assert job_id == "20261001-1030-999", job_id

    print("[douga_bridge] self-test OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS_DIR)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="ファイル書き込み・状態変更をせず、やる予定の内容を表示するだけ",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="サンプルのIssue本文でパーサーを検証して終了する(GitHub通信なし)",
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    process_new_requests(args.repo, args.jobs_dir, args.dry_run)
    process_completed_jobs(args.jobs_dir, args.state_dir, args.dry_run)


if __name__ == "__main__":
    main()
