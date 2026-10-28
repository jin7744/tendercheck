# TenderCheck

A verification agent that checks a public-tender bid package against the tender's own documents
before submission — every requirement is quoted from the source and every status is computed by code.

> Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, "Build for Your World").
> **All code in this repository was written during the hackathon building period, starting Oct 27, 2026.**
> The commit history is the record of what was built and when.

## What existed before the hackathon (draft — to be finalized before submission)

- No code. Before Oct 27 there was only a written plan: a design document and a test specification
  (Oct 16–22, 2026). The synthetic tender texts, bidder lists and answer keys in `scenarios/` were drafted
  as text inside that specification and transcribed into fixture files on Oct 27.
- Everything executable — engine, grader, model adapter, UI — was written from Oct 27 onward.

Status: work in progress. Everything in `scenarios/` is synthetic test data (fictional agencies, bidders and people).

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Machine-readable contracts: model output schemas, report schema |
| `engine/` | Normalization, model adapter, verification checks, matching, rules, report |
| `scenarios/` | Synthetic tender packages, bidder manifests, answer keys, adversarial test cards |
| `eval/` | Independent grader (does not import `engine/`) |
| `web/` | Single-page UI (`index.html`, `app.css`, `app.js`) and the replay bundle in `web/data/` |
| `tools/` | `run_scenario.py` (fake / replay / live runs + grading), `build_web_bundle.py` (replay bundle for the page), `serve.py` (page + API; `--rerun` re-sends recorded replies at $0, `--live` calls the model under caps), pre-commit scrub check |
| `docs/` | Experiment and decision logs |
