#!/usr/bin/env python3
"""Publish service client test script"""
import argparse
import json
import sys
from pathlib import Path

import requests

SERVICE_URL = "http://127.0.0.1:8000/api/v1"


def test_health():
    print("=== Health Check ===")
    resp = requests.get(f"{SERVICE_URL}/health")
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
    assert resp.status_code == 200
    print("[OK] Health check passed")
    print()


def test_publish(cookie: str, cover_path: str = None):
    print("=== Publish Test ===")
    data = {
        "title": "Test Article: Service Verification",
        "content": "This is a test article via the publish service API.",
        "cookie": cookie,
    }
    files = {}
    if cover_path and Path(cover_path).exists():
        files["cover_image"] = (Path(cover_path).name, open(cover_path, "rb"), "image/jpeg")
    print(f"URL: {SERVICE_URL}/publish")
    print(f"Title: {data['title']}")
    print(f"Content length: {len(data['content'])}")
    print(f"Cookie length: {len(data['cookie'])}")
    print(f"Cover image: {cover_path}")
    print()
    resp = requests.post(
        f"{SERVICE_URL}/publish",
        data=data,
        files=files if files else None,
        timeout=600,
    )
    print(f"Status: {resp.status_code}")
    rid = resp.headers.get("X-Request-ID", "N/A")
    rtime = resp.headers.get("X-Response-Time", "N/A")
    print(f"X-Request-ID: {rid}")
    print(f"X-Response-Time: {rtime}")
    print("Response:")
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    if resp.status_code == 200:
        print()
        print("[OK] Request processed successfully")
    else:
        print()
        print("[ERROR] Request failed")
    print()


def main():
    parser = argparse.ArgumentParser(description="Publish service client test")
    parser.add_argument("--test-health", action="store_true")
    parser.add_argument("--test-publish", action="store_true")
    parser.add_argument("--cookie-file", type=str)
    parser.add_argument("--cookie", type=str)
    parser.add_argument("--cover", type=str)
    args = parser.parse_args()
    if not args.test_health and not args.test_publish:
        args.test_health = True
        args.test_publish = True
    if args.test_health:
        try:
            test_health()
        except requests.ConnectionError:
            print("[ERROR] Cannot connect to service. Is it running?")
            sys.exit(1)
    if args.test_publish:
        cookie = args.cookie
        if args.cookie_file:
            cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        if not cookie:
            print("ERROR: Provide cookie via --cookie or --cookie-file")
            sys.exit(1)
        test_publish(cookie, args.cover)


if __name__ == "__main__":
    main()
