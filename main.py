"""
PetHub Social Media Agent - Main FastAPI Application
Handles automated posting to Facebook and Instagram, engagement tracking,
content scheduling, and a dashboard for monitoring.
"""

import json
import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import settings
from content_generator import prepare_post, get_all_content, select_next_content
from facebook_client import (
    post_to_facebook,
    get_post_engagement,
    get_page_stats,
    get_recent_posts as fb_recent_posts,
)
from instagram_client import (
    post_to_instagram,
    get_media_engagement,
    get_account_stats,
    get_recent_media as ig_recent_media,
)
from manager_client import (
    heartbeat,
    create_task,
    update_task,
    update_kpi,
    log_message,
    register_agent,
)
from engagement_optimizer import (
    analyze_post_performance,
    get_top_performers,
    select_optimized_content,
)
from trend_detector import get_trending_report, get_seasonal_suggestions
from reel_generator import generate_and_publish_reel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("social-agent")

scheduler = AsyncIOScheduler()
UK_TZ = pytz.timezone("Europe/London")

# ─── In-memory state (persisted to JSON) ────────────────────────────────────

state = {
    "posts": [],           # List of all posts made
    "posted_map": {},      # {fingerprint: last_posted_iso}
    "queue": [],           # Upcoming scheduled posts
    "engagement": {},      # {post_id_or_media_id: engagement_data}
    "fb_stats": {},        # Latest FB page stats
    "ig_stats": {},        # Latest IG account stats
    "last_post_time": None,
    "total_posts_fb": 0,
    "total_posts_ig": 0,
    "errors": [],          # Recent errors
    "started_at": None,
    "ab_tests": [],        # A/B test pairs
    "trends_report": {},   # Latest trends report
    "optimization_report": {},  # Latest optimization report
    "reels_generated": 0,       # Total reels generated
    "reels_history": [],        # List of reel generation results
    "reel_running": False,      # Whether a reel is currently being generated
}


def load_state():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    if os.path.exists(settings.DB_PATH):
        try:
            with open(settings.DB_PATH, "r") as f:
                data = json.load(f)
                state["posts"] = data.get("posts", [])
                state["posted_map"] = data.get("posted_map", {})
                state["queue"] = data.get("queue", [])
                state["engagement"] = data.get("engagement", {})
                state["fb_stats"] = data.get("fb_stats", {})
                state["ig_stats"] = data.get("ig_stats", {})
                state["last_post_time"] = data.get("last_post_time")
                state["total_posts_fb"] = data.get("total_posts_fb", 0)
                state["total_posts_ig"] = data.get("total_posts_ig", 0)
                state["errors"] = data.get("errors", [])[-50:]
                state["ab_tests"] = data.get("ab_tests", [])[-100:]
                state["trends_report"] = data.get("trends_report", {})
                state["optimization_report"] = data.get("optimization_report", {})
                state["reels_generated"] = data.get("reels_generated", 0)
                state["reels_history"] = data.get("reels_history", [])[-50:]
                state["reel_running"] = False  # Reset on load
                logger.info(f"Loaded state: {len(state['posts'])} posts, {len(state['posted_map'])} fingerprints")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")


def save_state():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    try:
        with open(settings.DB_PATH, "w") as f:
            json.dump(
                {
                    "posts": state["posts"][-200:],
                    "posted_map": state["posted_map"],
                    "queue": state["queue"],
                    "engagement": state["engagement"],
                    "fb_stats": state["fb_stats"],
                    "ig_stats": state["ig_stats"],
                    "last_post_time": state["last_post_time"],
                    "total_posts_fb": state["total_posts_fb"],
                    "total_posts_ig": state["total_posts_ig"],
                    "errors": state["errors"][-50:],
                    "ab_tests": state["ab_tests"][-100:],
                    "trends_report": state["trends_report"],
                    "optimization_report": state["optimization_report"],
                    "reels_generated": state["reels_generated"],
                    "reels_history": state["reels_history"][-50:],
                },
                f,
                default=str,
            )
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def add_error(msg: str):
    state["errors"].append({
        "message": msg,
        "time": datetime.now(timezone.utc).isoformat(),
    })
    state["errors"] = state["errors"][-50:]


# ─── Core posting functions ─────────────────────────────────────────────────

