"""
Content generator for social media posts.
Pulls content from WordPress, generates captions, hashtags, and selects
the best content to post next while avoiding duplicates.
"""

import base64
import hashlib
import logging
import random
import re
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("social-agent.content")

# ----- Hashtag pools by category keyword -----

HASHTAG_MAP = {
    "dog": ["#DogLovers", "#DogLife", "#DogSupplies", "#DogsOfInstagram", "#DogBeds", "#DogGrooming", "#DogToys", "#PuppyLove"],
    "cat": ["#CatLovers", "#CatLife", "#CatSupplies", "#CatsOfInstagram", "#CatBeds", "#CatToys", "#KittyLove", "#Meow"],
    "bed": ["#PetBeds", "#DogBeds", "#CatBeds", "#CozyPets", "#PetComfort", "#LuxuryPetBeds"],
    "toy": ["#PetToys", "#DogToys", "#CatToys", "#InteractivePetToys", "#PetPlay", "#FunForPets"],
    "food": ["#PetFood", "#DogFood", "#CatFood", "#HealthyPets", "#PetNutrition", "#NaturalPetFood"],
    "groom": ["#PetGrooming", "#DogGrooming", "#CatGrooming", "#PetCare", "#FurBaby", "#CleanPets"],
    "health": ["#PetHealth", "#HealthyPets", "#PetWellness", "#PetCare", "#HappyPets", "#PetVet"],
    "treat": ["#PetTreats", "#DogTreats", "#CatTreats", "#HealthyTreats", "#PetSnacks"],
    "collar": ["#PetCollars", "#DogCollars", "#CatCollars", "#PetAccessories", "#PetFashion"],
    "leash": ["#DogLeash", "#DogWalking", "#PetAccessories", "#WalkTheDog", "#DogTraining"],
    "fish": ["#FishKeeping", "#Aquarium", "#TropicalFish", "#FishTank", "#AquaticPets"],
    "bird": ["#BirdLovers", "#PetBirds", "#BirdCage", "#Parakeet", "#BirdLife"],
    "rabbit": ["#RabbitLovers", "#BunnyLife", "#PetRabbit", "#BunnyLove"],
    "small": ["#SmallPets", "#Hamster", "#GuineaPig", "#SmallAnimalCare"],
    "cloth": ["#PetClothing", "#DogClothes", "#PetFashion", "#DogFashion", "#PetOutfits"],
    "travel": ["#PetTravel", "#TravelWithPets", "#PetCarrier", "#DogTravel", "#PetAdventure"],
    "train": ["#PetTraining", "#DogTraining", "#PuppyTraining", "#ObedienceTraining"],
    "supply": ["#PetSupplies", "#PetShop", "#PetStore", "#PetEssentials"],
}

UNIVERSAL_HASHTAGS = ["#PetHub", "#PetHubOnline", "#PetSupplies", "#PetCare", "#UKPets", "#PetShop", "#HappyPets", "#PetProducts", "#PetLovers"]

# ----- Caption templates for Facebook (longer, link-focused) -----

FB_CTA_PHRASES = [
    "Shop now", "Explore our range", "Browse the collection", "Order today",
    "Visit us now", "Check it out", "Discover more", "Get yours today",
]

FB_TEMPLATES = [
    "Discover {title} at PetHub Online! {snippet} {cta} and give your pet the best they deserve.",
    "Looking for the perfect {category} for your furry friend? Check out {title}! {snippet} {cta}!",
    "Your pet deserves the best! Explore {title} on PetHub Online. {snippet} Free delivery available!",
    "New in store: {title}! {snippet} {cta} - browse our full range of premium pet products at PetHub Online.",
    "Treat your pet to something special! {title} is now available. {snippet} {cta}!",
    "Pet parents, you'll love this! {title} - {snippet} Only at PetHub Online. {cta}!",
    "Give your pet the comfort they deserve with {title}. {snippet} {cta}!",
    "Premium quality, affordable prices! {title} at PetHub Online. {snippet} {cta}!",
    "Your four-legged friend will thank you! {title} - everything they need. {snippet} {cta}!",
    "Make every day special for your pet! {title} is here. {snippet} {cta}!",
]

# Variant B templates for A/B testing (different tone/structure)
FB_TEMPLATES_B = [
    "Have you tried {title} yet? {snippet} Your pet will love it! {cta} at PetHub Online.",
    "Pet lovers, meet {title}! {snippet} Top quality {category} delivered to your door. {cta}!",
    "Why settle for less? {title} gives your pet exactly what they need. {snippet} {cta}!",
    "The best {category} just got better! {title} - {snippet} {cta} at PetHub Online!",
    "Our customers love {title} and your pet will too! {snippet} {cta} today!",
]

