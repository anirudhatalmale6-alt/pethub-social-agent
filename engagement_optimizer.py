"""
Engagement-based content prioritization.
Analyzes past post performance to optimize future content selection.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("social-agent.optimizer")


def _total_engagement(post: dict) -> int:
    eng = post.get("engagement", {})
    fb = eng.get("facebook", {})
    ig = eng.get("instagram", {})
    return (
        fb.get("likes", 0) + fb.get("comments", 0) + fb.get("shares", 0)
        + ig.get("likes", 0) + ig.get("comments", 0) + ig.get("saves", 0)
    )


def _post_hour(post: dict) -> Optional[int]:
    ts = post.get("posted_at", "")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.hour
    except (ValueError, TypeError):
        return None


def analyze_post_performance(posts: list) -> dict:
    """Analyze all past posts to find engagement patterns."""
    if not posts:
        return {"category_scores": {}, "hour_scores": {}, "type_scores": {}, "total_analyzed": 0}

    category_eng = defaultdict(lambda: {"total": 0, "count": 0})
    hour_eng = defaultdict(lambda: {"total": 0, "count": 0})
    type_eng = defaultdict(lambda: {"total": 0, "count": 0})

    for post in posts:
        eng = _total_engagement(post)
        cat = post.get("category", "unknown")
        category_eng[cat]["total"] += eng
        category_eng[cat]["count"] += 1

        hour = _post_hour(post)
        if hour is not None:
            hour_eng[hour]["total"] += eng
            hour_eng[hour]["count"] += 1

        ctype = post.get("content_type", "page")
        type_eng[ctype]["total"] += eng
        type_eng[ctype]["count"] += 1

    category_scores = {}
    for cat, data in category_eng.items():
        avg = data["total"] / data["count"] if data["count"] else 0
        category_scores[cat] = {"avg_engagement": round(avg, 2), "posts": data["count"], "total_engagement": data["total"]}

    hour_scores = {}
    for h, data in hour_eng.items():
        avg = data["total"] / data["count"] if data["count"] else 0
        hour_scores[str(h)] = {"avg_engagement": round(avg, 2), "posts": data["count"]}

    type_scores = {}
    for t, data in type_eng.items():
        avg = data["total"] / data["count"] if data["count"] else 0
        type_scores[t] = {"avg_engagement": round(avg, 2), "posts": data["count"]}

    # Sort categories by avg engagement
    top_categories = sorted(category_scores.items(), key=lambda x: x[1]["avg_engagement"], reverse=True)
    best_hours = sorted(hour_scores.items(), key=lambda x: x[1]["avg_engagement"], reverse=True)

    return {
        "category_scores": category_scores,
        "hour_scores": hour_scores,
        "type_scores": type_scores,
        "top_categories": [c[0] for c in top_categories[:5]],
        "best_hours": [int(h[0]) for h in best_hours[:3]],
        "total_analyzed": len(posts),
    }


def get_top_performers(posts: list, min_engagement: int = 5) -> list:
    """Return posts that had above-average engagement."""
    if not posts:
        return []

    engagements = [_total_engagement(p) for p in posts]
    avg_eng = sum(engagements) / len(engagements) if engagements else 0

    threshold = max(min_engagement, avg_eng)
    top = []
    for post, eng in zip(posts, engagements):
        if eng >= threshold:
            top.append({
                "title": post.get("title", ""),
                "category": post.get("category", ""),
                "fingerprint": post.get("fingerprint", ""),
                "engagement": eng,
                "posted_at": post.get("posted_at", ""),
                "fb_post_id": post.get("fb_post_id", ""),
                "ig_media_id": post.get("ig_media_id", ""),
            })

    top.sort(key=lambda x: x["engagement"], reverse=True)
    return top


def should_repost(post: dict, days_since: int = 14) -> bool:
    """Check if a high-performing post should be reposted (at least N days since last post)."""
    posted_at = post.get("posted_at", "")
    if not posted_at:
        return False

    try:
        last_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False

    now = datetime.now(timezone.utc)
    elapsed = (now - last_dt).days
    if elapsed < days_since:
        return False

    eng = _total_engagement(post) if "engagement" in post else post.get("engagement_total", 0)
    return eng >= 5


def select_optimized_content(
    all_content: list,
    posted_history: dict,
    post_performance: list,
) -> Optional[dict]:
    """
    Enhanced content selection that weighs engagement history.
    If a category/topic does well, prioritize similar content.
    Falls back to regular rotation if no engagement data yet.
    """
    if not all_content:
        return None

    performance = analyze_post_performance(post_performance)
    category_scores = performance.get("category_scores", {})

    # If we have no meaningful engagement data, return None to fall back to default
    total_eng = sum(cs["total_engagement"] for cs in category_scores.values())
    if total_eng == 0 or len(post_performance) < 3:
        return None

    now = datetime.now(timezone.utc)
    scored = []

    top_categories = set(performance.get("top_categories", [])[:5])

    for item in all_content:
        fp = item["fingerprint"]
        last_posted = posted_history.get(fp)

        if last_posted:
            try:
                last_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
                hours_since = (now - last_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                hours_since = 999
        else:
            hours_since = 999

        # Skip items posted within the last 48 hours
        if hours_since < 48:
            continue

        score = 0.0

        # Time-based score: prefer items not posted recently
        score += min(hours_since / 24, 30)  # Cap at 30 days worth

        # Category boost: if this category performs well, boost it
        cat = item.get("category", "")
        cat_data = category_scores.get(cat)
        if cat_data and cat_data["avg_engagement"] > 0:
            score += cat_data["avg_engagement"] * 2

        # Top category bonus
        if cat in top_categories:
            score += 10

        # Blog post bonus
        if item.get("type") == "post":
            score += 5

        # Check if this content was a top performer before - repost bonus
        for perf in get_top_performers(post_performance):
            if perf.get("fingerprint") == fp and should_repost(perf):
                score += 15
                break

        scored.append((score, item))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)

    # Pick from top 5 with weighted randomness
    import random
    top_n = min(5, len(scored))
    candidates = scored[:top_n]
    weights = [c[0] + 1 for c in candidates]  # +1 to avoid zero weights
    total_w = sum(weights)
    probs = [w / total_w for w in weights]
    chosen_idx = random.choices(range(top_n), weights=probs, k=1)[0]

    return candidates[chosen_idx][1]
