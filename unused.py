#!/usr/bin/env python3
"""
TikTok video scraper — searches by phrase, sorts by likes.

Usage:
    python3 scrape.py "search phrase" --count 50 --out results.json

Requirements:
    - ms_token cookie from tiktok.com (see README below)
    - Set MS_TOKEN env var or pass --ms-token flag

How to get ms_token:
    1. Open tiktok.com in your browser and do any search
    2. Open DevTools → Application → Cookies → tiktok.com
    3. Copy the value of the "msToken" cookie
    4. export MS_TOKEN="<value>"
"""

import asyncio
import json
import os
import argparse
import sys
from TikTokApi import TikTokApi
from TikTokApi.exceptions import InvalidResponseException


async def search_most_liked(api, search_term: str, count: int):
    """Search videos sorted by most liked (sort_type=1) via the web API."""
    found = 0
    cursor = 0
    search_id = ""
    while found < count:
        params = {
            "keyword": search_term,
            "cursor": cursor,
            "from_page": "search",
            "sort_type": 1,  # 0=relevance, 1=most liked, 2=latest
            "web_search_code": '{"tiktok":{"client_params_x":{"search_engine":{"ies_mt_user_live_video_card_use_libra":1,"mt_search_general_user_live_card":1}},"search_server":{}}}',
        }
        if search_id:
            params["search_id"] = search_id

        resp = await api.make_request(
            url="https://www.tiktok.com/api/search/item/full/",
            params=params,
        )
        if resp is None:
            raise InvalidResponseException(resp, "TikTok returned an invalid response.")

        for video in resp.get("item_list", []):
            yield api.video(data=video)
            found += 1

        if not resp.get("has_more", False):
            return

        cursor = resp.get("cursor")
        search_id = resp.get("rid", "")


async def search_videos(phrase: str, count: int, ms_token: str) -> list[dict]:
    results = []

    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=[ms_token],
            num_sessions=1,
            sleep_after=3,
            headless=True,
        )

        print(f"Searching for '{phrase}' (fetching up to {count} videos, sorted by most liked)...")

        async for video in search_most_liked(api, phrase, count):
            data = video.as_dict
            stats = data.get("stats", {})
            author = data.get("author", {})
            desc = data.get("desc", "")

            results.append({
                "id": data.get("id"),
                "description": desc,
                "author": author.get("uniqueId", ""),
                "likes": stats.get("diggCount", 0),
                "comments": stats.get("commentCount", 0),
                "shares": stats.get("shareCount", 0),
                "views": stats.get("playCount", 0),
                "url": f"https://www.tiktok.com/@{author.get('uniqueId', '')}/video/{data.get('id')}",
            })

    results.sort(key=lambda v: v["likes"], reverse=True)
    return results


def main():
    parser = argparse.ArgumentParser(description="Scrape TikTok videos by search phrase, sorted by likes")
    parser.add_argument("phrase", help="Search phrase")
    parser.add_argument("--count", type=int, default=30, help="Number of videos to fetch (default: 30)")
    parser.add_argument("--out", default=None, help="Output JSON file (default: print to stdout)")
    parser.add_argument("--ms-token", default=None, help="TikTok msToken cookie value")
    args = parser.parse_args()

    ms_token = args.ms_token or os.environ.get("MS_TOKEN", "")
    if not ms_token:
        print("Error: msToken required. Set MS_TOKEN env var or use --ms-token.", file=sys.stderr)
        print("See script header for instructions on getting your msToken.", file=sys.stderr)
        sys.exit(1)

    videos = asyncio.run(search_videos(args.phrase, args.count, ms_token))

    if not videos:
        print("No videos found.")
        return

    if args.out:
        with open(args.out, "w") as f:
            json.dump(videos, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(videos)} videos to {args.out}")
    else:
        print(f"\nTop {len(videos)} videos for '{args.phrase}' sorted by likes:\n")
        for i, v in enumerate(videos, 1):
            print(f"{i:2}. {v['likes']:>10,} likes  @{v['author']}")
            print(f"     {v['description'][:80]}")
            print(f"     {v['url']}\n")


if __name__ == "__main__":
    main()
