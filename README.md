# TenderCheck

A verification agent that checks a public-tender bid package against the tender's own documents
before submission — every requirement is quoted from the source and every status is computed by code.

> Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, "Build for Your World").
> **All code in this repository was written during the hackathon building period, starting Oct 27, 2026.**
> The commit history is the record of what was built and when.

Status: work in progress. Everything in `scenarios/` is synthetic test data (fictional agencies, bidders and people).

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Machine-readable contracts: model output schemas, report schema |
| `engine/` | Normalization, model adapter, verification checks, matching, rules, report |
| `scenarios/` | Synthetic tender packages, bidder manifests, answer keys, adversarial test cards |
| `eval/` | Independent grader (does not import `engine/`) |
| `web/` | Single-page UI |
| `tools/` | Dev utilities (pre-commit scrub check) |
| `docs/` | Experiment and decision logs |
