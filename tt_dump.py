import json
from mitmproxy import http


def response(flow: http.HTTPFlow):
    host = flow.request.host
    if not ("tiktok" in host or "tiktokv" in host):
        return
    path = flow.request.path

    if "search" not in path:
        return

    is_known = "search/item" in path or "search/stream" in path or "search/single" in path
    if not is_known:
        return

    query = dict(flow.request.query)
    keyword = query.get("keyword", "")
    sort_type = query.get("sort_type", "rel")
    cursor = query.get("cursor", "0")

    body = flow.response.content or b""

    try:
        data = json.loads(body)
    except Exception as e:
        enc = flow.response.headers.get("content-encoding", "")
        ctype = flow.response.headers.get("content-type", "")
        head = bytes(body[:120])
        raw_dump = f"/tmp/tt_raw_{keyword}_{sort_type}_cursor{cursor}.bin"
        try:
            with open(raw_dump, "wb") as rf:
                rf.write(body)
        except Exception:
            pass
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: JSON parse failed "
              f"(enc={enc!r} ctype={ctype!r} len={len(body)} head={head!r}) saved={raw_dump}: {e}",
              flush=True)
        return

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
    else:
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: matched but 0 videos (keys={list(data.keys())[:8]})", flush=True)
