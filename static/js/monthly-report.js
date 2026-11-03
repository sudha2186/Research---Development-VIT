const months = ["January","February","March","April","May","June","July","August","September","October","November","December"];

function initSelectors() {
    const now = new Date();
    const mSel = document.getElementById('monthSelect');
    if (!mSel) return;
    mSel.innerHTML = '';
    months.forEach((m, i) => {
        const opt = document.createElement('option');
        opt.value = i + 1; opt.textContent = m;
        if (i + 1 === now.getMonth() + 1) opt.selected = true;
        mSel.appendChild(opt);
    });
    const ySel = document.getElementById('yearSelect');
    if (!ySel) return;
    ySel.innerHTML = '';
    for (let y = now.getFullYear(); y >= now.getFullYear() - 5; y--) {
        const opt = document.createElement('option');
        opt.value = y; opt.textContent = y;
        ySel.appendChild(opt);
    }
}

async function loadReport() {
    const mSel = document.getElementById('monthSelect');
    const ySel = document.getElementById('yearSelect');
    if (!mSel || !ySel) return;

    const month = mSel.value;
    const year = ySel.value;
    const dept = (typeof lockedDept !== 'undefined' && lockedDept) || document.getElementById('deptSelect')?.value || '';

    const container = document.getElementById('reportContent');
    if (container) {
        container.innerHTML = '<div class="spinner">Loading...</div>';
    }

    const res = await fetch(`/api/reports/monthly?year=${year}&month=${month}&dept=${encodeURIComponent(dept)}`);
    if (!res.ok) {
        if (container) {
            container.innerHTML = '<div class="alert alert-error" style="margin-top:1rem;">Access denied: You do not have permission to view monthly reports for this department.</div>';
        }
        return;
    }
    const d = await res.json();

    let html = `<h2>${months[d.month-1]} ${d.year} — ${d.department}</h2>
    <p style="color:#555; margin-bottom:1rem;"><strong>${d.count}</strong> publication(s) added this month.</p>`;

    if (d.count > 0) {
        html += `<table><thead><tr><th>#</th><th>Faculty</th><th>Department</th><th>Title</th><th>Year</th><th>Source</th></tr></thead><tbody>`;
        d.publications.forEach((p, i) => {
            const src = p.source === 'manual' ? '✍️ Manual' : '🎓 Scholar';
            html += `<tr><td>${i+1}</td><td>${p.faculty}</td><td>${p.department}</td><td>${p.title}</td><td>${p.pub_year}</td><td>${src}</td></tr>`;
        });
        html += `</tbody></table>`;
    } else {
        html += `<div class="alert alert-info">No publications recorded for this period.</div>`;
    }

    if (container) {
        container.innerHTML = html;
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initSelectors();
        loadReport();
    });
} else {
    initSelectors();
    loadReport();
}
