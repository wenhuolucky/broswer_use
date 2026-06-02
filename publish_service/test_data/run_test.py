#!/usr/bin/env python3
"""Run publish-service tests with the bundled test_data fixtures."""

import json
import sys
from pathlib import Path

import requests

SERVICE_URL = "http://127.0.0.1:8000/api/v1"
TEST_DATA_DIR = Path(__file__).parent


def load_title():
    return (TEST_DATA_DIR / "title.txt").read_text(encoding="utf-8").strip()


def load_article():
    return (TEST_DATA_DIR / "article.md").read_text(encoding="utf-8")


def load_cookie():
    with open(TEST_DATA_DIR / "cookie_user2.json", "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def get_default_cover():
    cover = TEST_DATA_DIR / "cover.jpg"
    return str(cover) if cover.exists() else None


def test_health():
    print("=" * 60)
    print("Health Check")
    print("=" * 60)
    resp = requests.get(f"{SERVICE_URL}/health")
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
    assert resp.status_code == 200, f"Health check failed with status {resp.status_code}"
    print("[OK] Health check passed\n")


def test_publish(cover_path=None):
    print("=" * 60)
    print("Publish Test")
    print("=" * 60)

    title = load_title()
    content = load_article()
    cookie_data = load_cookie()

    if cover_path is None:
        cover_path = get_default_cover()

    print(f"Title: {title}")
    print(f"Content length: {len(content)} chars (Markdown)")
    print("Body images: use Markdown network URLs only")
    print(f"Cookie count: {len(cookie_data.get('cookies', []))}")
    print(f"Cover: {cover_path or 'none'}")
    print()

    data = {
        "title": title,
        "content": content,
        "cookie": json.dumps(cookie_data, ensure_ascii=False),
    }

    files = {}
    if cover_path and Path(cover_path).exists():
        files["cover_image"] = (Path(cover_path).name, open(cover_path, "rb"), "image/jpeg")

    print("Sending request...")
    resp = requests.post(
        f"{SERVICE_URL}/publish",
        data=data,
        files=files if files else None,
        timeout=600,
    )

    print(f"\nStatus: {resp.status_code}")
    rid = resp.headers.get("X-Request-ID", "N/A")
    rtime = resp.headers.get("X-Response-Time", "N/A")
    print(f"X-Request-ID: {rid}")
    print(f"X-Response-Time: {rtime}")
    print("\nResponse:")
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))

    if resp.status_code == 200:
        print("\n[OK] Request processed successfully")
    else:
        print("\n[ERROR] Request failed")
    print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test publish service with bundled test_data")
    parser.add_argument("--health", action="store_true", help="Only run health check")
    parser.add_argument("--publish", action="store_true", help="Only run publish test")
    parser.add_argument("--no-cover", action="store_true", help="Publish without cover image")
    parser.add_argument("--cover", type=str, help="Custom cover image path")
    args = parser.parse_args()

    if not args.health and not args.publish:
        args.health = True
        args.publish = True

    if args.health:
        try:
            test_health()
        except requests.ConnectionError:
            print("[ERROR] Cannot connect to service at http://127.0.0.1:8000")
            print("Please start the service first:")
            print("  ./venv/Scripts/python.exe -m publish_service.server")
            sys.exit(1)

    if args.publish:
        cover = None if args.no_cover else (args.cover or get_default_cover())
        test_publish(cover)


if __name__ == "__main__":
    main()
