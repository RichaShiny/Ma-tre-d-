# Speech-quality lab

## Why it belongs in Maître d’

A restaurant voice host needs more than a pleasant voice. If speech recognition changes “five” to “four” or drops “allergy,” the downstream agent can take the wrong action. This lab adds a repeatable foundation for inspecting transcription quality, characterizing data gaps, selecting examples for review, and exporting annotations for future adaptation experiments.

## Implemented

- A strict JSONL dataset schema with unique IDs, split, source, language, condition, domain, speaker grouping, reference, two hypotheses, confidence, and model version.
- A reproducible SHA-256 fingerprint of the loaded dataset and a versioned normalization rule.
- Corpus-weighted word error rate and character error rate, plus breakdowns by split, language, speech condition, domain, and speaker grouping.
- Low-support flags for observed slices with fewer than three examples. This threshold is a configurable reporting heuristic in Python, not statistical evidence of representativeness.
- A deterministic review queue using uncertainty, disagreement, and language rarity, with no ground-truth error signal in the selection score.
- Append-only SQLite annotation revisions recording reviewer alias, rationale, timestamp, and dataset fingerprint.
- Development-only exports with provenance. No test example can enter the review endpoint or adaptation export.
- A browser workbench and a reusable command-line report generator.

## Sample provenance and scope

All twelve records in `datasets/speech_samples.jsonl` are manually authored synthetic fixtures. Eight belong to development and four to test. English, Spanish, and English/Spanish code-switched text are included. Noise and overlapping-speech tags describe intended scenarios: there are no corresponding audio files. Confidence values, hypotheses, and alternate hypotheses are invented fixtures, not outputs from an ASR model or an ensemble.

Speaker grouping means an authored solo/multiple-speaker scenario. No speaker identity, accent, demographic attribute, or acoustic property is inferred. There is no duration analysis or actual speaker characterization yet.

The tiny test split provides a fixed software regression example, not an unbiased or representative speech benchmark. Templates and domains overlap between splits. Real benchmarking will require independent conversations, speaker/session-disjoint splits, adequate slice support, and native-speaker review where appropriate.

## Metric definitions

Normalize both reference and hypothesis with Unicode NFKC, case folding, and replacement of non-alphanumeric punctuation with spaces. Collapse whitespace. Accents remain significant. Digits and spelled-out numbers remain distinct. A reference must include at least one letter or number.

Word error rate is unit-cost Levenshtein distance over normalized whitespace-delimited words divided by the number of reference words. Corpus WER sums edit counts before dividing by total reference words. Character error rate uses normalized characters with spaces excluded. WER and CER can exceed 100% due to insertions. Empty hypotheses count as deletions. Empty reference texts are rejected.

These metrics cannot tell us whether the correct date, guest count, or allergy request reached a booking tool. A later evaluation suite must separately measure critical-slot accuracy and completed booking correctness. The normalization is suitable for these whitespace-delimited fixtures; language-specific tokenization is needed before adding languages with different segmentation needs.

## Review priority

For unreviewed development examples:

`priority = 0.5 × (1 − confidence) + 0.3 × disagreement + 0.2 × language_rarity`

Disagreement is word-level edit distance between normalized hypotheses divided by the larger hypothesis length (or 1). Rarity is the reciprocal of that language's sample count in the development pool. Ties break by sample ID. This ranking does not use reference labels. It is a review-selection heuristic, not a calibrated estimate of annotation value or an implemented model-training loop.

A human can inspect both hypotheses and edit a reference draft, with an alias and rationale. Since audio is unavailable, every saved annotation is explicitly marked text-reviewed and `audio_verified: false`. Reviews do not change frozen fixture metrics. Dataset changes invalidate prior review eligibility by fingerprint, retaining older revisions in storage without silently applying them to new data.

## API and CLI

- `GET /quality`: workbench.
- `GET /api/quality/report`: fingerprint, normalization, aggregate metrics, slices, and limitations.
- `GET /api/quality/queue`: prioritized unreviewed development examples.
- `POST /api/quality/reviews/{sample_id}`: save `{reference, reviewer, reason}`.
- `GET /api/quality/export`: reviewed development examples in JSONL, latest revision per example.
- `python -m app.quality --dataset datasets/speech_samples.jsonl --output data/report.json`: reproducible offline report.

## Privacy and production boundaries

Use fictional data and reviewer aliases. Exports include transcript text and annotations. Provenance tracking does not automatically detect PII, establish consent, or certify GDPR compliance. This local prototype has no reviewer authentication. Do not import real call transcripts until consent, access controls, redaction validation, deletion and retention policies, and secure storage are implemented.

## Next experiments

1. Collect licensed or consented restaurant-like audio with explicit language and condition metadata. Add audio integrity, duration, provenance, and recording-level split checks.
2. Run actual ASR providers through a common adapter, preserving raw outputs and model versions. Calibrate confidence before using it across providers.
3. Add audio playback and native-speaker adjudication. Model-assisted labels remain proposals until appropriately verified.
4. Compare random, uncertainty-only, and diversity-aware selection using equal annotation budgets. Measure error reduction on a separate, untouched test corpus, repeated over multiple seeds.
5. Use reviewed training data for a controlled domain-adaptation experiment. Record dataset/model/config versions and compare to a prompting or vocabulary baseline. Keep development and test data out of training, including near-duplicate conversations.

No external service or paid job is invoked by this milestone.
