/* TenderCheck page. Every string from a report, a model or a user goes in through textContent (no innerHTML).
   Interaction and motion rationale: docs/design-direction.md; review fixes: docs/design/review-20261028.md. */
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
  const DEFAULT_CASE = "A-M2";
  const SMALL = matchMedia("(max-width: 672px)");
  const CALM = matchMedia("(prefers-reduced-motion: reduce)");

  const $ = (id) => document.getElementById(id);
  const S = { samples: [], data: null, report: null, filter: null, docs: [], index: {}, cited: new Set(), injected: new Set(),
              live: null, busy: false, lastFocus: null, loading: 0 };

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
  const say = (text) => { $("announce").textContent = ""; requestAnimationFrame(() => { $("announce").textContent = text; }); };

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

  function setBanner(text, retryId) {
    const b = clear($("banner"));
    b.hidden = !text;
    if (!text) return;
    b.append(text);
    if (retryId) b.append(" ", h("button", { type: "button", class: "link", onclick: () => select(retryId) }, "Retry"));
  }

  async function select(id) {
    const ticket = ++S.loading;
    $("split").setAttribute("aria-busy", "true");
    let data;
    try {
      data = await getJSON(`data/${encodeURIComponent(id)}.json`);
    } catch {
      if (ticket !== S.loading) return;
      $("split").removeAttribute("aria-busy");
      if (S.data) {
        $("case").value = S.data.id;
        history.replaceState(null, "", `#${S.data.id}`);
        setBanner(`Could not load ${id}; still showing ${S.data.id}.`, id);
      } else {
        setBanner(`Could not load ${id}.`, id);
      }
      return;
    }
    if (ticket !== S.loading) return;  // a later choice won
    $("split").removeAttribute("aria-busy");
    if (location.hash.slice(1) !== id) history.replaceState(null, "", `#${id}`);
    $("case").value = id;
    S.data = data;
    S.filter = null;
    $("case-blurb").textContent = `${data.title}: ${data.blurb}`;
    for (const el of ["rows", "act", "pre", "conflicts", "questions", "summary", "doc-view", "bidder", "nis", "flags"]) $(el).lang = data.lang;
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
    // rebuild only when the documents or their flags change, so the focused tab survives a click
    const tabs = $("doc-tabs");
    const key = S.docs.map((d) => d.doc_id).join("|") + "#" + [...S.injected].join("|");
    if (S.tabsKey === key) {
      [...tabs.children].forEach((b, i) => b.setAttribute("aria-pressed", String(i === active)));
      return;
    }
    S.tabsKey = key;
    clear(tabs);
    S.docs.forEach((d, i) => {
      const flagged = [...S.injected].some((s) => s.startsWith(`${d.doc_id}-`));
      tabs.append(h("button", { type: "button", class: "doc-tab", "aria-pressed": String(i === active),
        onclick: () => showDoc(i) }, d.title, h("small", null, d.doc_id),
        flagged ? h("span", { class: "dot", title: "Contains an instruction aimed at AI (ignored)" }) : null,
        flagged ? h("span", { class: "sr-only" }, " (contains an ignored instruction aimed at AI)") : null));
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
    return target;
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
    if (!seg) { say("The quoted line was not found in the documents shown."); return; }
    const { doc, line } = S.index[seg];
    showDoc(doc, seg, line.text.includes(quote) ? quote : null, ref);
    say(`${ref ? `${ref}: ` : ""}${S.docs[doc].title}, line ${line.n} opened.`);
    if (SMALL.matches) openSheet(from);
  }

  /* mobile: the documents pane is a modal bottom sheet */
  const BACKGROUND = [".topbar", ".casebar", ".findings", ".foot", ".skip"];
  function openSheet(from) {
    S.lastFocus = from || document.activeElement;
    const ev = $("evidence");
    ev.classList.add("open");
    ev.setAttribute("role", "dialog");
    ev.setAttribute("aria-modal", "true");
    for (const sel of BACKGROUND) document.querySelector(sel).inert = true;
    document.body.classList.add("locked");
    $("scrim").hidden = false;
    $("ev-close").focus();
  }
  function closeSheet() {
    const ev = $("evidence");
    ev.classList.remove("open");
    ev.removeAttribute("role");
    ev.removeAttribute("aria-modal");
    for (const sel of BACKGROUND) document.querySelector(sel).inert = false;
    document.body.classList.remove("locked");
    $("scrim").hidden = true;
    if (S.lastFocus && S.lastFocus.focus) S.lastFocus.focus();
  }
  $("ev-close").addEventListener("click", closeSheet);
  $("scrim").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => {
    const ev = $("evidence");
    if (!ev.classList.contains("open")) return;
    if (e.key === "Escape") { closeSheet(); return; }
    if (e.key !== "Tab") return;
    // keep focus inside the sheet while it is a modal dialog
    const f = [...ev.querySelectorAll('button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  SMALL.addEventListener("change", () => { if (!SMALL.matches && $("evidence").classList.contains("open")) closeSheet(); });

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
    setBanner(x.banner || "");
    // the per-status counts are already in the tally; keep the basis and what was compared
    $("summary").textContent = [report.basis, (x.summary_line || "").split(/[:：]/)[0].trim() + (x.summary_line ? "." : "")]
      .filter((s) => s && s !== ".").join(" ");

    S.cited = new Set(Object.values(rows).map((r) => r.seg_id).filter(Boolean));
    S.injected = new Set((report.untrusted_instructions || []).map((u) => findSeg(u.quote)).filter(Boolean));
    showDoc(0);

    renderProofline(report, x);
    renderTally(report.status_counts || {});

    const byId = Object.fromEntries(report.requirements.map((r) => [r.id, r]));
    const pos = Object.fromEntries((x.display_order || []).map((id, i) => [id, i]));
    const order = report.requirements.map((r) => r.id).sort((a, b) =>
      ORDER.indexOf(byId[a].status) - ORDER.indexOf(byId[b].status) || (pos[a] ?? 1e3) - (pos[b] ?? 1e3));
    const list = clear($("rows"));
    order.forEach((id, i) => list.append(row(byId[id], rows[id] || {}, i === 0)));
    $("rows-empty").hidden = true;

    renderAct(x.top_actions || [], byId, rows);
    renderConflicts(report.conflicts || [], x.agency_questions || []);
    renderQuestions(x.agency_questions || [], report.bidder_questions || []);
    const nis = clear($("nis"));
    for (const t of report.not_in_source || []) nis.append(h("li", null, t));
    $("nis-sec").hidden = !(report.not_in_source || []).length;
    const pre = clear($("pre"));
    (x.pre_submission_checklist || []).forEach((t, i) => {
      const id = `pre-${i}`;
      pre.append(h("li", null, h("input", { type: "checkbox", id }), h("label", { for: id }, t.replace(/^\[ \]\s*/, ""))));
    });
    renderFlags(report, x.flags || {});
    renderLog(x, run);
  }

  function quoteStats(x) {
    const q = (x.verification_log || {}).quote_checks || {};
    const total = q.total ?? 0, passed = q.passed ?? 0, unverified = q.unverified ?? 0;
    return { total, passed, unverified, removed: Math.max(0, total - passed - unverified), requoted: q.requoted || 0,
             retracted: q.retracted || 0 };
  }

  function renderProofline(report, x) {
    const box = clear($("proofline"));
    const q = quoteStats(x);
    const inv = x.invariants || {};
    const invOk = (inv.checks || []).filter((c) => c.ok).length;
    const go = (target, open) => () => {
      const el = $(target);
      if (open) el.open = true;
      el.scrollIntoView({ behavior: CALM.matches ? "auto" : "smooth", block: "start" });
    };
    const item = (label, value, target, open) => h("button", { type: "button", class: "proof-item", onclick: go(target, open) },
      h("b", null, value), label);
    box.append(...[
      item("quotes verified by code", `${q.passed} of ${q.passed + q.unverified}`, "log", true),
      item("report checks passed", `${invOk} of ${(inv.checks || []).length}`, "log", true),
      (report.conflicts || []).length ? item(report.conflicts.length > 1 ? "conflicts between documents" : "conflict between documents", String(report.conflicts.length), "conf-sec") : null,
      (report.untrusted_instructions || []).length ? item("AI instruction in the tender, ignored", String(report.untrusted_instructions.length), "flag-sec") : null,
      ((x.flags || {}).pii_masked || []).length ? item("kinds of personal data masked", String(x.flags.pii_masked.length), "flag-sec") : null,
    ].filter(Boolean));
  }

  function renderTally(counts) {
    const tally = $("tally");
    for (const b of tally.querySelectorAll("button")) b.remove();
    const all = Object.values(counts).reduce((a, b) => a + b, 0);
    const mk = (key, label, n, st) => h("button", { type: "button", class: st ? `st ${st}` : null, "aria-pressed": String(S.filter === key),
      "data-key": key || "", disabled: key && !n ? true : null, onclick: (e) => setFilter(key, e.currentTarget) },
      st ? icon(st) : null, h("span", null, label), h("b", null, String(n)));
    tally.append(mk(null, "All", all, null));
    for (const k of ORDER) tally.append(mk(k, STATUS[k], counts[k] || 0, k));
    moveInk();
  }
  function moveInk() {
    const btn = $("tally").querySelector('button[aria-pressed="true"]');
    const ink = $("tally-ink");
    if (!btn) { ink.style.transform = "scaleX(0)"; return; }
    ink.style.transform = `translateX(${btn.offsetLeft}px) scaleX(${btn.offsetWidth})`;
  }
  function setFilter(key) {
    S.filter = key;
    for (const b of $("tally").querySelectorAll("button")) b.setAttribute("aria-pressed", String((b.dataset.key || null) === key));
    moveInk();
    let shown = 0;
    for (const li of $("rows").children) { li.hidden = !!key && li.dataset.status !== key; shown += li.hidden ? 0 : 1; }
    $("rows-empty").hidden = shown > 0;
    rove($("rows").querySelector(".row:not([hidden])"));
    say(key ? `${shown} ${STATUS[key]} shown.` : "All requirements shown.");
  }
  $("show-all").addEventListener("click", () => setFilter(null));
  addEventListener("resize", moveInk);
  document.fonts.ready.then(moveInk);

  function checkFacts(meta) {
    const checks = meta.checks || [];
    const bad = checks.filter((c) => c.result === "fail" || c.result === "unknown");
    const passed = checks.filter((c) => c.result === "pass").map((c) => c.id);
    const na = checks.filter((c) => c.result === "not_applicable").length;
    return h("dd", null,
      bad.map((c) => h("span", { class: `chk-line ${c.result}` }, `${c.id} ${c.result === "fail" ? "failed" : "could not be decided"}`, c.detail ? `: ${c.detail}` : "")),
      h("span", { class: "chk-sum" }, [passed.length ? `Passed: ${passed.join(", ")}` : "", na ? `${na} not applicable` : ""].filter(Boolean).join(" · ")));
  }

  function row(r, meta, first) {
    const cond = r.condition_state ? COND[r.condition_state] : "";
    const p = meta.provenance;
    const facts = h("dl", { class: "facts" },
      r.evidence_files && r.evidence_files.length ? [h("dt", null, "Files"), h("dd", { class: "mono" }, r.evidence_files.join(", "))] : null,
      r.depends_on ? [h("dt", null, "Depends on"), h("dd", { class: "mono" }, r.depends_on)] : null,
      r.depends_on_conflict ? [h("dt", null, "Hinges on"), h("dd", null, `conflict ${r.depends_on_conflict}`)] : null,
      (meta.checks || []).length ? [h("dt", null, "Checks"), checkFacts(meta)] : null,
      p ? [h("dt", null, "Who did what"), h("dd", null, `extracted by ${p.extracted_by} · quote verified by ${p.verified_by} · files matched by ${p.matched_by} · status by ${p.status_by}`)] : null);
    const more = h("details", { class: "more", onclick: (e) => e.stopPropagation() }, h("summary", null, "Quote and proof"),
      h("blockquote", { class: "quote" }, r.quote), facts);
    const li = h("li", { class: "row", id: `row-${r.id}`, "data-id": r.id, "data-status": r.status, tabindex: first ? "0" : "-1", "aria-current": "false" },
      statusTag(r.status, cond),
      h("div", { class: "row-title" }, h("span", { class: "rid mono" }, r.id), meta.requirement || r.quote, h("span", { class: "kind" }, KIND[r.kind] || r.kind)),
      h("button", { type: "button", class: "loc", tabindex: "-1", title: "Open this line in the tender",
        onclick: (e) => { e.stopPropagation(); pick(li, e.currentTarget); } }, `${r.location || "location not stated"} →`),
      r.reason ? h("div", { class: "row-sub" }, r.reason) : null,
      r.bidder_action ? h("div", { class: "row-act" }, r.bidder_action) : null,
      more);
    li._r = r; li._meta = meta; li._more = more;
    li.addEventListener("click", () => pick(li, li));
    li.addEventListener("keydown", (e) => {
      if (e.target !== li) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(li, li); }
      else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); step(li, 1); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); step(li, -1); }
    });
    return li;
  }

  function rove(li) {
    if (!li) return;
    for (const x of $("rows").children) x.tabIndex = -1;
    li.tabIndex = 0;
  }
  function step(li, dir) {
    let n = dir > 0 ? li.nextElementSibling : li.previousElementSibling;
    while (n && n.hidden) n = dir > 0 ? n.nextElementSibling : n.previousElementSibling;
    if (n) { rove(n); n.focus(); }
  }

  function pick(li, from) {
    for (const x of $("rows").children) { x.setAttribute("aria-current", "false"); x._more.open = false; }
    li.setAttribute("aria-current", "true");
    li._more.open = true;
    rove(li);
    openLine(li._r.quote, li._meta.seg_id, from, li._r.id);
  }
  function pickById(id, from) {
    const li = $(`row-${id}`);
    if (!li) return;
    if (li.hidden) setFilter(null);
    li.scrollIntoView({ behavior: CALM.matches ? "auto" : "smooth", block: "nearest" });
    pick(li, from);
    li.focus({ preventScroll: true });
  }

  function linkify(text, byId) {
    // "R18: …" and "C1" become controls that open the row or the conflict; the rest stays text.
    const parts = [];
    let last = 0;
    for (const m of text.matchAll(/\b([RC]\d{1,3})\b/g)) {
      const id = m[1];
      if (id[0] === "R" && !byId[id]) continue;
      parts.push(text.slice(last, m.index));
      parts.push(id[0] === "R"
        ? h("button", { type: "button", class: "ref mono", onclick: (e) => pickById(id, e.currentTarget) }, id)
        : h("button", { type: "button", class: "ref mono", onclick: () => $(`conf-${id}`) && $(`conf-${id}`).scrollIntoView({ behavior: CALM.matches ? "auto" : "smooth", block: "start" }) }, id));
      last = m.index + id.length;
    }
    parts.push(text.slice(last));
    return parts;
  }

  function renderAct(actions, byId) {
    const act = clear($("act"));
    for (const t of actions) act.append(h("li", null, h("span", null, linkify(t, byId))));
    $("act-sec").hidden = !actions.length;
  }

  function quoteButton(side) {
    return h("button", { type: "button", class: "quote quote-btn", title: "Open this line in the tender",
      onclick: (e) => openLine(side.quote, null, e.currentTarget) }, h("span", { class: "quote-text" }, side.quote), h("span", { class: "open-line" }, "Open line →"));
  }

  function copyButton(text) {
    return h("button", { type: "button", class: "copy", onclick: async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; b.classList.add("done"); }
      catch { b.textContent = "Copy failed"; }
      setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 1600);
    } }, "Copy");
  }

  function renderConflicts(list, agency) {
    const box = clear($("conflicts"));
    $("conf-sec").hidden = !list.length;
    for (const c of list) {
      const [a, b] = c.sides || [];
      const ask = c.agency_question || (agency.find((q) => q.for === c.id) || {}).question;
      box.append(h("article", { class: "conflict", id: `conf-${c.id}` },
        h("div", { class: "conflict-h" }, h("span", { class: "mono" }, c.id), c.topic || ""),
        h("div", { class: "sides" },
          [a, b].filter(Boolean).map((s) => h("div", { class: "side" }, h("span", { class: "hint mono" }, s.location || ""), quoteButton(s)))),
        ask ? h("div", { class: "q" }, h("div", null, h("span", { class: "to" }, "Ask the issuing authority"), ask), copyButton(ask)) : null));
    }
  }

  function renderQuestions(agency, bidder) {
    const box = clear($("questions"));
    const other = agency.filter((q) => !/^C\d+$/.test(q.for || ""));
    $("q-sec").hidden = !other.length && !bidder.length;
    for (const q of other) box.append(h("div", { class: "q" }, h("div", null, h("span", { class: "to" }, `To the issuing authority · ${q.for || ""}`), q.question), copyButton(q.question)));
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
    const q = quoteStats(x);
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
          `${q.total} records proposed · ${q.passed} verified (${q.requoted} after re-quoting) · ${q.unverified} left unverified · ${q.removed} removed as duplicates, joins or instruction text · ${q.retracted} retracted`);
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
    $("rerun-btn").disabled = !S.live.live_enabled || S.busy;
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
          say("Re-run finished.");
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
      const first = S.samples.find((s) => s.id === want) || S.samples.find((s) => s.id === DEFAULT_CASE) || S.samples[0];
      if (first) await select(first.id);
    } catch {
      $("case-title").textContent = "Could not load the recorded runs.";
      setBanner("The recorded runs could not be loaded.", null);
    }
    S.live = await probe();
    updateRerun();
  })();
  addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (S.samples.some((s) => s.id === id) && (!S.data || S.data.id !== id) && !S.busy) select(id);
  });
})();
