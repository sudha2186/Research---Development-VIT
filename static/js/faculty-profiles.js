let allPubs = [];
let pubChartInstance = null;

// Check fetch status
async function checkStatus() {
    const banner = document.getElementById('statusBanner');
    if (!banner) return;
    const res = await fetch('/api/status');
    const s = await res.json();
    if (s.fetched < s.total_faculty) {
        banner.style.display = 'block';
        const fCount = document.getElementById('fetchedCount');
        const tCount = document.getElementById('totalCount');
        if (fCount) fCount.textContent = s.fetched;
        if (tCount) tCount.textContent = s.total_faculty;
    }
}

async function loadAllFaculty() {
    const tbody = document.getElementById('facultyTableBody');
    if (!tbody) return;
    const res = await fetch('/api/faculty/all');
    const faculty = await res.json();
    tbody.innerHTML = '';
    faculty.forEach((f, i) => {
        tbody.innerHTML += `<tr>
            <td>${i+1}</td>
            <td><strong>${f['Name']}</strong></td>
            <td>${f['Department']}</td>
            <td>${f['Scholar ID'] !== 'Not Found' ? '🟢 Has Scholar ID' : '🔴 No Scholar ID'}</td>
        </tr>`;
    });
    const spinner = document.getElementById('tableSpinner');
    const table = document.getElementById('facultyTable');
    if (spinner) spinner.style.display = 'none';
    if (table) table.style.display = 'table';
}

let searchTimeout = null;
function searchFaculty() {
    clearTimeout(searchTimeout);
    const input = document.getElementById('searchInput');
    if (!input) return;
    const q = input.value.trim();
    if (!q) {
        document.getElementById('matchList').innerHTML = '';
        document.getElementById('profileSection').style.display = 'none';
        document.getElementById('fullTable').style.display = 'block';
        return;
    }
    searchTimeout = setTimeout(async () => {
        const res = await fetch(`/api/faculty/search?q=${encodeURIComponent(q)}`);
        const matches = await res.json();
        const matchList = document.getElementById('matchList');
        if (matches.length === 0) {
            matchList.innerHTML = '<div class="alert alert-warning">No faculty found.</div>';
            document.getElementById('profileSection').style.display = 'none';
            document.getElementById('fullTable').style.display = 'none';
            return;
        }
        document.getElementById('fullTable').style.display = 'none';
        matchList.innerHTML = `<select onchange="loadProfile(this.value)" style="padding:10px 16px; border:2px solid #004080; border-radius:8px; font-size:1rem; min-width:300px;">
            <option value="">-- Select a faculty --</option>
            ${matches.map(f => `<option value="${f['Scholar ID']}">${f['Name']} (${f['Department']})</option>`).join('')}
        </select>`;
    }, 300);
}

async function loadProfile(scholarId) {
    if (!scholarId || scholarId === 'Not Found') {
        document.getElementById('profileSection').style.display = 'none';
        return;
    }

    document.getElementById('profileSection').style.display = 'block';
    document.getElementById('pubSection').style.display = 'none';
    document.getElementById('chartSection').style.display = 'none';
    document.getElementById('noScholarMsg').style.display = 'none';
    document.getElementById('pubTableBody').innerHTML = '';

    const res = await fetch(`/api/faculty/profile?id=${encodeURIComponent(scholarId)}`);
    const d = await res.json();

    document.getElementById('profileImg').src = d.image_url;
    document.getElementById('profileName').textContent = '👤 ' + d['Name'];
    document.getElementById('profileDept').textContent = '🏢 Department: ' + d['Department'];
    document.getElementById('profileAffiliation').textContent = d.affiliation || '';
    document.getElementById('profileInterests').textContent = d.interests?.length ? '🔬 ' + d.interests.join(', ') : '';
    document.getElementById('profileScholarLink').href = d.scholar_url;

    document.getElementById('metricCitations').textContent = d.cited_by ?? '—';
    document.getElementById('metricCitations5y').textContent = d.cited_by_5y ?? '—';
    document.getElementById('metricHindex').textContent = d.hindex ?? '—';
    document.getElementById('metricHindex5y').textContent = d.hindex5y ?? '—';
    document.getElementById('metricI10').textContent = d.i10index ?? '—';
    document.getElementById('metricI105y').textContent = d.i10index5y ?? '—';

    if (d.data_source === 'scholar' && d.publications?.length > 0) {
        allPubs = d.publications;
        renderPublications(allPubs);
        renderChart(allPubs);
        document.getElementById('pubSection').style.display = 'block';
        document.getElementById('chartSection').style.display = 'block';
        document.getElementById('pubCount').textContent = `(${allPubs.length} total)`;
    } else {
        document.getElementById('noScholarMsg').style.display = 'block';
    }
}

