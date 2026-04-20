#!/usr/bin/env python3
"""
Download TikTok videos from the database using yt-dlp.

Usage:
  python3 download.py <keyword> [--min-likes N] [--limit N] [--out DIR]
"""

import argparse
import os
import subprocess
import sys
from sqlalchemy import text
from db import engine

def query_videos(keyword: str, min_likes: int, limit: int) -> list[dict]:
    sql = text("""
        SELECT DISTINCT ON (v.aweme_id)
            v.aweme_id,
            v.likes,
            v.description,
            a.unique_id AS author
        FROM search_results sr
        JOIN searches s ON s.id = sr.search_id
        JOIN videos v ON v.aweme_id = sr.video_id
        LEFT JOIN authors a ON a.uid = v.author_uid
        WHERE s.keyword = :keyword
          AND v.likes >= :min_likes
        ORDER BY v.aweme_id, v.likes DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"keyword": keyword, "min_likes": min_likes}).fetchall()

    videos = [dict(r._mapping) for r in rows]
    videos.sort(key=lambda v: v["likes"] or 0, reverse=True)
    return videos[:limit]


def download_video(rank: int, video: dict, out_dir: str) -> bool:
    author = video["author"] or "unknown"
    aweme_id = video["aweme_id"]
    url = f"https://www.tiktok.com/@{author}/video/{aweme_id}"
    filename = f"{aweme_id}.%(ext)s"
    out_path = os.path.join(out_dir, filename)

    likes = video["likes"] or 0
    print(f"\n[{rank}] {likes:,} likes  @{author}")
    print(f"     {url}")

    result = subprocess.run(
        ["yt-dlp", "--no-playlist", "-o", out_path, url],
        capture_output=False,
    )
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Download TikTok videos from DB")
    parser.add_argument("keyword", help="Search keyword used when scraping")
    parser.add_argument("--min-likes", type=int, default=10_000,
                        help="Minimum likes threshold (default: 10000)")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max videos to download (default: 100)")
    parser.add_argument("--out", default="./videos",
                        help="Output directory (default: ./videos)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    print(f"[*] Querying DB for '{args.keyword}' (min {args.min_likes:,} likes)...")
    videos = query_videos(args.keyword, args.min_likes, args.limit)

    if not videos:
        print("No videos found. Check the keyword and --min-likes threshold.")
        sys.exit(1)

    print(f"[*] Found {len(videos)} videos. Downloading to {args.out}/")

    ok = fail = 0
    for i, video in enumerate(videos, 1):
        if download_video(i, video, args.out):
            ok += 1
        else:
            fail += 1

    print(f"\n[*] Done. {ok} downloaded, {fail} failed.")


if __name__ == "__main__":
    main()
