"""
Google Trends integration for pet-related topics in the UK.

Uses pytrends to discover trending pet queries, rising topics, and
interest-over-time data that the social agent can use to create timely,
relevant posts.
"""

import logging
import random
from typing import Optional

logger = logging.getLogger("social.trends")

PET_SEED_KEYWORDS = [
    "dog food",
    "cat toys",
    "pet supplies",
    "dog beds",
    "cat litter",
    "dog grooming",
    "cat food",
    "dog toys",
    "pet health",
    "dog training",
    "puppy supplies",
    "kitten supplies",
    "dog treats",
    "cat beds",
    "pet care",
]


def get_trending_pet_topics(
    timeframe: str = "now 7-d",
    geo: str = "GB",
) -> Optional[list[dict]]:
    """Fetch trending pet-related topics from Google Trends for the UK.

    Picks 3 random seed keywords (to stay within rate limits) and collects
    both "top" and "rising" related queries for each seed.

    Args:
        timeframe: pytrends timeframe string (default: last 7 days).
        geo: ISO country code (default: "GB" for United Kingdom).

    Returns:
        A deduplicated, sorted list of up to 15 trending query dicts, each
        with keys ``query``, ``value``, ``type`` ("top" or "rising"), and
        ``seed``.  Returns ``None`` on complete failure.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends is not installed -- run: pip install pytrends")
        return None

    try:
        pytrends = TrendReq(hl="en-GB", tz=0)
        seeds = random.sample(PET_SEED_KEYWORDS, min(3, len(PET_SEED_KEYWORDS)))

        all_trends: list[dict] = []

        for keyword in seeds:
            try:
                pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
                related = pytrends.related_queries()

                if keyword not in related:
                    continue

                top_df = related[keyword].get("top")
                rising_df = related[keyword].get("rising")

                if top_df is not None and not top_df.empty:
                    for _, row in top_df.head(5).iterrows():
                        all_trends.append({
                            "query": row["query"],
                            "value": int(row["value"]),
                            "type": "top",
                            "seed": keyword,
                        })

                if rising_df is not None and not rising_df.empty:
                    for _, row in rising_df.head(3).iterrows():
                        all_trends.append({
                            "query": row["query"],
                            "value": int(row["value"]),
                            "type": "rising",
                            "seed": keyword,
                        })

            except Exception as exc:
                logger.debug("Trends lookup failed for '%s': %s", keyword, exc)
                continue

        # Deduplicate by lowercased query, keeping highest-value entry
        seen: set[str] = set()
        unique: list[dict] = []
        for trend in sorted(all_trends, key=lambda x: x["value"], reverse=True):
            key = trend["query"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(trend)

        logger.info(
            "Google Trends: fetched %d raw results, %d unique (seeds: %s)",
            len(all_trends),
            len(unique),
            ", ".join(seeds),
        )
        return unique[:15]

    except Exception as exc:
        logger.error("Google Trends fetch failed: %s", exc)
        return None


def get_trending_interest(
    keyword: str,
    timeframe: str = "today 3-m",
    geo: str = "GB",
) -> Optional[dict]:
    """Get interest-over-time data for a specific keyword.

    Useful for checking whether a topic is currently rising or falling in
    search popularity before deciding to create content around it.

    Args:
        keyword: The search term to look up.
        timeframe: pytrends timeframe string (default: last 3 months).
        geo: ISO country code (default: "GB").

    Returns:
        dict with ``keyword``, ``current`` (latest value), ``average``,
        ``peak``, and ``trend`` ("rising" or "falling").
        Returns ``None`` on failure or if no data is available.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends is not installed -- run: pip install pytrends")
        return None

    try:
        pytrends = TrendReq(hl="en-GB", tz=0)
        pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
        interest = pytrends.interest_over_time()

        if interest is None or interest.empty:
            logger.info("No interest-over-time data for '%s'", keyword)
            return None

        values = interest[keyword].tolist()
        if not values:
            return None

        current = values[-1]
        average = sum(values) / len(values)
        peak = max(values)

        # Determine trend direction: compare the latter quarter with the
        # first quarter for a more robust signal than just first-vs-last.
        quarter = max(len(values) // 4, 1)
        early_avg = sum(values[:quarter]) / quarter
        late_avg = sum(values[-quarter:]) / quarter
        trend = "rising" if late_avg >= early_avg else "falling"

        result = {
            "keyword": keyword,
            "current": int(current),
            "average": round(average, 1),
            "peak": int(peak),
            "trend": trend,
            "data_points": len(values),
        }
        logger.info(
            "Interest for '%s': current=%d, avg=%.1f, peak=%d, trend=%s",
            keyword,
            result["current"],
            result["average"],
            result["peak"],
            result["trend"],
        )
        return result

    except Exception as exc:
        logger.error("Interest lookup failed for '%s': %s", keyword, exc)
        return None