# ----- Caption templates for Instagram (shorter, hashtag-heavy) -----

IG_TEMPLATES = [
    "{title} - the best for your pet! Link in bio",
    "Your pet deserves {title}! Link in bio",
    "New arrival: {title}! Link in bio",
    "Spoil your pet with {title}! Link in bio",
    "Premium pet care starts here! {title}",
    "Happy pets start with great products! {title}",
    "Treat your pet today! {title}",
    "Because they deserve the best - {title}",
    "Pet love is {title}! Link in bio",
    "Paws up for {title}! Link in bio",
]

# Variant B templates for A/B testing (different emoji/tone)
IG_TEMPLATES_B = [
    "Meet {title}! Your fur baby will love it",
    "Obsessed with {title}! Link in bio",
    "This is THE one! {title} for your pet",
    "Cannot get enough of {title}! Link in bio",
    "Your pet called - they want {title}!",
]

# ----- Emoji sets -----
PET_EMOJIS = [
    "\U0001F43E", "\U0001F436", "\U0001F431", "\U0001F415", "\U0001F408",
    "\U00002764\U0000FE0F", "\U0001F31F", "\U0001F389", "\U0001F381",
    "\U0001F6D2", "\U0001F3E0", "\U00002728", "\U0001F49B", "\U0001F490",
    "\U0001F60D", "\U0001F4AB", "\U0001F43B", "\U0001F430", "\U0001F41F",
    "\U0001F426", "\U0001F525", "\U0001F4A5", "\U0001F38A",
]


