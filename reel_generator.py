"""
Facebook Reels generator module for PetHub Online social media agent.
Creates vertical (9:16) video reels from WordPress product page content,
using Pillow for slide generation and FFmpeg for video composition.
"""

import logging
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont

from config import settings
from content_generator import (
    fetch_wp_pages,
    generate_hashtags,
    guess_category,
    strip_html,
    extract_snippet,
)

logger = logging.getLogger("social-agent.reels")

# ─── Constants ─────────────────────────────────────────────────────────────

REEL_WIDTH = 1080
REEL_HEIGHT = 1920
IMAGE_AREA_HEIGHT = 1080  # Top portion for the product image
FFMPEG_BIN = "/usr/bin/ffmpeg"

DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Pet-product page keywords used to filter relevant pages
PRODUCT_KEYWORDS = [
    "dog", "cat", "pet", "puppy", "kitten", "bed", "toy", "groom",
    "collar", "leash", "food", "treat", "supply", "health", "fish",
    "bird", "rabbit", "hamster", "guinea", "clothing", "harness",
    "bowl", "feeder", "carrier", "crate", "litter", "scratching",
    "brush", "shampoo",
]

# Engaging CTA phrases for the final slide
REEL_CTAS = [
    "Shop now at pethubonline.com!\nLink in bio",
    "Visit pethubonline.com today!\nLink in bio",
    "Browse the full range at\npethubonline.com!",
    "Treat your pet today!\npethubonline.com",
    "Order now at pethubonline.com!\nFree delivery available",
]

# Emojis for reel descriptions
REEL_EMOJIS = [
    "\U0001F43E", "\U0001F436", "\U0001F431", "\U0001F525",
    "\U00002728", "\U0001F31F", "\U0001F6D2", "\U00002764\U0000FE0F",
    "\U0001F389", "\U0001F4AB",
]


# ─── Font helpers ──────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load DejaVu font if available, otherwise fall back to Pillow default."""
    path = DEJAVU_BOLD if bold else DEJAVU_REGULAR
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    # Fallback
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
               max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


# ─── 1. fetch_reel_content ─────────────────────────────────────────────────

async def fetch_reel_content() -> dict:
    """
    Fetch WordPress pages via the REST API, pick a product page suitable
    for a reel, and extract images, title, category, URL, and a snippet.
    """
    logger.info("Fetching WordPress pages for reel content...")
    pages = await fetch_wp_pages()

    if not pages:
        raise ValueError("No WordPress pages found")

    # Filter to product-related pages
    product_pages = []
    for page in pages:
        title_raw = page.get("title", {}).get("rendered", "")
        title = strip_html(title_raw).lower()
        content_raw = page.get("content", {}).get("rendered", "")
        content_text = strip_html(content_raw).lower()
        combined = title + " " + content_text

        # Skip utility pages
        if title in ["shop", "cart", "checkout", "my account", "privacy policy",
                      "terms and conditions", "home", "about", "contact"]:
            continue

        # Must match at least one product keyword
        if any(kw in combined for kw in PRODUCT_KEYWORDS):
            product_pages.append(page)

    if not product_pages:
        # Fall back to all non-utility pages
        logger.warning("No product pages found, using all available pages")
        product_pages = [
            p for p in pages
            if strip_html(p.get("title", {}).get("rendered", "")).lower()
            not in ["shop", "cart", "checkout", "my account", "privacy policy",
                     "terms and conditions"]
        ]

    if not product_pages:
        raise ValueError("No suitable pages found for reel content")

    # Pick a random product page
    page = random.choice(product_pages)

    title = strip_html(page.get("title", {}).get("rendered", ""))
    content_raw = page.get("content", {}).get("rendered", "")
    link = page.get("link", "")
    category = guess_category(title, content_raw)
    snippet = extract_snippet(content_raw, 120)

    # Extract image URLs from the page content
    img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
    all_images = img_pattern.findall(content_raw)

    # Filter to reasonable image URLs (skip tiny icons, data URIs, etc.)
    images = []
    for url in all_images:
        if url.startswith("data:"):
            continue
        # Skip very small images (likely icons)
        if any(skip in url.lower() for skip in ["icon", "logo", "favicon", "1x1", "pixel"]):
            continue
        images.append(url)

    # Take 3-5 images
    images = images[:5] if len(images) >= 5 else images[:max(len(images), 0)]

    # If fewer than 3 images, try the featured image
    if len(images) < 3:
        featured_media = page.get("featured_media", 0)
        if featured_media:
            from content_generator import fetch_featured_image_url
            feat_url = await fetch_featured_image_url(featured_media)
            if feat_url and feat_url not in images:
                images.insert(0, feat_url)

    if not images:
        raise ValueError(f"No images found for page '{title}'")

    # Ensure we have 3-5 images
    images = images[:5]

    # Extract headings / subheadings for slide text
    heading_pattern = re.compile(r'<h[2-4][^>]*>(.*?)</h[2-4]>', re.IGNORECASE | re.DOTALL)
    headings = [strip_html(h) for h in heading_pattern.findall(content_raw)]
    # Remove empty headings
    headings = [h for h in headings if h.strip()]

    logger.info(f"Selected page for reel: '{title}' with {len(images)} images")

    return {
        "title": title,
        "images": images,
        "category": category,
        "url": link,
        "snippet": snippet,
        "headings": headings,
    }