async def do_post_to_both(prepared: Optional[dict] = None, variant: str = "A") -> dict:
    """Post to both Facebook and Instagram."""
    if not prepared:
        # Try engagement-optimized selection first
        all_content = await get_all_content()
        optimized = select_optimized_content(all_content, state["posted_map"], state["posts"])
        if optimized:
            prepared = await prepare_post(state["posted_map"], selected_content=optimized, variant=variant)
        else:
            prepared = await prepare_post(state["posted_map"], variant=variant)
    if not prepared:
        msg = "No content available to post"
        logger.warning(msg)
        add_error(msg)
        return {"success": False, "error": msg}

    results = {"title": prepared["title"], "url": prepared["url"], "facebook": None, "instagram": None}

    # Post to Facebook
    fb_result = await post_to_facebook(
        caption=prepared["fb_caption"],
        link=prepared["url"],
        image_url=prepared.get("image_url"),
    )
    results["facebook"] = fb_result

    if fb_result["success"]:
        state["total_posts_fb"] += 1

    # Post to Instagram (only if we have an image)
    if prepared.get("image_url"):
        ig_result = await post_to_instagram(
            image_url=prepared["image_url"],
            caption=prepared["ig_caption"],
        )
        results["instagram"] = ig_result
        if ig_result["success"]:
            state["total_posts_ig"] += 1
    else:
        results["instagram"] = {
            "success": False,
            "error": "No image available for Instagram post",
            "platform": "instagram",
        }

    # Record the post
    now_iso = datetime.now(timezone.utc).isoformat()
    post_record = {
        "id": len(state["posts"]) + 1,
        "content_id": prepared["content_id"],
        "fingerprint": prepared["fingerprint"],
        "title": prepared["title"],
        "url": prepared["url"],
        "category": prepared["category"],
        "content_type": prepared["content_type"],
        "image_url": prepared.get("image_url"),
        "fb_post_id": fb_result.get("post_id", ""),
        "fb_success": fb_result["success"],
        "fb_error": fb_result.get("error", ""),
        "ig_media_id": results["instagram"].get("media_id", "") if results["instagram"] else "",
        "ig_success": results["instagram"]["success"] if results["instagram"] else False,
        "ig_error": results["instagram"].get("error", "") if results["instagram"] else "",
        "posted_at": now_iso,
        "engagement": {},
        "variant": prepared.get("variant", "A"),
        "ab_test_id": prepared.get("ab_test_id"),
    }
    state["posts"].append(post_record)
    state["posted_map"][prepared["fingerprint"]] = now_iso
    state["last_post_time"] = now_iso

    if not fb_result["success"]:
        add_error(f"FB post failed for '{prepared['title']}': {fb_result.get('error', 'unknown')}")
    if results["instagram"] and not results["instagram"]["success"]:
        add_error(f"IG post failed for '{prepared['title']}': {results['instagram'].get('error', 'unknown')}")

    save_state()

    # Notify manager
    success_count = sum(1 for p in ["facebook", "instagram"] if results.get(p, {}).get("success"))
    await log_message(
        "info" if success_count > 0 else "error",
        f"Social post '{prepared['title']}': FB={'OK' if fb_result['success'] else 'FAIL'}, IG={'OK' if results['instagram'].get('success') else 'FAIL'}",
    )

    return results


async def do_post_facebook_only() -> dict:
    """Post to Facebook only."""
    prepared = await prepare_post(state["posted_map"])
    if not prepared:
        return {"success": False, "error": "No content available"}

    fb_result = await post_to_facebook(
        caption=prepared["fb_caption"],
        link=prepared["url"],
        image_url=prepared.get("image_url"),
    )

    if fb_result["success"]:
        state["total_posts_fb"] += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        state["posts"].append({
            "id": len(state["posts"]) + 1,
            "content_id": prepared["content_id"],
            "fingerprint": prepared["fingerprint"],
            "title": prepared["title"],
            "url": prepared["url"],
            "category": prepared["category"],
            "content_type": prepared["content_type"],
            "fb_post_id": fb_result.get("post_id", ""),
            "fb_success": True,
            "ig_success": False,
            "posted_at": now_iso,
            "engagement": {},
        })
        state["posted_map"][prepared["fingerprint"]] = now_iso
        state["last_post_time"] = now_iso
        save_state()
    else:
        add_error(f"FB-only post failed: {fb_result.get('error', 'unknown')}")
        save_state()

    return {"title": prepared["title"], "facebook": fb_result}


