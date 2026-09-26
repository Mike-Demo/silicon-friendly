#!/usr/bin/env python3
"""
Standalone Silicon Friendly agent-readiness audit runner.

Replicates the audit logic from the silicon-friendly repo's websites/tasks.py
(_prefetch_website_data + _build_level_prompt) WITHOUT Django/Celery/Claude CLI.

Usage:
    python3 audit.py <domain> [--out DIR]

Produces:
    <domain>.data.json   - prefetched evidence bundle
    <domain>.prompts.md  - the 5 per-level judge prompts (paste to any LLM judge)

The judge step ("strict but fair", JSON pass/reason per criterion) is done by
feeding each level prompt to an LLM and recording the verdicts.

Fetch behavior matches upstream exactly:
    UA = "SiliconFriendly/1.0 (+https://siliconfriendly.com)", 10s timeout.
"""
import json
import re
import sys
import os
import urllib.request
import urllib.error

FETCH_UA = "SiliconFriendly/1.0 (+https://siliconfriendly.com)"
FETCH_TIMEOUT = 10

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
    "l4_idempotency_keys": "Write operations support idempotency keys",
    "l5_event_streaming": "Supports event streaming (SSE, WebSockets) for real-time updates",
    "l5_capability_negotiation": "Supports agent-to-agent capability negotiation",
    "l5_subscription_api": "Has a subscription/management API for agents",
    "l5_workflow_orchestration": "Supports multi-step workflow orchestration",
    "l5_proactive_notify": "Can proactively notify agents of relevant changes",
    "l5_cross_service_handoff": "Supports cross-service handoff between agents",
}

LEVEL_RANGES = {lvl: [k for k in CRITERIA_DOCS if k.startswith(f"l{lvl}_")] for lvl in range(1, 6)}


def _fetch_url(url, timeout=FETCH_TIMEOUT):
    """Fetch a URL. Returns dict(status, headers, body) or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                body = raw.decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return {"status": resp.status, "headers": dict(resp.headers), "body": body}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {"status": e.code, "headers": dict(e.headers), "body": body}
    except Exception:
        return None


def prefetch_website_data(domain):
    """Fetch all relevant data from a website for analysis. Mirrors upstream."""
    base = f"https://{domain}"
    data = {"domain": domain}

    homepage = _fetch_url(base)
    if homepage:
        data["homepage_html"] = homepage["body"][:50000]
        data["homepage_headers"] = homepage["headers"]
        data["homepage_status"] = homepage["status"]
    else:
        data["homepage_html"] = ""
        data["homepage_headers"] = {}
        data["homepage_status"] = None

    for key, path in [("robots_txt", "/robots.txt"), ("sitemap_xml", "/sitemap.xml"),
                      ("llms_txt", "/llms.txt"), ("agent_json", "/.well-known/agent.json")]:
        result = _fetch_url(f"{base}{path}")
        data[key + "_status"] = result["status"] if result else None
        if result and result["status"] == 200:
            data[key] = result["body"][:10000]
        else:
            data[key] = None

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

    data["openapi_spec"] = None
    data["openapi_path"] = None
    for path in ["/openapi.json", "/swagger.json", "/api-docs"]:
        result = _fetch_url(f"{base}{path}")
        if result and result["status"] == 200:
            data["openapi_spec"] = result["body"][:10000]
            data["openapi_path"] = path
            break

    data["docs_found_at"] = None
    for path in ["/docs", "/documentation", "/api/docs"]:
        result = _fetch_url(f"{base}{path}")
        if result and result["status"] == 200:
            data["docs_found_at"] = path
            data["docs_html"] = result["body"][:10000]
            break

    err = _fetch_url(f"{base}/this-page-does-not-exist-sf-check")
    if err:
        data["error_response"] = {
            "status": err["status"],
            "content_type": err["headers"].get("Content-Type", ""),
            "body": err["body"][:3000],
        }
    else:
        data["error_response"] = None

    search = _fetch_url(f"{base}/search") or _fetch_url(f"{base}/api/search")
    data["search_response"] = {"status": search["status"]} if search else None

    data["rate_limit_headers"] = {k: v for k, v in data["homepage_headers"].items()
                                  if any(x in k.lower() for x in ["ratelimit", "retry-after", "x-rate"])}
    return data


def build_level_prompt(level, domain, data):
    fields = LEVEL_RANGES[level]
    level_name = LEVEL_NAMES[level]
    criteria_text = "".join(f"- {f}: {CRITERIA_DOCS[f]}\n" for f in fields)
    context = f"Website: {domain}\n\n"

    if level == 1:
        context += f"Homepage HTTP status: {data.get('homepage_status')}\n"
        context += f"Homepage HTML (first 20000 chars):\n{data.get('homepage_html', '')[:20000]}\n"
    elif level == 2:
        context += f"robots.txt (status {data.get('robots_txt_status')}):\n{data.get('robots_txt') or 'NOT FOUND'}\n\n"
        context += f"sitemap.xml (status {data.get('sitemap_xml_status')}, first 5000 chars):\n{(data.get('sitemap_xml') or 'NOT FOUND')[:5000]}\n\n"
        context += f"llms.txt (status {data.get('llms_txt_status')}):\n{(data.get('llms_txt') or 'NOT FOUND')[:5000]}\n\n"
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
        context += f"/.well-known/agent.json (status {data.get('agent_json_status')}):\n{data.get('agent_json') or 'NOT FOUND'}\n\n"
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 audit.py <domain> [--out DIR]")
        sys.exit(1)
    domain = sys.argv[1].strip().lower()
    domain = re.sub(r'^https?://', '', domain).split('/')[0].rstrip('.')
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "."
    os.makedirs(out_dir, exist_ok=True)

    print(f"Fetching {domain} ...", flush=True)
    data = prefetch_website_data(domain)
    with open(os.path.join(out_dir, f"{domain}.data.json"), "w") as f:
        json.dump(data, f, indent=1)
    print(f"  homepage={data.get('homepage_status')} robots={data.get('robots_txt_status')} "
          f"sitemap={data.get('sitemap_xml_status')} llms={data.get('llms_txt_status')} "
          f"agent={data.get('agent_json_status')} docs={data.get('docs_found_at')} "
          f"api={data['api_response']['status'] if data.get('api_response') else None}")

    with open(os.path.join(out_dir, f"{domain}.prompts.md"), "w") as f:
        for lvl in range(1, 6):
            f.write(f"# Level {lvl}: {LEVEL_NAMES[lvl]}\n\n")
            f.write(build_level_prompt(lvl, domain, data))
            f.write("\n\n---\n\n")
    print(f"Wrote {domain}.data.json and {domain}.prompts.md to {out_dir}")


if __name__ == "__main__":
    main()
