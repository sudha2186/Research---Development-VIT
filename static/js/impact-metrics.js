async function loadMetrics() {
    const res = await fetch('/api/department/metrics');
    const data = await res.json();

    const labels = data.map(d => d['Department']);
    const pubs = data.map(d => d['Publications']);
    const citations = data.map(d => d['Citations']);
    const crossref = data.map(d => d['Crossref_Citations']);
    const hindex = data.map(d => d['h_index']);

    const canvas = document.getElementById('metricsChart');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Publications',
                        data: pubs,
                        backgroundColor: 'rgba(0, 123, 255, 0.75)',
                        borderRadius: 5
                    },
                    {
                        label: 'Citations',
                        data: citations,
                        backgroundColor: 'rgba(156, 39, 176, 0.75)',
                        borderRadius: 5
                    },
                    {
                        label: 'Crossref Citations',
                        data: crossref,
                        backgroundColor: 'rgba(76, 175, 80, 0.75)',
                        borderRadius: 5
                    },
                    {
                        label: 'h-index',
                        data: hindex,
                        type: 'line',
                        borderColor: 'orange',
                        backgroundColor: 'rgba(255,165,0,0.15)',
                        pointBackgroundColor: 'orange',
                        tension: 0.3,
                        yAxisID: 'y2'
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { position: 'top' },
                    title: { display: true, text: 'Research Metrics by Department', font: { size: 16 } }
                },
                scales: {
                    x: { ticks: { maxRotation: 35 } },
                    y: { title: { display: true, text: 'Counts (Publications, Citations, Crossref)' } },
                    y2: { position: 'right', title: { display: true, text: 'h-index' }, grid: { drawOnChartArea: false } }
                }
            }
        });
    }

    // Table
    const tbody = document.getElementById('metricsBody');
    if (tbody) {
        tbody.innerHTML = '';
        data.forEach(d => {
            tbody.innerHTML += `<tr>
                <td><strong>${d['Department']}</strong></td>
                <td>${d['Publications']}</td>
                <td>${d['Citations']}</td>
                <td>${d['Crossref_Citations']}</td>
                <td>${d['h_index']}</td>
            </tr>`;
        });
    }

    const spinner = document.getElementById('tableSpinner');
    const table = document.getElementById('metricsTable');
    if (spinner) spinner.style.display = 'none';
    if (table) table.style.display = 'table';
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadMetrics);
} else {
    loadMetrics();
}
