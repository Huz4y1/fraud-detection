const BLUE = "#1f77b4";
const LIGHT_BLUE = "#9ecae1";
const RED = "#d62728";
const EMPTY = '<p class="empty">No data for these filters.</p>';

const state = { tab: "live", minutes: "15", decision: "", categories: new Set(), state: "", minAmount: "", maxAmount: "" };
let seq = 0;

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = n => Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 });
const money = n => "$" + Math.round(n ?? 0).toLocaleString();
const cents = n => "$" + Number(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n, digits = 2) => (n == null ? "-" : (n * 100).toFixed(digits) + "%");
const ratio = (a, b, digits = 1) => (b ? pct(a / b, digits) : "-");
const clock = iso => (iso ? iso.slice(11, 19) : "");
const month = iso => new Date(iso).toLocaleString(undefined, { month: "short", year: "numeric" });
const nice = text => String(text ?? "").replaceAll("_", " ");
const decision = value => (value === "flagged" ? '<span class="flagged">flagged</span>' : "approved");
const verdict = value => (value === 1 ? "fraud" : value === 0 ? "legit" : "");

function query() {
  const params = new URLSearchParams();
  if (state.minutes) params.set("minutes", state.minutes);
  if (state.decision) params.set("decision", state.decision);
  if (state.state) params.set("state", state.state);
  if (state.minAmount !== "") params.set("min_amount", state.minAmount);
  if (state.maxAmount !== "") params.set("max_amount", state.maxAmount);
  state.categories.forEach(category => params.append("category", category));
  return params.toString();
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function run(task) {
  try {
    await task();
    $("error").hidden = true;
  } catch (error) {
    $("error").textContent = `Could not load data: ${error.message}`;
    $("error").hidden = false;
  }
}

function kpis(el, items) {
  el.innerHTML = items
    .map(([label, value]) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`)
    .join("");
}

function table(el, rows, columns) {
  if (!rows.length) {
    el.innerHTML = EMPTY;
    return;
  }
  const cell = (column, row) => {
    const value = column.format ? column.format(row[column.key], row) : row[column.key];
    return `<td class="${column.num ? "num" : ""}">${column.html ? value : esc(value)}</td>`;
  };
  const head = columns.map(c => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map(row => `<tr>${columns.map(c => cell(c, row)).join("")}</tr>`).join("");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function bars(el, rows, label, value, { color = BLUE, format = num } = {}) {
  if (!rows.length) {
    el.innerHTML = EMPTY;
    return;
  }
  const max = Math.max(0, ...rows.map(r => r[value])) || 1;
  el.innerHTML = rows
    .map(r => {
      const name = nice(r[label]);
      const text = format(r[value]);
      return `<div class="hbar" title="${esc(name)}: ${esc(text)}"><span class="name">${esc(name)}</span><span class="track"><span class="fill" style="width:${(r[value] / max) * 100}%;background:${color}"></span></span><span class="num">${esc(text)}</span></div>`;
    })
    .join("");
}

function chart(el, rows, x, series, { line = false, format = num, label = String } = {}) {
  if (!rows.length) {
    el.innerHTML = EMPTY;
    return;
  }
  const W = 640, H = 240, L = 52, R = 8, T = 10, B = 26;
  const top = Math.max(0, ...rows.flatMap(r => series.map(s => r[s.key] ?? 0))) || 1;
  const unit = 10 ** Math.floor(Math.log10(top));
  const max = Math.ceil(top / unit) * unit;
  const step = (W - L - R) / rows.length;
  const y = v => H - B - (v / max) * (H - T - B);
  const cx = i => L + step * i + step / 2;
  const parts = [];
  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4;
    parts.push(`<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`);
    parts.push(`<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(format(v))}</text>`);
  }
  const every = Math.ceil(rows.length / 8);
  rows.forEach((r, i) => {
    if (i % every === 0) parts.push(`<text x="${cx(i)}" y="${H - 6}" text-anchor="middle">${esc(label(r[x]))}</text>`);
  });
  series.forEach((s, j) => {
    if (line) {
      const points = rows.map((r, i) => `${cx(i)},${y(r[s.key] ?? 0)}`).join(" ");
      parts.push(`<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${points}"/>`);
    }
    const width = (step * 0.8) / series.length;
    rows.forEach((r, i) => {
      const v = r[s.key];
      if (v == null) return;
      const tip = `<title>${esc(label(r[x]))} - ${esc(s.name)}: ${esc(format(v))}</title>`;
      if (line) {
        parts.push(`<rect x="${L + step * i}" y="${T}" width="${step}" height="${H - T - B}" fill="transparent">${tip}</rect>`);
      } else {
        const bx = L + step * i + step * 0.1 + j * width;
        parts.push(`<rect x="${bx}" y="${y(v)}" width="${Math.max(width - 1, 1)}" height="${H - B - y(v)}" fill="${s.color}">${tip}</rect>`);
      }
    });
  });
  const legend = series.length > 1
    ? `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`
    : "";
  el.innerHTML = `${legend}<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s => s.name).join(", "))}">${parts.join("")}</svg>`;
}

