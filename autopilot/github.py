"""GitHub API を gh コマンド経由で呼ぶ薄い層。Actions では GH_TOKEN=github.token で動く。"""
import json
import os
import subprocess

TIMEOUT_SEC = 60


class GitHubError(RuntimeError):
    pass


def repo():
    name = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not name:
        raise GitHubError("GITHUB_REPOSITORY が未設定です（Actions の外では手で入れてください）")
    return name


def available():
    return bool(os.environ.get("GH_TOKEN", "").strip() and os.environ.get("GITHUB_REPOSITORY", "").strip())


def api(method, path, body=None, token_env="GH_TOKEN"):
    cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    env = dict(os.environ)
    if token_env != "GH_TOKEN":
        env["GH_TOKEN"] = os.environ.get(token_env, "")
    stdin = None
    if body is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(body)
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env)
    if proc.returncode != 0:
        raise GitHubError(f"gh api {method} {path} 失敗: {(proc.stderr or proc.stdout)[:300]}")
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def ensure_label(name, color="ededed", description=""):
    try:
        api("GET", f"/repos/{repo()}/labels/{name}")
    except GitHubError:
        api("POST", f"/repos/{repo()}/labels", {"name": name, "color": color, "description": description})


def list_issues(label, state="open"):
    items = api("GET", f"/repos/{repo()}/issues?labels={label}&state={state}&per_page=50")
    return [i for i in items if "pull_request" not in i]


def find_open_issue(title, label):
    for issue in list_issues(label):
        if issue["title"] == title:
            return issue
    return None


def create_issue(title, body, labels):
    return api("POST", f"/repos/{repo()}/issues", {"title": title, "body": body, "labels": labels})


def comment(number, body):
    return api("POST", f"/repos/{repo()}/issues/{number}/comments", {"body": body})


def add_labels(number, labels):
    return api("POST", f"/repos/{repo()}/issues/{number}/labels", {"labels": labels})


def close_issue(number):
    return api("PATCH", f"/repos/{repo()}/issues/{number}", {"state": "closed"})


def workflow_runs(workflow_file, per_page=10, created=None):
    query = f"per_page={per_page}"
    if created:
        query += f"&created={created}"
    got = api("GET", f"/repos/{repo()}/actions/workflows/{workflow_file}/runs?{query}")
    return got.get("workflow_runs", [])


def disable_workflow(workflow_file):
    api("PUT", f"/repos/{repo()}/actions/workflows/{workflow_file}/disable")


def run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run_id}" if run_id else ""
