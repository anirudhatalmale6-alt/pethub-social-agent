"""
Dynamic call-to-action selector.

Uses a multi-armed bandit approach (exploit best-performing CTAs 80% of the
time, explore less-used categories 20% of the time) combined with optional
AI-generated CTAs for contextually perfect messaging.

Persists performance data in a JSON file so the system learns which CTA
styles resonate best with the Pet Hub Online audience over time.
"""

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("social.cta")

CTA_DATA_PATH = Path("/opt/pethub-agents/social-agent/data/cta_performance.json")

# CTA categories with variant phrases
CTA_VARIANTS: dict[str, list[str]] = {
    "shop": [
        "Shop now at PetHub Online",
        "Browse our collection",
        "Get yours today",
        "Order now",
    ],
    "discover": [
        "Discover more",
        "Explore our range",
        "Find the perfect match",
        "See what's new",
    ],
    "urgency": [
        "Don't miss out",
        "Limited availability",
        "Grab yours before they're gone",
        "While stocks last",
    ],
    "social_proof": [
        "Join thousands of happy pet parents",
        "Our customers love this",
        "Top rated by pet owners",
        "Bestseller",
    ],
    "emotional": [
        "Your pet deserves the best",
        "Give your furry friend something special",
        "Because they're worth it",
        "Make their day",
    ],
    "value": [
        "Free delivery available",
        "Great value for your pet",
        "Premium quality, affordable prices",
        "Best deals on pet supplies",
    ],
}

# Exploration probability (epsilon in epsilon-greedy strategy)
EXPLORE_PROBABILITY = 0.20


def _ensure_data_dir() -> None:
    """Create the data directory if it doesn't exist."""
    CTA_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_cta_data() -> dict:
    """Load CTA performance data from disk, or initialise with defaults.

    Returns:
        dict with keys:
            cta_scores - {category: {"uses": int, "total_engagement": float}}
            history    - list of recent CTA usage records
    """
    _ensure_data_dir()

    if CTA_DATA_PATH.exists():
        try:
            data = json.loads(CTA_DATA_PATH.read_text(encoding="utf-8"))
            if "cta_scores" not in data:
                data["cta_scores"] = {}
            if "history" not in data:
                data["history"] = []
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load CTA data, starting fresh: %s", exc)

    return {
        "cta_scores": {},
        "history": [],
    }


def save_cta_data(data: dict) -> None:
    """Persist CTA performance data to disk."""
    _ensure_data_dir()
    # Keep history bounded
    data["history"] = data.get("history", [])[-200:]
    try:
        CTA_DATA_PATH.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Failed to save CTA data: %s", exc)


def _avg_engagement(scores: dict, category: str) -> float:
    """Return the average engagement for a CTA category, or 0.0 if unused."""
    entry = scores.get(category, {})
    uses = entry.get("uses", 0)
    if uses == 0:
        return 0.0
    return entry.get("total_engagement", 0.0) / uses