const views = {
  live: {
    url: () => `/dashboard/live?${query()}`,
    draw(d) {
      const k = d.kpis;
      const q = d.quality;
      $("live-status").textContent = `Stream: ${d.running ? "running" : "stopped"} | Model: v${d.model_version} | Threshold: ${d.threshold.toFixed(2)}`;
      kpis($("live-kpis"), [
        ["Transactions", num(k.transactions)],
        ["Flagged", num(k.flagged)],
        ["Flag rate", ratio(k.flagged, k.transactions, 2)],
        ["Volume", money(k.volume)],
        ["Flagged amount", money(k.flagged_volume)],
        ["Live precision", ratio(q.tp, q.tp + q.fp)],
        ["Live recall", ratio(q.tp, q.tp + q.fn)],
      ]);
      document.querySelectorAll(".bucket").forEach(el => (el.textContent = `(per ${d.bucket_seconds}s)`));
      chart($("live-in"), d.timeline, "bucket", [{ key: "incoming", name: "Transactions", color: BLUE }], { line: true, label: clock });
      chart($("live-out"), d.timeline, "bucket", [{ key: "flagged", name: "Flagged", color: RED }], { label: clock });
      table($("live-feed"), d.feed, [
        { key: "time", label: "Time", format: clock },
        { key: "customer", label: "Customer" },
        { key: "state", label: "State" },
        { key: "merchant", label: "Merchant" },
        { key: "category", label: "Category", format: nice },
        { key: "amount", label: "Amount", format: cents, num: true },
        { key: "fraud_score", label: "Score", format: v => v.toFixed(3), num: true },
        { key: "decision", label: "Decision", format: decision, html: true },
        { key: "verdict", label: "Feedback", format: verdict },
        { key: "model_version", label: "Model", format: v => `v${v}` },
      ]);
      const flagged = d.categories.filter(r => r.flagged > 0).sort((a, b) => b.flagged - a.flagged);
      bars($("live-categories"), flagged, "category", "flagged", { color: RED });
      table($("live-merchants"), d.merchants, [
        { key: "merchant", label: "Merchant" },
        { key: "category", label: "Category", format: nice },
        { key: "transactions", label: "Transactions", num: true },
        { key: "flagged", label: "Flagged", num: true },
      ]);
    },
  },
  review: {
    url: () => `/dashboard/review?${query()}`,
    draw(d) {
      $("review-count").textContent = `${num(d.backlog)} flagged transactions waiting for review`;
      table($("review-queue"), d.queue, [
        { key: "scored_at", label: "Time", format: clock },
        { key: "merchant", label: "Merchant" },
        { key: "category", label: "Category", format: nice },
        { key: "customer", label: "Customer", format: (v, r) => `${v}, ${r.city} ${r.state}` },
        { key: "amount", label: "Amount", format: cents, num: true },
        { key: "fraud_score", label: "Score", format: v => v.toFixed(3), num: true },
        { key: "hour", label: "Hour", num: true },
        { key: "distance_km", label: "Km from home", format: v => Math.round(v), num: true },
        { key: "txn_count_24h", label: "Txns in 24h", num: true },
        { key: "amount_to_avg", label: "x Usual spend", format: v => (v == null ? "first" : v.toFixed(1)), num: true },
        {
          key: "trans_num",
          label: "Label",
          html: true,
          format: v => `<button data-trans="${esc(v)}" data-fraud="true">Fraud</button> <button data-trans="${esc(v)}" data-fraud="false">Legit</button>`,
        },
      ]);
    },
  },
  history: {
    url: () => `/dashboard/history?${query()}`,
    draw(d) {
      const k = d.kpis;
      kpis($("history-kpis"), [
        ["Transactions", num(k.transactions)],
        ["Frauds", num(k.frauds)],
        ["Fraud rate", ratio(k.frauds, k.transactions, 2)],
        ["Fraud amount", money(k.fraud_volume)],
        ["Avg fraud amount", k.avg_fraud == null ? "-" : cents(k.avg_fraud)],
        ["Avg legit amount", k.avg_legit == null ? "-" : cents(k.avg_legit)],
      ]);
      chart($("history-hour"), d.hour, "hour", [{ key: "fraud_rate", name: "Fraud rate", color: BLUE }], { format: v => pct(v, 1) });
      bars($("history-band"), d.band, "band", "fraud_rate", { format: v => pct(v, 1) });
      chart($("history-month"), d.month, "month", [{ key: "frauds", name: "Frauds", color: BLUE }], { label: month });
      bars($("history-category"), [...d.category].sort((a, b) => b.fraud_rate - a.fraud_rate), "category", "fraud_rate", { format: v => pct(v, 2) });
      table($("history-state"), [...d.state].sort((a, b) => b.fraud_rate - a.fraud_rate), [
        { key: "state", label: "State" },
        { key: "transactions", label: "Transactions", format: num, num: true },
        { key: "frauds", label: "Frauds", format: num, num: true },
        { key: "fraud_rate", label: "Fraud rate", format: v => pct(v, 2), num: true },
        { key: "fraud_volume", label: "Fraud amount", format: money, num: true },
      ]);
      table($("history-jobs"), d.jobs, [
        { key: "job", label: "Occupation" },
        { key: "transactions", label: "Transactions", format: num, num: true },
        { key: "frauds", label: "Frauds", format: num, num: true },
      ]);
    },
  },
  model: {
    url: () => "/dashboard/model",
    draw(d) {
      const versions = d.versions;
      const champion = versions.filter(v => v.promoted).at(-1);
      const q = d.quality;
      kpis($("model-kpis"), [
        ["Production model", `v${champion.version}`],
        ["PR-AUC", champion.pr_auc.toFixed(4)],
        ["Threshold", champion.threshold.toFixed(3)],
        ["Live precision", ratio(q.tp, q.tp + q.fp)],
        ["Live recall", ratio(q.tp, q.tp + q.fn)],
      ]);
      $("model-confusion").textContent =
        `Caught fraud: ${num(q.tp)} | False alarms: ${num(q.fp)} | Missed fraud: ${num(q.fn)} | Correctly approved: ${num(q.tn)} | ` +
        `Labels: ${num(d.sources.analyst)} analyst, ${num(d.sources.chargeback)} chargeback, ${num(d.sources.settled)} settled`;
      chart(
        $("model-chart"),
        versions,
        "version",
        [
          { key: "pr_auc", name: "New model", color: BLUE },
          { key: "champion_pr_auc", name: "Production model it was compared to", color: LIGHT_BLUE },
        ],
        { format: v => v.toFixed(2), label: v => `v${v}` },
      );
      table($("model-versions"), [...versions].reverse(), [
        { key: "version", label: "Version", format: v => `v${v}` },
        { key: "created_at", label: "Trained", format: v => v.slice(0, 16).replace("T", " ") },
        { key: "promoted", label: "Promoted", format: v => (v ? "yes" : "no") },
        { key: "pr_auc", label: "PR-AUC", format: v => v.toFixed(4), num: true },
        { key: "champion_pr_auc", label: "Compared to", format: v => (v == null ? "-" : v.toFixed(4)), num: true },
        { key: "precision", label: "Precision", format: v => pct(v, 1), num: true },
        { key: "recall", label: "Recall", format: v => pct(v, 1), num: true },
        { key: "threshold", label: "Threshold", format: v => v.toFixed(3), num: true },
        { key: "feedback_rows", label: "Feedback rows", format: num, num: true },
        { key: "train_rows", label: "Train rows", format: num, num: true },
      ]);
    },
  },
};