async def do_post_instagram_only() -> dict:
    """Post to Instagram only."""
    prepared = await prepare_post(state["posted_map"])
    if not prepared:
        return {"success": False, "error": "No content available"}

    if not prepared.get("image_url"):
        return {"success": False, "error": "No image available for Instagram"}

    ig_result = await post_to_instagram(
        image_url=prepared["image_url"],
        caption=prepared["ig_caption"],
    )

    if ig_result["success"]:
        state["total_posts_ig"] += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        state["posts"].append({
            "id": len(state["posts"]) + 1,
            "content_id": prepared["content_id"],
            "fingerprint": prepared["fingerprint"],
            "title": prepared["title"],
            "url": prepared["url"],
            "category": prepared["category"],
            "content_type": prepared["content_type"],
            "ig_media_id": ig_result.get("media_id", ""),
            "fb_success": False,
            "ig_success": True,
            "posted_at": now_iso,
            "engagement": {},
        })
        state["posted_map"][prepared["fingerprint"]] = now_iso
        state["last_post_time"] = now_iso
        save_state()
    else:
        add_error(f"IG-only post failed: {ig_result.get('error', 'unknown')}")
        save_state()

    return {"title": prepared["title"], "instagram": ig_result}


# ─── A/B Testing ──────────────────────────────────────────────────────────

async def start_ab_test(content_override: Optional[dict] = None) -> dict:
    """Start a new A/B test: prepare variant A and queue variant B for next slot."""
    all_content = await get_all_content()
    if not all_content:
        return {"success": False, "error": "No content available"}

    if content_override:
        selected = content_override
    else:
        optimized = select_optimized_content(all_content, state["posted_map"], state["posts"])
        if optimized:
            selected = optimized
        else:
            selected = select_next_content(all_content, state["posted_map"])

    if not selected:
        return {"success": False, "error": "No content selected"}

    test_id = f"ab_{len(state['ab_tests']) + 1}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"

    # Post variant A now
    prepared_a = await prepare_post(state["posted_map"], selected_content=selected, variant="A")
    if not prepared_a:
        return {"success": False, "error": "Failed to prepare variant A"}

    prepared_a["ab_test_id"] = test_id
    result_a = await do_post_to_both(prepared=prepared_a, variant="A")

    # Create test record
    ab_test = {
        "test_id": test_id,
        "content_id": selected["id"],
        "fingerprint": selected["fingerprint"],
        "title": selected.get("title", ""),
        "category": selected.get("category", ""),
        "status": "variant_a_posted",
        "variant_a": {
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "fb_post_id": result_a.get("facebook", {}).get("post_id", ""),
            "ig_media_id": result_a.get("instagram", {}).get("media_id", ""),
            "fb_success": result_a.get("facebook", {}).get("success", False),
            "ig_success": result_a.get("instagram", {}).get("success", False),
            "engagement": {},
        },
        "variant_b": {
            "posted_at": None,
            "fb_post_id": "",
            "ig_media_id": "",
            "fb_success": False,
            "ig_success": False,
            "engagement": {},
        },
        "winner": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluated_at": None,
        # Store selected content data for variant B posting later
        "_pending_content": {
            "id": selected["id"],
            "title": selected.get("title", ""),
            "content": selected.get("content", ""),
            "url": selected.get("url", ""),
            "category": selected.get("category", ""),
            "type": selected.get("type", "page"),
            "fingerprint": selected.get("fingerprint", ""),
            "featured_media_id": selected.get("featured_media_id", 0),
        },
    }
    state["ab_tests"].append(ab_test)
    save_state()

    logger.info(f"A/B test {test_id} started: variant A posted for '{selected.get('title', '')}'")
    return {"success": True, "test_id": test_id, "variant_a_result": result_a}


async def post_pending_ab_variant_b():
    """Post variant B for any A/B test waiting for its second variant."""
    for test in state["ab_tests"]:
        if test["status"] != "variant_a_posted":
            continue

        pending = test.get("_pending_content")
        if not pending:
            test["status"] = "error"
            continue

        prepared_b = await prepare_post(state["posted_map"], selected_content=pending, variant="B")
        if not prepared_b:
            test["status"] = "error"
            continue

        prepared_b["ab_test_id"] = test["test_id"]
        result_b = await do_post_to_both(prepared=prepared_b, variant="B")

        test["variant_b"]["posted_at"] = datetime.now(timezone.utc).isoformat()
        test["variant_b"]["fb_post_id"] = result_b.get("facebook", {}).get("post_id", "")
        test["variant_b"]["ig_media_id"] = result_b.get("instagram", {}).get("media_id", "")
        test["variant_b"]["fb_success"] = result_b.get("facebook", {}).get("success", False)
        test["variant_b"]["ig_success"] = result_b.get("instagram", {}).get("success", False)
        test["status"] = "both_posted"
        del test["_pending_content"]

        logger.info(f"A/B test {test['test_id']}: variant B posted")

    save_state()


