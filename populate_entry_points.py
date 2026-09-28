#!/usr/bin/env python3
"""Populate siliconfriendly_entry_point for all websites by checking common agent discovery URLs."""
import os
import sys
import django
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "siliconfriendly.settings")
sys.path.insert(0, "/home/ubuntu/silicon-friendly")
django.setup()

from websites.models import Website

def build_validated_url(base_url: str) -> str:
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

# Priority order - first match wins
DISCOVERY_PATHS = [
    "/llms.txt",
    "/skill.md",
    "/.well-known/agent.json",
    "/agent.json",
    "/openapi.json",
    "/swagger.json",
    "/api/docs",
    "/api/v1/docs",
    "/docs/api",
    "/.well-known/ai-plugin.json",
]

def check_website(website):
    """Check a website for agent discovery files. Return (website_id, url) or None."""
    domain = website.url
    
    # Skip if already has an entry point
    if website.siliconfriendly_entry_point:
        return None
    
    for path in DISCOVERY_PATHS:
        for scheme in ["https", "http"]:
            url = f"{scheme}://{domain}{path}"
            try:
                validated_url = build_validated_url(url)
                resp = requests.head(validated_url, timeout=5, allow_redirects=True)
                if resp.status_code == 200:
                    # For HEAD, verify with GET for small files
                    if path.endswith(('.txt', '.md', '.json')):
                        get_resp = requests.get(validated_url, timeout=5, allow_redirects=True)
                        if get_resp.status_code == 200 and len(get_resp.text.strip()) > 10:
                            # Make sure it's not an HTML error page
                            content = get_resp.text.strip()[:100].lower()
                            if not content.startswith('<!doctype') and not content.startswith('<html'):
                                print(f"  FOUND: {domain} -> {validated_url}")
                                return (website.id, validated_url)
                    else:
                        # For /api/docs type paths, just check status
                        print(f"  FOUND: {domain} -> {validated_url}")
                        return (website.id, validated_url)
            except Exception:
                continue
            break  # If https worked or failed, don't try http for same path
    
    return None

websites = list(Website.objects.filter(siliconfriendly_entry_point="").order_by("id"))
total = len(websites)
print(f"Checking {total} websites for agent discovery files...")

found = 0
with ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(check_website, w): w for w in websites}
    done = 0
    for future in as_completed(futures):
        done += 1
        if done % 50 == 0:
            print(f"  Progress: {done}/{total} checked, {found} found")
        result = future.result()
        if result:
            wid, url = result
            Website.objects.filter(id=wid).update(siliconfriendly_entry_point=url)
            found += 1

print(f"\nDone! Found entry points for {found}/{total} websites.")
