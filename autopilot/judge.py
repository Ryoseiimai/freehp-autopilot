"""Yes/No の判定係。TYPESAFE_API_KEY があれば Jev、無ければ Claude が代わりに答える。

Jev の仕様（~/.claude/skills/jev-judge/scripts/judge.py と同じ）:
  POST https://api.typesafe.ai/v1/systemone
  {"state": ..., "model": "jev-latest", "questions": {id: {"type": "noul", "instructions": ...}}}
  → {"answers": {id: {"type": "noul", "noul": 0.0〜1.0}}}
Jev は理由の文章を返さない仕様なので、reason は Claude 代替のときだけ入る。
"""
import json
import os
import urllib.error
import urllib.request

from . import llm
from .common import log

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_TIMEOUT_SEC = 30


def source():
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return "jev"
    if llm.available():
        return "claude"
    return None


def yes_no(state, questions):
    """questions: {id: 質問文}。戻り値: {id: {"yes": bool, "prob": float, "reason": str|None, "source": str}}"""
    which = source()
    if which == "jev":
        try:
            return _jev(state, questions)
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
            log(f"  Jev の呼び出しに失敗したので Claude で代わりに判定します: {e}")
            if not llm.available():
                raise
    if which is None:
        raise llm.LLMError("判定係（TYPESAFE_API_KEY か Claude の鍵）が未設定です")
    return _claude(state, questions)


def _jev(state, questions):
    payload = {
        "state": state,
        "model": JEV_MODEL,
        "questions": {qid: {"type": "noul", "instructions": q} for qid, q in questions.items()},
    }
    req = urllib.request.Request(
        JEV_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY'].strip()}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=JEV_TIMEOUT_SEC) as resp:
            parsed = json.load(resp)
    except urllib.error.HTTPError as e:
        raise urllib.error.URLError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    answers = parsed["answers"]
    result = {}
    for qid in questions:
        prob = float(answers[qid]["noul"])
        result[qid] = {"yes": prob >= 0.5, "prob": prob, "reason": None, "source": "jev"}
    return result


def _claude(state, questions):
    lines = "\n".join(f'- "{qid}": {q}' for qid, q in questions.items())
    prompt = (
        "次の「状態」について、各質問に Yes/No で答えてください。\n"
        "Yes である確率（0.0〜1.0）と、根拠を日本語1文で付けます。\n"
        '出力は JSON だけ: {"<質問id>": {"prob": 0.0〜1.0, "reason": "…"}, ...}\n\n'
        f"状態:\n{state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)}\n\n"
        f"質問:\n{lines}\n"
    )
    parsed = llm.ask_json(prompt, "あなたは厳しめの判定係です。迷ったら No 寄りに答えます。JSON 以外は出力しません。")
    result = {}
    for qid in questions:
        item = parsed.get(qid, {})
        prob = float(item.get("prob", 0))
        result[qid] = {"yes": prob >= 0.5, "prob": prob, "reason": item.get("reason"), "source": "claude"}
    return result