def strip_html(html_text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_snippet(content: str, max_length: int = 140) -> str:
    """Extract a clean snippet from page content."""
    text = strip_html(content)
    if len(text) <= max_length:
        return text
    # Cut at sentence boundary
    cut = text[:max_length]
    last_period = cut.rfind(".")
    last_excl = cut.rfind("!")
    last_quest = cut.rfind("?")
    best = max(last_period, last_excl, last_quest)
    if best > 40:
        return cut[: best + 1]
    return cut.rsplit(" ", 1)[0] + "..."


def guess_category(title: str, content: str) -> str:
    """Guess the product/content category from text. Title takes priority."""
    title_lower = title.lower()
    content_lower = strip_html(content).lower()
    categories = {
        "dog beds": ["dog bed", "dog beds", "puppy bed", "canine bed"],
        "cat beds": ["cat bed", "cat beds", "kitten bed", "feline bed"],
        "dog toys": ["dog toy", "dog toys", "puppy toy", "chew toy"],
        "cat toys": ["cat toy", "cat toys", "kitten toy", "laser toy", "feather toy"],
        "dog grooming": ["dog groom", "dog grooming"],
        "cat grooming": ["cat groom", "cat grooming"],
        "dog collars": ["dog collar", "dog lead", "dog leash", "dog harness"],
        "cat collars": ["cat collar"],
        "dog training": ["dog train", "training", "behaviour"],
        "dog health": ["dog health", "dog care"],
        "pet food": ["pet food", "dog food", "cat food", "kibble", "wet food", "treats"],
        "grooming": ["groom", "shampoo", "brush", "nail clipper", "fur care"],
        "collars & leads": ["collar", "lead", "leash", "harness"],
        "pet health": ["health", "supplement", "vitamin", "flea", "tick", "wormer"],
        "pet clothing": ["coat", "jacket", "clothing", "jumper", "bandana"],
        "dog supplies": ["dog supply", "dog supplies", "dog bowl", "dog feeding"],
        "cat supplies": ["cat supply", "cat supplies", "cat litter", "litter tray"],
        "cat scratching": ["scratching post", "scratching"],
        "pet supplies": ["supply", "supplies", "bowl", "feeder", "carrier", "crate"],
        "fish supplies": ["fish", "aquarium", "tank", "filter"],
        "bird supplies": ["bird", "cage", "parrot", "parakeet"],
        "small pets": ["hamster", "guinea pig", "rabbit", "hutch", "small animal"],
    }
    # First pass: check title only for the most precise match
    for category, keywords in categories.items():
        for kw in keywords:
            if kw in title_lower:
                return category
    # Second pass: check combined title + content
    combined = title_lower + " " + content_lower
    for category, keywords in categories.items():
        for kw in keywords:
            if kw in combined:
                return category
    return "pet supplies"


def generate_hashtags(title: str, content: str, platform: str = "instagram", max_tags: int = 25) -> list[str]:
    """Generate relevant hashtags based on content."""
    combined = (title + " " + strip_html(content)).lower()
    tags = set()

    # Add category-specific tags
    for keyword, hashtag_list in HASHTAG_MAP.items():
        if keyword in combined:
            tags.update(random.sample(hashtag_list, min(3, len(hashtag_list))))

    # Always add some universal tags
    universal_pick = random.sample(UNIVERSAL_HASHTAGS, min(4, len(UNIVERSAL_HASHTAGS)))
    tags.update(universal_pick)

    # If we have too few, pad with more universal ones
    if len(tags) < 8:
        extras = ["#Pets", "#PetOwners", "#FurBaby", "#PetParents", "#PetWorld", "#AnimalLovers", "#PetAccessories", "#OnlinePetShop"]
        tags.update(random.sample(extras, min(4, len(extras))))

    # For Instagram, add extra niche hashtags to reach 25
    if platform == "instagram" and len(tags) < 20:
        ig_extras = [
            "#PetLife", "#DogsOfIG", "#CatsOfIG", "#PetStore", "#PetObsessed",
            "#PetLover", "#FurryFriend", "#PetCommunity", "#PetMom", "#PetDad",
            "#PetFamily", "#PetStagram", "#PawPrint", "#AnimalLove", "#PetStyle",
        ]
        remaining = 25 - len(tags)
        tags.update(random.sample(ig_extras, min(remaining, len(ig_extras))))

    tag_list = list(tags)
    random.shuffle(tag_list)

    if platform == "facebook":
        return tag_list[:min(8, max_tags)]  # FB: 6-8 hashtags
    return tag_list[:min(max_tags, 25)]  # IG: up to 25 hashtags


def generate_caption_facebook(title: str, content: str, url: str, variant: str = "A") -> str:
    """Generate a Facebook-optimized caption (up to 400 chars body + URL + hashtags)."""
    snippet = extract_snippet(content, 200)
    category = guess_category(title, content)
    cta = random.choice(FB_CTA_PHRASES)
    templates = FB_TEMPLATES if variant == "A" else FB_TEMPLATES_B
    template = random.choice(templates)
    caption = template.format(title=title, snippet=snippet, category=category, cta=cta)

    # Trim caption body to 400 chars max
    if len(caption) > 400:
        caption = caption[:397] + "..."

    emojis = random.sample(PET_EMOJIS, 3)
    caption = f"{emojis[0]} {caption} {emojis[1]}"

    tags = generate_hashtags(title, content, "facebook", max_tags=8)
    tag_str = " ".join(tags[:8])

    full_caption = f"{caption}\n\n{emojis[2]} {cta}: {url}\n\n{tag_str}"
    return full_caption


def generate_caption_instagram(title: str, content: str, variant: str = "A") -> str:
    """Generate an Instagram-optimized caption (under 150 chars before hashtags, no links)."""
    templates = IG_TEMPLATES if variant == "A" else IG_TEMPLATES_B
    template = random.choice(templates)
    caption = template.format(title=title)

    emojis = random.sample(PET_EMOJIS, 5)
    caption = f"{emojis[0]} {caption} {emojis[1]}{emojis[2]}"

    # Keep caption body under 150 chars
    if len(caption) > 150:
        caption = caption[:147] + "..."

    tags = generate_hashtags(title, content, "instagram", max_tags=25)
    tag_str = " ".join(tags[:25])

    full_caption = f"{caption}\n\n{emojis[3]}{emojis[4]} {tag_str}"
    return full_caption


async def fetch_wp_pages() -> list[dict]:
    """Fetch all published pages from WordPress."""
    auth_str = base64.b64encode(
        f"{settings.WP_USER}:{settings.WP_APP_PASSWORD}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth_str}"}

    all_items = []
    page_num = 1

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{settings.WP_URL}/wp-json/wp/v2/pages",
                params={"per_page": 100, "page": page_num, "status": "publish"},
                headers=headers,
            )
            if resp.status_code != 200:
                break
            items = resp.json()
            if not items:
                break
            all_items.extend(items)
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            if page_num >= total_pages:
                break
            page_num += 1

    return all_items


async def fetch_wp_posts() -> list[dict]:
    """Fetch all published posts from WordPress."""
    auth_str = base64.b64encode(
        f"{settings.WP_USER}:{settings.WP_APP_PASSWORD}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth_str}"}

    all_items = []
    page_num = 1

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{settings.WP_URL}/wp-json/wp/v2/posts",
                params={"per_page": 100, "page": page_num, "status": "publish"},
                headers=headers,
            )
            if resp.status_code != 200:
                break
            items = resp.json()
            if not items:
                break
            all_items.extend(items)
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            if page_num >= total_pages:
                break
            page_num += 1

    return all_items


