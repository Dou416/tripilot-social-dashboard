#!/usr/bin/env python3
"""
Social media content collector for Tripilot.
Fetches engagement data from Reddit, Twitter/X, TikTok, and Xiaohongshu.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_FILE = DATA_DIR / "social_data.json"

load_dotenv(BASE_DIR / ".env")


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Reddit (public JSON API — no auth needed)
# ---------------------------------------------------------------------------

def fetch_reddit(config: dict) -> dict | None:
    username = config.get("reddit", {}).get("username")
    if not username:
        log.warning("Reddit: no username configured, skipping")
        return None

    log.info(f"Reddit: fetching posts for u/{username}")
    url = f"https://www.reddit.com/user/{username}/submitted.json"
    headers = {"User-Agent": "TripilotDashboard/1.0"}

    try:
        resp = requests.get(url, headers=headers, params={"limit": 100}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"Reddit: request failed — {e}")
        return None

    children = data.get("data", {}).get("children", [])
    posts = []
    total_score = 0
    total_comments = 0

    for child in children:
        p = child.get("data", {})
        score = p.get("score", 0)
        comments = p.get("num_comments", 0)
        total_score += score
        total_comments += comments

        posts.append({
            "title": p.get("title", ""),
            "url": f"https://reddit.com{p.get('permalink', '')}",
            "subreddit": p.get("subreddit", ""),
            "score": score,
            "comments": comments,
            "upvote_ratio": p.get("upvote_ratio", 0),
            "date": datetime.fromtimestamp(
                p.get("created_utc", 0), tz=timezone.utc
            ).isoformat(),
        })

    # Sort by date descending
    posts.sort(key=lambda x: x["date"], reverse=True)

    result = {
        "account": username,
        "profile_url": f"https://reddit.com/user/{username}",
        "stats": {
            "post_count": len(posts),
            "total_score": total_score,
            "total_comments": total_comments,
        },
        "posts": posts,
    }
    log.info(f"Reddit: collected {len(posts)} posts")
    return result


# ---------------------------------------------------------------------------
# Twitter / X (twikit — uses internal API, needs username+password)
# ---------------------------------------------------------------------------

def fetch_twitter(config: dict) -> dict | None:
    username = config.get("twitter", {}).get("username")
    tw_user = os.getenv("TWITTER_USERNAME")
    tw_pass = os.getenv("TWITTER_PASSWORD")

    if not username:
        log.warning("Twitter: no username configured, skipping")
        return None
    if not tw_user or not tw_pass:
        log.warning("Twitter: TWITTER_USERNAME / TWITTER_PASSWORD not set in .env, skipping")
        return None

    log.info(f"Twitter: fetching tweets for @{username}")

    try:
        import asyncio
        from twikit import Client

        async def _fetch():
            client = Client("en-US")

            cookies_path = BASE_DIR / ".twitter_cookies.json"
            if cookies_path.exists():
                client.load_cookies(str(cookies_path))
                log.info("Twitter: loaded saved cookies")
            else:
                await client.login(
                    auth_info_1=tw_user,
                    auth_info_2=tw_user,
                    password=tw_pass,
                )
                client.save_cookies(str(cookies_path))
                log.info("Twitter: logged in and saved cookies")

            user = await client.get_user_by_screen_name(username)
            tweets = await user.get_tweets("Tweets", count=40)

            posts = []
            total_likes = 0
            total_retweets = 0
            total_views = 0

            for t in tweets:
                likes = t.favorite_count or 0
                retweets = t.retweet_count or 0
                views = getattr(t, "view_count", None) or 0
                total_likes += likes
                total_retweets += retweets
                total_views += int(views) if views else 0

                posts.append({
                    "title": (t.text or "")[:120],
                    "url": f"https://x.com/{username}/status/{t.id}",
                    "likes": likes,
                    "retweets": retweets,
                    "views": int(views) if views else 0,
                    "comments": t.reply_count or 0,
                    "date": t.created_at if isinstance(t.created_at, str)
                        else t.created_at.isoformat() if t.created_at else "",
                })

            posts.sort(key=lambda x: x["date"], reverse=True)

            return {
                "account": username,
                "profile_url": f"https://x.com/{username}",
                "stats": {
                    "post_count": len(posts),
                    "total_likes": total_likes,
                    "total_retweets": total_retweets,
                    "total_views": total_views,
                },
                "posts": posts,
            }

        result = asyncio.run(_fetch())
        log.info(f"Twitter: collected {len(result['posts'])} tweets")
        return result

    except Exception as e:
        log.error(f"Twitter: failed — {e}")
        return None


# ---------------------------------------------------------------------------
# TikTok (TikTokApi — needs ms_token cookie for reliability)
# ---------------------------------------------------------------------------

def fetch_tiktok(config: dict) -> dict | None:
    username = config.get("tiktok", {}).get("username")
    if not username:
        log.warning("TikTok: no username configured, skipping")
        return None

    ms_token = os.getenv("TIKTOK_MS_TOKEN", "")
    if not ms_token:
        log.warning("TikTok: TIKTOK_MS_TOKEN not set in .env, skipping (required for API access)")
        return None

    log.info(f"TikTok: fetching videos for @{username}")

    try:
        import asyncio
        from TikTokApi import TikTokApi

        async def _fetch():
            async with TikTokApi() as api:
                await api.create_sessions(
                    ms_tokens=[ms_token],
                    num_sessions=1,
                    sleep_after=3,
                )
                user = api.user(username)
                videos = []
                async for video in user.videos(count=30):
                    info = video.as_dict
                    stats = info.get("stats", {})
                    videos.append({
                        "title": info.get("desc", "")[:120],
                        "url": f"https://www.tiktok.com/@{username}/video/{info.get('id', '')}",
                        "views": stats.get("playCount", 0),
                        "likes": stats.get("diggCount", 0),
                        "comments": stats.get("commentCount", 0),
                        "shares": stats.get("shareCount", 0),
                        "date": datetime.fromtimestamp(
                            info.get("createTime", 0), tz=timezone.utc
                        ).isoformat(),
                    })

                videos.sort(key=lambda x: x["date"], reverse=True)

                total_views = sum(v["views"] for v in videos)
                total_likes = sum(v["likes"] for v in videos)

                return {
                    "account": username,
                    "profile_url": f"https://www.tiktok.com/@{username}",
                    "stats": {
                        "post_count": len(videos),
                        "total_views": total_views,
                        "total_likes": total_likes,
                    },
                    "posts": videos,
                }

        result = asyncio.run(_fetch())
        log.info(f"TikTok: collected {len(result['posts'])} videos")
        return result

    except Exception as e:
        log.error(f"TikTok: failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Xiaohongshu (xhs library — needs browser cookie)
# ---------------------------------------------------------------------------

def fetch_xiaohongshu(config: dict) -> dict | None:
    user_id = config.get("xiaohongshu", {}).get("user_id")
    cookie = os.getenv("XHS_COOKIE", "")

    if not user_id:
        log.warning("Xiaohongshu: no user_id configured, skipping")
        return None
    if not cookie:
        log.warning("Xiaohongshu: XHS_COOKIE not set in .env, skipping")
        return None

    log.info(f"Xiaohongshu: fetching notes for user {user_id}")

    try:
        from xhs import XhsClient

        client = XhsClient(cookie=cookie)
        notes_res = client.get_user_notes(user_id)
        notes_list = notes_res.get("notes", []) if isinstance(notes_res, dict) else []

        posts = []
        total_likes = 0
        total_collects = 0

        for note in notes_list:
            note_id = note.get("note_id", "")
            # Fetch full note detail for engagement metrics
            try:
                detail = client.get_note_by_id(note_id)
                interact = detail.get("interact_info", {})
                likes = int(interact.get("liked_count", "0"))
                collects = int(interact.get("collected_count", "0"))
                comments = int(interact.get("comment_count", "0"))
                shares = int(interact.get("share_count", "0"))
            except Exception:
                likes = note.get("liked_count", 0)
                collects = 0
                comments = 0
                shares = 0

            total_likes += likes
            total_collects += collects

            posts.append({
                "title": note.get("display_title", note.get("title", "")),
                "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "likes": likes,
                "collects": collects,
                "comments": comments,
                "shares": shares,
                "date": datetime.fromtimestamp(
                    note.get("time", 0) / 1000, tz=timezone.utc
                ).isoformat() if note.get("time") else "",
            })

        posts.sort(key=lambda x: x["date"], reverse=True)

        result = {
            "account": user_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{user_id}",
            "stats": {
                "post_count": len(posts),
                "total_likes": total_likes,
                "total_collects": total_collects,
            },
            "posts": posts,
        }
        log.info(f"Xiaohongshu: collected {len(posts)} notes")
        return result

    except Exception as e:
        log.error(f"Xiaohongshu: failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    log.info("Starting social media data collection")

    platforms = {}

    # Fetch all platforms — each one handles its own errors
    reddit_data = fetch_reddit(config)
    if reddit_data:
        platforms["reddit"] = reddit_data

    twitter_data = fetch_twitter(config)
    if twitter_data:
        platforms["twitter"] = twitter_data

    tiktok_data = fetch_tiktok(config)
    if tiktok_data:
        platforms["tiktok"] = tiktok_data

    xhs_data = fetch_xiaohongshu(config)
    if xhs_data:
        platforms["xiaohongshu"] = xhs_data

    output = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Data saved to {OUTPUT_FILE}")
    log.info(f"Platforms collected: {list(platforms.keys()) or 'none'}")

    if not platforms:
        log.warning("No platform data collected. Check config.yaml and .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
