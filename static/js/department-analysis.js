let activityChartInstance = null;
let trendChartInstance = null;

function exportCSV() {
    const dept = (typeof lockedDept !== 'undefined' && lockedDept) || document.getElementById('deptSelect')?.value || '';
    window.location.href = `/api/department/export-csv?dept=${encodeURIComponent(dept)}`;
}

async function loadAnalysis() {
    const dept = (typeof lockedDept !== 'undefined' && lockedDept) || document.getElementById('deptSelect')?.value || '';
    const container = document.getElementById('analysisContent');
    if (!container) return;
    container.innerHTML = '<div class="spinner">Loading...</div>';

    const res = await fetch(`/api/analysis/department?dept=${encodeURIComponent(dept)}`);
    if (!res.ok) {
        container.innerHTML = '<div class="alert alert-error" style="margin-top:1rem;">Access denied: You do not have permission to view analysis for this department.</div>';
        return;
    }
    const d = await res.json();

    let html = `
    <div class="metrics-row">
        <div class="metric-box"><div class="metric-value">${d.faculty_count}</div><div class="metric-label">👥 Total Faculty</div></div>
        <div class="metric-box"><div class="metric-value">${d.total_publications}</div><div class="metric-label">📄 Total Publications</div></div>
        <div class="metric-box"><div class="metric-value">${d.total_citations}</div><div class="metric-label">📈 Total Citations</div></div>
        <div class="metric-box"><div class="metric-value">${d.manually_added_publications}</div><div class="metric-label">✍️ Manually Added</div></div>
    </div>`;

    // Activity Comparison Section
    if (d.department_activity && d.department_activity.length > 0) {
        html += `
        <div style="background:#fff; border-radius:14px; padding:1.5rem; box-shadow:0 4px 14px rgba(0,0,0,0.08); margin:1.5rem 0;">
            <h2>📊 Department Activity Comparison (6-Month Window)</h2>
            <canvas id="deptActivityChart" height="90" style="margin-top:1rem;"></canvas>
        </div>

        <h2>🏷️ Department Activity Scores</h2>
        <div class="metrics-row" style="margin-bottom:1.5rem;">`;

        d.department_activity.forEach(da => {
            const badge = da.needs_attention
                ? `<div style="margin-top:6px;"><span class="badge-count" style="background:#dc3545; font-size:0.75rem; padding:3px 8px; border-radius:6px;">⚠️ Needs Attention</span></div>`
                : `<div style="margin-top:6px;"><span style="background:#d4edda; color:#155724; font-size:0.75rem; padding:3px 8px; border-radius:6px; font-weight:600;">✅ Active</span></div>`;
            html += `
            <div class="metric-box" style="flex:1; min-width:180px; ${da.needs_attention ? 'border:2px solid #dc3545;' : ''}">
                <div style="font-weight:700; color:#004080; margin-bottom:4px;">${da.department}</div>
                <div class="metric-value" style="font-size:1.5rem;">${da.activity_score}%</div>
                <div class="metric-label">${da.active_faculty} / ${da.total_faculty} Faculty Active</div>
                ${badge}
            </div>`;
        });

        html += `</div>`;
    }

    // Monthly Trend Chart Section
    if (d.monthly_trend && d.monthly_trend.length > 0) {
        html += `
        <div style="background:#fff; border-radius:14px; padding:1.5rem; box-shadow:0 4px 14px rgba(0,0,0,0.08); margin:1.5rem 0;">
            <h2>📈 Publication Activity Trend (${d.department})</h2>
            <p style="color:#666; font-size:0.88rem;">Publication additions over time for offline & momentum tracking.</p>
            <canvas id="deptTrendChart" height="90" style="margin-top:1rem;"></canvas>
        </div>`;
    }

    // Inactive Faculty Section
    if (d.inactive_faculty && d.inactive_faculty.length > 0) {
        html += `
        <h2>⚠️ Inactive Faculty (${d.inactive_faculty.length})</h2>
        <p style="color:#555; margin-bottom:0.8rem; font-size:0.9rem;">Faculty members with no publication in the last 6 months. Use this list to follow up directly.</p>
        <table style="margin-bottom:2rem;">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Name</th>
                    <th>Department</th>
                    <th>Publications</th>
                    <th>Citations</th>
                    <th>Latest Manual Addition</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>`;

        d.inactive_faculty.forEach((f, i) => {
            const actionBtn = f.scholar_id && f.scholar_id !== 'Not Found'
                ? `<a href="/admin/faculty/${encodeURIComponent(f.scholar_id)}" class="btn btn-outline" style="padding:4px 10px; font-size:0.78rem;">Inspect Faculty</a>`
                : `<span style="color:#aaa; font-size:0.8rem;">No Scholar ID</span>`;
            const latestManual = f.latest_manual_pub ? f.latest_manual_pub.substring(0, 10) : 'None';

            html += `<tr>
                <td>${i+1}</td>
                <td><strong style="color:#dc3545;">🔴 ${f.name}</strong></td>
                <td>${f.department}</td>
                <td>${f.pub_count}</td>
                <td>${f.cited_by}</td>
                <td>${latestManual}</td>
                <td>${actionBtn}</td>
            </tr>`;
        });

        html += `</tbody></table>`;
    } else {
        html += `<div class="alert alert-info">🎉 Great news! All faculty members in this selection are currently Active.</div>`;
    }

    // Top / All Faculty Table
    html += `
    <h2>🏆 All Faculty Breakdown</h2>
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Name</th>
                <th>Department</th>
                <th>Citations</th>
                <th>Publications</th>
                <th>6-Month Activity</th>
                <th>Action</th>
            </tr>
        </thead>
        <tbody>`;

    if (d.top_faculty) {
        d.top_faculty.forEach((f, i) => {
            const statusDot = f.is_active
                ? `<span style="color:#28a745; font-weight:600;">🟢 Active</span>`
                : `<span style="color:#dc3545; font-weight:600;">🔴 Inactive</span>`;
            const actionBtn = f.scholar_id && f.scholar_id !== 'Not Found'
                ? `<a href="/admin/faculty/${encodeURIComponent(f.scholar_id)}" class="btn btn-outline" style="padding:4px 10px; font-size:0.78rem;">View Details</a>`
                : `<span style="color:#aaa; font-size:0.8rem;">No Scholar ID</span>`;

            html += `<tr>
                <td>${i+1}</td>
                <td><strong>${f.name}</strong></td>
                <td>${f.department || '—'}</td>
                <td>${f.cited_by}</td>
                <td>${f.pub_count}</td>
                <td>${statusDot}</td>
                <td>${actionBtn}</td>
            </tr>`;
        });
    }

    html += `</tbody></table>`;
    container.innerHTML = html;

    // Render department activity chart
    if (d.department_activity && d.department_activity.length > 0) {
        const canvas = document.getElementById('deptActivityChart');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            if (activityChartInstance) activityChartInstance.destroy();
            activityChartInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: d.department_activity.map(da => da.department),
                    datasets: [
                        {
                            label: 'Active Faculty (Recent Pub < 6mo)',
                            data: d.department_activity.map(da => da.active_faculty),
                            backgroundColor: 'rgba(40, 167, 69, 0.75)',
                            borderRadius: 5
                        },
                        {
                            label: 'Inactive Faculty',
                            data: d.department_activity.map(da => da.inactive_faculty),
                            backgroundColor: 'rgba(220, 53, 69, 0.75)',
                            borderRadius: 5
                        }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { position: 'top' } },
                    scales: {
                        x: { stacked: true },
                        y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } }
                    }
                }
            });
        }
    }

    // Render monthly trend chart
    if (d.monthly_trend && d.monthly_trend.length > 0) {
        const trendCanvas = document.getElementById('deptTrendChart');
        if (trendCanvas) {
            const ctx = trendCanvas.getContext('2d');
            if (trendChartInstance) trendChartInstance.destroy();
            trendChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: d.monthly_trend.map(t => t.label),
                    datasets: [{
                        label: 'Publications Added',
                        data: d.monthly_trend.map(t => t.count),
                        borderColor: '#004080',
                        backgroundColor: 'rgba(0, 64, 128, 0.15)',
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: '#004080'
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, ticks: { stepSize: 1 } }
                    }
                }
            });
        }
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadAnalysis);
} else {
    loadAnalysis();
}