async def fetch_featured_image_url(media_id: int) -> Optional[str]:
    """Fetch the URL of a featured image from WordPress."""
    if not media_id:
        return None

    auth_str = base64.b64encode(
        f"{settings.WP_USER}:{settings.WP_APP_PASSWORD}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth_str}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.WP_URL}/wp-json/wp/v2/media/{media_id}",
                headers=headers,
            )
            if resp.status_code == 200:
                media = resp.json()
                # Try to get full size, fall back to medium_large, then medium
                sizes = media.get("media_details", {}).get("sizes", {})
                for size_key in ["full", "medium_large", "large", "medium"]:
                    if size_key in sizes:
                        return sizes[size_key]["source_url"]
                # Fallback to source_url
                return media.get("source_url")
    except Exception as e:
        logger.error(f"Failed to fetch featured image {media_id}: {e}")

    return None


def content_fingerprint(wp_item: dict) -> str:
    """Generate a unique fingerprint for a WP page/post."""
    title = wp_item.get("title", {}).get("rendered", "")
    item_id = wp_item.get("id", 0)
    return hashlib.md5(f"{item_id}-{title}".encode()).hexdigest()[:12]


async def get_all_content() -> list[dict]:
    """Fetch all WordPress pages and posts, normalized for social posting."""
    pages = await fetch_wp_pages()
    posts = await fetch_wp_posts()

    all_content = []

    for item in pages + posts:
        title = strip_html(item.get("title", {}).get("rendered", ""))
        content_raw = item.get("content", {}).get("rendered", "")
        excerpt_raw = item.get("excerpt", {}).get("rendered", "")
        link = item.get("link", "")
        item_id = item.get("id", 0)
        featured_media = item.get("featured_media", 0)
        item_type = "page" if item in pages else "post"
        modified = item.get("modified", "")

        if not title or title.lower() in ["shop", "cart", "checkout", "my account", "privacy policy", "terms and conditions"]:
            continue

        all_content.append({
            "id": item_id,
            "title": title,
            "content": content_raw,
            "excerpt": excerpt_raw,
            "url": link,
            "featured_media_id": featured_media,
            "type": item_type,
            "modified": modified,
            "fingerprint": content_fingerprint(item),
            "category": guess_category(title, content_raw),
        })

    return all_content


def select_next_content(all_content: list[dict], posted_history: dict) -> Optional[dict]:
    """
    Select the next piece of content to post.
    Avoids recently posted content, rotates through all available.
    posted_history = {fingerprint: last_posted_iso_string}
    """
    if not all_content:
        return None

    now = datetime.now(timezone.utc)

    # Score each content item - lower is better (more suitable to post)
    scored = []
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
            hours_since = 999  # Never posted

        # Prefer items that haven't been posted recently
        # Give bonus to posts (vs pages) for variety
        score = -hours_since  # More negative = longer since posted = better
        if item["type"] == "post":
            score -= 24  # Slight bonus for blog posts

        scored.append((score, item))

    scored.sort(key=lambda x: x[0])

    # Pick from top candidates with some randomness
    top_n = min(5, len(scored))
    candidates = [s[1] for s in scored[:top_n]]
    return random.choice(candidates)


async def prepare_post(posted_history: dict, selected_content: Optional[dict] = None, variant: str = "A") -> Optional[dict]:
    """
    Prepare a complete social media post with captions for both platforms.
    Returns dict with all data needed for posting, or None if nothing to post.
    Accepts optional pre-selected content and variant for A/B testing.
    """
    if selected_content:
        selected = selected_content
    else:
        all_content = await get_all_content()
        if not all_content:
            logger.warning("No content found in WordPress")
            return None

        selected = select_next_content(all_content, posted_history)
        if not selected:
            logger.warning("No content selected for posting")
            return None

    # Generate captions
    fb_caption = generate_caption_facebook(selected["title"], selected["content"], selected["url"], variant=variant)
    ig_caption = generate_caption_instagram(selected["title"], selected["content"], variant=variant)

    # Get featured image
    image_url = await fetch_featured_image_url(selected["featured_media_id"])

    # If no featured image, try to use the site logo or a default
    if not image_url:
        # Try to extract first image from content
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', selected["content"])
        if img_match:
            image_url = img_match.group(1)

    return {
        "content_id": selected["id"],
        "fingerprint": selected["fingerprint"],
        "title": selected["title"],
        "url": selected["url"],
        "category": selected["category"],
        "content_type": selected["type"],
        "image_url": image_url,
        "fb_caption": fb_caption,
        "ig_caption": ig_caption,
        "variant": variant,
        "platform_specs": {
            "facebook": {
                "max_caption_chars": 400,
                "recommended_image_size": "1200x630",
                "hashtag_limit": 8,
                "includes_url": True,
                "includes_cta": True,
            },
            "instagram": {
                "max_caption_chars": 150,
                "recommended_image_size": "1080x1080",
                "hashtag_limit": 25,
                "includes_url": False,
                "link_in_bio": True,
            },
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
