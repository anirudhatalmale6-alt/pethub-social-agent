"""
Autonomous posting schedule optimizer.

Analyzes engagement data to learn the best posting times and frequency,
replacing the fixed 9am/1pm/6pm UK schedule with data-driven windows.
Stores learning data in a JSON file and adjusts recommendations as more
posts are tracked.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("social.scheduler")

SCHEDULE_DATA_PATH = Path("/opt/pethub-agents/social-agent/data/schedule_learning.json")

# Default posting hours (UK time) used until enough data is gathered
DEFAULT_HOURS = [9, 13, 18]

# Minimum number of tracked posts before we trust the learned schedule
MIN_POSTS_FOR_LEARNING = 10


def _ensure_data_dir() -> None:
    """Create the data directory if it doesn't exist."""
    SCHEDULE_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_schedule_data() -> dict:
    """Load schedule learning data from disk, or initialise with defaults.

    Returns:
        dict with keys:
            hourly_engagement  - {hour_str: [score, score, ...]} (last 30 per hour)
            best_hours         - list of best posting hours (UK time)
            adjustments_made   - number of schedule adjustments applied
            last_updated       - ISO timestamp of last update
    """
    _ensure_data_dir()

    if SCHEDULE_DATA_PATH.exists():
        try:
            data = json.loads(SCHEDULE_DATA_PATH.read_text(encoding="utf-8"))
            # Validate expected keys
            if "hourly_engagement" not in data:
                data["hourly_engagement"] = {}
            if "best_hours" not in data:
                data["best_hours"] = list(DEFAULT_HOURS)
            if "adjustments_made" not in data:
                data["adjustments_made"] = 0
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load schedule data, starting fresh: %s", exc)

    return {
        "hourly_engagement": {},
        "best_hours": list(DEFAULT_HOURS),
        "adjustments_made": 0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def save_schedule_data(data: dict) -> None:
    """Persist schedule learning data to disk."""
    _ensure_data_dir()
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        SCHEDULE_DATA_PATH.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Failed to save schedule data: %s", exc)


def record_post_engagement(hour: int, engagement_score: float, data: dict) -> None:
    """Record engagement for a post published at *hour* (0-23 UK time).

    Keeps at most the last 30 entries per hour to avoid unbounded growth
    while still providing a reasonable rolling window for analysis.
    """
    key = str(hour)
    bucket = data.setdefault("hourly_engagement", {}).setdefault(key, [])
    bucket.append(engagement_score)
    # Trim to last 30 observations
    if len(bucket) > 30:
        data["hourly_engagement"][key] = bucket[-30:]
    logger.debug("Recorded engagement %.2f for hour %d (%d samples)", engagement_score, hour, len(data["hourly_engagement"][key]))


def analyze_best_posting_times(data: dict) -> list[int]:
    """Determine the top 3-4 posting hours based on average engagement.

    Hours are in UK time (Europe/London).  If fewer than
    ``MIN_POSTS_FOR_LEARNING`` total posts have been tracked the function
    returns the safe default schedule ``[9, 13, 18]``.

    Returns:
        Sorted list of the best 3-4 hours (ascending).
    """
    hourly = data.get("hourly_engagement", {})

    # Count total tracked posts across all hours
    total_posts = sum(len(scores) for scores in hourly.values())
    if total_posts < MIN_POSTS_FOR_LEARNING:
        logger.info(
            "Not enough data for learning (%d/%d posts tracked), using defaults",
            total_posts,
            MIN_POSTS_FOR_LEARNING,
        )
        return list(DEFAULT_HOURS)

    # Calculate average engagement per hour
    hour_averages: list[tuple[int, float]] = []
    for hour_str, scores in hourly.items():
        if not scores:
            continue
        try:
            hour_int = int(hour_str)
        except ValueError:
            continue
        avg = sum(scores) / len(scores)
        hour_averages.append((hour_int, avg))

    if not hour_averages:
        return list(DEFAULT_HOURS)

    # Sort descending by average engagement
    hour_averages.sort(key=lambda x: x[1], reverse=True)

    # Pick top 3 hours; include a 4th if its average is at least 70% of the
    # top hour's average (so we don't add a mediocre 4th slot).
    top_avg = hour_averages[0][1] if hour_averages else 0
    best: list[int] = []
    for hour, avg in hour_averages:
        if len(best) < 3:
            best.append(hour)
        elif len(best) < 4 and top_avg > 0 and avg >= top_avg * 0.70:
            best.append(hour)
        else:
            break

    best.sort()
    return best


def get_recommended_schedule(data: dict) -> dict:
    """Return the current recommended posting schedule with confidence level.

    Returns:
        dict with:
            recommended_hours_utc  - best hours (note: stored in UK time despite key name)
            confidence             - "high" / "medium" / "low"
            total_posts_analyzed   - number of engagement records across all hours
            adjustments_made       - how many times the schedule has been updated
    """
    best_hours = analyze_best_posting_times(data)
    total_posts = sum(len(v) for v in data.get("hourly_engagement", {}).values())

    if total_posts > 50:
        confidence = "high"
    elif total_posts > 20:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "recommended_hours_utc": best_hours,
        "confidence": confidence,
        "total_posts_analyzed": total_posts,
        "adjustments_made": data.get("adjustments_made", 0),
    }