function refresh() {
  const id = ++seq;
  const view = views[state.tab];
  return run(async () => {
    const data = await api(view.url());
    if (id === seq) view.draw(data);
  });
}

document.querySelectorAll(".buttons[data-filter]").forEach(group => {
  group.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    group.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === button));
    state[group.dataset.filter] = button.dataset.value;
    refresh();
  });
});

$("categories").addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  const category = button.dataset.value;
  if (state.categories.has(category)) state.categories.delete(category);
  else state.categories.add(category);
  button.classList.toggle("active", state.categories.has(category));
  button.setAttribute("aria-pressed", String(state.categories.has(category)));
  refresh();
});

$("state").addEventListener("change", event => {
  state.state = event.target.value;
  refresh();
});

$("min-amount").addEventListener("change", event => {
  state.minAmount = event.target.value;
  refresh();
});

$("max-amount").addEventListener("change", event => {
  state.maxAmount = event.target.value;
  refresh();
});

$("reset").addEventListener("click", () => {
  Object.assign(state, { minutes: "15", decision: "", state: "", minAmount: "", maxAmount: "" });
  state.categories.clear();
  document.querySelectorAll(".buttons[data-filter] button").forEach(b => {
    b.classList.toggle("active", b.dataset.value === state[b.parentElement.dataset.filter]);
  });
  document.querySelectorAll("#categories button").forEach(b => {
    b.classList.remove("active");
    b.setAttribute("aria-pressed", "false");
  });
  $("state").value = "";
  $("min-amount").value = "";
  $("max-amount").value = "";
  refresh();
});

