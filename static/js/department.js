async function loadDepartment() {
    const dept = document.getElementById('deptSelect').value;
    document.getElementById('deptContent').innerHTML = '<div class="spinner">Loading...</div>';

    const res = await fetch(`/api/department/faculty?dept=${encodeURIComponent(dept)}`);
    const faculty = await res.json();

    // Aggregate dept stats
    let totalCitations = 0, totalPubs = 0, hasScholar = 0;
    faculty.forEach(f => {
        if (f.data_source === 'scholar') {
            totalCitations += (f.cited_by || 0);
            totalPubs += (f.pub_count || 0);
            hasScholar++;
        }
    });

    let html = `<h2 style="margin-bottom:0.5rem;">Department: ${dept}</h2>
    <p style="color:#555; margin-bottom:1rem;">Total Faculty: <strong>${faculty.length}</strong></p>`;

    if (hasScholar > 0) {
        html += `<div class="metrics-row" style="margin-bottom:1.5rem;">
            <div class="metric-box"><div class="metric-value">${totalPubs}</div><div class="metric-label">📄 Total Publications</div></div>
            <div class="metric-box"><div class="metric-value">${totalCitations}</div><div class="metric-label">📈 Total Citations</div></div>
            <div class="metric-box"><div class="metric-value">${hasScholar}/${faculty.length}</div><div class="metric-label">✅ Scholar Data Ready</div></div>
        </div>`;
    }

    html += `<table>
        <thead>
            <tr><th>#</th><th>Name</th><th>Publications</th><th>Citations</th><th>h-index</th><th>i10-index</th><th>Profile</th></tr>
        </thead><tbody>`;

    faculty.forEach((f, i) => {
        const sid = f['Scholar ID'];
        const profileLink = sid && sid !== 'Not Found'
            ? `<a href="/faculty-profiles?search=${encodeURIComponent(f['Name'])}" class="btn" style="padding:5px 12px; font-size:0.8rem;">View Profile</a>`
            : '<span style="color:#aaa">No ID</span>';
        const pubCount = f.pub_count != null ? f.pub_count : '—';
        const citations = f.cited_by != null ? f.cited_by : '—';
        const hindex = f.hindex != null ? f.hindex : '—';
        const i10 = f.i10index != null ? f.i10index : '—';
        const statusDot = f.data_source === 'scholar' ? '🟢' : '🟡';
        html += `<tr>
            <td>${i+1}</td>
            <td><strong>${statusDot} ${f['Name']}</strong></td>
            <td>${pubCount}</td>
            <td>${citations}</td>
            <td>${hindex}</td>
            <td>${i10}</td>
            <td>${profileLink}</td>
        </tr>`;
    });

    html += `</tbody></table>
    <p style="font-size:0.8rem; color:#888; margin-top:0.8rem;">🟢 = Full scholar data available &nbsp; 🟡 = Fetch pending (run fetch_data.py)</p>`;
    document.getElementById('deptContent').innerHTML = html;
}

window.onload = loadDepartment;
