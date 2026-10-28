/* TenderCheck page. Every string from a report, a model or a user goes in through textContent (no innerHTML).
   Interaction and motion rationale: docs/design-direction.md. */
(() => {
  "use strict";

  const STATUS = {
    "error": "Error", "missing": "Missing", "needs-confirmation": "Needs check", "conditional": "Conditional", "met": "Met",
  };
  const ORDER = ["error", "missing", "needs-confirmation", "conditional", "met"];
  const COND = { triggered: "applies to you", unknown: "may apply", not_triggered: "does not apply" };
  const KIND = { deadline: "deadline", document: "document", attribute: "attribute", validity: "validity",
                 file_rule: "file rule", conditional: "conditional" };
  const STEP = {
    U:  ["model", "Read the bidder's note on its own", "it can list what to check; it cannot touch a status"],
    E1: ["model", "Extracted the requirements", "one record per obligation, each with an exact quote"],
    E2: ["model", "Repaired its own quotes", "after code rejected them"],
    E3: ["model", "Re-read unclear lines", "code asked for a second look"],
    E4: ["model", "Swept for missed lines", "lines with requirement words that nothing cited"],
    M:  ["model", "Matched the bidder's files", "and read facts written in the file notes"],
    C:  ["model", "Judged possible conflicts", "code proposed the pairs; quotes must prove a difference"],
    RULES: ["code", "Decided every status", "rules over verified quotes and file checks"],
    DONE:  ["code", "Checked the invariants", "no status without a verified quote; no secrets in the output"],
  };
  const STAGES = ["U", "E1", "E2", "E3", "E4", "M", "C", "RULES", "DONE"];
  const SMALL = matchMedia("(max-width: 672px)");
  const CALM = matchMedia("(prefers-reduced-motion: reduce)");

  const $ = (id) => document.getElementById(id);
  const S = { samples: [], data: null, report: null, filter: null, docs: [], index: {}, cited: new Set(), injected: new Set(),
              live: null, busy: false, lastFocus: null };

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const clear = (el) => { el.replaceChildren(); return el; };
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

  /* status icon: shape carries the meaning as well as colour */
  function icon(status) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    const add = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); svg.append(e); };
    const stroke = { fill: "none", stroke: "currentColor", "stroke-width": "1.6" };
    if (status === "error") { add("circle", { cx: 8, cy: 8, r: 7, fill: "currentColor" }); add("path", { d: "M5.5 5.5l5 5M10.5 5.5l-5 5", stroke: "var(--bg)", "stroke-width": "1.6" }); }
    else if (status === "missing") { add("circle", { cx: 8, cy: 8, r: 6.2, ...stroke }); add("path", { d: "M5 8h6", ...stroke }); }
    else if (status === "needs-confirmation") { add("path", { d: "M8 1.8l6.6 12H1.4z", ...stroke }); add("path", { d: "M8 6v4M8 11.4v1", ...stroke }); }
    else if (status === "conditional") { add("path", { d: "M8 1.6l6.4 6.4L8 14.4 1.6 8z", ...stroke }); }
    else { add("circle", { cx: 8, cy: 8, r: 7, fill: "currentColor" }); add("path", { d: "M4.8 8.2l2.2 2.2 4.2-4.6", stroke: "var(--bg)", "stroke-width": "1.6", fill: "none" }); }
    return svg;
  }
  const statusTag = (status, extra) => h("span", { class: `st ${status}` }, icon(status),
    h("span", null, STATUS[status] || status, extra ? h("small", { class: "cond" }, extra) : null));

  async function getJSON(url, opts) {
    const r = await fetch(url, Object.assign({ cache: "no-cache" }, opts || {}));
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.error || r.statusText), { status: r.status });
    return body;
  }

  /* ── cases ─────────────────────────────────────────── */
  function renderCasePicker() {
    const sel = clear($("case"));
    const groups = {};
    for (const s of S.samples) (groups[s.package] = groups[s.package] || []).push(s);
    for (const [pkg, items] of Object.entries(groups)) {
      sel.append(h("optgroup", { label: `Package ${pkg} · ${items[0].lang === "ko" ? "Korean tender" : "English tender"}` },
        items.map((s) => h("option", { value: s.id }, `${s.id}: ${s.title.split("·").pop().trim()}`))));
    }
  }
  $("case").addEventListener("change", (e) => select(e.target.value));

  async function select(id) {
    if (location.hash.slice(1) !== id) history.replaceState(null, "", `#${id}`);
    $("case").value = id;
    const data = await getJSON(`data/${encodeURIComponent(id)}.json`);
    S.data = data;
    S.filter = null;
    $("case-title").textContent = data.title;
    $("case-blurb").textContent = data.blurb;
    setDocs(data.sources);
    $("bidder").textContent = data.manifest_text || "";
    $("bidder-note").hidden = !data.user_message;
    $("bidder-note").textContent = data.user_message ? `Bidder's note, read as data only: “${data.user_message}”` : "";
    render(data.report, { mode: "replay", models: data.run.models, calls: data.run.call_log, cost: data.run.cost_usd });
    updateRerun();
  }

  /* ── evidence ──────────────────────────────────────── */
  function setDocs(docs) {
    S.docs = docs;
    S.index = {};
    docs.forEach((d, i) => { for (const ln of d.lines) S.index[ln.seg_id] = { doc: i, line: ln }; });
    showDoc(0);
  }

  function renderTabs(active) {
    const tabs = clear($("doc-tabs"));
    S.docs.forEach((d, i) => {
      const flagged = [...S.injected].some((s) => s.startsWith(`${d.doc_id}-`));
      tabs.append(h("button", { type: "button", role: "tab", class: "doc-tab", "aria-selected": String(i === active),
        onclick: () => showDoc(i) }, d.title, h("small", null, d.doc_id),
        flagged ? h("span", { class: "dot", title: "Contains an instruction aimed at AI (ignored)" }) : null));
    });
  }

  function showDoc(i, seg, quote, ref) {
    renderTabs(i);
    const d = S.docs[i];
    const view = clear($("doc-view"));
    let target = null;
    for (const ln of d.lines) {
      const t = h("span", { class: "t" });
      const at = seg === ln.seg_id && quote ? ln.text.indexOf(quote) : -1;
      if (at >= 0) t.append(ln.text.slice(0, at), h("mark", null, quote), ln.text.slice(at + quote.length));
      else t.textContent = ln.text;
      const cls = ["line", S.cited.has(ln.seg_id) && "cited", S.injected.has(ln.seg_id) && "injected", seg === ln.seg_id && "target"];
      const row = h("div", { class: cls.filter(Boolean).join(" "), title: ln.location || null },
        h("span", { class: "n", "data-ref": seg === ln.seg_id && ref ? ref : null }, String(ln.n)), t);
      if (seg === ln.seg_id) target = row;
      view.append(row);
    }
    if (target) {
      const top = target.offsetTop - view.clientHeight * 0.3;
      view.scrollTo({ top: Math.max(0, top), behavior: CALM.matches ? "auto" : "smooth" });
    } else view.scrollTop = 0;
  }

  function findSeg(quote, hint) {
    if (hint && S.index[hint]) return hint;
    const q = norm(quote);
    if (!q) return null;
    for (const [seg, v] of Object.entries(S.index)) if (norm(v.line.text).includes(q)) return seg;
    const head = q.slice(0, 40);
    for (const [seg, v] of Object.entries(S.index)) if (norm(v.line.text).includes(head)) return seg;
    return null;
  }

  function openLine(quote, segHint, from, ref) {
    const seg = findSeg(quote, segHint);
    if (!seg) return;
    const { doc, line } = S.index[seg];
    showDoc(doc, seg, line.text.includes(quote) ? quote : null, ref);
    if (SMALL.matches) openSheet(from);
  }

  function openSheet(from) {
    S.lastFocus = from || document.activeElement;
    $("evidence").classList.add("open");
    $("scrim").hidden = false;
    $("ev-close").focus();
  }
  function closeSheet() {
    $("evidence").classList.remove("open");
    $("scrim").hidden = true;
    if (S.lastFocus && S.lastFocus.focus) S.lastFocus.focus();
  }
  $("ev-close").addEventListener("click", closeSheet);
  $("scrim").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("evidence").classList.contains("open")) closeSheet(); });

  /* ── findings ──────────────────────────────────────── */
  function render(report, run) {
    S.report = report;
    const x = report.x_meta || {};
    const rows = Object.fromEntries((x.rows || []).map((r) => [r.id, r]));
    const mode = $("mode");
    mode.textContent = run.mode === "live" ? "Live" : "Replay";
    mode.className = `mode${run.mode === "live" ? " live" : ""}`;
    $("model").textContent = (run.models && run.models.length ? run.models : (x.model || {}).resolved || ["n/a"]).join(", ");
    $("as-of").textContent = (x.as_of || "n/a").replace("T", " ").replace(/:00([+-]\d\d:\d\d)$/, " $1");
    $("disclaimer").textContent = report.disclaimer || "";
    $("banner").hidden = !x.banner;
    $("banner").textContent = x.banner || "";
    $("summary").textContent = [report.basis, x.summary_line].filter(Boolean).join(" ");

    S.cited = new Set(Object.values(rows).map((r) => r.seg_id).filter(Boolean));
    S.injected = new Set((report.untrusted_instructions || []).map((u) => findSeg(u.quote)).filter(Boolean));
    showDoc(0);

    renderTally(report.status_counts || {});
    const act = clear($("act"));
    for (const t of x.top_actions || []) act.append(h("li", null, t));
    $("act-sec").hidden = !(x.top_actions || []).length;

    const byId = Object.fromEntries(report.requirements.map((r) => [r.id, r]));
    const order = (x.display_order || []).filter((id) => byId[id]);
    for (const r of report.requirements) if (!order.includes(r.id)) order.push(r.id);
    const list = clear($("rows"));
    for (const id of order) list.append(row(byId[id], rows[id] || {}));
    $("rows-empty").hidden = true;

    renderConflicts(report.conflicts || []);
    renderQuestions(x.agency_questions || [], report.bidder_questions || []);
    const nis = clear($("nis"));
    for (const t of report.not_in_source || []) nis.append(h("li", null, t));
    $("nis-sec").hidden = !(report.not_in_source || []).length;
    const pre = clear($("pre"));
    for (const t of x.pre_submission_checklist || []) pre.append(h("li", null, t.replace(/^\[ \]\s*/, "")));
    renderFlags(report, x.flags || {});
    renderLog(x, run);
  }

  function renderTally(counts) {
    const tally = $("tally");
    for (const b of tally.querySelectorAll("button")) b.remove();
    const all = Object.values(counts).reduce((a, b) => a + b, 0);
    const mk = (key, label, n, st) => h("button", { type: "button", role: "tab", "aria-selected": String(S.filter === key),
      "data-key": key, onclick: (e) => setFilter(key, e.currentTarget) }, st ? icon(st) : null, label, h("b", null, String(n)));
    tally.append(mk(null, "All", all, null));
    for (const k of ORDER) {
      const b = mk(k, STATUS[k], counts[k] || 0, k);
      b.classList.add(`st`, k);
      tally.append(b);
    }
    moveInk(tally.querySelector('button[aria-selected="true"]'));
  }
  function moveInk(btn) {
    const ink = $("tally-ink");
    if (!btn) { ink.style.width = "0"; return; }
    ink.style.width = `${btn.offsetWidth}px`;
    ink.style.transform = `translateX(${btn.offsetLeft}px)`;
  }
  function setFilter(key, btn) {
    S.filter = S.filter === key || key === null ? null : key;
    for (const b of $("tally").querySelectorAll("button")) b.setAttribute("aria-selected", String((b.dataset.key || null) === S.filter || (!b.dataset.key && S.filter === null)));
    moveInk($("tally").querySelector('button[aria-selected="true"]') || btn);
    let shown = 0;
    for (const li of $("rows").children) { li.hidden = !!S.filter && li.dataset.status !== S.filter; shown += li.hidden ? 0 : 1; }
    $("rows-empty").hidden = shown > 0;
  }
  addEventListener("resize", () => moveInk($("tally").querySelector('button[aria-selected="true"]')));
  document.fonts.ready.then(() => moveInk($("tally").querySelector('button[aria-selected="true"]')));

  function row(r, meta) {
    const cond = r.condition_state ? COND[r.condition_state] : "";
    const checks = (meta.checks || []).map((c) => h("span", { class: `chk ${c.result}`, title: c.detail || null }, `${c.id} ${c.result}`));
    const p = meta.provenance;
    const facts = h("dl", { class: "facts" },
      r.evidence_files && r.evidence_files.length ? [h("dt", null, "Files"), h("dd", { class: "mono" }, r.evidence_files.join(", "))] : null,
      r.depends_on ? [h("dt", null, "Depends on"), h("dd", { class: "mono" }, r.depends_on)] : null,
      r.depends_on_conflict ? [h("dt", null, "Hinges on"), h("dd", null, `conflict ${r.depends_on_conflict}`)] : null,
      checks.length ? [h("dt", null, "Quote checks"), h("dd", null, checks)] : null,
      p ? [h("dt", null, "Who did what"), h("dd", null, `extracted by ${p.extracted_by} · quote verified by ${p.verified_by} · files matched by ${p.matched_by} · status by ${p.status_by}${meta.rule ? ` (rule ${meta.rule})` : ""}`)] : null);
    const li = h("li", { class: "row", "data-status": r.status, tabindex: "0", "aria-current": "false" },
      statusTag(r.status, cond),
      h("div", { class: "row-title" }, h("span", { class: "rid mono" }, r.id), meta.requirement || r.quote, h("span", { class: "kind" }, KIND[r.kind] || r.kind)),
      h("button", { type: "button", class: "loc", title: "Open this line in the tender", onclick: (e) => { e.stopPropagation(); pick(li, r, meta, e.currentTarget); } },
        `${r.location || "location not stated"} ↗`),
      r.reason ? h("div", { class: "row-sub" }, r.reason) : null,
      r.bidder_action ? h("div", { class: "row-act" }, r.bidder_action) : null,
      h("details", { class: "more", onclick: (e) => e.stopPropagation() }, h("summary", null, "Quote and proof"),
        h("blockquote", { class: "quote" }, r.quote), facts));
    li.addEventListener("click", () => pick(li, r, meta, li));
    li.addEventListener("keydown", (e) => { if (e.target === li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(li, r, meta, li); } });
    return li;
  }

  function pick(li, r, meta, from) {
    for (const x of $("rows").children) x.setAttribute("aria-current", "false");
    li.setAttribute("aria-current", "true");
    openLine(r.quote, meta.seg_id, from, r.id);
  }

  function quoteButton(side, cls) {
    return h("button", { type: "button", class: `quote ${cls || ""}`, title: "Open this line in the tender",
      onclick: (e) => openLine(side.quote, null, e.currentTarget) }, side.quote);
  }

  function renderConflicts(list) {
    const box = clear($("conflicts"));
    $("conf-sec").hidden = !list.length;
    for (const c of list) {
      const [a, b] = c.sides || [];
      box.append(h("article", { class: "conflict" },
        h("div", { class: "conflict-h" }, h("span", { class: "mono" }, c.id), c.topic || ""),
        h("div", { class: "sides" },
          [a, b].filter(Boolean).map((s) => h("div", { class: "side" }, h("span", { class: "hint mono" }, s.location || ""), quoteButton(s)))),
        c.agency_question ? h("p", { class: "ask" }, h("strong", null, "Ask the issuing authority: "), c.agency_question) : null));
    }
  }

  function copyButton(text) {
    return h("button", { type: "button", class: "copy", onclick: async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; b.classList.add("done"); }
      catch { b.textContent = "Copy failed"; }
      setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 1600);
    } }, "Copy");
  }

  function renderQuestions(agency, bidder) {
    const box = clear($("questions"));
    $("q-sec").hidden = !agency.length && !bidder.length;
    for (const q of agency) box.append(h("div", { class: "q" }, h("div", null, h("span", { class: "to" }, `To the issuing authority · ${q.for || ""}`), q.question), copyButton(q.question)));
    for (const q of bidder) box.append(h("div", { class: "q" }, h("div", null, h("span", { class: "to" }, "To you, the bidder"), q), copyButton(q)));
  }

  function renderFlags(report, flags) {
    const box = clear($("flags"));
    const items = [];
    for (const u of report.untrusted_instructions || []) {
      items.push(h("div", { class: "flag" }, h("b", null, `An instruction aimed at AI was found in the tender and ${u.action || "ignored"}`),
        h("span", { class: "hint mono" }, u.location || ""), quoteButton(u),
        h("span", { class: "hint" }, "It never became a requirement and changed no status.")));
    }
    if ((flags.pii_masked || []).length) {
      items.push(h("div", { class: "flag" }, h("b", null, "Personal data was masked before any model call"),
        h("div", { class: "tags" }, flags.pii_masked.map((p) => h("span", null, `${p.type} × ${p.count}`)))));
    }
    if (flags.forced_verdict_request_ignored) {
      items.push(h("div", { class: "flag" }, h("b", null, "A request to call the bid compliant was ignored"),
        h("span", { class: "hint" }, "Statuses come only from the documents and the checks.")));
    }
    box.append(...items);
    $("flag-sec").hidden = !items.length;
  }

  function renderLog(x, run) {
    const v = x.verification_log || {};
    const body = clear($("log-body"));
    const calls = run.calls || (v.calls || []).map((c) => ({ stage: c.stage, model: c.model_resolved, tokens_in: c.tokens_in,
      tokens_out: c.tokens_out, cost_usd: c.cost_usd, latency_ms: c.latency_ms, outcome: c.error ? "error" : "response" }));
    const by = {};
    for (const c of calls) (by[c.stage] = by[c.stage] || []).push(c);
    const qc = v.quote_checks || {};
    const steps = h("ol", { class: "steps" });
    const add = (who, title, note, num, warn) => steps.append(h("li", { class: `step ${who}${warn ? " warn" : ""}` },
      h("span", { class: "who" }, who), h("div", null, title, note ? h("span", { class: "hint" }, `: ${note}`) : null, num ? h("span", { class: "n" }, num) : null)));
    for (const st of STAGES.slice(0, 7)) {
      const cs = by[st];
      if (!cs) continue;
      const sum = (k) => cs.reduce((a, c) => a + (c[k] || 0), 0);
      add(STEP[st][0], STEP[st][1], STEP[st][2],
        `${cs.length} call${cs.length > 1 ? "s" : ""} · ${sum("tokens_in").toLocaleString()} in / ${sum("tokens_out").toLocaleString()} out tokens · ${(sum("latency_ms") / 1000).toFixed(1)} s · $${sum("cost_usd").toFixed(4)}`,
        cs.some((c) => c.outcome !== "response"));
      if (st === "E1") {
        add("code", "Verified every quote", "exact text in one place, field values inside the quote, dates recomputed",
          `${qc.passed ?? "?"} of ${qc.total ?? "?"} verified · ${qc.requoted || 0} re-quoted · ${qc.retracted || 0} retracted · ${qc.unverified || 0} left unverified`);
        const joins = (v.validity_merges || []).length + (v.list_entry_joins || []).length;
        if (joins) add("code", "Rejoined split items", "one obligation on one line stays one record", `${joins} join${joins > 1 ? "s" : ""}`);
      }
      if (st === "C" && (v.member_rule_conditions || []).length)
        add("code", "Added consortium conditions", "a member rule binds only a joint venture", `${v.member_rule_conditions.length} record(s)`);
    }
    if (v.status_hint_downgrades) add("code", "Kept the model's doubts", "a model hint can only make a result more cautious", `${v.status_hint_downgrades}`);
    add("code", STEP.RULES[1], STEP.RULES[2]);
    const inv = x.invariants || {};
    add("code", "Checked the invariants", inv.ok ? "all passed" : "a check failed; see the JSON",
      (inv.checks || []).map((c) => `${c.id} ${c.ok ? "ok" : "FAIL"}`).join(" · "), !inv.ok);
    body.append(steps, h("p", { class: "totals" },
      `${calls.filter((c) => c.outcome === "response").length} model calls · $${(run.cost ?? calls.reduce((a, c) => a + (c.cost_usd || 0), 0)).toFixed(4)} · ${[...new Set(calls.map((c) => c.model).filter(Boolean))].join(", ") || "n/a"}`));
  }

  $("download").addEventListener("click", () => {
    if (!S.report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(S.report, null, 2)], { type: "application/json" }));
    const a = h("a", { href: url, download: `tendercheck-${S.data ? S.data.id : "report"}.json` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  });

  /* ── re-run / live ─────────────────────────────────── */
  async function probe() {
    try { const hl = await getJSON("api/health"); return hl.live_enabled ? await getJSON("api/budget") : null; }
    catch { return null; }
  }

  function updateRerun() {
    const box = $("rerun");
    box.hidden = !S.live;
    if (!S.live) return;
    const rerun = S.live.kind === "rerun";
    $("rerun-h").textContent = rerun ? "Re-run from the recording" : "Run live";
    $("rerun-note").textContent = rerun
      ? "No model is called: this case's recorded replies go back through the current code, stage by stage."
      : `Calls the model for real · ${S.live.remaining_this_hour ?? "?"} left for you this hour, ${S.live.remaining_today} today.`;
    $("rerun-btn").disabled = !S.live.live_enabled || S.busy || (rerun && S.data && S.data.rerun === false);
  }

  function stages(events) {
    const ol = $("stages");
    ol.hidden = false;
    clear(ol);
    const seen = events.map((e) => e.stage);
    const last = seen[seen.length - 1];
    for (const st of STAGES) {
      if (["U", "E2", "E3", "E4"].includes(st) && !seen.includes(st)) continue;
      const cls = st === last && st !== "DONE" ? "now" : seen.includes(st) ? "done" : "";
      ol.append(h("li", { class: cls || null }, STEP[st][1]));
    }
  }

  $("rerun-btn").addEventListener("click", async () => {
    if (!S.data) return;
    const caseId = S.data.id;
    S.busy = true;
    $("case").disabled = true;
    updateRerun();
    $("rerun-err").hidden = true;
    stages([]);
    try {
      const { job_id } = await getJSON("api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: caseId }) });
      const until = Date.now() + 5 * 60 * 1000;
      let misses = 0;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1200));
        if (Date.now() > until) throw new Error("No answer after 5 minutes.");
        let job;
        try { job = await getJSON(`api/jobs/${job_id}`); misses = 0; }
        catch (err) { if (++misses >= 3) throw err; continue; }
        stages(job.stage_events || []);
        if (S.data.id !== caseId) break;  // the case changed meanwhile: do not paint another case's report
        if (job.state === "done") {
          render(job.report, { mode: job.report.x_meta.mode === "live" ? "live" : "replay", models: (job.report.x_meta.model || {}).resolved || [] });
          break;
        }
        if (job.state === "failed") throw new Error(`${job.error || "The run stopped"}.`);
      }
    } catch (err) {
      $("rerun-err").hidden = false;
      $("rerun-err").textContent = `${err.message} The recorded result stays on screen.`;
    } finally {
      S.busy = false;
      $("case").disabled = false;
      S.live = await probe();
      updateRerun();
    }
  });

  /* ── boot ──────────────────────────────────────────── */
  (async () => {
    try {
      S.samples = (await getJSON("data/samples.json")).samples || [];
      renderCasePicker();
      const want = location.hash.slice(1);
      const first = S.samples.find((s) => s.id === want) || S.samples[0];
      if (first) await select(first.id);
    } catch {
      $("case-title").textContent = "Could not load the recorded runs.";
    }
    S.live = await probe();
    updateRerun();
  })();
  addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (S.samples.some((s) => s.id === id) && (!S.data || S.data.id !== id)) select(id);
  });
})();
