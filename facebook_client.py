"""
Facebook Graph API client for posting content and fetching engagement.
Uses the Page Access Token for all operations.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("social-agent.facebook")


async def post_to_facebook(
    caption: str,
    link: Optional[str] = None,
    image_url: Optional[str] = None,
) -> dict:
    """
    Post to a Facebook Page.

    For link posts: POST /{page_id}/feed with message + link
    For photo posts: POST /{page_id}/photos with url + caption
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if image_url and not link:
                # Photo post (image only, no link)
                resp = await client.post(
                    f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}/photos",
                    data={
                        "url": image_url,
                        "caption": caption,
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )
            elif image_url and link:
                # Link post with message (FB will auto-generate preview from link)
                # Include image as part of the link post message
                resp = await client.post(
                    f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}/feed",
                    data={
                        "message": caption,
                        "link": link,
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )
            else:
                # Text + link post
                resp = await client.post(
                    f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}/feed",
                    data={
                        "message": caption,
                        "link": link or "",
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )

            data = resp.json()

            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                error_code = data["error"].get("code", 0)
                logger.error(f"Facebook API error ({error_code}): {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "platform": "facebook",
                }

            post_id = data.get("id") or data.get("post_id", "")
            logger.info(f"Posted to Facebook: {post_id}")
            return {
                "success": True,
                "post_id": post_id,
                "platform": "facebook",
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Facebook posting failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "platform": "facebook",
        }


async def get_post_engagement(post_id: str) -> dict:
    """
    Fetch engagement metrics for a Facebook post.
    Uses the post fields endpoint for likes, comments, shares.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get basic engagement counts
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{post_id}",
                params={
                    "fields": "likes.summary(true),comments.summary(true),shares,created_time,message",
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                logger.error(f"FB engagement error: {data['error'].get('message', 'Unknown')}")
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            likes = data.get("likes", {}).get("summary", {}).get("total_count", 0)
            comments = data.get("comments", {}).get("summary", {}).get("total_count", 0)
            shares = data.get("shares", {}).get("count", 0)

            # Try to get post insights (reach, impressions)
            reach = 0
            impressions = 0
            try:
                insights_resp = await client.get(
                    f"{settings.FB_GRAPH_URL}/{post_id}/insights",
                    params={
                        "metric": "post_impressions,post_impressions_unique",
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )
                insights_data = insights_resp.json()
                if "data" in insights_data:
                    for metric in insights_data["data"]:
                        if metric["name"] == "post_impressions":
                            impressions = metric.get("values", [{}])[0].get("value", 0)
                        elif metric["name"] == "post_impressions_unique":
                            reach = metric.get("values", [{}])[0].get("value", 0)
            except Exception as e:
                logger.debug(f"Could not fetch post insights: {e}")

            return {
                "success": True,
                "post_id": post_id,
                "platform": "facebook",
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "reach": reach,
                "impressions": impressions,
                "engagement_total": likes + comments + shares,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Failed to fetch FB engagement for {post_id}: {e}")
        return {"success": False, "error": str(e), "post_id": post_id}


async def get_page_stats() -> dict:
    """Fetch Facebook Page statistics."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Page info
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}",
                params={
                    "fields": "name,fan_count,followers_count,talking_about_count,new_like_count",
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            return {
                "success": True,
                "platform": "facebook",
                "page_name": data.get("name", "PetHub Online"),
                "followers": data.get("followers_count", data.get("fan_count", 0)),
                "likes": data.get("fan_count", 0),
                "talking_about": data.get("talking_about_count", 0),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Failed to fetch FB page stats: {e}")
        return {"success": False, "error": str(e), "platform": "facebook"}


async def get_recent_posts(limit: int = 10) -> list[dict]:
    """Fetch recent posts from the Facebook Page."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}/feed",
                params={
                    "fields": "id,message,created_time,permalink_url,likes.summary(true),comments.summary(true),shares",
                    "limit": limit,
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                return []

            posts = []
            for post in data.get("data", []):
                posts.append({
                    "post_id": post.get("id", ""),
                    "message": (post.get("message", ""))[:120],
                    "created_time": post.get("created_time", ""),
                    "permalink": post.get("permalink_url", ""),
                    "likes": post.get("likes", {}).get("summary", {}).get("total_count", 0),
                    "comments": post.get("comments", {}).get("summary", {}).get("total_count", 0),
                    "shares": post.get("shares", {}).get("count", 0),
                })
            return posts

    except Exception as e:
        logger.error(f"Failed to fetch FB recent posts: {e}")
        return []