async def evaluate_ab_tests():
    """Evaluate A/B tests that have both variants posted for at least 24 hours."""
    now = datetime.now(timezone.utc)

    for test in state["ab_tests"]:
        if test["status"] != "both_posted":
            continue

        b_posted = test["variant_b"].get("posted_at")
        if not b_posted:
            continue

        try:
            b_dt = datetime.fromisoformat(b_posted.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        if (now - b_dt).total_seconds() < 86400:
            continue

        # Collect engagement for both variants
        eng_a = 0
        eng_b = 0

        for variant_key, variant_label in [("variant_a", "A"), ("variant_b", "B")]:
            v = test[variant_key]
            total = 0

            fb_id = v.get("fb_post_id", "")
            if fb_id:
                fb_eng = await get_post_engagement(fb_id)
                if fb_eng.get("success"):
                    v["engagement"]["facebook"] = {
                        "likes": fb_eng.get("likes", 0),
                        "comments": fb_eng.get("comments", 0),
                        "shares": fb_eng.get("shares", 0),
                    }
                    total += fb_eng.get("engagement_total", 0)

            ig_id = v.get("ig_media_id", "")
            if ig_id:
                ig_eng = await get_media_engagement(ig_id)
                if ig_eng.get("success"):
                    v["engagement"]["instagram"] = {
                        "likes": ig_eng.get("likes", 0),
                        "comments": ig_eng.get("comments", 0),
                        "saves": ig_eng.get("saves", 0),
                    }
                    total += ig_eng.get("engagement_total", 0)

            v["engagement"]["total"] = total
            if variant_label == "A":
                eng_a = total
            else:
                eng_b = total

            await asyncio.sleep(0.5)

        if eng_a > eng_b:
            test["winner"] = "A"
        elif eng_b > eng_a:
            test["winner"] = "B"
        else:
            test["winner"] = "tie"

        test["status"] = "evaluated"
        test["evaluated_at"] = now.isoformat()
        logger.info(f"A/B test {test['test_id']} evaluated: winner={test['winner']} (A={eng_a}, B={eng_b})")

    save_state()


# ─── Reel generation ───────────────────────────────────────────────────────

async def scheduled_reel_generation():
    """Generate and publish a Facebook Reel (scheduled job)."""
    if state["reel_running"]:
        logger.warning("Reel generation already in progress, skipping scheduled run")
        return

    logger.info("Running scheduled reel generation...")
    state["reel_running"] = True
    save_state()

    try:
        result = await generate_and_publish_reel()
        state["reels_history"].append(result)
        state["reels_history"] = state["reels_history"][-50:]
        if result.get("success"):
            state["reels_generated"] += 1
            await log_message("info", f"Reel published: '{result.get('title', '')}' (reel_id={result.get('reel_id', '')})")
        else:
            add_error(f"Reel generation failed: {result.get('error', 'unknown')}")
            await log_message("error", f"Reel generation failed: {result.get('error', 'unknown')}")
    except Exception as e:
        logger.error(f"Scheduled reel generation failed: {e}", exc_info=True)
        add_error(f"Scheduled reel generation failed: {e}")
        state["reels_history"].append({
            "success": False,
            "error": str(e),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        state["reel_running"] = False
        save_state()


# ─── Scheduled jobs ─────────────────────────────────────────────────────────

async def send_heartbeat():
    metrics = {
        "tasks_completed": state["total_posts_fb"] + state["total_posts_ig"],
        "tasks_failed": len(state["errors"]),
        "avg_latency_ms": 0,
    }
    await heartbeat("active", metrics)


async def scheduled_morning_post():
    """Morning post at 9am UK time."""
    logger.info("Running scheduled morning post...")

    # Check if there's a pending A/B test variant B to post
    pending_ab = any(t["status"] == "variant_a_posted" for t in state["ab_tests"])
    if pending_ab:
        logger.info("Posting A/B test variant B instead of regular post")
        await post_pending_ab_variant_b()
        return

    task = await create_task("Morning Social Post", "social_post", {"schedule": "morning"})
    task_id = task["id"] if task else None
    if task_id:
        await update_task(task_id, "in_progress")

    try:
        result = await do_post_to_both()
        success = result.get("facebook", {}).get("success", False) or result.get("instagram", {}).get("success", False)
        if task_id:
            await update_task(
                task_id,
                "completed" if success else "failed",
                output_data={"title": result.get("title", ""), "fb": bool(result.get("facebook", {}).get("success")), "ig": bool(result.get("instagram", {}).get("success"))},
                error_message=None if success else "Post failed on one or both platforms",
            )
    except Exception as e:
        logger.error(f"Morning post failed: {e}")
        if task_id:
            await update_task(task_id, "failed", error_message=str(e))
        add_error(f"Morning scheduled post failed: {e}")
        save_state()


async def scheduled_lunchtime_post():
    """Lunchtime post at 1pm UK time."""
    logger.info("Running scheduled lunchtime post...")

    pending_ab = any(t["status"] == "variant_a_posted" for t in state["ab_tests"])
    if pending_ab:
        logger.info("Posting A/B test variant B instead of regular post")
        await post_pending_ab_variant_b()
        return

    task = await create_task("Lunchtime Social Post", "social_post", {"schedule": "lunchtime"})
    task_id = task["id"] if task else None
    if task_id:
        await update_task(task_id, "in_progress")

    try:
        result = await do_post_to_both()
        success = result.get("facebook", {}).get("success", False) or result.get("instagram", {}).get("success", False)
        if task_id:
            await update_task(
                task_id,
                "completed" if success else "failed",
                output_data={"title": result.get("title", ""), "fb": bool(result.get("facebook", {}).get("success")), "ig": bool(result.get("instagram", {}).get("success"))},
                error_message=None if success else "Post failed on one or both platforms",
            )
    except Exception as e:
        logger.error(f"Lunchtime post failed: {e}")
        if task_id:
            await update_task(task_id, "failed", error_message=str(e))
        add_error(f"Lunchtime scheduled post failed: {e}")
        save_state()


async def scheduled_evening_post():
    """Evening post at 6pm UK time."""
    logger.info("Running scheduled evening post...")

    # Check if there's a pending A/B test variant B to post
    pending_ab = any(t["status"] == "variant_a_posted" for t in state["ab_tests"])
    if pending_ab:
        logger.info("Posting A/B test variant B instead of regular post")
        await post_pending_ab_variant_b()
        return

    task = await create_task("Evening Social Post", "social_post", {"schedule": "evening"})
    task_id = task["id"] if task else None
    if task_id:
        await update_task(task_id, "in_progress")

    try:
        result = await do_post_to_both()
        success = result.get("facebook", {}).get("success", False) or result.get("instagram", {}).get("success", False)
        if task_id:
            await update_task(
                task_id,
                "completed" if success else "failed",
                output_data={"title": result.get("title", ""), "fb": bool(result.get("facebook", {}).get("success")), "ig": bool(result.get("instagram", {}).get("success"))},
                error_message=None if success else "Post failed on one or both platforms",
            )
    except Exception as e:
        logger.error(f"Evening post failed: {e}")
        if task_id:
            await update_task(task_id, "failed", error_message=str(e))
        add_error(f"Evening scheduled post failed: {e}")
        save_state()


async def collect_engagement():
    """Fetch engagement data for all recent posts."""
    logger.info("Collecting engagement data...")
    updated = 0

    for post in state["posts"][-50:]:
        fb_id = post.get("fb_post_id", "")
        ig_id = post.get("ig_media_id", "")

        if fb_id:
            fb_eng = await get_post_engagement(fb_id)
            if fb_eng.get("success"):
                state["engagement"][fb_id] = fb_eng
                post["engagement"]["facebook"] = {
                    "likes": fb_eng.get("likes", 0),
                    "comments": fb_eng.get("comments", 0),
                    "shares": fb_eng.get("shares", 0),
                    "reach": fb_eng.get("reach", 0),
                    "impressions": fb_eng.get("impressions", 0),
                }
                updated += 1

        if ig_id:
            ig_eng = await get_media_engagement(ig_id)
            if ig_eng.get("success"):
                state["engagement"][ig_id] = ig_eng
                post["engagement"]["instagram"] = {
                    "likes": ig_eng.get("likes", 0),
                    "comments": ig_eng.get("comments", 0),
                    "saves": ig_eng.get("saves", 0),
                    "reach": ig_eng.get("reach", 0),
                    "impressions": ig_eng.get("impressions", 0),
                }
                updated += 1

        # Don't hammer the API
        await asyncio.sleep(0.5)

    # Update platform stats
    state["fb_stats"] = await get_page_stats()
    state["ig_stats"] = await get_account_stats()

    save_state()

    # Evaluate any pending A/B tests
    await evaluate_ab_tests()

    # Report to manager
    total_engagement = sum(
        e.get("engagement_total", 0)
        for e in state["engagement"].values()
        if e.get("success")
    )
    await update_kpi("social_traffic", float(total_engagement))
    await log_message("info", f"Engagement collected: {updated} posts updated, total engagement={total_engagement}")
    logger.info(f"Engagement collection complete: {updated} posts updated")


async def refresh_queue():
    """Refresh the posting queue with upcoming scheduled slots."""
    now_uk = datetime.now(UK_TZ)
    queue = []

    for days_ahead in range(3):
        day = now_uk + timedelta(days=days_ahead)

        morning = day.replace(hour=settings.MORNING_HOUR, minute=0, second=0, microsecond=0)
        evening = day.replace(hour=settings.EVENING_HOUR, minute=0, second=0, microsecond=0)

        if morning > now_uk:
            queue.append({
                "scheduled_at": morning.isoformat(),
                "slot": "morning",
                "platform": "both",
                "status": "pending",
            })
        if evening > now_uk:
            queue.append({
                "scheduled_at": evening.isoformat(),
                "slot": "evening",
                "platform": "both",
                "status": "pending",
            })

    state["queue"] = queue[:10]  # Next 10 scheduled slots
    save_state()


async def daily_trend_analysis():
    """Run daily trend detection and optimization analysis."""
    logger.info("Running daily trend and optimization analysis...")
    try:
        all_content = await get_all_content()

        # Trends report
        trends = get_trending_report(all_content, state["posted_map"], state["engagement"])
        state["trends_report"] = trends

        # Optimization report
        perf = analyze_post_performance(state["posts"])
        top = get_top_performers(state["posts"])
        state["optimization_report"] = {
            "performance": perf,
            "top_performers": top[:10],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        save_state()
        await log_message("info", f"Trend analysis complete: {len(trends.get('recommendations', []))} recommendations")
        logger.info("Daily trend and optimization analysis complete")
    except Exception as e:
        logger.error(f"Trend analysis failed: {e}")
        add_error(f"Trend analysis failed: {e}")
        save_state()


# ─── App lifecycle ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_state()
    state["started_at"] = datetime.now(timezone.utc).isoformat()

    # Register with manager
    await register_agent()

    # Heartbeat every 120 seconds
    scheduler.add_job(send_heartbeat, "interval", seconds=settings.HEARTBEAT_INTERVAL, id="heartbeat")

    # Morning post at 9am UK time
    scheduler.add_job(
        scheduled_morning_post,
        CronTrigger(hour=settings.MORNING_HOUR, minute=0, timezone=UK_TZ),
        id="morning_post",
    )

    # Lunchtime post at 1pm UK time
    scheduler.add_job(
        scheduled_lunchtime_post,
        CronTrigger(hour=13, minute=0, timezone=UK_TZ),
        id="lunchtime_post",
    )

    # Evening post at 6pm UK time
    scheduler.add_job(
        scheduled_evening_post,
        CronTrigger(hour=settings.EVENING_HOUR, minute=0, timezone=UK_TZ),
        id="evening_post",
    )

    # Engagement collection every 4 hours
    scheduler.add_job(
        collect_engagement,
        "interval",
        hours=4,
        id="engagement_collection",
    )

    # Daily trend analysis at 8am UK time
    scheduler.add_job(
        daily_trend_analysis,
        CronTrigger(hour=8, minute=0, timezone=UK_TZ),
        id="trend_analysis",
    )

    # Facebook Reels: Tuesday 10am, Thursday 10am, Saturday 10am UK time
    scheduler.add_job(
        scheduled_reel_generation,
        CronTrigger(day_of_week="tue", hour=10, minute=0, timezone=UK_TZ),
        id="reel_tuesday",
    )
    scheduler.add_job(
        scheduled_reel_generation,
        CronTrigger(day_of_week="thu", hour=10, minute=0, timezone=UK_TZ),
        id="reel_thursday",
    )
    scheduler.add_job(
        scheduled_reel_generation,
        CronTrigger(day_of_week="sat", hour=10, minute=0, timezone=UK_TZ),
        id="reel_saturday",
    )

    scheduler.start()
    await send_heartbeat()
    await refresh_queue()
    await daily_trend_analysis()
    await log_message("info", "Social Media Agent started")
    logger.info("Social Media Agent started on port %d", settings.API_PORT)
    yield
    scheduler.shutdown()


app = FastAPI(
    title="PetHub Social Media Agent",
    description="Automated social media posting and engagement tracking for pethubonline.com",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/agents/social",
)


# ─── API Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    now = datetime.now(timezone.utc)
    uptime = None
    if state["started_at"]:
        try:
            started = datetime.fromisoformat(state["started_at"])
            uptime = str(now - started).split(".")[0]
        except Exception:
            pass

    return {
        "agent": "social",
        "status": "active",
        "uptime": uptime,
        "started_at": state["started_at"],
        "total_posts": len(state["posts"]),
        "total_posts_fb": state["total_posts_fb"],
        "total_posts_ig": state["total_posts_ig"],
        "last_post_time": state["last_post_time"],
        "next_scheduled": state["queue"][0] if state["queue"] else None,
        "recent_errors": len(state["errors"]),
        "fb_stats": state["fb_stats"],
        "ig_stats": state["ig_stats"],
    }


@app.post("/api/post/now")
async def trigger_post_now():
    """Trigger an immediate post to both platforms."""
    task = await create_task("Manual Social Post", "social_post", {"trigger": "manual"})
    task_id = task["id"] if task else None
    if task_id:
        await update_task(task_id, "in_progress")

    try:
        result = await do_post_to_both()
        success = result.get("facebook", {}).get("success", False) or result.get("instagram", {}).get("success", False)
        if task_id:
            await update_task(
                task_id,
                "completed" if success else "failed",
                output_data=result,
            )
        return result
    except Exception as e:
        if task_id:
            await update_task(task_id, "failed", error_message=str(e))
        raise HTTPException(500, f"Posting failed: {e}")


@app.post("/api/post/facebook")
async def trigger_post_facebook():
    """Post to Facebook only."""
    try:
        result = await do_post_facebook_only()
        return result
    except Exception as e:
        raise HTTPException(500, f"Facebook posting failed: {e}")


@app.post("/api/post/instagram")
async def trigger_post_instagram():
    """Post to Instagram only."""
    try:
        result = await do_post_instagram_only()
        return result
    except Exception as e:
        raise HTTPException(500, f"Instagram posting failed: {e}")


@app.get("/api/posts")
async def list_posts(limit: int = 20):
    """List recent posts with engagement data."""
    posts = state["posts"][-limit:]
    posts.reverse()
    return {
        "total": len(state["posts"]),
        "showing": len(posts),
        "posts": posts,
    }


@app.get("/api/queue")
async def get_queue():
    """Get upcoming scheduled posts."""
    await refresh_queue()
    return {
        "schedule": {
            "morning": f"{settings.MORNING_HOUR}:00 UK (Europe/London)",
            "evening": f"{settings.EVENING_HOUR}:00 UK (Europe/London)",
        },
        "queue": state["queue"],
    }


@app.get("/api/engagement")
async def get_engagement_summary():
    """Get engagement metrics summary."""
    total_likes = 0
    total_comments = 0
    total_shares = 0
    total_reach = 0
    total_impressions = 0
    total_saves = 0

    for eng in state["engagement"].values():
        if eng.get("success"):
            total_likes += eng.get("likes", 0)
            total_comments += eng.get("comments", 0)
            total_shares += eng.get("shares", 0)
            total_reach += eng.get("reach", 0)
            total_impressions += eng.get("impressions", 0)
            total_saves += eng.get("saves", 0)

    total_posts = len(state["posts"])
    engagement_rate = 0.0
    if total_impressions > 0:
        engagement_rate = round(((total_likes + total_comments + total_shares + total_saves) / total_impressions) * 100, 2)

    return {
        "total_posts": total_posts,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_saves": total_saves,
        "total_reach": total_reach,
        "total_impressions": total_impressions,
        "engagement_rate": engagement_rate,
        "fb_stats": state["fb_stats"],
        "ig_stats": state["ig_stats"],
        "posts_with_engagement": len(state["engagement"]),
    }


@app.get("/api/history")
async def get_history():
    """Get full posting history."""
    history = []
    for post in state["posts"]:
        history.append({
            "id": post.get("id"),
            "title": post.get("title", ""),
            "url": post.get("url", ""),
            "category": post.get("category", ""),
            "fb_success": post.get("fb_success", False),
            "ig_success": post.get("ig_success", False),
            "fb_post_id": post.get("fb_post_id", ""),
            "ig_media_id": post.get("ig_media_id", ""),
            "posted_at": post.get("posted_at", ""),
            "engagement": post.get("engagement", {}),
        })
    history.reverse()
    return {
        "total": len(history),
        "history": history,
    }


@app.post("/api/collect-engagement")
async def trigger_engagement_collection():
    """Trigger immediate engagement data collection."""
    asyncio.create_task(collect_engagement())
    return {"message": "Engagement collection started", "status": "running"}


@app.get("/api/content")
async def list_available_content():
    """List all available WordPress content for posting."""
    try:
        content = await get_all_content()
        for item in content:
            item["last_posted"] = state["posted_map"].get(item["fingerprint"])
        return {
            "total": len(content),
            "content": content,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch content: {e}")


@app.get("/api/errors")
async def get_errors():
    """Get recent errors."""
    return {"errors": state["errors"][-20:]}


@app.get("/api/ab-tests")
async def list_ab_tests():
    """List all A/B tests."""
    tests = list(reversed(state["ab_tests"][-50:]))
    return {
        "total": len(state["ab_tests"]),
        "tests": tests,
    }


@app.post("/api/ab-test/start")
async def trigger_ab_test():
    """Manually start a new A/B test."""
    try:
        result = await start_ab_test()
        return result
    except Exception as e:
        raise HTTPException(500, f"A/B test failed: {e}")


@app.get("/api/ab-test/{test_id}")
async def get_ab_test(test_id: str):
    """Get results of a specific A/B test."""
    for test in state["ab_tests"]:
        if test["test_id"] == test_id:
            return test
    raise HTTPException(404, f"A/B test '{test_id}' not found")


@app.get("/api/trends")
async def get_trends():
    """Get trending topics report."""
    if not state["trends_report"]:
        try:
            all_content = await get_all_content()
            state["trends_report"] = get_trending_report(all_content, state["posted_map"], state["engagement"])
            save_state()
        except Exception as e:
            raise HTTPException(500, f"Failed to generate trends: {e}")
    return state["trends_report"]


@app.get("/api/optimization")
async def get_optimization():
    """Get engagement optimization report."""
    if not state["optimization_report"]:
        perf = analyze_post_performance(state["posts"])
        top = get_top_performers(state["posts"])
        state["optimization_report"] = {
            "performance": perf,
            "top_performers": top[:10],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state()
    return state["optimization_report"]


@app.post("/api/reels/generate")
async def trigger_reel_generation():
    """Generate and publish a Facebook Reel."""
    if state["reel_running"]:
        raise HTTPException(409, "Reel generation is already in progress")

    state["reel_running"] = True
    save_state()

    try:
        result = await generate_and_publish_reel()
        state["reels_history"].append(result)
        state["reels_history"] = state["reels_history"][-50:]
        if result.get("success"):
            state["reels_generated"] += 1
        else:
            add_error(f"Reel generation failed: {result.get('error', 'unknown')}")
        save_state()
        return result
    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        state["reels_history"].append(error_result)
        add_error(f"Reel generation failed: {e}")
        save_state()
        raise HTTPException(500, f"Reel generation failed: {e}")
    finally:
        state["reel_running"] = False
        save_state()


@app.get("/api/reels/status")
async def get_reels_status():
    """Return reel generation status and history."""
    return {
        "reels_generated": state["reels_generated"],
        "reel_running": state["reel_running"],
        "history_count": len(state["reels_history"]),
        "history": list(reversed(state["reels_history"][-20:])),
        "schedule": {
            "days": ["Tuesday", "Thursday", "Saturday"],
            "time": "10:00 UK (Europe/London)",
        },
    }


@app.get("/", response_class=HTMLResponse)
async def social_dashboard():
    with open("templates/social_dashboard.html", "r") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.API_PORT, reload=False)
