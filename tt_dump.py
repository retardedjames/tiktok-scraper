
import json, gzip
from mitmproxy import http

def request(flow: http.HTTPFlow):
    host = flow.request.host
    if not ("tiktok" in host or "tiktokv" in host):
        return
    if "search" in flow.request.path:
        q = dict(flow.request.query)
        print(f"[req] {flow.request.path} kw={q.get('keyword','')!r} sort={q.get('sort_type','?')} cursor={q.get('cursor','?')}", flush=True)


def response(flow: http.HTTPFlow):
    host = flow.request.host
    if not ("tiktok" in host or "tiktokv" in host):
        return
    path = flow.request.path
    is_known = "search/item" in path or "search/stream" in path or "search/single" in path
    if not is_known:
        # Sniff any other TikTok path for aweme lists so we can find cursor=0
        try:
            body_peek = flow.response.content
            enc_peek = flow.response.headers.get("content-encoding", "")
            if "br" in enc_peek:
                import brotli as _br; body_peek = _br.decompress(body_peek)
            elif "gzip" in enc_peek:
                body_peek = gzip.decompress(body_peek)
            data_peek = json.loads(body_peek)
            has_videos = (data_peek.get("aweme_list") or data_peek.get("item_list")
                          or data_peek.get("video_list"))
            if has_videos:
                q = dict(flow.request.query)
                print(f"[mitmproxy] FOUND VIDEOS on non-search path: {path} "
                      f"kw={q.get('keyword','')!r} sort={q.get('sort_type','?')} "
                      f"cursor={q.get('cursor','?')} count={len(has_videos)}", flush=True)
        except Exception:
            pass
        return

    query = dict(flow.request.query)
    keyword = query.get("keyword", "")
    sort_type = query.get("sort_type", "rel")
    cursor = query.get("cursor", "0")

    body = flow.response.content
    enc = flow.response.headers.get("content-encoding", "")

    if "br" in enc:
        try:
            import brotli
            body = brotli.decompress(body)
        except Exception as e:
            print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: brotli decompress failed: {e}", flush=True)
            return
    elif "gzip" in enc:
        try:
            body = gzip.decompress(body)
        except Exception as e:
            print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: gzip decompress failed: {e}", flush=True)
            return

    try:
        data = json.loads(body)
    except Exception as e:
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: JSON parse failed (enc={enc!r}): {e}", flush=True)
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
