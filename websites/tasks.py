import json
import logging
import os
import re
import subprocess
import time
import requests as http_requests
from celery import shared_task
from django.core.cache import cache
from django.db.models import Count
from google import genai
from google.genai import types as genai_types
from urllib.parse import urlparse, urlunparse
import env

logger = logging.getLogger(__name__)

CLAUDE_PATH = os.path.expanduser("~/.local/bin/claude")
CLAUDE_MAX_CONCURRENT = 4
CLAUDE_SEMAPHORE_KEY = "claude_cli_slots"
FETCH_TIMEOUT = 10
FETCH_UA = "SiliconFriendly/1.0 (+https://siliconfriendly.com)"

LEVEL_NAMES = {
    1: "Basic Accessibility",
    2: "Discoverability",
    3: "Structured Interaction",
    4: "Agent Integration",
    5: "Autonomous Operation",
}

CRITERIA_DOCS = {
    "l1_semantic_html": "Uses semantic HTML elements (header, nav, main, article, section, footer) instead of just divs",
    "l1_meta_tags": "Has proper meta tags (title, description, og:tags, twitter:card)",
    "l1_schema_org": "Includes Schema.org JSON-LD structured data",
    "l1_no_captcha": "Does not block automated access with CAPTCHAs on public content",
    "l1_ssr_content": "Content is server-side rendered (visible in HTML source, not just JS-rendered)",
    "l1_clean_urls": "Uses clean, readable URLs (no excessive query params or hash fragments)",
    "l2_robots_txt": "Has a robots.txt that allows legitimate bot access",
    "l2_sitemap": "Provides an XML sitemap",
    "l2_llms_txt": "Has a /llms.txt file describing the site for LLMs",
    "l2_openapi_spec": "Publishes an OpenAPI/Swagger specification for its API",
    "l2_documentation": "Has comprehensive, machine-readable documentation",
    "l2_text_content": "Primary content is text-based (not locked in images/videos/PDFs)",
    "l3_structured_api": "Provides a structured REST or GraphQL API",
    "l3_json_responses": "API returns JSON responses with consistent schema",
    "l3_search_filter_api": "API supports search and filtering parameters",
    "l3_a2a_agent_card": "Has an A2A agent card at /.well-known/agent.json",
    "l3_rate_limits_documented": "Rate limits are documented and return proper 429 responses with Retry-After",
    "l3_structured_errors": "API returns structured error responses with error codes and messages",
    "l4_mcp_server": "Provides an MCP (Model Context Protocol) server",
    "l4_webmcp": "Supports WebMCP for browser-based agent interaction",
    "l4_write_api": "API supports write operations (POST/PUT/PATCH/DELETE), not just reads",
    "l4_agent_auth": "Supports agent-friendly authentication (API keys, OAuth client credentials)",
    "l4_webhooks": "Supports webhooks for event notifications",
    "l4_idempotency": "Write operations support idempotency keys",
    "l5_event_streaming": "Supports event streaming (SSE, WebSockets) for real-time updates",
    "l5_agent_negotiation": "Supports agent-to-agent capability negotiation",
    "l5_subscription_api": "Has a subscription/management API for agents",
    "l5_workflow_orchestration": "Supports multi-step workflow orchestration",
    "l5_proactive_notifications": "Can proactively notify agents of relevant changes",
    "l5_cross_service_handoff": "Supports cross-service handoff between agents",
}

_GEMINI_CLIENT = None


def _get_client():
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        _GEMINI_CLIENT = genai.Client(api_key=env.GEMINI_API_KEY)
    return _GEMINI_CLIENT


def _normalise_token(t):
    t = t.lower().strip()
    t = re.sub(r'[\s\-/]+', '_', t)
    t = re.sub(r'[^a-z0-9_]', '', t)
    return t


