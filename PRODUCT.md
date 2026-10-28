# TenderCheck: product context

Derived from the repository's own documents (design plan 09, docs/design-direction.md, README); not from a chat prompt.
Owner confirmation pending (2026-10-28).

## Register
product: a working tool screen. The design serves a checking task; there is no marketing page.

## Product purpose
A verification agent that checks a public-tender bid package against the tender's own documents before submission.
The model extracts requirements, matches the bidder's files and judges conflicts; code verifies every quote against
the source and computes every status. It never states eligibility or that a bid is compliant; a person decides.

## Users
- Hackathon judges (Meta Global AI Developer Hackathon): desktop, a few minutes per entry, must see within seconds
  that the agent's claims are anchored to source lines and checked by code.
- Bid preparers at small firms: checking a package the day before upload, in Korean or English tenders.
- Procurement reviewers: reading why each item passed or failed, and which clauses disagree.

Physical scene: a bid preparer at an office desk on a 24-inch monitor in daylight, the tender PDF open in another
window, going row by row the afternoon before the upload deadline, wary of missing one line.

## Primary job
Open a case, see what is wrong (worst first), open the exact tender line that proves each finding, and copy the
question to send to the issuing authority.

## Brand personality
Exact, sober, accountable. Reads like an audit working paper, not like an assistant. Plain sentences, no hype.

## Anti-references
- Chatbot or "AI assistant" styling (gradients, glow, sparkle icons, avatar bubbles).
- The rejected first draft of this page: cream paper texture, serif display type, rotated rubber-stamp badges,
  ruled-paper lines, staggered row entrance.
- Generic SaaS dashboard: hero metrics, identical card grids.

## Strategic principles
1. Evidence next to the claim: every finding opens its source line.
2. Status by icon shape + word + colour, never colour alone.
3. Model work and code work are labelled separately; the run log shows both.
4. Replay and live are always labelled; a replayed run never looks live.
5. Synthetic data only; the page says so.

## Accessibility
WCAG 2.2 AA contrast for text and status, full keyboard path (rows, citations, filters, sheet), visible focus,
reduced-motion respected, Korean and English text in one family.