function renderChart(pubs) {
    const yearCounts = {};
    pubs.forEach(p => {
        const y = p.year;
        if (y && y !== 'N/A') yearCounts[y] = (yearCounts[y] || 0) + 1;
    });
    const sorted = Object.keys(yearCounts).sort();
    const canvas = document.getElementById('pubChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (pubChartInstance) pubChartInstance.destroy();
    pubChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sorted,
            datasets: [{
                label: 'Publications',
                data: sorted.map(y => yearCounts[y]),
                backgroundColor: 'rgba(0, 64, 128, 0.75)',
                borderRadius: 5
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { stepSize: 1 }, title: { display: true, text: 'Number of Papers' } },
                x: { title: { display: true, text: 'Year' } }
            }
        }
    });
}

function renderPublications(pubs) {
    // Populate year filter
    const years = [...new Set(pubs.map(p => p.year).filter(y => y && y !== 'N/A'))].sort().reverse();
    const yearSel = document.getElementById('yearFilter');
    if (yearSel) {
        yearSel.innerHTML = '<option value="">All Years</option>' + years.map(y => `<option value="${y}">${y}</option>`).join('');
    }
    buildTable(pubs);
}

function buildTable(pubs) {
    const tbody = document.getElementById('pubTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (pubs.length === 0) {
        document.getElementById('noPubsMsg').style.display = 'block';
        return;
    }
    document.getElementById('noPubsMsg').style.display = 'none';
    pubs.forEach((p, i) => {
        const title = p.url
            ? `<a href="${p.url}" target="_blank" style="color:#004080; text-decoration:none; font-weight:500;">${p.title}</a>`
            : `<span style="font-weight:500;">${p.title}</span>`;
        const sourceBadge = p.source === 'manual'
            ? `<span style="background:#fff3cd; color:#856404; padding:2px 8px; border-radius:6px; font-size:0.75rem; font-weight:600;">✍️ Manually added</span>`
            : `<span style="background:#d1ecf1; color:#0c5460; padding:2px 8px; border-radius:6px; font-size:0.75rem; font-weight:600;">🎓 Scholar</span>`;
        tbody.innerHTML += `<tr>
            <td style="color:#888; min-width:30px;">${i+1}</td>
            <td>${title}</td>
            <td style="white-space:nowrap;">${p.year}</td>
            <td style="color:#555; font-size:0.85rem;">${p.journal || '—'}</td>
            <td style="text-align:center;">${p.citations || 0}</td>
            <td>${sourceBadge}</td>
        </tr>`;
    });
}

function filterPubs() {
    const q = document.getElementById('pubSearch').value.toLowerCase();
    const yr = document.getElementById('yearFilter').value;
    const filtered = allPubs.filter(p =>
        (!q || p.title.toLowerCase().includes(q) || (p.journal||'').toLowerCase().includes(q)) &&
        (!yr || p.year == yr)
    );
    buildTable(filtered);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        checkStatus();
        loadAllFaculty();
    });
} else {
    checkStatus();
    loadAllFaculty();
}
