// Checker.js — Polling-based checker with suspenseful reveal

// Security helpers (Aikido SAST remediation)
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}
// Only allow http(s) destinations — blocks javascript: and other dangerous schemes.
function safeRedirect(url) {
    try {
        var parsed = new URL(url, window.location.origin);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
            window.location.href = parsed.href;
        }
    } catch (e) { /* ignore malformed URLs */ }
}

const LEVEL_NAMES = {
    1: 'Basic Accessibility',
    2: 'Discoverability',
    3: 'Structured Interaction',
    4: 'Agent Integration',
    5: 'Autonomous Operation'
};

const CRITERIA_LABELS = {
    l1_semantic_html: 'Semantic HTML',
    l1_meta_tags: 'Meta Tags',
    l1_schema_org: 'Schema.org Data',
    l1_no_captcha: 'No CAPTCHA Walls',
    l1_ssr_content: 'Server-Side Content',
    l1_clean_urls: 'Clean URLs',
    l2_robots_txt: 'robots.txt',
    l2_sitemap: 'XML Sitemap',
    l2_llms_txt: 'llms.txt',
    l2_openapi_spec: 'OpenAPI Spec',
    l2_documentation: 'Documentation',
    l2_text_content: 'Text Content',
    l3_structured_api: 'Structured API',
    l3_json_responses: 'JSON Responses',
    l3_search_filter_api: 'Search/Filter API',
    l3_a2a_agent_card: 'A2A Agent Card',
    l3_rate_limits_documented: 'Rate Limits',
    l3_structured_errors: 'Structured Errors',
    l4_mcp_server: 'MCP Server',
    l4_webmcp: 'WebMCP',
    l4_write_api: 'Write API',
    l4_agent_auth: 'Agent Auth',
    l4_webhooks: 'Webhooks',
    l4_idempotency: 'Idempotency',
    l5_event_streaming: 'Event Streaming',
    l5_agent_negotiation: 'Agent Negotiation',
    l5_subscription_api: 'Subscription API',
    l5_workflow_orchestration: 'Workflow Orchestration',
    l5_proactive_notifications: 'Proactive Notifications',
    l5_cross_service_handoff: 'Cross-Service Handoff'
};

const STATUS_MESSAGES = {
    'queued': 'waiting in queue...',
    'fetching': 'fetching website data...',
    'step_0': 'analyzing website...',
    'step_1': 'checking level 1 — basic accessibility...',
    'step_2': 'checking level 2 — discoverability...',
    'step_3': 'checking level 3 — structured interaction...',
    'step_4': 'checking level 4 — agent integration...',
    'step_5': 'checking level 5 — autonomous operation...',
    'step_6': 'generating report...',
    'saving': 'saving results...',
};

var jobId = null;
var pollInterval = null;
var renderedLevels = {};
var animatingLevel = false;
var pendingLevels = [];
var allData = null;

function getCsrfToken() {
    var value = '; ' + document.cookie;
    var parts = value.split('; csrftoken=');
    if (parts.length === 2) return parts.pop().split(';').shift();
    return '';
}

function startCheck() {
    fetch('/api/check/' + encodeURIComponent(DOMAIN) + '/start/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        },
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
        if (data.exists) {
            safeRedirect(data.url);
            return;
        }
        if (data.error) {
            showError(data.error);
            return;
        }
        jobId = data.job_id;
        pollInterval = setInterval(pollStatus, 3000);
        pollStatus();
    })
    .catch(function() {
        showError('Could not start check. Please try again.');
    });
}

function pollStatus() {
    if (!jobId) return;

    fetch('/api/check/' + encodeURIComponent(DOMAIN) + '/status/?job_id=' + jobId)
    .then(function(res) { return res.json(); })
    .then(function(data) {
        if (data.error && data.status === undefined) {
            showError(data.error);
            clearInterval(pollInterval);
            return;
        }

        allData = data;

        // Update step text
        var stepText = STATUS_MESSAGES[data.status] || data.status;
        var stepEl = document.getElementById('current-step-text');
        if (stepEl) stepEl.textContent = stepText;

        // Queue position
        var queueEl = document.getElementById('queue-status');
        var queuePosEl = document.getElementById('queue-position');
        if (data.status === 'queued' && data.queue_position && queueEl) {
            queueEl.style.display = 'block';
            if (queuePosEl) queuePosEl.textContent = data.queue_position;
        } else if (queueEl) {
            queueEl.style.display = 'none';
        }

        // Show level results section once past intro
        if (data.status !== 'queued' && data.status !== 'fetching' && data.status !== 'step_0') {
            var intro = document.getElementById('checker-intro');
            var results = document.getElementById('level-results');
            if (intro) intro.style.display = 'none';
            if (results) results.style.display = 'block';
        }

        // Queue up completed levels for animated reveal
        for (var l = 1; l <= 5; l++) {
            if (data['level_' + l] && !renderedLevels[l]) {
                renderedLevels[l] = true;
                pendingLevels.push({ level: l, data: data['level_' + l] });
            }
        }
        processNextLevel();

        // Done — redirect to website page
        if (data.status === 'done') {
            clearInterval(pollInterval);
            // Wait for any remaining animations then redirect
            var checkRedirect = setInterval(function() {
                if (!animatingLevel && pendingLevels.length === 0) {
                    clearInterval(checkRedirect);
                    if (data.website_url) {
                        setTimeout(function() { safeRedirect(data.website_url); }, 1500);
                    }
                }
            }, 500);
        }

        // Error
        if (data.status === 'error') {
            clearInterval(pollInterval);
            showError(data.error || 'Check failed.');
        }
    })
    .catch(function() {});
}

