"""
Trend detection for social media content.
Uses seasonal data, content gaps, and engagement patterns
to suggest optimal posting topics.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("social-agent.trends")

# Seasonal pet content suggestions by month
SEASONAL_TOPICS = {
    1: [
        {"topic": "New Year Pet Resolutions", "tags": ["#NewYearNewPet", "#PetGoals2026", "#HealthyPets"]},
        {"topic": "Winter Warmth - Cozy Pet Beds", "tags": ["#WinterPets", "#CozyPetBeds", "#WarmPets"]},
        {"topic": "Indoor Play Ideas for Cold Days", "tags": ["#IndoorPetFun", "#PetToys", "#WinterActivities"]},
    ],
    2: [
        {"topic": "Valentine's Day - Love Your Pet", "tags": ["#PetValentine", "#LoveYourPet", "#PawfectLove"]},
        {"topic": "Pet Dental Health Month", "tags": ["#PetDentalHealth", "#DogTeeth", "#CatTeeth"]},
        {"topic": "Rainy Day Pet Entertainment", "tags": ["#RainyDayPets", "#IndoorPetPlay"]},
    ],
    3: [
        {"topic": "Spring Grooming - Shedding Season", "tags": ["#SpringGrooming", "#SheddingSeason", "#PetGrooming"]},
        {"topic": "Spring Walks & Outdoor Gear", "tags": ["#SpringWalks", "#DogWalking", "#PetOutdoors"]},
        {"topic": "Flea & Tick Prevention Season", "tags": ["#FleaPrevention", "#TickControl", "#PetHealth"]},
    ],
    4: [
        {"topic": "Easter Pet Safety", "tags": ["#EasterPetSafety", "#PetSafety", "#ChocolateDangers"]},
        {"topic": "Spring Cleaning - Pet Accessories", "tags": ["#PetSpringClean", "#CleanPetBeds"]},
        {"topic": "Outdoor Adventure Gear", "tags": ["#PetAdventure", "#DogHiking", "#OutdoorPets"]},
    ],
    5: [
        {"topic": "National Pet Month UK", "tags": ["#NationalPetMonth", "#UKPets", "#CelebratePets"]},
        {"topic": "Summer Prep - Cooling Products", "tags": ["#CoolingMats", "#SummerPets", "#BeatTheHeat"]},
        {"topic": "Garden Safety for Pets", "tags": ["#GardenPetSafety", "#PetFriendlyGarden"]},
    ],
    6: [
        {"topic": "Summer Cooling Mats & Bowls", "tags": ["#SummerPetCare", "#CoolingMats", "#HydratePets"]},
        {"topic": "Travel with Pets - Holiday Prep", "tags": ["#PetTravel", "#HolidayWithPets", "#PetCarrier"]},
        {"topic": "Sun Protection for Pets", "tags": ["#PetSunSafety", "#DogSunscreen"]},
    ],
    7: [
        {"topic": "Fireworks Safety - Keep Pets Calm", "tags": ["#FireworksSafety", "#CalmPets", "#PetAnxiety"]},
        {"topic": "Summer Hydration Essentials", "tags": ["#PetHydration", "#WaterBowls", "#SummerPets"]},
        {"topic": "Beach & Pool Safety for Dogs", "tags": ["#DogBeach", "#DogSwimming", "#PetPoolSafety"]},
    ],
    8: [
        {"topic": "Back to School - Pet Routines", "tags": ["#BackToSchool", "#PetRoutine", "#SeparationAnxiety"]},
        {"topic": "Late Summer Outdoor Adventures", "tags": ["#PetAdventures", "#DogWalks", "#SummerFun"]},
        {"topic": "Pet First Aid Awareness", "tags": ["#PetFirstAid", "#PetEmergency", "#PetSafety"]},
    ],
    9: [
        {"topic": "Autumn Pet Care Tips", "tags": ["#AutumnPets", "#FallPetCare", "#SeasonalPetCare"]},
        {"topic": "Back to Routine - Training Products", "tags": ["#DogTraining", "#PetTraining", "#BackToRoutine"]},
        {"topic": "Harvest Season Treats", "tags": ["#PetTreats", "#AutumnTreats", "#HealthyPetSnacks"]},
    ],
    10: [
        {"topic": "Halloween Pet Safety & Costumes", "tags": ["#HalloweenPets", "#PetCostumes", "#PetSafety"]},
        {"topic": "Cozy Autumn Beds & Blankets", "tags": ["#CozyPets", "#PetBlankets", "#AutumnComfort"]},
        {"topic": "Dark Evenings - Reflective Gear", "tags": ["#ReflectiveDogGear", "#SafeWalks", "#DarkEvenings"]},
    ],
    11: [
        {"topic": "Bonfire Night - Pet Anxiety", "tags": ["#BonfireNight", "#PetAnxiety", "#CalmPets"]},
        {"topic": "Winter Prep - Warm Coats & Boots", "tags": ["#DogCoats", "#PetWinterWear", "#WarmPets"]},
        {"topic": "Black Friday Pet Deals", "tags": ["#BlackFriday", "#PetDeals", "#PetShopSale"]},
    ],
    12: [
        {"topic": "Christmas Gift Guide for Pets", "tags": ["#ChristmasPets", "#PetGifts", "#PetStocking"]},
        {"topic": "Holiday Pet Safety (Decorations & Food)", "tags": ["#HolidayPetSafety", "#ChristmasDangers"]},
        {"topic": "New Year Pet Party Essentials", "tags": ["#NewYearPets", "#PetParty", "#CelebratePets"]},
    ],
}

# Evergreen content suggestions when seasonal data runs thin
EVERGREEN_TOPICS = [
    {"topic": "Top 10 Must-Have Pet Products", "tags": ["#PetEssentials", "#PetMustHaves"]},
    {"topic": "How to Choose the Right Pet Bed", "tags": ["#PetBeds", "#PetComfort"]},
    {"topic": "Healthy Treats vs Unhealthy Ones", "tags": ["#PetNutrition", "#HealthyPetTreats"]},
    {"topic": "Grooming Tips for New Pet Owners", "tags": ["#PetGrooming", "#NewPetOwner"]},
    {"topic": "Best Toys for Bored Pets", "tags": ["#PetToys", "#PetEnrichment"]},
    {"topic": "Pet Collar & Lead Guide", "tags": ["#PetCollars", "#DogLeads"]},
]


def get_seasonal_suggestions() -> list:
    """Return current seasonal content suggestions."""
    now = datetime.now(timezone.utc)
    month = now.month

    suggestions = []

    # Current month topics
    current = SEASONAL_TOPICS.get(month, [])
    for item in current:
        suggestions.append({
            "topic": item["topic"],
            "tags": item["tags"],
            "relevance": "current_month",
            "priority": "high",
        })

    # Next month preview (start suggesting a week before)
    if now.day >= 24:
        next_month = (month % 12) + 1
        upcoming = SEASONAL_TOPICS.get(next_month, [])
        for item in upcoming[:2]:
            suggestions.append({
                "topic": f"[Upcoming] {item['topic']}",
                "tags": item["tags"],
                "relevance": "next_month",
                "priority": "medium",
            })

    # Pad with evergreen if we have fewer than 3
    if len(suggestions) < 3:
        import random
        picks = random.sample(EVERGREEN_TOPICS, min(2, len(EVERGREEN_TOPICS)))
        for item in picks:
            suggestions.append({
                "topic": item["topic"],
                "tags": item["tags"],
                "relevance": "evergreen",
                "priority": "low",
            })

    return suggestions


def get_content_gaps(all_content: list, posted_history: dict) -> list:
    """Find content that hasn't been posted in a while."""
    now = datetime.now(timezone.utc)
    gaps = []

    for item in all_content:
        fp = item.get("fingerprint", "")
        last_posted = posted_history.get(fp)

        if not last_posted:
            days_since = None
            gap_score = 100
        else:
            try:
                last_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
                days_since = (now - last_dt).days
                gap_score = days_since
            except (ValueError, TypeError):
                days_since = None
                gap_score = 50

        # Only include items not posted in 7+ days or never posted
        if days_since is None or days_since >= 7:
            gaps.append({
                "title": item.get("title", ""),
                "category": item.get("category", ""),
                "fingerprint": fp,
                "url": item.get("url", ""),
                "days_since_posted": days_since,
                "gap_score": gap_score,
            })

    gaps.sort(key=lambda x: x["gap_score"], reverse=True)
    return gaps[:20]