@shared_task
def generate_website_embedding(website_id):
    from websites.models import Website, Keyword

    try:
        website = Website.objects.get(id=website_id)
    except Website.DoesNotExist:
        return

    text = f"{website.name}. {website.description}"
    client = _get_client()

    # Generate embedding
    config = genai_types.EmbedContentConfig(
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=768,
    )
    res = client.models.embed_content(
        model="gemini-embedding-001",
        contents=[text],
        config=config,
    )
    embedding = res.embeddings[0].values
    # Normalize
    norm = sum(x * x for x in embedding) ** 0.5
    if norm > 0:
        embedding = [x / norm for x in embedding]

    website.embedding = embedding
    website.save(update_fields=["embedding"])

    # Generate keywords
    _generate_keywords(website, text)


def _generate_keywords(website, text):
    from websites.models import Keyword

    client = _get_client()
    prompt = f"Generate 20 search keywords/tokens for this website. Return a JSON array of strings.\n\nWebsite: {website.name}\nDomain: {website.url}\nDescription: {text}"

    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=genai_types.Schema(
            type=genai_types.Type.ARRAY,
            items=genai_types.Schema(type=genai_types.Type.STRING),
        ),
    )
    res = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=prompt,
        config=config,
    )

    try:
        tokens_raw = json.loads(res.text)
    except (json.JSONDecodeError, AttributeError):
        return

    tokens = set()
    for t in tokens_raw:
        normalized = _normalise_token(t)
        if normalized and len(normalized) >= 2:
            tokens.add(normalized)
            # Expand compound tokens
            parts = normalized.split('_')
            if len(parts) > 1:
                for p in parts:
                    if len(p) >= 2:
                        tokens.add(p)

    # Clear old keywords for this website
    website.keywords.clear()

    for t in tokens:
        kw, _ = Keyword.objects.get_or_create(token=t)
        kw.websites.add(website)


@shared_task
def daily_verification_crunch():
    """Daily cron: recompute website criteria from verifications."""
    from websites.models import Website, WebsiteVerification, CRITERIA_FIELDS

    uncounted = WebsiteVerification.objects.filter(counted=False)
    affected_ids = set(uncounted.values_list("website_id", flat=True))

    if not affected_ids:
        return "No uncounted verifications."

    for website_id in affected_ids:
        try:
            website = Website.objects.get(id=website_id)
        except Website.DoesNotExist:
            continue

        verifications = WebsiteVerification.objects.filter(website=website)

        for field in CRITERIA_FIELDS:
            weighted_true = 0
            weighted_total = 0
            for v in verifications:
                weight = 100 if v.is_trusted else 1
                if getattr(v, field):
                    weighted_true += weight
                weighted_total += weight

            if weighted_total > 0:
                setattr(website, field, weighted_true > weighted_total / 2)

        # Check if verified: trusted verification OR 12+ verifications
        trusted = verifications.filter(is_trusted=True).first()
        total_count = verifications.count()
        website.verified = trusted is not None or total_count >= 12
        if trusted:
            website.trusted_verification = WebsiteVerification.objects.filter(website=website, is_trusted=True).first()

        website.save()

    # Mark all as counted
    uncounted.update(counted=True)

    return f"Processed {len(affected_ids)} websites."


# ---------------------------------------------------------------------------
# Claude CLI website check
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str:
    """Validate URL to prevent SSRF attacks. Returns validated URL or raises ValueError."""
    try:
        # Minimal path validation
        if "/../" in url or re.search(r"/%2e%2e/", url, re.IGNORECASE):
            raise ValueError("Invalid path")
        
        parsed = urlparse(url)
        
        # Protocol check
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Invalid protocol")
        
        # Host check
        if not parsed.hostname:
            raise ValueError("Invalid host")
        
        return urlunparse(parsed)
    except Exception:
        raise ValueError("Invalid URL")

