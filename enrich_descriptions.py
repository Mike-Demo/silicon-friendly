"""
Enrich website descriptions by fetching homepage content and using Gemini to write
detailed, useful descriptions. Updates DB and triggers embedding regeneration.

Run on server: cd /home/ubuntu/silicon-friendly && source venv/bin/activate && python3 enrich_descriptions.py
"""
import os
import sys
import time
import json
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "siliconfriendly.settings")
django.setup()

import re
from urllib.parse import urlparse, urlunparse
import requests
from websites.models import Website
from websites.tasks import generate_website_embedding
from google import genai
from google.genai import types as genai_types
import env

client = genai.Client(api_key=env.GEMINI_API_KEY)

BATCH_SIZE = 10
SLEEP_BETWEEN = 1  # seconds between API calls to avoid rate limits


def build_validated_url(base_url: str, hostname: str) -> str:
    try:
        parsed = urlparse(base_url)
        
        if not parsed.hostname:
            raise ValueError("Invalid host")
        allowed_domains = ["example.com"]  # add your allowed domains here
        if parsed.hostname.lower() not in allowed_domains:
            raise ValueError("Invalid host")
        
        return urlunparse(parsed)
    except Exception:
        raise ValueError("Invalid URL")


def fetch_homepage(url, timeout=10):
    """Fetch homepage content, return truncated text."""
    try:
        validated_url = build_validated_url(f"https://{url}", url)
        resp = requests.get(validated_url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SiliconFriendly/1.0)"
        })
        text = resp.text[:8000]  # truncate to avoid huge payloads
        return text
    except Exception:
        return ""


def generate_description(url, name, homepage_content):
    """Use Gemini to generate a detailed description."""
    prompt = f"""You are writing a description for a website/API directory entry.
The description should tell a reader exactly what this website/service is, what it does, and how it can be used.
A silicon (AI agent) or carbon (human) should understand what they're dealing with without visiting the website.

Write 2-4 sentences. Be specific and practical. No marketing fluff.

Website: {name}
Domain: {url}
Homepage content (truncated):
{homepage_content[:5000]}

Write ONLY the description, nothing else."""

    try:
        res = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.3,
            ),
        )
        desc = res.text.strip()
        # Clean up any quotes or markdown
        desc = desc.strip('"\'')
        if len(desc) < 20:
            return None
        return desc
    except Exception as e:
        print(f"  Gemini error for {url}: {e}")
        return None


def main():
    websites = Website.objects.all().order_by('id')
    total = websites.count()
    print(f"Total websites to enrich: {total}")

    updated = 0
    failed = 0
    skipped = 0

    for i, website in enumerate(websites):
        print(f"\n[{i+1}/{total}] {website.url} ({website.name})")
        print(f"  Current: {website.description[:80]}...")

        # Fetch homepage
        homepage = fetch_homepage(website.url)
        if not homepage:
            print(f"  Could not fetch homepage, trying with www...")
            homepage = fetch_homepage(f"www.{website.url}")

        if not homepage:
            print(f"  SKIP - no homepage content")
            failed += 1
            continue

        # Generate new description
        new_desc = generate_description(website.url, website.name, homepage)

        if not new_desc:
            print(f"  SKIP - Gemini returned empty")
            failed += 1
            continue

        # Update DB
        website.description = new_desc
        website.save(update_fields=["description"])

        # Trigger embedding regeneration async via celery
        generate_website_embedding.delay(website.id)

        print(f"  NEW: {new_desc[:100]}...")
        updated += 1

        # Rate limiting
        time.sleep(SLEEP_BETWEEN)

        # Progress report every 50
        if (i + 1) % 50 == 0:
            print(f"\n=== PROGRESS: {i+1}/{total} | Updated: {updated} | Failed: {failed} ===\n")

    print(f"\n{'='*50}")
    print(f"DONE")
    print(f"Total: {total}")
    print(f"Updated: {updated}")
    print(f"Failed: {failed}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
