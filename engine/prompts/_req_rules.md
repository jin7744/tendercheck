version: req_rules/1.0.0
---
WHAT COUNTS AS A REQUIREMENT
A requirement is something the issuing authority says a bidder must submit, or how it must be submitted: a document, a deadline, the submission method, file type, size, naming or merging, a signature or seal, original or copy, notarization, a validity period, an amount, or a rule on consortium members.
- One obligation per record. If one sentence states two obligations (for example a file type and a size limit), make two records, each quoting its own part.
- If two places state the same obligation, even with different values, make one record for each place. Merge nothing and do not choose between them.
- A property of a listed document (notarized, certified copy, valid until a date, signed by every member) that is stated in its own sentence or clause gets its own record, with attribute_of set to the tmp_id of that document's record. A property written inside the document's own list entry stays in that record's fields (sign_seal, copy_type, amount, notarized).
- A document required for each member or partner: quantity_rule per_member. One per person (for example per proposed staff member): per_person.
- If a clause points to another clause for its value (for example "the date given in Clause 4.2"), the referenced clause gets its own record when it states a requirement.
- Never add a document, page, deadline or rule that the SOURCE does not state, even if tenders often ask for it.

QUOTE RULES
- quote: the words that state this one obligation, copied exactly as written (same spelling, case and punctuation) from a single segment, 15-300 characters. Leave out a leading clause number such as "(b)" or "4.1" and trailing list punctuation such as ";" or "and".
- Never paraphrase, translate, shorten with "...", or join text from different places.
- location: the doc_id of the document and the seg_id of the segment the quote comes from.
- Every value you put in a field must be written inside that quote (only tz_quote may come from elsewhere). Do not compute or convert values.

FIELDS (when the quote does not state a field, use "", 0, [], "none" or "not_stated")
- tmp_id: R1, R2, ... unique.
- category: the closest enum value.
- subject: short lowercase English noun phrase naming the document or topic, at most 60 characters. Use the same subject wherever the same document or topic appears.
- requirement: one {{OUTPUT_LANGUAGE}} sentence, at most 200 characters, restating the obligation.
- mandatory: conditional when it applies only in a stated situation, with conditions: condition_quote = the condition words copied exactly from the same paragraph, fact_key (consortium, usage_seal, foreign_entity, subcontracting, sme, other), applies_when yes or no. optional when the source says it may be submitted; unclear when you cannot tell.
- doc_code: the document code only if that exact code is printed somewhere in the SOURCE (for example in a checklist); otherwise "".
- deadline: kind; raw = the date and time words copied exactly from the quote (one date only); iso_local = YYYY-MM-DDTHH:MM:SS, plus a UTC offset such as +05:30 only when the quote or tz_quote states the time zone; tz_quote = a sentence from the same document that states the time zone, only when the quote itself does not.
- validity: rule_type valid_for_from_issue (valid N days or months from issue), issued_within_before_anchor (issued within N days or months before an event), valid_until_date (valid until a stated date) or valid_at_least_after_anchor (valid at least N days or months after an event); raw = the validity words copied exactly; days and months as written; until_iso = YYYY-MM-DD for valid_until_date; anchor_event.
- format: extensions as written, without the dot (for example "docx"); max_size_mb; filename_template copied exactly; merge_single_pdf yes when documents must be merged into one file, with merge_scope = the tmp_ids of the document records to merge.
- member_rule: max_members and lead_share_min_pct as written. amount: currency, value and ratio_pct as written.
- sign_seal, copy_type, notarized: only what the quote states.