def suggest_frequency_adjustment(
    data: dict,
    current_daily_posts: int = 3,
) -> dict:
    """Suggest whether to increase, maintain, or decrease posting frequency.

    Compares recent engagement (last 10 per-hour entries where available) with
    older engagement.  If the trend is positive, suggest maintaining or a slight
    increase; if negative, suggest reducing to avoid audience fatigue.

    Args:
        data: The schedule learning dict (from ``load_schedule_data``).
        current_daily_posts: How many posts are made per day right now.

    Returns:
        dict with keys ``suggestion`` ("maintain" | "increase" | "decrease"),
        ``reason`` (human-readable), and ``suggested_daily_posts`` (int).
    """
    hourly = data.get("hourly_engagement", {})
    all_scores: list[float] = []
    for scores in hourly.values():
        all_scores.extend(scores)

    if len(all_scores) < 6:
        return {
            "suggestion": "maintain",
            "reason": "Not enough data to make a frequency recommendation yet.",
            "suggested_daily_posts": current_daily_posts,
        }

    # Split into older half and newer half
    midpoint = len(all_scores) // 2
    older = all_scores[:midpoint]
    newer = all_scores[midpoint:]

    older_avg = sum(older) / len(older) if older else 0
    newer_avg = sum(newer) / len(newer) if newer else 0

    # Avoid division by zero
    if older_avg == 0:
        change_pct = 0.0
    else:
        change_pct = ((newer_avg - older_avg) / older_avg) * 100

    if change_pct > 15:
        # Strong upward trend -- consider a small increase
        suggested = min(current_daily_posts + 1, 5)  # Cap at 5/day
        return {
            "suggestion": "increase",
            "reason": (
                f"Engagement is trending up ({change_pct:+.1f}%). "
                f"Consider adding one more daily post (up to {suggested}/day)."
            ),
            "suggested_daily_posts": suggested,
        }
    elif change_pct < -15:
        # Significant downward trend -- suggest reducing
        suggested = max(current_daily_posts - 1, 1)  # Minimum 1/day
        return {
            "suggestion": "decrease",
            "reason": (
                f"Engagement is trending down ({change_pct:+.1f}%). "
                f"Reducing to {suggested} post(s)/day may help avoid audience fatigue."
            ),
            "suggested_daily_posts": suggested,
        }
    else:
        return {
            "suggestion": "maintain",
            "reason": (
                f"Engagement is stable ({change_pct:+.1f}%). "
                f"Current frequency of {current_daily_posts} posts/day looks good."
            ),
            "suggested_daily_posts": current_daily_posts,
        }
