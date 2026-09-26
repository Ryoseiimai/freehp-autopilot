"""X API v2（OAuth 1.0a ユーザーコンテキスト）の最小クライアント。標準ライブラリだけで動く。

鍵: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET
料金の罠（2026-09 時点）: 投稿は1件 $0.015、URL 付きは $0.20（13倍）。読み取りも従量課金。
投稿（POST /2/tweets）は自動で再試行しない。タイムアウトでも X 側では投稿済みのことがあるため。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.x.com"
KEY_NAMES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
TIMEOUT_SEC = 30
MAX_WEIGHTED_LENGTH = 280
URL_WEIGHT = 23
# URL は ASCII の範囲だけを拾う（直後に日本語が続いても URL に含めない）
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&'*+,;=%]+")
# twitter-text v3 で重み1になる範囲。これ以外（日本語など）は重み2
LIGHT_RANGES = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))


class XError(RuntimeError):
    pass


class AccountMismatch(XError):
    pass


def credentials_present():
    return all(os.environ.get(k, "").strip() for k in KEY_NAMES)


def weighted_length(text):
    total = 0
    rest = URL_RE.sub("", text)
    total += URL_WEIGHT * len(URL_RE.findall(text))
    for ch in rest:
        code = ord(ch)
        total += 1 if any(lo <= code <= hi for lo, hi in LIGHT_RANGES) else 2
    return total


def _enc(s):
    return urllib.parse.quote(str(s), safe="~")


def sign(method, url, params, creds, nonce, timestamp):
    """OAuth 1.0a（HMAC-SHA1）の Authorization ヘッダを作る。JSON の本文は署名に含めない仕様。"""
    key, secret, token, token_secret = creds
    oauth = {
        "oauth_consumer_key": key,
        "oauth_token": token,
        "oauth_nonce": nonce,
        "oauth_timestamp": str(timestamp),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }
    pairs = sorted((_enc(k), _enc(v)) for k, v in list(oauth.items()) + list(params.items()))
    normalized = "&".join(f"{k}={v}" for k, v in pairs)
    base = "&".join(_enc(p) for p in (method, url, normalized))
    signing_key = f"{_enc(secret)}&{_enc(token_secret)}"
    digest = hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in sorted(oauth.items()))


def _auth_header(method, url, query):
    creds = tuple(os.environ[k].strip() for k in KEY_NAMES)
    return sign(method, url, query, creds, secrets.token_hex(16), int(time.time()))


def call(method, path, payload=None, query=None):
    if not credentials_present():
        raise XError("X の鍵が未設定です")
    query = {k: str(v) for k, v in (query or {}).items()}
    url = API_BASE + path
    full = url + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Authorization": _auth_header(method, url, query)}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(full, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise XError(f"X API {method} {path} HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e


def me():
    return call("GET", "/2/users/me")["data"]


def assert_handle(expected):
    """投稿・返信の前に必ず呼ぶ。鍵が別アカウントのものなら1文字も出さずに止める。"""
    user = me()
    if user.get("username", "").lower() != expected.lower():
        raise AccountMismatch(f"鍵のアカウントが @{user.get('username')} で、期待した @{expected} と違うので止めました")
    return user


def upload_image(data):
    body = {"media": base64.b64encode(data).decode(), "media_category": "tweet_image"}
    return call("POST", "/2/media/upload", body)["data"]["id"]


def create_post(text, media_ids=None, reply_to=None):
    body = {"text": text}
    if media_ids:
        body["media"] = {"media_ids": list(media_ids)}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    return call("POST", "/2/tweets", body)["data"]["id"]


def mentions(user_id, since_id=None, max_results=20):
    query = {
        "max_results": max_results,
        "tweet.fields": "created_at,author_id,conversation_id,text",
        "expansions": "author_id",
        "user.fields": "username",
    }
    if since_id:
        query["since_id"] = since_id
    return call("GET", f"/2/users/{user_id}/mentions", query=query)


def metrics(ids):
    if not ids:
        return {}
    got = call("GET", "/2/tweets", query={"ids": ",".join(ids[:100]), "tweet.fields": "public_metrics"})
    return {t["id"]: t.get("public_metrics", {}) for t in got.get("data", [])}


if __name__ == "__main__":
    # 鍵の確認だけ（投稿はしない）: python3 -m autopilot.xapi
    import sys

    from .common import load_config

    if not credentials_present():
        print("X の鍵が未設定です（確認はスキップ）")
        sys.exit(0)
    expected = load_config()["brand"]["x_handle"]
    user = assert_handle(expected)
    print(f"鍵のアカウントは @{user['username']}（期待どおり @{expected}）")
