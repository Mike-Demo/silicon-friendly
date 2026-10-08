// Bubble Chart - SVG-based non-overlapping bubble visualization

// Escape user-influenced strings before injecting into the SVG markup.
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}

function renderBubbleChart(websites, currentDomain, currentLevel) {
    const container = document.getElementById('bubble-chart');
    if (!container) return;

    const width = container.clientWidth || 600;
    const height = Math.min(width * 0.75, 500);

    // Level to radius mapping
    function levelToRadius(level) {
        const base = Math.min(width, height) * 0.04;
        return base + level * base * 0.6;
    }

    // Level to color
    const COLORS = {
        0: '#666666', 1: '#e67e22', 2: '#f1c40f',
        3: '#27ae60', 4: '#3498db', 5: '#9b59b6'
    };

    // Create bubbles data
    let bubbles = websites.map(w => ({
        domain: w.domain || w.url || w.name,
        level: w.level || 0,
        radius: levelToRadius(w.level || 0),
        color: COLORS[w.level || 0],
        isCurrent: (w.domain || w.url || '').replace(/^https?:\/\//, '').split('/')[0] === currentDomain,
        x: width / 2 + (Math.random() - 0.5) * width * 0.5,
        y: height / 2 + (Math.random() - 0.5) * height * 0.5
    }));

    // Simple force simulation to avoid overlap
    for (let iter = 0; iter < 100; iter++) {
        for (let i = 0; i < bubbles.length; i++) {
            for (let j = i + 1; j < bubbles.length; j++) {
                const a = bubbles[i], b = bubbles[j];
                const dx = b.x - a.x;
                const dy = b.y - a.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                const minDist = a.radius + b.radius + 4;

                if (dist < minDist && dist > 0) {
                    const force = (minDist - dist) / dist * 0.5;
                    const fx = dx * force;
                    const fy = dy * force;
                    a.x -= fx;
                    a.y -= fy;
                    b.x += fx;
                    b.y += fy;
                }
            }

            // Keep within bounds
            const b = bubbles[i];
            b.x = Math.max(b.radius + 5, Math.min(width - b.radius - 5, b.x));
            b.y = Math.max(b.radius + 5, Math.min(height - b.radius - 5, b.y));
        }

        // Gravity toward center
        bubbles.forEach(b => {
            b.x += (width / 2 - b.x) * 0.01;
            b.y += (height / 2 - b.y) * 0.01;
        });
    }

    // Render SVG
    let svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;

    // Draw bubbles (smaller ones on top)
    bubbles.sort((a, b) => b.radius - a.radius);

    bubbles.forEach((b, i) => {
        const opacity = b.isCurrent ? 1 : 0.7;
        const strokeWidth = b.isCurrent ? 3 : 1;
        const strokeColor = b.isCurrent ? '#5a9a6b' : 'rgba(255,255,255,0.1)';

        svg += `<circle cx="${b.x}" cy="${b.y}" r="${b.radius}" fill="${b.color}" opacity="${opacity}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`;

        // Label for larger bubbles or current
        if (b.radius > 20 || b.isCurrent) {
            const label = b.domain.length > 12 ? b.domain.substring(0, 10) + '..' : b.domain;
            const fontSize = Math.max(8, Math.min(12, b.radius * 0.4));
            svg += `<text x="${b.x}" y="${b.y - 2}" text-anchor="middle" fill="white" font-family="Inter, sans-serif" font-size="${fontSize}" font-weight="${b.isCurrent ? '700' : '400'}">${escapeHtml(label)}</text>`;
            svg += `<text x="${b.x}" y="${b.y + fontSize}" text-anchor="middle" fill="white" font-family="Inter, sans-serif" font-size="${fontSize * 0.8}" opacity="0.8">L${b.level}</text>`;
        }
    });

    svg += '</svg>';
    container.innerHTML = svg;
}
