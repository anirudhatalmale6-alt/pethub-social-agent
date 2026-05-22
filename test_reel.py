#!/usr/bin/env python3
"""
Quick test script for the reel generator module.
Tests slide creation with Pillow and video composition with FFmpeg
without making any Facebook API calls.
"""

import os
import sys
import time
import tempfile

# Ensure we can import from the project
sys.path.insert(0, os.path.dirname(__file__))


def test_create_slide():
    """Test 1: Create a test slide with Pillow."""
    print("=" * 60)
    print("TEST 1: Create a test slide with Pillow")
    print("=" * 60)

    from PIL import Image
    from reel_generator import create_slide

    # Create a test image (a simple colored rectangle simulating a product photo)
    test_img_path = "/tmp/pethub_test_input.png"
    img = Image.new("RGB", (800, 600), (100, 150, 200))
    # Draw a simple pattern to make it visually interesting
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([(100, 100), (700, 500)], fill=(200, 100, 50))
    draw.ellipse([(250, 150), (550, 450)], fill=(255, 200, 100))
    draw.text((300, 280), "TEST", fill=(255, 255, 255))
    img.save(test_img_path)
    print(f"  Created test input image: {test_img_path}")

    # Create a slide
    slide_output = "/tmp/pethub_test_slide.png"
    result = create_slide(
        image_path=test_img_path,
        text="Premium Dog Beds for Your Furry Friend",
        subtitle="Comfort & quality at Pet Hub Online",
        slide_num=1,
        total_slides=4,
        output_path=slide_output,
    )

    assert os.path.exists(result), f"Slide was not created at {result}"
    size = os.path.getsize(result)
    assert size > 0, "Slide file is empty"

    # Verify dimensions
    slide_img = Image.open(result)
    assert slide_img.size == (1080, 1920), f"Wrong dimensions: {slide_img.size}"

    print(f"  PASS: Slide created at {result} ({size:,} bytes, {slide_img.size[0]}x{slide_img.size[1]})")

    # Clean up test input
    os.remove(test_img_path)

    return slide_output


def test_create_multiple_slides():
    """Create multiple test slides for video composition."""
    print()
    print("=" * 60)
    print("TEST 2: Create multiple slides")
    print("=" * 60)

    from PIL import Image, ImageDraw
    from reel_generator import create_slide

    slides = []
    colors = [(180, 60, 60), (60, 180, 60), (60, 60, 180), (180, 180, 60)]
    texts = [
        ("Top Dog Beds for Your Pet!", "Premium comfort & quality"),
        ("Orthopedic Memory Foam", "Perfect for senior dogs"),
        ("Waterproof & Washable", "Easy to clean and maintain"),
        ("Shop now at pethubonline.com!", "Link in bio"),
    ]

    for i in range(4):
        # Create test image
        img_path = f"/tmp/pethub_test_img_{i}.png"
        img = Image.new("RGB", (800, 600), colors[i])
        draw = ImageDraw.Draw(img)
        draw.rectangle([(50, 50), (750, 550)], outline=(255, 255, 255), width=5)
        draw.text((300, 280), f"Slide {i+1}", fill=(255, 255, 255))
        img.save(img_path)

        # Create slide
        slide_path = f"/tmp/pethub_test_slide_{i}.png"
        create_slide(
            image_path=img_path,
            text=texts[i][0],
            subtitle=texts[i][1],
            slide_num=i + 1,
            total_slides=4,
            output_path=slide_path,
        )

        assert os.path.exists(slide_path), f"Slide {i} not created"
        slides.append(slide_path)
        os.remove(img_path)  # Clean up input
        print(f"  Slide {i+1}: {slide_path} ({os.path.getsize(slide_path):,} bytes)")

    print(f"  PASS: Created {len(slides)} slides")
    return slides


def test_compose_reel(slide_paths):
    """Test 3: Compose slides into a video with FFmpeg."""
    print()
    print("=" * 60)
    print("TEST 3: Compose reel video with FFmpeg (crossfade transitions)")
    print("=" * 60)

    from reel_generator import compose_reel

    output_video = f"/tmp/pethub_test_reel_{int(time.time())}.mp4"

    start_time = time.time()
    result = compose_reel(slide_paths, output_video, duration_per_slide=3.0)
    elapsed = time.time() - start_time

    assert os.path.exists(result), f"Video was not created at {result}"
    size = os.path.getsize(result)
    assert size > 0, "Video file is empty"
    assert size > 10000, f"Video file suspiciously small: {size} bytes"

    print(f"  PASS: Video created at {result}")
    print(f"         Size: {size:,} bytes ({size / 1024:.1f} KB)")
    print(f"         Composition time: {elapsed:.1f}s")

    # Quick probe with ffmpeg to verify it's a valid video
    import subprocess
    probe = subprocess.run(
        ["/usr/bin/ffmpeg", "-i", result, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    # ffmpeg -i writes info to stderr
    if "Video:" in probe.stderr:
        # Extract resolution and duration info
        for line in probe.stderr.split("\n"):
            if "Video:" in line:
                print(f"         Codec info: {line.strip()}")
            if "Duration:" in line:
                print(f"         {line.strip()}")
    else:
        print(f"  WARNING: Could not probe video metadata")

    return result


def test_reel_description():
    """Test 4: Generate a reel description."""
    print()
    print("=" * 60)
    print("TEST 4: Generate reel description")
    print("=" * 60)

    from reel_generator import generate_reel_description

    desc = generate_reel_description(
        title="Best Dog Beds UK",
        category="dog beds",
        url="https://pethubonline.com/dog-beds/",
    )

    assert len(desc) > 0, "Description is empty"
    assert len(desc) <= 300, f"Description too long: {len(desc)} chars"
    assert "#" in desc, "No hashtags in description"
    assert "pethubonline.com" in desc, "URL not in description"

    print(f"  PASS: Description generated ({len(desc)} chars)")
    print(f"  Content: {desc}")
    return desc


def cleanup(files):
    """Remove test files."""
    print()
    print("Cleaning up test files...")
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
                print(f"  Removed: {f}")
        except OSError:
            pass


def main():
    print()
    print("*" * 60)
    print("  PetHub Reel Generator - Test Suite")
    print("*" * 60)
    print()

    all_files = []
    passed = 0
    failed = 0

    # Test 1: Single slide
    try:
        slide = test_create_slide()
        all_files.append(slide)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 2: Multiple slides
    try:
        slides = test_create_multiple_slides()
        all_files.extend(slides)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1
        slides = []

    # Test 3: Compose video
    if slides:
        try:
            video = test_compose_reel(slides)
            all_files.append(video)
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    # Test 4: Description
    try:
        test_reel_description()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Cleanup
    cleanup(all_files)

    # Summary
    print()
    print("=" * 60)
    total = passed + failed
    if failed == 0:
        print(f"  ALL {total} TESTS PASSED")
    else:
        print(f"  {passed}/{total} tests passed, {failed} FAILED")
    print("=" * 60)
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