def _fetch_url(url, timeout=FETCH_TIMEOUT):
    """Fetch a URL. Returns (status, headers_dict, body) or None on error."""
    try:
        validated_url = _validate_url(url)
        resp = http_requests.get(validated_url, headers={"User-Agent": FETCH_UA}, timeout=timeout, allow_redirects=True)
        return {"status": resp.status_code, "headers": dict(resp.headers), "body": resp.text}
    except http_requests.exceptions.SSLError:
        try:
            fallback = url.replace("https://", "http://", 1)
            validated_fallback = _validate_url(fallback)
            resp = http_requests.get(validated_fallback, headers={"User-Agent": FETCH_UA}, timeout=timeout, allow_redirects=True)
            return {"status": resp.status_code, "headers": dict(resp.headers), "body": resp.text}
        except Exception:
            return None
    except Exception:
        return None


def _prefetch_website_data(domain):
    """Fetch all relevant data from a website for Claude analysis."""
    base = f"https://{domain}"
    data = {"domain": domain}

    # Homepage
    homepage = _fetch_url(base)
    if homepage:
        data["homepage_html"] = homepage["body"][:50000]
        data["homepage_headers"] = homepage["headers"]
    else:
        data["homepage_html"] = ""
        data["homepage_headers"] = {}

    # Standard files
    for key, path in [("robots_txt", "/robots.txt"), ("sitemap_xml", "/sitemap.xml"),
                      ("llms_txt", "/llms.txt"), ("agent_json", "/.well-known/agent.json")]:
        result = _fetch_url(f"{base}{path}")
        if result and result["status"] == 200:
            data[key] = result["body"][:10000]
        else:
            data[key] = None

    # API endpoint
    api_result = _fetch_url(f"{base}/api/") or _fetch_url(f"{base}/api")
    if api_result:
        data["api_response"] = {
            "status": api_result["status"],
            "content_type": api_result["headers"].get("Content-Type", ""),
            "body": api_result["body"][:5000],
            "headers": {k: v for k, v in api_result["headers"].items()
                        if any(x in k.lower() for x in ["ratelimit", "retry-after", "x-rate"])},
        }
    else:
        data["api_response"] = None

    # OpenAPI spec
    data["openapi_spec"] = None
    for path in ["/openapi.json", "/swagger.json", "/api-docs"]:
        result = _fetch_url(f"{base}{path}")
        if result and result["status"] == 200:
            data["openapi_spec"] = result["body"][:10000]
            data["openapi_path"] = path
            break

    # Docs
    data["docs_found_at"] = None
    for path in ["/docs", "/documentation", "/api/docs"]:
        result = _fetch_url(f"{base}{path}")
        if result and result["status"] == 200:
            data["docs_found_at"] = path
            data["docs_html"] = result["body"][:10000]
            break

    # Error response (404 check)
    err = _fetch_url(f"{base}/this-page-does-not-exist-sf-check")
    if err:
        data["error_response"] = {
            "status": err["status"],
            "content_type": err["headers"].get("Content-Type", ""),
            "body": err["body"][:3000],
        }
    else:
        data["error_response"] = None

    # Search endpoint
    search = _fetch_url(f"{base}/search") or _fetch_url(f"{base}/api/search")
    data["search_response"] = {"status": search["status"]} if search else None

    # Rate limit headers from homepage
    data["rate_limit_headers"] = {k: v for k, v in data["homepage_headers"].items()
                                  if any(x in k.lower() for x in ["ratelimit", "retry-after", "x-rate"])}

    return data


