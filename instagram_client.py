"""
Instagram Content Publishing API client via Facebook Graph API.
Uses the two-step container creation + publish flow.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("social-agent.instagram")


async def post_to_instagram(
    image_url: str,
    caption: str,
) -> dict:
    """
    Post an image to Instagram using the Content Publishing API.

    Step 1: Create a media container with image_url and caption
    Step 2: Publish the container

    Note: Instagram requires a publicly accessible image URL.
    """
    if not image_url:
        return {
            "success": False,
            "error": "Instagram requires an image URL for posting",
            "platform": "instagram",
        }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: Create media container
            logger.info(f"Creating Instagram media container for image: {image_url[:80]}...")
            container_resp = await client.post(
                f"{settings.FB_GRAPH_URL}/{settings.IG_ACCOUNT_ID}/media",
                data={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            container_data = container_resp.json()

            if "error" in container_data:
                error_msg = container_data["error"].get("message", "Unknown error")
                error_code = container_data["error"].get("code", 0)
                logger.error(f"Instagram container error ({error_code}): {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "platform": "instagram",
                    "step": "container_creation",
                }

            container_id = container_data.get("id")
            if not container_id:
                return {
                    "success": False,
                    "error": "No container ID returned",
                    "platform": "instagram",
                    "step": "container_creation",
                }

            logger.info(f"Container created: {container_id}")

            # Brief pause to let Instagram process the container
            import asyncio
            await asyncio.sleep(3)

            # Check container status before publishing
            status_ok = False
            for attempt in range(10):
                status_resp = await client.get(
                    f"{settings.FB_GRAPH_URL}/{container_id}",
                    params={
                        "fields": "status_code,status",
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )
                status_data = status_resp.json()
                status_code = status_data.get("status_code", "")
                if status_code == "FINISHED":
                    status_ok = True
                    break
                elif status_code == "ERROR":
                    error_detail = status_data.get("status", "Container processing failed")
                    return {
                        "success": False,
                        "error": f"Container error: {error_detail}",
                        "platform": "instagram",
                        "step": "container_status",
                    }
                logger.info(f"Container status: {status_code}, waiting... (attempt {attempt + 1})")
                await asyncio.sleep(3)

            if not status_ok:
                return {
                    "success": False,
                    "error": "Container processing timed out",
                    "platform": "instagram",
                    "step": "container_status",
                }

            # Step 2: Publish the container
            logger.info(f"Publishing Instagram container {container_id}...")
            publish_resp = await client.post(
                f"{settings.FB_GRAPH_URL}/{settings.IG_ACCOUNT_ID}/media_publish",
                data={
                    "creation_id": container_id,
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            publish_data = publish_resp.json()

            if "error" in publish_data:
                error_msg = publish_data["error"].get("message", "Unknown error")
                error_code = publish_data["error"].get("code", 0)
                logger.error(f"Instagram publish error ({error_code}): {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "platform": "instagram",
                    "step": "publish",
                }

            media_id = publish_data.get("id", "")
            logger.info(f"Posted to Instagram: {media_id}")
            return {
                "success": True,
                "media_id": media_id,
                "container_id": container_id,
                "platform": "instagram",
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Instagram posting failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "platform": "instagram",
        }


async def get_media_engagement(media_id: str) -> dict:
    """Fetch engagement metrics for an Instagram media post."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get basic media info
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{media_id}",
                params={
                    "fields": "id,caption,like_count,comments_count,media_type,permalink,timestamp",
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                logger.error(f"IG engagement error: {data['error'].get('message', 'Unknown')}")
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            likes = data.get("like_count", 0)
            comments = data.get("comments_count", 0)

            # Try to get insights (reach, impressions, saves)
            reach = 0
            impressions = 0
            saves = 0
            try:
                insights_resp = await client.get(
                    f"{settings.FB_GRAPH_URL}/{media_id}/insights",
                    params={
                        "metric": "impressions,reach,saved",
                        "access_token": settings.FB_PAGE_TOKEN,
                    },
                )
                insights_data = insights_resp.json()
                if "data" in insights_data:
                    for metric in insights_data["data"]:
                        name = metric.get("name", "")
                        value = metric.get("values", [{}])[0].get("value", 0)
                        if name == "impressions":
                            impressions = value
                        elif name == "reach":
                            reach = value
                        elif name == "saved":
                            saves = value
            except Exception as e:
                logger.debug(f"Could not fetch IG insights: {e}")

            return {
                "success": True,
                "media_id": media_id,
                "platform": "instagram",
                "likes": likes,
                "comments": comments,
                "saves": saves,
                "reach": reach,
                "impressions": impressions,
                "engagement_total": likes + comments + saves,
                "permalink": data.get("permalink", ""),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Failed to fetch IG engagement for {media_id}: {e}")
        return {"success": False, "error": str(e), "media_id": media_id}


async def get_account_stats() -> dict:
    """Fetch Instagram account statistics."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.IG_ACCOUNT_ID}",
                params={
                    "fields": "id,username,name,followers_count,follows_count,media_count,biography,profile_picture_url",
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            return {
                "success": True,
                "platform": "instagram",
                "username": data.get("username", settings.IG_USERNAME),
                "name": data.get("name", "PetHub Online"),
                "followers": data.get("followers_count", 0),
                "following": data.get("follows_count", 0),
                "media_count": data.get("media_count", 0),
                "biography": data.get("biography", ""),
                "profile_picture": data.get("profile_picture_url", ""),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Failed to fetch IG account stats: {e}")
        return {"success": False, "error": str(e), "platform": "instagram"}


async def get_recent_media(limit: int = 10) -> list[dict]:
    """Fetch recent media from Instagram account."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.IG_ACCOUNT_ID}/media",
                params={
                    "fields": "id,caption,like_count,comments_count,media_type,permalink,timestamp,thumbnail_url,media_url",
                    "limit": limit,
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()

            if "error" in data:
                return []

            media = []
            for item in data.get("data", []):
                media.append({
                    "media_id": item.get("id", ""),
                    "caption": (item.get("caption", ""))[:120],
                    "likes": item.get("like_count", 0),
                    "comments": item.get("comments_count", 0),
                    "media_type": item.get("media_type", ""),
                    "permalink": item.get("permalink", ""),
                    "timestamp": item.get("timestamp", ""),
                    "thumbnail_url": item.get("thumbnail_url", ""),
                    "media_url": item.get("media_url", ""),
                })
            return media

    except Exception as e:
        logger.error(f"Failed to fetch IG recent media: {e}")
        return []
