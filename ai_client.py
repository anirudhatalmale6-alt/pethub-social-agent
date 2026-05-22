"""OpenAI GPT integration for the Social Agent.

Uses httpx to call OpenAI API directly (no openai SDK dependency).
Provides AI-powered caption generation, A/B variants, and trending content suggestions.
"""

import json
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("social.ai")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 15.0

SYSTEM_PROMPT = (
    "You are a social media copywriter for Pet Hub Online (pethubonline.com), "
    "a UK-based pet supplies affiliate website. You write engaging, friendly posts "
    "that drive clicks and conversions. Your tone is warm, knowledgeable, and pet-loving."
)


async def _call_openai(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    temperature: float = 0.8,
    max_tokens: int = 500,
) -> Optional[str]:
    """Low-level helper to call the OpenAI chat completions endpoint."""
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        logger.error("OpenAI API timeout after %.0fs", TIMEOUT)
        return None
    except httpx.HTTPStatusError as exc:
        logger.error("OpenAI API HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        logger.error("OpenAI API unexpected error: %s", exc)
        return None


async def ai_generate_caption(
    title: str,
    content_snippet: str,
    url: str,
    platform: str,
    category: str,
) -> Optional[str]:
    """Generate a social media caption for a product/article page.

    Args:
        title: Page title.
        content_snippet: First ~200 chars of page content.
        url: Full URL to the page.
        platform: "facebook" or "instagram".
        category: Product category (e.g. "dog food", "cat toys").

    Returns:
        Generated caption string, or None if the API call fails.
    """
    if platform == "facebook":
        style_instruction = (
            "Write an engaging Facebook post caption (200-300 characters). "
            "Include a clear call-to-action, the URL, relevant emojis, and 6-8 hashtags. "
            f"The link is: {url}"
        )
    else:
        style_instruction = (
            "Write a punchy Instagram caption (100-150 characters before hashtags). "
            'Include "link in bio" as the CTA, use emojis, and add 15-20 relevant hashtags. '
            "Do NOT include the URL in the caption body."
        )

    user_prompt = (
        f"Platform: {platform}\n"
        f"Category: {category}\n"
        f"Title: {title}\n"
        f"Content: {content_snippet}\n\n"
        f"{style_instruction}\n\n"
        "Return ONLY the caption text, nothing else."
    )

    return await _call_openai(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )


async def ai_generate_ab_variant(
    original_caption: str,
    platform: str,
) -> Optional[str]:
    """Create a meaningfully different A/B variant of an existing caption.

    Args:
        original_caption: The original caption to create a variant of.
        platform: "facebook" or "instagram".

    Returns:
        A variant caption string, or None if the API call fails.
    """
    user_prompt = (
        f"Here is an existing {platform} caption:\n\n"
        f"{original_caption}\n\n"
        "Create a meaningfully different variant for A/B testing. Change the tone, "
        "structure, emoji usage, and call-to-action style. Keep the same core message "
        "and any URLs/hashtag count, but make it feel distinctly different.\n\n"
        "Return ONLY the new caption text, nothing else."
    )

    return await _call_openai(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )


async def ai_suggest_trending_content(
    existing_pages: list[str],
    season: str,
) -> Optional[list[dict]]:
    """Suggest 3-5 trending content ideas based on existing pages and season.

    Args:
        existing_pages: List of existing page titles on the site.
        season: Current season (e.g. "spring", "summer", "winter", "autumn").

    Returns:
        List of dicts with "title", "reason", "keywords", or None on failure.
    """
    pages_str = "\n".join(f"- {p}" for p in existing_pages[:30])

    user_prompt = (
        f"Current season: {season}\n"
        f"Existing pages on the site:\n{pages_str}\n\n"
        "Suggest 3-5 new content ideas for a UK pet supplies affiliate site. "
        "Consider seasonal trends, gaps in existing content, and high-intent keywords.\n\n"
        "Return a JSON array where each item has:\n"
        '- "title": suggested article/page title\n'
        '- "reason": why this content would perform well now\n'
        '- "keywords": list of 3-5 target keywords\n\n'
        "Return ONLY the JSON array, no markdown formatting."
    )

    result = await _call_openai(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model="gpt-4o",
        temperature=0.7,
        max_tokens=800,
    )

    if result is None:
        return None

    try:
        # Strip markdown code fences if present
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        logger.warning("OpenAI returned non-list for trending content: %s", type(parsed))
        return None
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse trending content JSON: %s", exc)
        return None