function processNextLevel() {
    if (animatingLevel || pendingLevels.length === 0) return;
    var next = pendingLevels.shift();
    animateLevel(next.level, next.data);
}

function animateLevel(level, levelData) {
    animatingLevel = true;

    var container = document.getElementById('levels-container');

    // Update the step text to show current level
    var stepEl = document.getElementById('current-step-text');
    if (stepEl) stepEl.textContent = 'checking level ' + level + ' — ' + LEVEL_NAMES[level].toLowerCase() + '...';

    // Create the level block with section label
    var block = document.createElement('div');
    block.className = 'checker-levels-wrap';
    block.style.animation = 'fadeIn 0.4s ease';

    var section = document.createElement('div');
    section.className = 'section checker-level-section';

    var label = document.createElement('div');
    label.className = 'section-label';
    label.textContent = 'LEVEL ' + level + ' / ' + LEVEL_NAMES[level].toUpperCase();
    section.appendChild(label);

    var grid = document.createElement('div');
    grid.className = 'checker-criteria-grid';
    section.appendChild(grid);

    block.appendChild(section);
    container.appendChild(block);

    // Get the criteria keys and shuffle order for suspense
    var keys = Object.keys(levelData.results);
    var shuffled = keys.slice().sort(function() { return Math.random() - 0.5; });

    // First: add all rows with spinners
    var rows = [];
    shuffled.forEach(function(key) {
        var row = document.createElement('div');
        row.className = 'criteria-row';
        row.innerHTML =
            '<span class="criteria-row-label">' + escapeHtml(CRITERIA_LABELS[key] || key) + '</span>' +
            '<span class="criteria-row-status"><span class="criterion-spinner"></span></span>';
        grid.appendChild(row);
        rows.push({ el: row, key: key, pass: levelData.results[key] });
    });

    // Animate rows appearing
    var rowIndex = 0;
    function showNextRow() {
        if (rowIndex >= rows.length) {
            // All rows visible, now reveal results one by one
            revealResults();
            return;
        }
        rows[rowIndex].el.classList.add('visible');
        rowIndex++;
        setTimeout(showNextRow, 80);
    }
    setTimeout(showNextRow, 200);

    // Reveal pass/fail one at a time with random delays
    function revealResults() {
        var revealIndex = 0;
        function revealNext() {
            if (revealIndex >= rows.length) {
                // All revealed — show level summary
                var passed = 0;
                rows.forEach(function(r) { if (r.pass) passed++; });
                label.textContent = 'LEVEL ' + level + ' / ' + LEVEL_NAMES[level].toUpperCase() + ' — ' + passed + '/' + rows.length;

                // Show level result terminal block
                var resultBlock = document.createElement('div');
                resultBlock.className = 'checker-level-result ' + (passed >= 4 ? 'level-passed' : 'level-failed');
                resultBlock.innerHTML = '<div class="terminal-result-block"><span>> LEVEL ' + escapeHtml(level) + (passed >= 4 ? ' PASSED' : ' FAILED') + ' — ' + escapeHtml(passed) + '/' + escapeHtml(rows.length) + ' criteria met</span></div>';
                section.appendChild(resultBlock);

                animatingLevel = false;
                // Process next level after a brief pause
                setTimeout(processNextLevel, 800);
                return;
            }
            var r = rows[revealIndex];
            var status = r.el.querySelector('.criteria-row-status');
            r.el.classList.add(r.pass ? 'pass' : 'fail');
            status.textContent = r.pass ? 'PASS' : 'FAIL';
            revealIndex++;
            var delay = 2000 + Math.random() * 6000; // 2-8 seconds
            setTimeout(revealNext, delay);
        }
        revealNext();
    }
}

function showError(message) {
    var intro = document.getElementById('checker-intro');
    var results = document.getElementById('level-results');
    var final = document.getElementById('checker-final');
    var error = document.getElementById('checker-error');
    if (intro) intro.style.display = 'none';
    if (results) results.style.display = 'none';
    if (final) final.style.display = 'none';
    if (error) error.style.display = 'block';
    var msg = document.getElementById('error-message');
    if (msg) msg.textContent = message;
}

document.addEventListener('DOMContentLoaded', function() {
    if (IS_LOGGED_IN) {
        startCheck();
    }
});