def _acquire_claude_slot(timeout=600):
    """Block until a Claude CLI slot is available."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = cache.get(CLAUDE_SEMAPHORE_KEY, 0)
        if current < CLAUDE_MAX_CONCURRENT:
            cache.set(CLAUDE_SEMAPHORE_KEY, current + 1, timeout=600)
            return True
        time.sleep(2)
    raise TimeoutError("Could not acquire Claude CLI slot")


def _release_claude_slot():
    """Release a Claude CLI slot."""
    current = cache.get(CLAUDE_SEMAPHORE_KEY, 1)
    cache.set(CLAUDE_SEMAPHORE_KEY, max(0, current - 1), timeout=600)


def _run_claude(prompt, timeout=180):
    """Run claude -p with Sonnet. Returns raw stdout string."""
    _acquire_claude_slot()
    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", prompt, "--model", "sonnet"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI error: {result.stderr[:500]}")
        return result.stdout.strip()
    finally:
        _release_claude_slot()


def _parse_json_from_claude(raw):
    """Extract JSON from Claude's response (handles markdown code fences)."""
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try extracting from code fence
    match = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding first { to last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from Claude response: {raw[:200]}")


def _build_step0_prompt(domain, data):
    html_snippet = data.get("homepage_html", "")[:5000]
    return f"""You are analyzing the website {domain}.

Based on the following homepage HTML, provide:
1. The website's name (what it's commonly known as)
2. A description (2-3 sentences, 150+ characters) of what the website does, aimed at helping AI agents understand the service

Homepage HTML (first 5000 chars):
{html_snippet}

Respond ONLY with a JSON object, nothing else:
{{"name": "...", "description": "..."}}"""


def _build_level_prompt(level, domain, name, description, data):
    from websites.models import LEVEL_RANGES
    fields = LEVEL_RANGES[level]
    level_name = LEVEL_NAMES[level]

    criteria_text = ""
    for f in fields:
        criteria_text += f"- {f}: {CRITERIA_DOCS[f]}\n"

    # Include relevant data per level
    context = f"Website: {name} ({domain})\nDescription: {description}\n\n"

    if level == 1:
        context += f"Homepage HTML (first 20000 chars):\n{data.get('homepage_html', '')[:20000]}\n"
    elif level == 2:
        context += f"robots.txt:\n{data.get('robots_txt') or 'NOT FOUND'}\n\n"
        context += f"sitemap.xml (first 5000 chars):\n{(data.get('sitemap_xml') or 'NOT FOUND')[:5000]}\n\n"
        context += f"llms.txt:\n{(data.get('llms_txt') or 'NOT FOUND')[:5000]}\n\n"
        context += f"OpenAPI spec found: {data.get('openapi_spec') is not None} (at {data.get('openapi_path', 'N/A')})\n"
        if data.get("openapi_spec"):
            context += f"OpenAPI spec (first 3000 chars):\n{data['openapi_spec'][:3000]}\n\n"
        context += f"Documentation page found at: {data.get('docs_found_at') or 'NOT FOUND'}\n"
        html = data.get("homepage_html", "")
        text_len = len(re.sub(r'<[^>]+>', '', html))
        context += f"Homepage HTML length: {len(html)} chars, text content length: {text_len} chars\n"
    elif level == 3:
        if data.get("api_response"):
            ar = data["api_response"]
            context += f"/api/ response: status={ar['status']}, content-type={ar['content_type']}\n"
            context += f"Body (first 2000 chars):\n{ar['body'][:2000]}\n\n"
        else:
            context += "/api/ endpoint: NOT FOUND\n\n"
        context += f"/.well-known/agent.json:\n{data.get('agent_json') or 'NOT FOUND'}\n\n"
        context += f"Rate limit headers: {data.get('rate_limit_headers') or 'NONE FOUND'}\n\n"
        if data.get("error_response"):
            er = data["error_response"]
            context += f"404 error response: status={er['status']}, content-type={er['content_type']}\n"
            context += f"Body (first 1000 chars):\n{er['body'][:1000]}\n\n"
        else:
            context += "404 error response: COULD NOT FETCH\n\n"
        context += f"Search endpoint: {data.get('search_response') or 'NOT FOUND'}\n"
    elif level == 4:
        context += f"Homepage HTML (first 15000 chars, check for MCP/WebMCP scripts):\n{data.get('homepage_html', '')[:15000]}\n\n"
        context += f"/.well-known/agent.json:\n{data.get('agent_json') or 'NOT FOUND'}\n\n"
        if data.get("api_response"):
            context += f"/api/ response: status={data['api_response']['status']}, content-type={data['api_response']['content_type']}\n\n"
        context += f"OpenAPI spec found: {data.get('openapi_spec') is not None}\n"
        if data.get("openapi_spec"):
            context += f"OpenAPI spec (first 5000 chars):\n{data['openapi_spec'][:5000]}\n\n"
        context += f"Documentation found at: {data.get('docs_found_at') or 'NOT FOUND'}\n"
        if data.get("docs_html"):
            context += f"Docs HTML (first 5000 chars):\n{data['docs_html'][:5000]}\n\n"
        context += f"llms.txt:\n{(data.get('llms_txt') or 'NOT FOUND')[:5000]}\n"
    elif level == 5:
        context += f"Homepage HTML (first 10000 chars):\n{data.get('homepage_html', '')[:10000]}\n\n"
        context += f"/.well-known/agent.json:\n{data.get('agent_json') or 'NOT FOUND'}\n\n"
        if data.get("api_response"):
            context += f"/api/ response body (first 3000 chars):\n{data['api_response']['body'][:3000]}\n\n"
        if data.get("openapi_spec"):
            context += f"OpenAPI spec (first 5000 chars):\n{data['openapi_spec'][:5000]}\n\n"
        if data.get("docs_html"):
            context += f"Docs HTML (first 5000 chars):\n{data['docs_html'][:5000]}\n\n"
        context += f"llms.txt:\n{(data.get('llms_txt') or 'NOT FOUND')[:5000]}\n"

    field_names = ', '.join(f'"{f}"' for f in fields)

    return f"""You are evaluating {domain} for Silicon Friendly Level {level} ({level_name}).

{context}

Check each of the following 6 criteria based on the data above. Be strict but fair.

Criteria:
{criteria_text}

IMPORTANT: Evaluate ALL 6 criteria. Do not skip any, even if previous ones failed.

Respond ONLY with a JSON object (no markdown, no code fences, no explanation), with exactly these keys: {field_names}

Each value must be an object with "pass" (boolean) and "reason" (string explaining why).

Example format:
{{"{fields[0]}": {{"pass": true, "reason": "Found semantic elements: header, nav, main, footer"}}, ...}}"""


def _build_report_prompt(job):
    all_reasoning = {}
    all_results = {}
    for level in range(1, 6):
        results = getattr(job, f"level_{level}_results") or {}
        reasoning = getattr(job, f"level_{level}_reasoning") or {}
        all_results.update(results)
        all_reasoning.update(reasoning)

    results_text = ""
    for level in range(1, 6):
        results = getattr(job, f"level_{level}_results") or {}
        reasoning = getattr(job, f"level_{level}_reasoning") or {}
        passed = sum(1 for v in results.values() if v)
        results_text += f"\n### Level {level}: {LEVEL_NAMES[level]} ({passed}/6)\n"
        for field, passed_val in results.items():
            status = "PASS" if passed_val else "FAIL"
            reason = reasoning.get(field, "")
            label = CRITERIA_DOCS.get(field, field)
            results_text += f"- [{status}] {label}: {reason}\n"

    return f"""Write a Silicon Friendly evaluation report for {job.domain} ({job.website_name}).

Overall Level: L{job.overall_level}
{job.website_description}

{results_text}

Write a comprehensive Markdown report covering:
1. **Executive Summary** - level achieved, what this means for AI agents
2. **What {job.domain} Does Well** - highlight the passes
3. **Level-by-Level Breakdown** - specific findings for each level
4. **Top 3 Recommendations** - actionable steps to improve their silicon friendliness
5. **Conclusion**

Be specific, practical, and reference actual findings. Write in a professional but approachable tone.
Respond with raw Markdown only (no JSON wrapping, no outer code fences)."""


def _send_check_report_email(job):
    """Email report summary — no attachment (avoids spam filters)."""
    if not job.carbon or not job.carbon.email:
        return
    import html as html_mod
    from common.mail import send_email
    from websites.report_pdf import _get_competitors, _build_ranked_list

    website = job.website
    competitors = _get_competitors(website) if website else []
    ranked_list, rank = _build_ranked_list(website, competitors) if website and competitors else ([], 1)
    total = len(ranked_list) if ranked_list else 1

    name = html_mod.escape(job.website_name or job.domain)
    level = job.overall_level
    domain = job.domain

    subject = f"SiliconFriendly Report - {job.website_name or domain} ranks #{rank} out of {total} competitors"
    page_url = f"https://siliconfriendly.com/w/{domain}/"
    report_url = f"https://siliconfriendly.com/api/check/{domain}/report/{job.id}/"

    # First 3 and last 3 lines of report
    report_lines = [l for l in (job.report_md or "").strip().split('\n') if l.strip()]
    first_3 = report_lines[:3] if len(report_lines) >= 3 else report_lines
    last_3 = report_lines[-3:] if len(report_lines) >= 6 else []
    from websites.report_pdf import _md_to_html
    report_preview = _md_to_html('\n'.join(first_3))
    if last_3:
        report_preview += '<p style="color:#999;margin:12px 0;">...</p>'
        report_preview += _md_to_html('\n'.join(last_3))

    html_body = f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;background:#ede8e0;">

<div style="padding:40px 32px;text-align:center;">
    <a href="https://siliconfriendly.com" style="text-decoration:none;">
        <div style="font-family:'Courier New',monospace;font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.15em;margin-bottom:16px;">siliconfriendly.com</div>
    </a>
    <div style="font-family:'Courier New',monospace;font-size:56px;font-weight:700;color:#1a1a1a;">L{level}</div>
    <div style="font-size:28px;font-weight:900;color:#1a1a1a;margin-top:8px;letter-spacing:-0.03em;">
        <a href="{page_url}" style="color:#1a1a1a;text-decoration:none;">{html_mod.escape(domain)}</a>
    </div>
    <div style="font-family:'Courier New',monospace;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:0.15em;margin-top:8px;">
        {'not silicon friendly yet' if level == 0 else f'level {level}: {LEVEL_NAMES.get(level, "").lower()}'}
    </div>
</div>

<div style="padding:0 32px 28px;">
    <p style="font-size:15px;color:#1a1a1a;line-height:1.6;margin:0 0 20px;">
        <a href="{page_url}" style="color:#1a1a1a;text-decoration:underline;font-weight:700;">{name}</a> achieved <strong>Level {level}</strong>, ranking <strong>#{rank} among {total}</strong> competitors.
    </p>

    <div style="font-family:'Courier New',monospace;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;color:#999;margin-bottom:12px;">Report Preview</div>
    <div style="font-size:13px;color:#1a1a1a;line-height:1.7;margin-bottom:24px;">
        {report_preview}
    </div>

    <div style="text-align:center;margin:28px 0;">
        <a href="{page_url}" style="display:inline-block;background:#1a1a1a;color:#ede8e0;padding:14px 32px;text-decoration:none;font-size:15px;font-weight:700;">View Full Report on SiliconFriendly</a>
    </div>
    <p style="text-align:center;font-size:12px;color:#999;margin:0;">
        The PDF of your report can be seen once you login to your account.
    </p>
</div>

<div style="padding:20px 32px;border-top:1px solid #d4cfc7;text-align:center;">
    <a href="https://siliconfriendly.com" style="font-family:'Courier New',monospace;font-size:11px;color:#666;text-decoration:none;font-weight:700;">siliconfriendly.com</a>
    <span style="color:#d4cfc7;margin:0 6px;">&middot;</span>
    <a href="https://unlikefraction.com" style="font-family:'Courier New',monospace;font-size:11px;color:#999;text-decoration:none;">unlikefraction.com</a>
</div>

</div>"""

    try:
        send_email(
            to_email=job.carbon.email,
            subject=subject,
            html_body=html_body,
        )
    except Exception as e:
        logger.error("Failed to send check report email: %s", e)


@shared_task
def run_website_check(check_job_id):
    """Main task: run Claude Sonnet to check a website across all 5 levels."""
    from websites.models import CheckJob, Website, LEVEL_RANGES

    job = CheckJob.objects.get(id=check_job_id)

    try:
        # Step: Fetch data
        job.status = "fetching"
        job.save(update_fields=["status", "updated_at"])
        data = _prefetch_website_data(job.domain)

        if not data.get("homepage_html"):
            raise RuntimeError(f"Could not fetch {job.domain} — site may be down or unreachable")

        # Step 0: Get name + description
        job.status = "step_0"
        job.save(update_fields=["status", "updated_at"])
        raw = _run_claude(_build_step0_prompt(job.domain, data))
        info = _parse_json_from_claude(raw)
        job.website_name = info.get("name", job.domain)[:255]
        job.website_description = info.get("description", "")
        job.save(update_fields=["website_name", "website_description", "updated_at"])

        # Create website immediately (all criteria default False)
        website, created = Website.objects.get_or_create(
            url=job.domain,
            defaults={
                "name": job.website_name,
                "description": job.website_description,
                "submitted_by_carbon": job.carbon,
            },
        )
        if not created:
            website.name = job.website_name
            website.description = job.website_description
            if job.carbon and not website.submitted_by_carbon:
                website.submitted_by_carbon = job.carbon
            website.save(update_fields=["name", "description", "submitted_by_carbon", "updated_at"])
        job.website = website
        job.save(update_fields=["website", "updated_at"])

        # Steps 1-5: Check each level
        all_results = {}
        for level in range(1, 6):
            job.status = f"step_{level}"
            job.save(update_fields=["status", "updated_at"])

            prompt = _build_level_prompt(level, job.domain, job.website_name, job.website_description, data)
            raw = _run_claude(prompt)
            parsed = _parse_json_from_claude(raw)

            fields = LEVEL_RANGES[level]
            results = {}
            reasoning = {}
            for field in fields:
                field_data = parsed.get(field, {"pass": False, "reason": "Not evaluated by Claude"})
                if isinstance(field_data, dict):
                    results[field] = bool(field_data.get("pass", False))
                    reasoning[field] = str(field_data.get("reason", ""))
                else:
                    results[field] = bool(field_data)
                    reasoning[field] = ""

            setattr(job, f"level_{level}_results", results)
            setattr(job, f"level_{level}_reasoning", reasoning)
            job.save(update_fields=[f"level_{level}_results", f"level_{level}_reasoning", "updated_at"])
            all_results.update(results)

        # Compute overall level (highest passing, not cumulative)
        overall = 0
        for level in range(1, 6):
            fields = LEVEL_RANGES[level]
            passed = sum(1 for f in fields if all_results.get(f, False))
            if passed >= 4:
                overall = level
        job.overall_level = overall

        # Step 6: Generate report
        job.status = "step_6"
        job.save(update_fields=["status", "overall_level", "updated_at"])
        job.report_md = _run_claude(_build_report_prompt(job), timeout=240)

        # Update Website model with check results
        job.status = "saving"
        job.save(update_fields=["status", "report_md", "updated_at"])

        website = job.website
        for field, value in all_results.items():
            setattr(website, field, value)
        website.verified = True
        website.save()

        job.status = "done"
        job.save(update_fields=["status", "updated_at"])

        # Generate embedding async
        try:
            generate_website_embedding.delay(website.id)
        except Exception:
            pass

        # Send email report
        _send_check_report_email(job)

    except Exception as e:
        logger.exception("CheckJob %s failed: %s", check_job_id, e)
        job.status = "error"
        job.error_message = str(e)[:2000]
        job.save(update_fields=["status", "error_message", "updated_at"])
