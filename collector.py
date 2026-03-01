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


def normalize_twitter_handle(value: str | None) -> str:
    """Normalizes @handle/user input into a bare Twitter handle."""
    if not value:
        return ""
    return value.strip().lstrip("@")


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


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
    username = normalize_twitter_handle(config.get("twitter", {}).get("username"))
    tw_user = normalize_twitter_handle(os.getenv("TWITTER_USERNAME"))
    tw_pass = os.getenv("TWITTER_PASSWORD")
    tw_email = os.getenv("TWITTER_EMAIL", "").strip()

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
                    auth_info_2=tw_email or tw_user,
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
# Trend Fetching
# ---------------------------------------------------------------------------

def fetch_reddit_trends(keywords: list[str]) -> dict | None:
    """Fetches trending posts on Reddit for a list of keywords."""
    log.info(f"Reddit Trends: fetching posts for keywords: {keywords}")
    all_posts = []
    for keyword in keywords:
        log.info(f"Reddit Trends: searching for '{keyword}'")
        url = "https://www.reddit.com/search.json"
        headers = {"User-Agent": "TripilotDashboard/1.0"}
        params = {"q": keyword, "sort": "top", "t": "week", "limit": 20}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for child in children:
                p = child.get("data", {})
                all_posts.append({
                    "keyword": keyword,
                    "title": p.get("title", ""),
                    "url": f"https://reddit.com{p.get('permalink', '')}",
                    "subreddit": p.get("subreddit", ""),
                    "score": p.get("score", 0),
                    "comments": p.get("num_comments", 0),
                    "upvote_ratio": p.get("upvote_ratio", 0),
                    "date": datetime.fromtimestamp(
                        p.get("created_utc", 0), tz=timezone.utc
                    ).isoformat(),
                })
        except Exception as e:
            log.error(f"Reddit Trends: request failed for '{keyword}' — {e}")
            continue

    if not all_posts:
        return None

    # Sort by score descending
    all_posts.sort(key=lambda x: x["score"], reverse=True)

    return {
        "keyword_results": all_posts,
        "stats": {"post_count": len(all_posts)},
    }

def fetch_twitter_trends(keywords: list[str]) -> dict | None:
    """Fetches trending tweets on Twitter for a list of keywords."""
    log.info(f"Twitter Trends: fetching tweets for keywords: {keywords}")
    tw_user = normalize_twitter_handle(os.getenv("TWITTER_USERNAME"))
    tw_pass = os.getenv("TWITTER_PASSWORD")
    tw_email = os.getenv("TWITTER_EMAIL", "").strip()

    if not tw_user or not tw_pass:
        log.warning("Twitter Trends: TWITTER_USERNAME / TWITTER_PASSWORD not set in .env, skipping")
        return None

    all_tweets = []
    try:
        import asyncio
        from twikit import Client

        async def _fetch():
            client = Client("en-US")
            cookies_path = BASE_DIR / ".twitter_cookies.json"
            if cookies_path.exists():
                client.load_cookies(str(cookies_path))
                log.info("Twitter Trends: loaded saved cookies")
            else:
                await client.login(
                    auth_info_1=tw_user,
                    auth_info_2=tw_email or tw_user,
                    password=tw_pass,
                )
                client.save_cookies(str(cookies_path))
                log.info("Twitter Trends: logged in and saved cookies")

            for keyword in keywords:
                log.info(f"Twitter Trends: searching for '{keyword}'")
                try:
                    search_results = await client.search_tweet(keyword, "Top")
                except Exception as e:
                    log.error(f"Twitter Trends: search failed for '{keyword}' — {e}")
                    continue

                count = 0
                for t in search_results:
                    if count >= 20:
                        break
                    all_tweets.append({
                        "keyword": keyword,
                        "title": (t.text or "")[:120],
                        "url": f"https://x.com/{t.user.screen_name}/status/{t.id}",
                        "likes": t.favorite_count or 0,
                        "retweets": t.retweet_count or 0,
                        "views": getattr(t, "view_count", None) or 0,
                        "comments": t.reply_count or 0,
                        "date": t.created_at if isinstance(t.created_at, str)
                            else t.created_at.isoformat() if t.created_at else "",
                    })
                    count += 1

        asyncio.run(_fetch())

        if not all_tweets:
            return None

        # Sort by likes descending
        all_tweets.sort(key=lambda x: x["likes"], reverse=True)

        return {
            "keyword_results": all_tweets,
            "stats": {"post_count": len(all_tweets)},
        }

    except Exception as e:
        log.error(f"Twitter Trends: failed — {e}")
        return None

