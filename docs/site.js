// Shared helpers for the msvoters site. No build step; Chart.js comes from a CDN.
"use strict";

const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const fmtSigned = (n) => (n == null ? "–" : (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(Math.round(n)).toLocaleString("en-US"));
const pct = (a, b) => (b ? (100 * a / b).toFixed(1) + "%" : "–");
const signClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });

async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function renderChrome(active, latest) {
  const pages = [["index.html", "This week"], ["counties.html", "Counties"], ["trends.html", "Trends"]];
  document.body.insertAdjacentHTML("afterbegin",
    `<header class="site"><div class="in"><a class="brand" href="index.html">Mississippi voter rolls</a><nav>` +
    pages.map(([h, t]) => `<a href="${h}"${h === active ? ' class="on"' : ""}>${t}</a>`).join("") +
    `</nav></div></header>`);
  document.body.insertAdjacentHTML("beforeend",
    `<footer>Data: Mississippi Secretary of State weekly voter-file drops` +
    (latest ? ` · snapshot ${fmtDate(latest.snapshot)}` : "") +
    `. Party is <em>derived</em> from the most recent primary a voter took part in; Mississippi has no party registration.</footer>`);
}

function showError(e) {
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<div class="callout">Could not load data: ${esc(e.message)}</div>`);
  console.error(e);
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const palette = () => [css("--c1"), css("--c2"), css("--c3"), css("--c4"), css("--c5")];

function sparkline(canvas, values, color) {
  new Chart(canvas, {
    type: "line",
    data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color || css("--c1"),
      borderWidth: 1.5, pointRadius: 0, tension: .25, fill: false }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } } },
  });
}

function lineChart(canvas, labels, datasets, opts = {}) {
  const pal = palette();
  return new Chart(canvas, {
    type: opts.type || "line",
    data: { labels, datasets: datasets.map((d, i) => ({ borderColor: pal[i % pal.length],
      backgroundColor: pal[i % pal.length], borderWidth: 2, pointRadius: labels.length > 40 ? 0 : 2,
      tension: .2, ...d })) },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: datasets.length > 1, labels: { color: css("--text") } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label ? c.dataset.label + ": " : ""}${fmt(c.parsed.y)}` } } },
      scales: { x: { stacked: !!opts.stacked, ticks: { color: css("--muted"), maxTicksLimit: 8 }, grid: { display: false } },
        y: { stacked: !!opts.stacked, beginAtZero: !!opts.zero, ticks: { color: css("--muted"), callback: (v) => fmt(v) },
          grid: { color: css("--border") } } } },
  });
}

// Make a table's headers sortable. `rows` is re-rendered by render(sortedRows).
function makeSortable(table, columns, rows, render, initial) {
  let key = initial.key, dir = initial.dir;
  const head = table.querySelector("thead tr");
  head.innerHTML = columns.map((c) => `<th class="sortable" data-k="${c.key}">${c.label}</th>`).join("");
  function draw() {
    const col = columns.find((c) => c.key === key);
    const sorted = [...rows()].sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
    head.querySelectorAll("th").forEach((th) =>
      th.setAttribute("aria-sort", th.dataset.k === key ? (dir > 0 ? "ascending" : "descending") : "none"));
    render(sorted);
  }
  head.addEventListener("click", (e) => {
    const th = e.target.closest("th"); if (!th) return;
    const k = th.dataset.k;
    if (k === key) dir = -dir; else { key = k; dir = k === columns[0].key ? 1 : -1; }
    draw();
  });
  draw();
  return draw;
}