# ─── 2. create_slide ──────────────────────────────────────────────────────

def create_slide(
    image_path: str,
    text: str,
    subtitle: str,
    slide_num: int,
    total_slides: int,
    output_path: str,
) -> str:
    """
    Create a 1080x1920 vertical slide with:
    - Product image in the center/top portion (~1080x1080)
    - Gradient overlay at the bottom for text readability
    - Main text (white, bold) at the bottom
    - Subtitle in lighter color below
    - 'Pet Hub Online' branding in the corner
    - Slide counter in the top corner
    """
    # Create canvas
    canvas = Image.new("RGB", (REEL_WIDTH, REEL_HEIGHT), (15, 15, 25))

    # Load and place the product image
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to open image {image_path}: {e}")
        # Create a placeholder
        img = Image.new("RGB", (REEL_WIDTH, IMAGE_AREA_HEIGHT), (40, 40, 60))

    # Scale image to fit within 1080x1080 area, maintaining aspect ratio
    img_w, img_h = img.size
    scale = min(REEL_WIDTH / img_w, IMAGE_AREA_HEIGHT / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center the image in the top portion
    x_offset = (REEL_WIDTH - new_w) // 2
    y_offset = (IMAGE_AREA_HEIGHT - new_h) // 2 + 80  # Shift down slightly for counter
    canvas.paste(img, (x_offset, y_offset))

    draw = ImageDraw.Draw(canvas)

    # Draw gradient overlay at the bottom (from transparent to dark)
    gradient_start_y = IMAGE_AREA_HEIGHT + 80
    gradient_end_y = REEL_HEIGHT
    for y in range(gradient_start_y, gradient_end_y):
        progress = (y - gradient_start_y) / (gradient_end_y - gradient_start_y)
        alpha = int(220 * progress)
        draw.rectangle(
            [(0, y), (REEL_WIDTH, y)],
            fill=(15, 15, 25, 255),
        )

    # Also add a subtle overlay on the image bottom edge for blending
    for y in range(max(0, gradient_start_y - 200), gradient_start_y):
        progress = (y - (gradient_start_y - 200)) / 200
        opacity = int(180 * progress)
        overlay_color = (15, 15, 25)
        # Blend by drawing semi-transparent rectangles
        draw.rectangle(
            [(0, y), (REEL_WIDTH, y)],
            fill=(*overlay_color, opacity) if canvas.mode == "RGBA" else overlay_color,
        )

    # Load fonts
    font_title = _load_font(52, bold=True)
    font_subtitle = _load_font(36, bold=False)
    font_brand = _load_font(28, bold=True)
    font_counter = _load_font(32, bold=True)

    # Draw slide counter (top-right corner)
    counter_text = f"{slide_num}/{total_slides}"
    counter_bbox = draw.textbbox((0, 0), counter_text, font=font_counter)
    counter_w = counter_bbox[2] - counter_bbox[0]
    # Draw counter background pill
    pill_x = REEL_WIDTH - counter_w - 50
    pill_y = 35
    draw.rounded_rectangle(
        [(pill_x - 15, pill_y - 8), (pill_x + counter_w + 15, pill_y + 38)],
        radius=20,
        fill=(0, 0, 0, 180) if canvas.mode == "RGBA" else (30, 30, 40),
    )
    draw.text((pill_x, pill_y), counter_text, fill=(255, 255, 255), font=font_counter)

    # Draw main text at the bottom
    text_margin = 60
    max_text_width = REEL_WIDTH - (text_margin * 2)
    text_y_start = gradient_start_y + 40

    # Wrap main text
    lines = _wrap_text(text, font_title, max_text_width, draw)
    # Limit to 4 lines max
    lines = lines[:4]

    y_cursor = text_y_start
    for line in lines:
        # Draw text shadow for depth
        draw.text((text_margin + 2, y_cursor + 2), line, fill=(0, 0, 0), font=font_title)
        draw.text((text_margin, y_cursor), line, fill=(255, 255, 255), font=font_title)
        line_bbox = draw.textbbox((0, 0), line, font=font_title)
        line_height = line_bbox[3] - line_bbox[1]
        y_cursor += line_height + 12

    # Draw subtitle
    y_cursor += 20
    sub_lines = _wrap_text(subtitle, font_subtitle, max_text_width, draw)
    sub_lines = sub_lines[:3]
    for line in sub_lines:
        draw.text((text_margin + 1, y_cursor + 1), line, fill=(0, 0, 0), font=font_subtitle)
        draw.text((text_margin, y_cursor), line, fill=(200, 200, 220), font=font_subtitle)
        line_bbox = draw.textbbox((0, 0), line, font=font_subtitle)
        line_height = line_bbox[3] - line_bbox[1]
        y_cursor += line_height + 8

    # Draw branding (bottom-left corner)
    brand_text = "Pet Hub Online"
    brand_y = REEL_HEIGHT - 80
    # Accent bar
    draw.rectangle(
        [(text_margin, brand_y - 5), (text_margin + 4, brand_y + 30)],
        fill=(0, 180, 255),
    )
    draw.text((text_margin + 14, brand_y), brand_text, fill=(0, 180, 255), font=font_brand)

    # Add a subtle paw-print accent dot (top-left)
    draw.ellipse([(30, 30), (60, 60)], fill=(0, 180, 255))
    draw.ellipse([(38, 38), (52, 52)], fill=(15, 15, 25))

    # Save
    canvas.save(output_path, "PNG", quality=95)
    logger.info(f"Created slide {slide_num}/{total_slides}: {output_path}")
    return output_path


# ─── 3. compose_reel ──────────────────────────────────────────────────────

def compose_reel(
    slide_paths: list[str],
    output_path: str,
    duration_per_slide: float = 4.0,
) -> str:
    """
    Use FFmpeg to combine PNG slides into an MP4 with crossfade transitions.
    Each slide is shown for `duration_per_slide` seconds.
    Output: 1080x1920 MP4, H.264, 30fps.
    """
    if not slide_paths:
        raise ValueError("No slides to compose")

    if len(slide_paths) == 1:
        # Single slide - just make a simple video
        cmd = [
            FFMPEG_BIN, "-y",
            "-loop", "1", "-t", str(duration_per_slide),
            "-i", slide_paths[0],
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "30", "-preset", "medium",
            "-vf", f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease,pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            output_path,
        ]
        logger.info(f"FFmpeg single-slide command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")
        return output_path

    # Multiple slides: create individual clip videos, then chain xfade transitions
    transition_duration = 0.5
    temp_clips: list[str] = []
    temp_dir = tempfile.mkdtemp(prefix="pethub_reel_")

    try:
        # Step 1: Create individual video clips from each slide image
        for i, slide in enumerate(slide_paths):
            clip_path = os.path.join(temp_dir, f"clip_{i:03d}.mp4")
            cmd = [
                FFMPEG_BIN, "-y",
                "-loop", "1", "-t", str(duration_per_slide),
                "-i", slide,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", "30", "-preset", "medium",
                "-vf", f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease,pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                clip_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.error(f"FFmpeg clip creation failed for slide {i}: {result.stderr}")
                raise RuntimeError(f"FFmpeg clip creation failed: {result.stderr[-300:]}")
            temp_clips.append(clip_path)
            logger.info(f"Created video clip {i + 1}/{len(slide_paths)}")

        # Step 2: Chain xfade transitions between clips
        # For N clips, we need N-1 xfade operations, chained sequentially.
        # We process them iteratively: merge first two, then merge result with next, etc.

        current_video = temp_clips[0]
        current_duration = duration_per_slide

        for i in range(1, len(temp_clips)):
            next_clip = temp_clips[i]
            merged_path = os.path.join(temp_dir, f"merged_{i:03d}.mp4")

            # xfade offset = duration of current accumulated video minus transition overlap
            offset = current_duration - transition_duration

            cmd = [
                FFMPEG_BIN, "-y",
                "-i", current_video,
                "-i", next_clip,
                "-filter_complex",
                f"[0][1]xfade=transition=fade:duration={transition_duration}:offset={offset},format=yuv420p",
                "-c:v", "libx264", "-preset", "medium",
                "-r", "30",
                merged_path,
            ]
            logger.info(f"FFmpeg xfade step {i}/{len(temp_clips) - 1} (offset={offset:.1f}s)")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.error(f"FFmpeg xfade failed at step {i}: {result.stderr}")
                raise RuntimeError(f"FFmpeg xfade failed: {result.stderr[-300:]}")

            # Update accumulated duration: previous + new clip - transition overlap
            current_duration = current_duration + duration_per_slide - transition_duration
            current_video = merged_path

        # Step 3: Copy the final merged video to the output path
        if current_video != output_path:
            import shutil
            shutil.copy2(current_video, output_path)

        logger.info(f"Composed reel: {output_path} ({current_duration:.1f}s total)")
        return output_path

    finally:
        # Clean up temp clip files
        for clip in temp_clips:
            try:
                os.remove(clip)
            except OSError:
                pass
        # Clean up merged intermediates
        for f in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, f)
            try:
                os.remove(fpath)
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


# ─── 4. publish_reel ──────────────────────────────────────────────────────

async def publish_reel(video_path: str, description: str) -> dict:
    """
    Upload and publish a video as a Facebook Reel.
    Facebook Reels API 3-step flow:
      1. Start upload session -> get video_id
      2. Upload binary video file to rupload endpoint
      3. Finish upload -> publish the reel
    """
    page_id = settings.FB_PAGE_ID
    token = settings.FB_PAGE_TOKEN
    graph_url = settings.FB_GRAPH_URL

    if not os.path.exists(video_path):
        return {"success": False, "error": f"Video file not found: {video_path}"}

    file_size = os.path.getsize(video_path)
    logger.info(f"Publishing reel: {video_path} ({file_size} bytes)")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            # Step 1: Start upload session
            logger.info("Step 1: Starting upload session...")
            start_resp = await client.post(
                f"{graph_url}/{page_id}/video_reels",
                data={
                    "upload_phase": "start",
                    "access_token": token,
                },
            )
            start_data = start_resp.json()

            if "error" in start_data:
                error_msg = start_data["error"].get("message", "Unknown error")
                logger.error(f"Reel start failed: {error_msg}")
                return {"success": False, "error": error_msg, "phase": "start"}

            video_id = start_data.get("video_id")
            if not video_id:
                return {"success": False, "error": "No video_id returned from start phase",
                        "response": start_data}

            logger.info(f"Got video_id: {video_id}")

            # Step 2: Upload the video binary
            logger.info("Step 2: Uploading video binary...")
            with open(video_path, "rb") as vf:
                video_data = vf.read()

            upload_resp = await client.post(
                f"https://rupload.facebook.com/video-upload/v21.0/{video_id}",
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                content=video_data,
            )
            upload_data = upload_resp.json()

            if "error" in upload_data:
                error_msg = upload_data["error"].get("message", "Unknown error")
                logger.error(f"Reel upload failed: {error_msg}")
                return {"success": False, "error": error_msg, "phase": "upload"}

            logger.info(f"Upload response: {upload_data}")

            # Step 3: Finish and publish
            logger.info("Step 3: Finishing upload and publishing reel...")
            finish_resp = await client.post(
                f"{graph_url}/{page_id}/video_reels",
                data={
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "title": description[:70] if description else "Pet Hub Online",
                    "description": description,
                    "access_token": token,
                },
            )
            finish_data = finish_resp.json()

            if "error" in finish_data:
                error_msg = finish_data["error"].get("message", "Unknown error")
                logger.error(f"Reel finish failed: {error_msg}")
                return {"success": False, "error": error_msg, "phase": "finish"}

            reel_id = finish_data.get("id") or finish_data.get("video_id") or video_id
            logger.info(f"Reel published successfully: {reel_id}")

            return {
                "success": True,
                "reel_id": reel_id,
                "video_id": video_id,
                "platform": "facebook_reels",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }

    except httpx.TimeoutException:
        logger.error("Reel upload timed out")
        return {"success": False, "error": "Upload timed out"}
    except Exception as e:
        logger.error(f"Reel publishing failed: {e}")
        return {"success": False, "error": str(e)}


# ─── 5. generate_and_publish_reel ─────────────────────────────────────────

async def generate_and_publish_reel() -> dict:
    """
    Main orchestrator: fetch content, create slides, compose video,
    generate description, publish to Facebook, and clean up.
    """
    temp_files: list[str] = []
    timestamp = int(time.time())

    try:
        # 1. Fetch reel content from WordPress
        logger.info("=== Starting reel generation ===")
        content = await fetch_reel_content()
        title = content["title"]
        images = content["images"]
        category = content["category"]
        url = content["url"]
        snippet = content["snippet"]
        headings = content.get("headings", [])

        logger.info(f"Content: '{title}', {len(images)} images, category={category}")

        # 2. Download images to /tmp
        logger.info("Downloading images...")
        downloaded_images: list[str] = []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for i, img_url in enumerate(images):
                try:
                    resp = await client.get(img_url)
                    if resp.status_code == 200:
                        ext = ".jpg"
                        ct = resp.headers.get("content-type", "")
                        if "png" in ct:
                            ext = ".png"
                        elif "webp" in ct:
                            ext = ".webp"
                        img_path = f"/tmp/pethub_reel_img_{timestamp}_{i}{ext}"
                        with open(img_path, "wb") as f:
                            f.write(resp.content)
                        downloaded_images.append(img_path)
                        temp_files.append(img_path)
                        logger.info(f"Downloaded image {i + 1}: {img_path}")
                    else:
                        logger.warning(f"Failed to download image {img_url}: HTTP {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Failed to download image {img_url}: {e}")

        if not downloaded_images:
            return {
                "success": False,
                "error": "No images could be downloaded",
                "title": title,
            }

        # 3. Create slides with text overlays
        logger.info("Creating slides...")
        slide_paths: list[str] = []
        total_slides = min(len(downloaded_images), 5) + 1  # +1 for CTA slide

        # Prepare slide texts
        slide_texts: list[tuple[str, str]] = []

        # Slide 1: Title slide
        slide_texts.append((
            f"Top {category.title()} for Your Pet!",
            snippet if snippet else "Premium quality products for your furry friend",
        ))

        # Middle slides: Product images with descriptive text
        for i in range(1, len(downloaded_images)):
            if i - 1 < len(headings) and headings[i - 1]:
                main_text = headings[i - 1]
            else:
                main_text = f"Premium {category.title()}"

            sub_text = f"Quality products your pet will love"
            if i < len(headings):
                # Try to use next heading as subtitle
                sub_text = headings[i] if i < len(headings) else sub_text

            slide_texts.append((main_text, sub_text))

        # Ensure we have text for each image
        while len(slide_texts) < len(downloaded_images):
            slide_texts.append((
                f"Best {category.title()} Selection",
                "Only at Pet Hub Online",
            ))

        # Create image slides
        for i, img_path in enumerate(downloaded_images):
            slide_output = f"/tmp/pethub_reel_slide_{timestamp}_{i}.png"
            text, subtitle = slide_texts[i] if i < len(slide_texts) else (title, snippet)
            create_slide(
                image_path=img_path,
                text=text,
                subtitle=subtitle,
                slide_num=i + 1,
                total_slides=total_slides,
                output_path=slide_output,
            )
            slide_paths.append(slide_output)
            temp_files.append(slide_output)

        # Final CTA slide: reuse the first image as background
        cta_slide_output = f"/tmp/pethub_reel_slide_{timestamp}_cta.png"
        cta_text = random.choice(REEL_CTAS)
        create_slide(
            image_path=downloaded_images[0],
            text=cta_text,
            subtitle="Your one-stop shop for pet supplies",
            slide_num=total_slides,
            total_slides=total_slides,
            output_path=cta_slide_output,
        )
        slide_paths.append(cta_slide_output)
        temp_files.append(cta_slide_output)

        # 4. Compose into a reel video
        logger.info("Composing reel video...")
        output_video = f"/tmp/pethub_reel_{timestamp}.mp4"

        # Adjust duration per slide to target 15-30 seconds
        num_slides = len(slide_paths)
        if num_slides <= 3:
            duration_per = 5.0
        elif num_slides <= 4:
            duration_per = 4.5
        else:
            duration_per = 4.0

        compose_reel(slide_paths, output_video, duration_per_slide=duration_per)
        temp_files.append(output_video)  # Will be cleaned up only after publish

        # Verify the video
        if not os.path.exists(output_video) or os.path.getsize(output_video) == 0:
            return {
                "success": False,
                "error": "Video composition failed - empty output",
                "title": title,
            }

        video_size = os.path.getsize(output_video)
        logger.info(f"Reel video created: {output_video} ({video_size} bytes)")

        # 5. Generate reel description
        description = generate_reel_description(title, category, url)
        logger.info(f"Reel description: {description}")

        # 6. Publish to Facebook
        logger.info("Publishing reel to Facebook...")
        publish_result = await publish_reel(output_video, description)

        # 7. Build result
        result = {
            "success": publish_result.get("success", False),
            "title": title,
            "category": category,
            "url": url,
            "slides_count": num_slides,
            "video_path": output_video,
            "video_size_bytes": video_size,
            "description": description,
            "publish_result": publish_result,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        if publish_result.get("success"):
            result["reel_id"] = publish_result.get("reel_id")
            logger.info(f"=== Reel published successfully: {publish_result.get('reel_id')} ===")
        else:
            logger.error(f"=== Reel publish failed: {publish_result.get('error')} ===")

        return result

    except Exception as e:
        logger.error(f"Reel generation failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    finally:
        # 8. Clean up temp files (except the final video which may still be useful)
        logger.info("Cleaning up temporary files...")
        for fpath in temp_files:
            # Keep the final video for a while (caller may want it)
            if fpath.endswith(".mp4"):
                continue
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
                    logger.debug(f"Removed temp file: {fpath}")
            except OSError as e:
                logger.warning(f"Failed to remove temp file {fpath}: {e}")


# ─── 6. generate_reel_description ─────────────────────────────────────────

def generate_reel_description(title: str, category: str, url: str) -> str:
    """
    Create an engaging Facebook Reel description with emojis, hashtags, and URL.
    Kept under 300 characters.
    """
    emojis = random.sample(REEL_EMOJIS, 3)

    openers = [
        f"{emojis[0]} Check out the best {category} for your pet!",
        f"{emojis[0]} Your pet deserves the best {category}!",
        f"{emojis[0]} Top {category} picks for happy pets!",
        f"{emojis[0]} Spoil your fur baby with premium {category}!",
        f"{emojis[0]} Must-have {category} for pet parents!",
    ]

    opener = random.choice(openers)

    # Generate 5-8 hashtags
    tags = generate_hashtags(title, category, "facebook", max_tags=8)
    tag_str = " ".join(tags[:8])

    # Build description: opener + URL + hashtags
    desc = f"{opener} {emojis[1]}\n{url}\n{tag_str}"

    # Ensure under 300 chars
    if len(desc) > 300:
        # Trim hashtags
        while len(desc) > 300 and tags:
            tags.pop()
            tag_str = " ".join(tags)
            desc = f"{opener} {emojis[1]}\n{url}\n{tag_str}"

    return desc
