/* TenderCheck page. Every string from a report, a model or a user goes in through textContent (no innerHTML). */
(() => {
  "use strict";

  const STATUS = {
    "met": ["Met", "met"],
    "missing": ["Missing", "missing"],
    "error": ["Error", "error"],
    "conditional": ["Conditional", "conditional"],
    "needs-confirmation": ["Check", "needs-confirmation"],
  };
  const STATUS_ORDER = ["error", "missing", "needs-confirmation", "conditional", "met"];
  const COND = { triggered: "applies", unknown: "if it applies", not_triggered: "does not apply" };
  const KIND = { deadline: "deadline", document: "document", attribute: "attribute", validity: "validity",
                 file_rule: "file rule", conditional: "conditional" };
  const STAGE = {
    U:  ["model", "Read the bidder's note in isolation", "listed what the bidder asked to check; it never touches statuses"],
    E1: ["model", "Extracted requirements", "one record per obligation, each with an exact quote"],
    E2: ["model", "Repaired its own quotes", "after code rejected them"],
    E3: ["model", "Re-read unclear lines", "code asked for a second look"],
    E4: ["model", "Coverage sweep", "lines with requirement words that no record cited"],
    M:  ["model", "Matched the bidder's files", "and read facts written in the file notes"],
    C:  ["model", "Adjudicated conflicts", "code proposed the pairs; quotes must prove a difference"],
    RULES: ["code", "Decided every status", "rules S0–S16 over verified quotes and file checks"],
    DONE:  ["code", "Checked invariants", "no status without a verified quote, no secrets in output"],
  };
  const PROGRESS = ["U", "E1", "E2", "E3", "E4", "M", "C", "RULES", "DONE"];

  const $ = (id) => document.getElementById(id);
  const state = { samples: [], data: null, report: null, filter: null, docIndex: {}, live: null, job: null };

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "style") for (const [sk, sv] of Object.entries(v)) el.style.setProperty(sk, sv);
        else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

  async function getJSON(url, opts) {
    const r = await fetch(url, Object.assign({ cache: "no-cache" }, opts || {}));
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.error || r.statusText), { status: r.status });
    return body;
  }

  /* ── samples ───────────────────────────────────────── */
  function renderSamples() {
    const list = clear($("sample-list"));
    for (const s of state.samples) {
      const total = Object.values(s.status_counts).reduce((a, b) => a + b, 0) || 1;
      const bar = h("span", { class: "case-bar", "aria-hidden": "true" },
        STATUS_ORDER.map((k) => s.status_counts[k]
          ? h("i", { style: { width: `${(100 * s.status_counts[k]) / total}%`, background: `var(--${k === "needs-confirmation" ? "check" : k === "conditional" ? "cond" : k})` } })
          : null));
      list.append(h("button", { type: "button", class: "case", role: "tab", id: `case-${s.id}`, "aria-selected": "false",
        onclick: () => select(s.id) },
        h("span", { class: "case-id" }, `${s.id} · ${s.lang === "ko" ? "한국어" : "English"}`),
        h("span", { class: "case-title" }, s.title),
        h("span", { class: "case-blurb" }, s.blurb), bar));
    }
  }

  async function select(id, push = true) {
    for (const b of document.querySelectorAll(".case")) b.setAttribute("aria-selected", String(b.id === `case-${id}`));
    if (push && location.hash.slice(1) !== id) history.replaceState(null, "", `#${id}`);
    const data = await getJSON(`data/${encodeURIComponent(id)}.json`);
    state.data = data;
    state.filter = null;
    renderSources(data.sources);
    renderBidder(data.manifest_text, data.user_message);
    renderReport(data.report, { mode: "replay", models: data.run.models, callLog: data.run.call_log, cost: data.run.cost_usd });
    updateLiveBox();
  }

  /* ── evidence ──────────────────────────────────────── */
  function renderSources(docs) {
    const tabs = clear($("doc-tabs"));
    state.docIndex = {};
    docs.forEach((d, i) => {
      for (const ln of d.lines) state.docIndex[ln.seg_id] = { doc: i, line: ln };
      tabs.append(h("button", { type: "button", role: "tab", class: "doc-tab", id: `tab-${d.doc_id}`,
        "aria-selected": String(i === 0), onclick: () => showDoc(i) }, d.title, h("small", null, d.doc_id)));
    });
    state.docs = docs;
    showDoc(0);
  }

  function showDoc(i, focusSeg, quote) {
    const d = state.docs[i];
    for (const t of document.querySelectorAll(".doc-tab")) t.setAttribute("aria-selected", String(t.id === `tab-${d.doc_id}`));
    const view = clear($("doc-view"));
    const cited = state.cited || new Set();
    const injected = state.injected || new Set();
    let target = null;
    for (const ln of d.lines) {
      const t = h("span", { class: "t" });
      if (focusSeg === ln.seg_id && quote && ln.text.includes(quote)) {
        const at = ln.text.indexOf(quote);
        t.append(ln.text.slice(0, at), h("mark", null, quote), ln.text.slice(at + quote.length));
      } else t.textContent = ln.text;
      const row = h("div", { class: ["line", cited.has(ln.seg_id) && "cited", injected.has(ln.seg_id) && "injected",
        focusSeg === ln.seg_id && "focus"].filter(Boolean).join(" "), id: `L-${ln.seg_id}`, title: ln.location || null },
        h("span", { class: "n" }, String(ln.n)), t);
      if (focusSeg === ln.seg_id) target = row;
      view.append(row);
    }
    if (target) {
      const top = target.offsetTop - view.clientHeight / 3;
      view.scrollTo({ top: Math.max(0, top), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      if (window.matchMedia("(max-width: 900px)").matches) view.scrollIntoView({ block: "start" });
    } else view.scrollTop = 0;
  }

  function findSeg(quote, hint) {
    if (hint && state.docIndex[hint]) return hint;
    const q = norm(quote);
    if (!q) return null;
    for (const [seg, v] of Object.entries(state.docIndex)) if (norm(v.line.text).includes(q)) return seg;
    const head = q.slice(0, 40);
    for (const [seg, v] of Object.entries(state.docIndex)) if (norm(v.line.text).includes(head)) return seg;
    return null;
  }

  function openCitation(quote, segHint) {
    const seg = findSeg(quote, segHint);
    if (!seg) return;
    const { doc, line } = state.docIndex[seg];
    const exact = line.text.includes(quote) ? quote : null;
    showDoc(doc, seg, exact);
  }

  function citeButton(quote, location, seg) {
    return h("button", { type: "button", class: "cite", title: "Open this line in the evidence pane",
      onclick: () => openCitation(quote, seg) }, `“${quote}”`, h("span", { class: "loc" }, location || "location not stated"));
  }

  function renderBidder(text, msg) {
    $("bidder-list").textContent = text || "";
    const m = $("bidder-msg");
    m.hidden = !msg;
    m.textContent = msg ? `Bidder's note (treated as data): “${msg}”` : "";
  }

  /* ── findings ──────────────────────────────────────── */
  function renderReport(report, run) {
    state.report = report;
    const x = report.x_meta || {};
    const rows = Object.fromEntries((x.rows || []).map((r) => [r.id, r]));
    $("mode-badge").textContent = run.mode === "live" ? "Live" : "Replay";
    $("mode-badge").className = `mode mode-${run.mode}`;
    $("model-name").textContent = (run.models && run.models.length ? run.models : (x.model && x.model.resolved) || ["—"]).join(", ");
    $("as-of").textContent = (x.as_of || "—").replace("T", " ").replace(/:00([+-]\d\d:\d\d)$/, " $1");
    $("basis").textContent = report.basis || "";
    $("disclaimer").textContent = report.disclaimer || "";
    const banner = $("banner");
    banner.hidden = !x.banner;
    banner.textContent = x.banner || "";
    $("summary").textContent = x.summary_line || "";

    state.cited = new Set(Object.values(rows).map((r) => r.seg_id).filter(Boolean));
    state.injected = new Set((report.untrusted_instructions || []).map((u) => findSeg(u.quote)).filter(Boolean));
    for (const t of document.querySelectorAll(".doc-tab")) {
      const id = t.id.slice(4);
      t.classList.toggle("has-flag", [...state.injected].some((s) => s.startsWith(`${id}-`)));
    }
    if (state.docs) showDoc(0);

    // tally
    const tally = clear($("tally"));
    for (const k of STATUS_ORDER) {
      const n = (report.status_counts || {})[k] || 0;
      const [label, cls] = STATUS[k];
      tally.append(h("button", { type: "button", "aria-pressed": "false", style: { "--c": `var(--${cls === "needs-confirmation" ? "check" : cls === "conditional" ? "cond" : cls})` },
        onclick: (e) => setFilter(k, e.currentTarget) }, h("b", null, String(n)), h("span", null, label)));
    }

    // do first
    const df = clear($("do-first"));
    for (const t of x.top_actions || []) df.append(h("li", null, t));
    df.parentElement.hidden = !(x.top_actions || []).length;

    // ledger
    const byId = Object.fromEntries(report.requirements.map((r) => [r.id, r]));
    const order = (x.display_order || []).filter((id) => byId[id]);
    for (const r of report.requirements) if (!order.includes(r.id)) order.push(r.id);
    const ids = new Map(report.requirements.map((r) => [r.id, r.id]));
    const ledger = clear($("ledger"));
    order.forEach((id, i) => ledger.append(entry(byId[id], rows[id] || {}, i, ids)));

    renderConflicts(report.conflicts || []);
    renderQuestions(x.agency_questions || [], report.bidder_questions || []);
    const nis = clear($("not-in-source"));
    for (const t of report.not_in_source || []) nis.append(h("li", null, t));
    $("nis-block").hidden = !(report.not_in_source || []).length;
    const pre = clear($("pre-submit"));
    for (const t of x.pre_submission_checklist || []) pre.append(h("li", null, t.replace(/^\[ \]\s*/, "")));
    renderFlags(report, x.flags || {});
    renderCustody(x, run);
  }

  function entry(r, row, i, ids) {
    const [label, cls] = STATUS[r.status] || [r.status, ""];
    const condTxt = r.condition_state ? COND[r.condition_state] : "";
    const meta = h("div", { class: "meta" },
      r.evidence_files && r.evidence_files.length
        ? h("span", null, h("span", { class: "k" }, "files"), r.evidence_files.map((f, j) => [j ? ", " : "", h("span", { class: "file" }, f)]))
        : null,
      r.depends_on ? h("span", null, h("span", { class: "k" }, "depends on"), r.depends_on) : null,
      r.depends_on_conflict ? h("span", null, h("span", { class: "k" }, "hinges on"), `conflict ${r.depends_on_conflict}`) : null);
    const checks = (row.checks || []).map((c) => h("span", { class: `chk ${c.result}`, title: c.detail || null }, `${c.id} ${c.result}`));
    const prov = row.provenance
      ? h("div", { class: "prov" }, "extracted by ", h("b", null, row.provenance.extracted_by), " · quote verified by ",
          h("b", null, row.provenance.verified_by), " · files matched by ", h("b", null, row.provenance.matched_by),
          " · status by ", h("b", null, row.provenance.status_by), row.rule ? ` (rule ${row.rule})` : "")
      : null;
    return h("li", { class: "entry", "data-status": r.status, style: { "--i": String(Math.min(i, 30)) } },
      h("div", { class: "entry-id" }, r.id, h("small", null, KIND[r.kind] || r.kind)),
      h("div", { class: "entry-main" },
        h("p", { class: "req" }, row.requirement || r.quote),
        citeButton(r.quote, r.location, row.seg_id),
        meta.childNodes.length ? meta : null,
        r.reason ? h("p", { class: "reason" }, r.reason) : null,
        r.bidder_action ? h("p", { class: "action" }, r.bidder_action) : null,
        checks.length ? h("details", { class: "proof" }, h("summary", null, "Proof"),
          h("div", { class: "proof-grid" }, checks), prov) : null),
      h("div", { class: "stamp-cell" },
        h("span", { class: `stamp ${cls}`, style: { "--r": `${((i * 37) % 7) - 3.5}deg`, "--i": String(Math.min(i, 30)) },
          "aria-label": `Status: ${label}${condTxt ? `, ${condTxt}` : ""}` }, label, condTxt ? h("small", null, condTxt) : null)));
  }

  function setFilter(k, btn) {
    state.filter = state.filter === k ? null : k;
    for (const b of document.querySelectorAll(".tally button")) b.setAttribute("aria-pressed", "false");
    if (state.filter) btn.setAttribute("aria-pressed", "true");
    for (const li of document.querySelectorAll(".entry")) li.hidden = !!state.filter && li.dataset.status !== state.filter;
  }

  function renderConflicts(conflicts) {
    const box = clear($("conflicts"));
    $("conflicts-block").hidden = !conflicts.length;
    for (const c of conflicts) {
      const [a, b] = c.sides || [];
      box.append(h("article", { class: "conflict" },
        h("div", { class: "conflict-head" }, h("b", null, c.id), h("span", null, c.topic || "")),
        h("div", { class: "sides" },
          h("div", { class: "side" }, a ? citeButton(a.quote, a.location) : null),
          h("div", { class: "vs", "aria-hidden": "true" }, "vs"),
          h("div", { class: "side" }, b ? citeButton(b.quote, b.location) : null)),
        c.agency_question ? h("p", { class: "ask" }, c.agency_question) : null));
    }
  }

  function copyBtn(text) {
    return h("button", { type: "button", class: "copy", onclick: async (e) => {
      const btn = e.currentTarget;
      try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; btn.classList.add("done"); }
      catch { btn.textContent = "Select & copy"; }
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600);
    } }, "Copy");
  }

  function renderQuestions(agency, bidder) {
    const box = clear($("questions"));
    $("questions-block").hidden = !agency.length && !bidder.length;
    for (const q of agency) box.append(h("div", { class: "q" }, h("div", null, h("span", { class: "who" }, `For the issuing authority · ${q.for || ""}`), q.question), copyBtn(q.question)));
    for (const q of bidder) box.append(h("div", { class: "q" }, h("div", null, h("span", { class: "who" }, "For you, the bidder"), q), copyBtn(q)));
  }

  function renderFlags(report, flags) {
    const box = clear($("flags"));
    const items = [];
    for (const u of report.untrusted_instructions || []) {
      items.push(h("div", { class: "flag" }, h("b", null, `Instruction to AI found in the documents — ${u.action || "ignored"}`),
        citeButton(u.quote, u.location),
        h("span", { class: "meta" }, "Shown struck through in the evidence pane. It never became a requirement or changed a status.")));
    }
    if ((flags.pii_masked || []).length) {
      items.push(h("div", { class: "flag pii" }, h("b", null, "Personal data masked before any model call"),
        h("div", { class: "tags" }, flags.pii_masked.map((p) => h("span", null, `${p.type} × ${p.count}`)))));
    }
    if (flags.forced_verdict_request_ignored) {
      items.push(h("div", { class: "flag" }, h("b", null, "A request to declare the bid compliant was ignored"),
        "Statuses come only from the documents and the checks."));
    }
    for (const f of flags.filtered || []) items.push(h("div", { class: "flag" }, h("b", null, "Wording filtered"), String(f.field || f)));
    box.append(...items);
    $("flags-block").hidden = !items.length;
  }

  function renderCustody(x, run) {
    const v = x.verification_log || {};
    const body = clear($("custody-body"));
    const chain = h("ol", { class: "chain" });
    const calls = run.callLog || (v.calls || []).map((c) => ({ stage: c.stage, model: c.model_resolved, tokens_in: c.tokens_in, tokens_out: c.tokens_out, cost_usd: c.cost_usd, latency_ms: c.latency_ms, outcome: c.error ? "error" : "response" }));
    const byStage = {};
    for (const c of calls) (byStage[c.stage] = byStage[c.stage] || []).push(c);
    const qc = v.quote_checks || {};
    const add = (kind, title, note, num, warn) => chain.append(h("li", { class: `link ${kind}${warn ? " warn" : ""}` },
      h("span", { class: "who" }, kind), h("b", null, title), note ? ` — ${note}` : "", num ? h("div", { class: "num" }, num) : null));
    for (const st of PROGRESS.slice(0, 7)) {
      const cs = byStage[st];
      if (!cs) continue;
      const [kind, title, note] = STAGE[st];
      const tin = cs.reduce((a, c) => a + (c.tokens_in || 0), 0), tout = cs.reduce((a, c) => a + (c.tokens_out || 0), 0);
      const ms = cs.reduce((a, c) => a + (c.latency_ms || 0), 0), usd = cs.reduce((a, c) => a + (c.cost_usd || 0), 0);
      add(kind, title, note, `${cs.length} call${cs.length > 1 ? "s" : ""} · ${tin.toLocaleString()} in / ${tout.toLocaleString()} out tokens · ${(ms / 1000).toFixed(1)} s · $${usd.toFixed(4)}`,
        cs.some((c) => c.outcome !== "response"));
      if (st === "E1") {
        add("code", "Verified every quote (Q1–Q8)", "exact text, one place, fields grounded in the quote, dates recomputed",
          `${qc.passed ?? "?"} of ${qc.total ?? "?"} records verified · ${qc.requoted || 0} re-quoted · ${qc.retracted || 0} retracted · ${qc.unverified || 0} left unverified`);
        const joins = (v.validity_merges || []).length + (v.list_entry_joins || []).length;
        if (joins) add("code", "Rejoined split items", "one obligation written on one line stays one record", `${joins} join${joins > 1 ? "s" : ""}`);
      }
      if (st === "C" && (v.member_rule_conditions || []).length) {
        add("code", "Added consortium conditions", "a member rule binds only a joint venture", `${v.member_rule_conditions.length} record(s)`);
      }
    }
    if (v.status_hint_downgrades) add("code", "Kept the model's doubts", "a model hint can only make a result more cautious", `${v.status_hint_downgrades} downgrade(s)`);
    add("code", ...STAGE.RULES.slice(1));
    const inv = x.invariants || {};
    add("code", "Checked invariants", inv.ok ? "all passed" : "a check failed — see JSON",
      (inv.checks || []).map((c) => `${c.id} ${c.ok ? "ok" : "FAIL"}`).join(" · "), !inv.ok);
    body.append(chain);
    const total = calls.reduce((a, c) => a + (c.cost_usd || 0), 0);
    body.append(h("p", { class: "costline" },
      `${calls.filter((c) => c.outcome === "response").length} model calls · $${(run.cost ?? total).toFixed(4)} · model ${[...new Set(calls.map((c) => c.model).filter(Boolean))].join(", ") || "—"}`));
  }

  /* ── download ──────────────────────────────────────── */
  $("download").addEventListener("click", () => {
    if (!state.report) return;
    const blob = new Blob([JSON.stringify(state.report, null, 2)], { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `tendercheck-${state.data ? state.data.id : "report"}.json` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  });

  /* ── live ──────────────────────────────────────────── */
  async function probeLive() {
    try {
      const health = await getJSON("api/health");
      if (!health.live_enabled) return null;
      return await getJSON("api/budget");
    } catch { return null; }
  }

  function updateLiveBox() {
    const box = $("live-box");
    box.hidden = !state.live;
    if (!state.live) return;
    const rerun = state.live.kind === "rerun";
    $("live-h").textContent = rerun ? "Re-run from the recording" : "Run live";
    $("live-note").textContent = rerun
      ? "No model is called here: the case's recorded Muse Spark replies go back through the current code, stage by stage."
      : `Calls Muse Spark for real. ${state.live.remaining_this_hour ?? "?"} run(s) left for you this hour, ${state.live.remaining_today} today.`;
    $("run-sample").textContent = rerun ? "Re-run this case" : "Re-run this case live";
    $("toggle-own").hidden = rerun;
    $("run-sample").disabled = !state.live.live_enabled || state.job !== null;
  }

  function progress(events) {
    const ol = $("progress");
    ol.hidden = false;
    clear(ol);
    const seen = new Set(events.map((e) => e.stage));
    const errs = new Set(events.filter((e) => e.error).map((e) => e.stage));
    for (const st of PROGRESS) {
      if (["U", "E2", "E3", "E4"].includes(st) && !seen.has(st)) continue;
      ol.append(h("li", { class: [seen.has(st) && "on", errs.has(st) && "err"].filter(Boolean).join(" ") || null }, STAGE[st][1]));
    }
  }

  async function startJob(body, sources) {
    $("live-error").hidden = true;
    state.job = "starting";
    updateLiveBox();
    const live = state.live && state.live.kind === "live";
    $("mode-badge").textContent = live ? "Live" : "Replay · running";
    $("mode-badge").className = `mode mode-${live ? "live" : "replay"}`;
    progress([]);
    try {
      const { job_id } = await getJSON("api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      state.job = job_id;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await getJSON(`api/jobs/${job_id}`);
        progress(job.stage_events || []);
        if (job.state === "done") {
          if (sources) renderSources(sources);
          renderReport(job.report, { mode: job.report.x_meta.mode === "live" ? "live" : "replay",
            models: (job.report.x_meta.model || {}).resolved || [] });
          break;
        }
        if (job.state === "failed") throw new Error(`The run stopped (${job.error}). The recorded result stays on screen.`);
      }
    } catch (err) {
      const el = $("live-error");
      el.hidden = false;
      el.textContent = err.message || "Live run failed.";
      $("mode-badge").textContent = "Replay";
      $("mode-badge").className = "mode mode-replay";
    } finally {
      state.job = null;
      state.live = await probeLive();
      updateLiveBox();
    }
  }

  $("run-sample").addEventListener("click", () => state.data && startJob({ sample_id: state.data.id }));
  $("toggle-own").addEventListener("click", () => { const f = $("own-form"); f.hidden = !f.hidden; });
  $("own-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("own-doc").value, list = $("own-list").value;
    const lang = (document.querySelector('input[name="lang"]:checked') || {}).value || "en";
    const lines = text.split(/\r?\n/).map((t, i) => ({ seg_id: `D1-L${i + 1}`, n: i + 1, text: t, location: "" })).filter((l) => l.text.trim());
    startJob({ docs: [{ title: "Pasted tender", text }], list_text: list, output_lang: lang, user_message: $("own-msg").value },
      [{ doc_id: "D1", title: "Pasted tender", lines }]);
  });

  /* ── boot ──────────────────────────────────────────── */
  (async () => {
    try {
      const idx = await getJSON("data/samples.json");
      state.samples = idx.samples || [];
      renderSamples();
      const want = location.hash.slice(1);
      const first = state.samples.find((s) => s.id === want) || state.samples[0];
      if (first) await select(first.id, false);
    } catch (err) {
      $("summary").textContent = "Could not load the recorded runs.";
    }
    state.live = await probeLive();
    updateLiveBox();
  })();
  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (state.samples.some((s) => s.id === id) && (!state.data || state.data.id !== id)) select(id, false);
  });
})();