document.querySelector(".tabs").addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  state.tab = button.dataset.tab;
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b === button));
  document.querySelectorAll(".tab").forEach(section => (section.hidden = section.id !== state.tab));
  refresh();
});

$("review-refresh").addEventListener("click", refresh);

$("review-queue").addEventListener("click", event => {
  const button = event.target.closest("button[data-trans]");
  if (!button) return;
  button.parentElement.querySelectorAll("button").forEach(b => (b.disabled = true));
  run(async () => {
    await api("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trans_num: button.dataset.trans, is_fraud: button.dataset.fraud === "true" }),
    });
    await refresh();
  });
});

$("retrain").addEventListener("click", () => {
  const button = $("retrain");
  button.disabled = true;
  $("retrain-result").textContent = "Training, this takes about 30 seconds...";
  run(async () => {
    try {
      const result = await api("/dashboard/retrain", { method: "POST" });
      $("retrain-result").textContent = result.message;
    } catch (error) {
      $("retrain-result").textContent = "";
      throw error;
    } finally {
      button.disabled = false;
    }
    if (state.tab === "model") await refresh();
  });
});

run(async () => {
  const { categories, states } = await api("/dashboard/options");
  $("categories").innerHTML = categories
    .map(c => `<button data-value="${esc(c)}" aria-pressed="false">${esc(nice(c))}</button>`)
    .join("");
  $("state").insertAdjacentHTML("beforeend", states.map(s => `<option>${esc(s)}</option>`).join(""));
});

refresh();
setInterval(() => {
  if (state.tab === "live" && !document.hidden) refresh();
}, 2000);