def get_trending_report(
    all_content: list,
    posted_history: dict,
    engagement_data: Optional[dict] = None,
) -> dict:
    """Combine seasonal + gaps + engagement data into actionable suggestions."""
    seasonal = get_seasonal_suggestions()
    gaps = get_content_gaps(all_content, posted_history)

    # Analyze which categories have gaps
    gap_categories = {}
    for g in gaps:
        cat = g["category"]
        if cat not in gap_categories:
            gap_categories[cat] = 0
        gap_categories[cat] += 1

    # Build recommendations
    recommendations = []

    # Seasonal recommendations
    for s in seasonal[:3]:
        recommendations.append({
            "type": "seasonal",
            "suggestion": s["topic"],
            "tags": s["tags"],
            "priority": s["priority"],
            "reason": f"Seasonal content for this time of year",
        })

    # Content gap recommendations
    for g in gaps[:5]:
        label = f"Repost: {g['title']}" if g["days_since_posted"] else f"First post: {g['title']}"
        days_text = f"{g['days_since_posted']} days since last post" if g["days_since_posted"] else "Never posted"
        recommendations.append({
            "type": "content_gap",
            "suggestion": label,
            "category": g["category"],
            "priority": "high" if (g["days_since_posted"] or 999) > 14 else "medium",
            "reason": days_text,
        })

    # Category gap recommendations
    underposted = sorted(gap_categories.items(), key=lambda x: x[1], reverse=True)
    for cat, count in underposted[:3]:
        recommendations.append({
            "type": "category_gap",
            "suggestion": f"Post more {cat} content ({count} items need attention)",
            "category": cat,
            "priority": "medium",
            "reason": f"{count} pieces of {cat} content haven't been posted recently",
        })

    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(),
        "seasonal_suggestions": seasonal,
        "content_gaps": gaps[:10],
        "gap_categories": dict(underposted[:5]),
        "recommendations": recommendations,
        "total_content": len(all_content),
        "total_gaps": len(gaps),
    }