def fetch_tiktok_trends(keywords: list[str]) -> dict | None:
    """Fetches trending videos on TikTok for a list of keywords."""
    log.info(f"TikTok Trends: fetching videos for keywords: {keywords}")
    ms_token = os.getenv("TIKTOK_MS_TOKEN", "")
    if not ms_token:
        log.warning("TikTok Trends: TIKTOK_MS_TOKEN not set in .env, skipping (required for API access)")
        return None

    all_videos = []
    try:
        import asyncio
        from TikTokApi import TikTokApi

        def video_to_trend_row(video, keyword: str) -> dict:
            raw = getattr(video, "as_dict", None)
            if callable(raw):
                info = raw()
            elif isinstance(raw, dict):
                info = raw
            else:
                info = {}
            stats = info.get("stats", {})
            author = info.get("author", {})
            return {
                "keyword": keyword,
                "title": info.get("desc", "")[:120],
                "url": f"https://www.tiktok.com/@{author.get('uniqueId', '')}/video/{info.get('id', '')}",
                "views": stats.get("playCount", 0),
                "likes": stats.get("diggCount", 0),
                "comments": stats.get("commentCount", 0),
                "shares": stats.get("shareCount", 0),
                "date": datetime.fromtimestamp(
                    info.get("createTime", 0), tz=timezone.utc
                ).isoformat() if info.get("createTime") else "",
            }

        async def _fetch():
            async with TikTokApi() as api:
                await api.create_sessions(ms_tokens=[ms_token], num_sessions=1, sleep_after=3)
                for keyword in keywords:
                    log.info(f"TikTok Trends: searching for '{keyword}'")
                    collected = 0

                    # Strategy 1: keyword search endpoint (newer versions)
                    search_obj = getattr(api, "search", None)
                    if search_obj and hasattr(search_obj, "videos"):
                        try:
                            async for video in search_obj.videos(keyword, count=20):
                                all_videos.append(video_to_trend_row(video, keyword))
                                collected += 1
                        except Exception as e:
                            log.warning(f"TikTok Trends: keyword search failed for '{keyword}' — {e}")

                    # Strategy 2: hashtag endpoint fallback (older/newer variants)
                    if collected == 0 and hasattr(api, "hashtag"):
                        hashtag_name = keyword.strip().replace(" ", "")
                        if hashtag_name:
                            try:
                                try:
                                    hashtag = api.hashtag(name=hashtag_name)
                                except TypeError:
                                    hashtag = api.hashtag(hashtag_name)
                                async for video in hashtag.videos(count=20):
                                    all_videos.append(video_to_trend_row(video, keyword))
                                    collected += 1
                            except Exception as e:
                                log.error(f"TikTok Trends: hashtag search failed for '{keyword}' — {e}")

                    if collected == 0:
                        log.warning(f"TikTok Trends: no compatible search path returned results for '{keyword}'")

        asyncio.run(_fetch())

        if not all_videos:
            return None

        # Sort by views descending
        all_videos.sort(key=lambda x: x["views"], reverse=True)

        return {
            "keyword_results": all_videos,
            "stats": {"post_count": len(all_videos)},
        }

    except Exception as e:
        log.error(f"TikTok Trends: failed — {e}")
        return None

def fetch_instagram_trends(keywords: list[str]) -> dict | None:
    """Fetches trending posts on Instagram for a list of keywords."""
    log.info(f"Instagram Trends: fetching posts for keywords: {keywords}")
    insta_user = os.getenv("INSTAGRAM_USERNAME")
    insta_pass = os.getenv("INSTAGRAM_PASSWORD")

    if not insta_user or not insta_pass:
        log.warning("Instagram Trends: INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD not set in .env, skipping")
        return None
    
    all_posts = []
    try:
        import instaloader

        L = instaloader.Instaloader()
        L.login(insta_user, insta_pass)
        log.info("Instagram Trends: logged in")

        for keyword in keywords:
            # Instagram hashtag search does not support spaces.
            hashtag_name = keyword.strip().replace(" ", "")
            if not hashtag_name:
                continue
            log.info(f"Instagram Trends: searching for '#{hashtag_name}'")
            hashtag = instaloader.Hashtag.from_name(L.context, hashtag_name)
            for post in hashtag.get_posts():
                if len(all_posts) >= 20 * len(keywords):
                    break
                all_posts.append({
                    "keyword": keyword,
                    "title": post.caption[:120] if post.caption else "",
                    "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "likes": post.likes,
                    "comments": post.comments,
                    "date": post.date_utc.isoformat(),
                })
            if len(all_posts) >= 20 * len(keywords):
                break
        
        if not all_posts:
            return None

        # Sort by likes descending
        all_posts.sort(key=lambda x: x["likes"], reverse=True)
        
        # Limit to top 20 overall
        top_20_posts = all_posts[:20]

        return {
            "keyword_results": top_20_posts,
            "stats": {"post_count": len(top_20_posts)},
        }

    except Exception as e:
        log.error(f"Instagram Trends: failed — {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    log.info("Starting social media data collection")

    platforms = {}
    trends = {}
    reddit_enabled = not env_flag("DISABLE_REDDIT")

    # Fetch user-specific data
    if reddit_enabled:
        reddit_data = fetch_reddit(config)
        if reddit_data:
            platforms["reddit"] = reddit_data
    else:
        log.info("Reddit collection disabled by DISABLE_REDDIT")

    twitter_data = fetch_twitter(config)
    if twitter_data:
        platforms["twitter"] = twitter_data

    tiktok_data = fetch_tiktok(config)
    if tiktok_data:
        platforms["tiktok"] = tiktok_data

    xhs_data = fetch_xiaohongshu(config)
    if xhs_data:
        platforms["xiaohongshu"] = xhs_data
    
    # Fetch trending topics
    keywords = config.get("trends", {}).get("keywords", [])
    if keywords:
        if reddit_enabled:
            reddit_trends = fetch_reddit_trends(keywords)
            if reddit_trends:
                trends["reddit"] = reddit_trends

        twitter_trends = fetch_twitter_trends(keywords)
        if twitter_trends:
            trends["twitter"] = twitter_trends

        tiktok_trends = fetch_tiktok_trends(keywords)
        if tiktok_trends:
            trends["tiktok"] = tiktok_trends

        instagram_trends = fetch_instagram_trends(keywords)
        if instagram_trends:
            trends["instagram"] = instagram_trends


    output = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms,
        "trends": trends,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Data saved to {OUTPUT_FILE}")
    log.info(f"Platforms collected: {list(platforms.keys()) or 'none'}")
    log.info(f"Trends collected for: {list(trends.keys()) or 'none'}")

    if not platforms and not trends:
        log.warning("No data collected. Check config.yaml and .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
