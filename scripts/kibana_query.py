#!/usr/bin/env python3
"""
Kibana Log Query Tool
Usage: python3 kibana_query.py <search_keyword> [--env sit|stage|prod] [--minutes 30] [--index new-kklog-*]

All envs: auto anonymous login, sid cached in tempdir. No credentials needed.
"""
import urllib.request, json, http.cookiejar, os, sys, argparse
from pathlib import Path
import tempfile

ENVS = {
    "sit":   "https://kibana.sit.kkday.com",
    "stage": "https://kibana.stage.kkday.com",
    "prod":  "https://kibana.kkday.com",
}
DEFAULT_INDEX = "new-kklog-*"
SID_CACHE_TPL = str(Path(tempfile.gettempdir()) / ".kibana_{env}_sid")


def get_anonymous_sid(kibana_url):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        f"{kibana_url}/internal/security/login",
        data=json.dumps({
            "providerType": "anonymous", "providerName": "anonymous1",
            "currentURL": f"{kibana_url}/login"
        }).encode(),
        headers={"kbn-xsrf": "true", "content-type": "application/json"}
    )
    with opener.open(req, timeout=10) as r:
        r.read()
    for c in jar:
        if c.name == "sid":
            return c.value
    raise RuntimeError("No sid cookie returned from anonymous login")


def load_sid(env, kibana_url):
    cache = Path(SID_CACHE_TPL.format(env=env))
    if cache.exists():
        return cache.read_text().strip()
    sid = get_anonymous_sid(kibana_url)
    cache.write_text(sid)
    return sid


def refresh_sid(env, kibana_url):
    print(f"🔄 sid 已過期，重新 anonymous login ({env})...", file=sys.stderr)
    sid = get_anonymous_sid(kibana_url)
    Path(SID_CACHE_TPL.format(env=env)).write_text(sid)
    return sid


def get_auth_headers(env, kibana_url):
    sid = load_sid(env, kibana_url)
    return {"Cookie": f"sid={sid}"}


def do_query(kibana_url, auth_headers, index, keyword, from_t, to_t):
    payload = {"batch": [{"request": {"params": {"index": index, "body": {
        "sort": [{"@timestamp": {"order": "asc", "unmapped_type": "boolean"}}],
        "size": 50, "_source": True, "track_total_hits": True,
        "query": {"bool": {"filter": [
            {"multi_match": {"type": "phrase", "query": keyword, "lenient": True}},
            {"range": {"@timestamp": {"gte": from_t, "lte": to_t}}}
        ]}}
    }}}, "options": {"strategy": "ese"}}]}

    headers = {
        "accept": "*/*", "content-type": "application/json",
        "kbn-xsrf": "true", "kbn-version": "8.19.10",
        "elastic-api-version": "1", "x-elastic-internal-origin": "Kibana",
        **auth_headers
    }
    req = urllib.request.Request(
        f"{kibana_url}/internal/bsearch?compress=false",
        data=json.dumps(payload).encode(),
        headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, "EXPIRED"
        raise


def print_results(data):
    rr = data["result"]["rawResponse"]
    total = rr["hits"]["total"]
    if isinstance(total, dict):
        total = total.get("value", 0)
    print(f"✅ 找到 {total} 筆 log")
    print("---")
    for h in rr["hits"]["hits"]:
        s = h.get("_source", {})
        ts    = s.get("@timestamp", "")
        label = s.get("log_label", "")
        svc   = s.get("service") or s.get("app") or ""
        msg   = str(s.get("message", ""))[:300]
        url   = s.get("url") or s.get("request_uri") or ""
        rs    = s.get("response_status") or s.get("status") or ""
        exc   = str(s.get("exception", ""))[:500]

        print(f"[{ts}] {label} {svc}")
        if url: print(f"  url      : {url}")
        if rs:  print(f"  status   : {rs}")
        if msg: print(f"  message  : {msg}")
        if exc: print(f"  exception: {exc}")
        print()


def main():
    p = argparse.ArgumentParser(description="Kibana Log Query Tool")
    p.add_argument("keyword", help="Search keyword (phrase match)")
    p.add_argument("--env", default="sit", choices=["sit", "stage", "prod"],
                   help="環境 (default: sit)")
    p.add_argument("--minutes", type=int, default=30,
                   help="往前幾分鐘 (default: 30)")
    p.add_argument("--index", default=DEFAULT_INDEX,
                   help=f"Index pattern (default: {DEFAULT_INDEX})")
    args = p.parse_args()

    kibana_url = ENVS[args.env]
    from_t = f"now-{args.minutes}m"
    to_t   = "now"

    print(f"🔍 搜尋: {args.keyword}")
    print(f"🌐 環境: {args.env} ({kibana_url})")
    print(f"⏰ 時間: now-{args.minutes}m ~ now")
    print(f"📦 Index: {args.index}")
    print("---")

    auth = get_auth_headers(args.env, kibana_url)
    data, err = do_query(kibana_url, auth, args.index, args.keyword, from_t, to_t)

    if err == "EXPIRED":
        new_sid = refresh_sid(args.env, kibana_url)
        auth = {"Cookie": f"sid={new_sid}"}
        data, err = do_query(kibana_url, auth, args.index, args.keyword, from_t, to_t)

    if err:
        print(f"❌ 認證失敗: {err}", file=sys.stderr)
        sys.exit(1)

    print_results(data)


if __name__ == "__main__":
    main()
