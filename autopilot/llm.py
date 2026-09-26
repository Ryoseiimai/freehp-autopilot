"""Claude 呼び出し。鍵の種類で経路を切り替える。

- ANTHROPIC_API_KEY があれば Messages API を直接呼ぶ（画像つきの採点もできる）
- 無くて CLAUDE_CODE_OAUTH_TOKEN があれば Claude Code CLI（claude -p）を呼ぶ
  （claude-code-action と同じ認証。ワークフロー側で npm i -g @anthropic-ai/claude-code 済みの前提）
- 手元で試すときは AUTOPILOT_LLM=cli で、ログイン済みの claude -p を使える
- どれも無ければ available() が False。呼ぶ側が「未設定なのでスキップ」にする
"""
import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import load_config, log

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 3
API_TIMEOUT_SEC = 600
CLI_TIMEOUT_SEC = 900


class LLMError(RuntimeError):
    pass


def backend():
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "api"
    wants_cli = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or os.environ.get("AUTOPILOT_LLM") == "cli"
    if wants_cli and shutil.which("claude"):
        return "cli"
    return None


def available():
    return backend() is not None


def model_id(kind="default"):
    return load_config()["models"][kind]


def ask(prompt, system="", kind="default", max_tokens=4000, images=None):
    """テキストを1回だけ生成して返す。images は画像ファイルのパスのリスト（JPEG/PNG）。

    API 経路は画像を base64 で添える。CLI 経路は Read ツールだけを許して画像ファイルを開かせる。
    """
    which = backend()
    images = [Path(p) for p in (images or [])]
    if which == "api":
        return _ask_api(prompt, system, model_id(kind), max_tokens, images)
    if which == "cli":
        return _ask_cli(prompt, system, model_id(kind), images)
    raise LLMError("Claude の鍵（ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN）が未設定です")


def ask_json(prompt, system="", kind="default", max_tokens=4000, images=None):
    """JSON で答えさせて dict/list にして返す。崩れていたら1回だけ直させる。"""
    text = ask(prompt, system, kind, max_tokens, images)
    try:
        return extract_json(text)
    except ValueError:
        log("  JSON が読めなかったので1回だけ出し直させます")
        fixed = ask(
            "次の文章から JSON だけを取り出し、正しい JSON に直して返してください。説明は不要です。\n\n" + text,
            "あなたは JSON を整形する係です。JSON 以外は出力しません。",
            "default",
            max_tokens,
        )
        return extract_json(fixed)


def extract_json(text):
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("JSON が見つかりません")
    start = min(starts)
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON の解析に失敗: {e}") from e
    return obj


def _media_type(path):
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _ask_api(prompt, system, model, max_tokens, images):
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": _media_type(p), "data": base64.b64encode(p.read_bytes()).decode()}}
        for p in images
    ]
    content.append({"type": "text", "text": prompt})
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    if system:
        body["system"] = system
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    data = json.dumps(body).encode()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT_SEC) as resp:
                parsed = json.load(resp)
            texts = [b.get("text", "") for b in parsed.get("content", []) if b.get("type") == "text"]
            return "".join(texts).strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                log(f"  Claude API {e.code}、{attempt * 20}秒待って再試行")
                time.sleep(attempt * 20)
                continue
            raise LLMError(f"Claude API HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt * 20)
                continue
            raise LLMError(f"Claude API に接続できません: {e}") from e
    raise LLMError("Claude API の再試行が尽きました")


def _ask_cli(prompt, system, model, images):
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "project",
        "--strict-mcp-config",
    ]
    if images:
        # 画像を見せるときだけ Read ツールを許す（書き込み・実行のツールは渡さない）
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        for d in sorted({str(p.resolve().parent) for p in images}):
            cmd += ["--add-dir", d]
        prompt = "まず次の画像ファイルを Read ツールで開いて見てください:\n" + "\n".join(f"- {p.resolve()}" for p in images) + "\n\n" + prompt
    else:
        cmd += ["--tools", ""]
    if system:
        cmd += ["--system-prompt", system]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CLI_TIMEOUT_SEC)
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
        if parsed and not parsed.get("is_error") and parsed.get("result"):
            return parsed["result"].strip()
        detail = (proc.stderr or proc.stdout or "")[-300:]
        if attempt < MAX_ATTEMPTS:
            log(f"  claude -p が失敗（{attempt}回目）、再試行: {detail}")
            time.sleep(attempt * 15)
            continue
        raise LLMError(f"claude -p が失敗しました: {detail}")
    raise LLMError("claude -p の再試行が尽きました")
