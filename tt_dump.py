
import json, gzip
from mitmproxy import http

def response(flow: http.HTTPFlow):
    host = flow.request.host
    if not ("tiktok" in host or "tiktokv" in host):
        return
    path = flow.request.path
    if "search/item" not in path and "search/stream" not in path and "search/single" not in path:
        return

    body = flow.response.content
    enc = flow.response.headers.get("content-encoding", "")
    if "gzip" in enc:
        try:
            body = gzip.decompress(body)
        except Exception:
            pass

    try:
        data = json.loads(body)
    except Exception:
        return

    query = dict(flow.request.query)
    keyword = query.get("keyword", "")
    sort_type = query.get("sort_type", "rel")
    cursor = query.get("cursor", "0")

    raw_list = data.get("aweme_list") or data.get("item_list") or data.get("video_list") or []
    if not raw_list:
        components = data.get("data") or []
        raw_list = [c["aweme_info"] for c in components if "aweme_info" in c]

    if raw_list:
        fname = f"/tmp/tt_{keyword}_{sort_type}.jsonl"
        with open(fname, "a") as f:
            for v in raw_list:
                f.write(json.dumps(v) + "\n")
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: +{len(raw_list)} videos", flush=True)