def select_best_cta(
    content_category: str,
    platform: str,
    data: dict,
) -> str:
    """Select the best CTA for a post based on historical performance.

    Uses an epsilon-greedy strategy:
    - 80% of the time: pick from the top-performing CTA categories.
    - 20% of the time: explore a less-used or random category.

    Platform-specific tweaks:
    - Instagram posts favour "discover" / "emotional" categories (no direct
      links in captions), so "shop" and "urgency" are slightly de-prioritised.

    Args:
        content_category: The content category (e.g. "dog food", "cat toys").
                          Currently used for logging; future versions may map
                          content categories to preferred CTA styles.
        platform: "facebook" or "instagram".
        data: The CTA performance dict (from ``load_cta_data``).

    Returns:
        A specific CTA string from the selected category.
    """
    scores = data.get("cta_scores", {})
    categories = list(CTA_VARIANTS.keys())

    # Check if we're exploring or exploiting
    if random.random() < EXPLORE_PROBABILITY:
        # Explore: prefer categories with fewer uses
        usage_counts = [(cat, scores.get(cat, {}).get("uses", 0)) for cat in categories]
        usage_counts.sort(key=lambda x: x[1])
        # Pick from the bottom half (least used)
        bottom_half = usage_counts[: max(len(usage_counts) // 2, 1)]
        chosen_category = random.choice(bottom_half)[0]
        logger.debug("CTA explore: chose '%s' (least used)", chosen_category)
    else:
        # Exploit: pick the best-performing category
        ranked = sorted(categories, key=lambda c: _avg_engagement(scores, c), reverse=True)

        # Platform adjustment for Instagram: de-prioritise "shop"/"urgency"
        if platform == "instagram":
            ig_preferred = [c for c in ranked if c not in ("shop", "urgency")]
            if ig_preferred:
                ranked = ig_preferred + [c for c in ranked if c in ("shop", "urgency")]

        # If no engagement data at all, pick a sensible default
        top_avg = _avg_engagement(scores, ranked[0]) if ranked else 0
        if top_avg == 0:
            # No data yet -- pick from a reasonable default set
            chosen_category = random.choice(["shop", "discover", "emotional"])
        else:
            # Pick from top 2 performers with slight randomness
            top_n = min(2, len(ranked))
            chosen_category = random.choice(ranked[:top_n])

        logger.debug("CTA exploit: chose '%s' (avg eng %.2f)", chosen_category, _avg_engagement(scores, chosen_category))

    # Pick a random variant from the chosen category
    variants = CTA_VARIANTS.get(chosen_category, ["Shop now at PetHub Online"])
    cta = random.choice(variants)

    # Record this selection in history
    data.setdefault("history", []).append({
        "category": chosen_category,
        "cta": cta,
        "content_category": content_category,
        "platform": platform,
        "selected_at": datetime.now(timezone.utc).isoformat(),
    })

    return cta


def record_cta_performance(
    cta_category: str,
    engagement_score: float,
    data: dict,
) -> None:
    """Record how well a CTA performed for a given category.

    Updates the running totals so that ``select_best_cta`` can compute
    meaningful averages.

    Args:
        cta_category: One of the keys in ``CTA_VARIANTS``.
        engagement_score: A numeric engagement metric (likes + comments + shares etc.).
        data: The CTA performance dict (from ``load_cta_data``).
    """
    scores = data.setdefault("cta_scores", {})
    entry = scores.setdefault(cta_category, {"uses": 0, "total_engagement": 0.0})
    entry["uses"] += 1
    entry["total_engagement"] += engagement_score
    logger.debug(
        "Recorded CTA performance for '%s': +%.2f (total uses=%d, total_eng=%.2f)",
        cta_category,
        engagement_score,
        entry["uses"],
        entry["total_engagement"],
    )


async def ai_generate_cta(
    title: str,
    content_snippet: str,
    platform: str,
) -> Optional[str]:
    """Use AI to generate a contextually perfect CTA for specific content.

    Falls back to ``None`` on any error so the caller can use the
    bandit-selected CTA instead.

    Args:
        title: The post/product title.
        content_snippet: First ~200 characters of the content.
        platform: "facebook" or "instagram".

    Returns:
        A short CTA string (under 10 words), or None on failure.
    """
    try:
        from ai_client import _call_openai

        platform_hint = (
            "The CTA will appear in a Facebook post with a clickable link."
            if platform == "facebook"
            else "The CTA will appear in an Instagram caption (no clickable link, use 'link in bio' if needed)."
        )

        result = await _call_openai(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write compelling calls-to-action for a UK pet supplies "
                        "website called Pet Hub Online (pethubonline.com). "
                        "Keep CTAs short, warm, and action-oriented."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Write a short, compelling call-to-action (under 10 words) "
                        f"for this content:\n"
                        f"Title: {title}\n"
                        f"Snippet: {content_snippet[:200]}\n"
                        f"Platform: {platform}\n"
                        f"{platform_hint}\n\n"
                        f"Return ONLY the CTA text, nothing else."
                    ),
                },
            ],
            max_tokens=30,
        )
        if result:
            # Strip surrounding quotes if the model added them
            result = result.strip().strip('"').strip("'")
        return result
    except ImportError:
        logger.warning("ai_client module not available for CTA generation")
        return None
    except Exception as exc:
        logger.error("AI CTA generation failed: %s", exc)
        return None


def get_cta_performance_report(data: dict) -> dict:
    """Build a summary of CTA performance for the dashboard.

    Returns:
        dict with:
            categories          - per-category breakdown (avg engagement, uses)
            best_category       - name of the top-performing category
            worst_category      - name of the lowest-performing category
            total_uses          - total CTA selections tracked
            history_length      - number of history entries
    """
    scores = data.get("cta_scores", {})
    history = data.get("history", [])

    categories: dict[str, dict] = {}
    for cat in CTA_VARIANTS:
        entry = scores.get(cat, {"uses": 0, "total_engagement": 0.0})
        uses = entry.get("uses", 0)
        total_eng = entry.get("total_engagement", 0.0)
        avg = total_eng / uses if uses > 0 else 0.0
        categories[cat] = {
            "uses": uses,
            "total_engagement": round(total_eng, 2),
            "avg_engagement": round(avg, 2),
            "variants": CTA_VARIANTS[cat],
        }

    total_uses = sum(c["uses"] for c in categories.values())

    # Determine best/worst (only among categories with at least 1 use)
    used = {k: v for k, v in categories.items() if v["uses"] > 0}
    best_category = max(used, key=lambda k: used[k]["avg_engagement"]) if used else None
    worst_category = min(used, key=lambda k: used[k]["avg_engagement"]) if used else None

    return {
        "categories": categories,
        "best_category": best_category,
        "worst_category": worst_category,
        "total_uses": total_uses,
        "history_length": len(history),
    }
