import json
from mitmproxy import http


def _dechunk(data: bytes) -> bytes:
    """Strip HTTP chunked-transfer-encoding framing (hex-length\\r\\n + bytes\\r\\n ... 0\\r\\n\\r\\n).

    The TikTok /search/stream/ endpoint returns cursor=0 with chunked framing
    that mitmproxy surfaces as raw bytes. Other search endpoints arrive already
    dechunked. Returns input unchanged if it doesn't look chunked.
    """
    if not data or not data[:1].isalnum():
        return data
    parts = []
    i = 0
    n = len(data)
    while i < n:
        j = data.find(b"\r\n", i)
        if j < 0:
            return data
        try:
            size = int(data[i:j].split(b";", 1)[0], 16)
        except ValueError:
            return data
        i = j + 2
        if size == 0:
            return b"".join(parts)
        if i + size > n:
            return data
        parts.append(data[i:i + size])
        i += size + 2
    return b"".join(parts)


def _parse_json_docs(body: bytes):
    """Parse one or more JSON documents concatenated in a single body."""
    text = body.decode("utf-8", errors="replace").lstrip()
    decoder = json.JSONDecoder()
    docs = []
    while text:
        obj, end = decoder.raw_decode(text)
        docs.append(obj)
        text = text[end:].lstrip()
    return docs


def _extract_videos(data):
    raw_list = data.get("aweme_list") or data.get("item_list") or data.get("video_list") or []
    if raw_list:
        return raw_list
    components = data.get("data") or []
    return [c["aweme_info"] for c in components if isinstance(c, dict) and "aweme_info" in c]


def response(flow: http.HTTPFlow):
    host = flow.request.host
    if not ("tiktok" in host or "tiktokv" in host):
        return
    path = flow.request.path

    if "search" not in path:
        return
    if not ("search/item" in path or "search/stream" in path or "search/single" in path):
        return

    query = dict(flow.request.query)
    keyword = query.get("keyword", "")
    sort_type = query.get("sort_type", "rel")
    cursor = query.get("cursor", "0")

    body = _dechunk(flow.response.content or b"")

    try:
        docs = _parse_json_docs(body)
    except Exception as e:
        enc = flow.response.headers.get("content-encoding", "")
        ctype = flow.response.headers.get("content-type", "")
        raw_dump = f"/tmp/tt_raw_{keyword}_{sort_type}_cursor{cursor}.bin"
        try:
            with open(raw_dump, "wb") as rf:
                rf.write(flow.response.content or b"")
        except Exception:
            pass
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: JSON parse failed "
              f"(enc={enc!r} ctype={ctype!r} len={len(body)} head={bytes(body[:80])!r}) saved={raw_dump}: {e}",
              flush=True)
        return

    videos = []
    for d in docs:
        videos.extend(_extract_videos(d))

    if videos:
        fname = f"/tmp/tt_{keyword}_{sort_type}.jsonl"
        with open(fname, "a") as f:
            for v in videos:
                f.write(json.dumps(v) + "\n")
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: +{len(videos)} videos "
              f"({len(docs)} docs)", flush=True)
    else:
        keys = list(docs[0].keys())[:8] if docs else []
        print(f"[mitmproxy] {keyword} sort={sort_type} cursor={cursor}: matched but 0 videos "
              f"({len(docs)} docs, keys={keys})", flush=True)
