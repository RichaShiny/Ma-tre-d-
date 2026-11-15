# Build roadmap

## Current architecture

Browser reservation studio → FastAPI → deterministic reservation service → SQLite.

The UI can request actions, but the service owns booking policy. A database transaction and a unique active-table constraint protect capacity across concurrent requests. An idempotency key and payload fingerprint protect against duplicate submissions. No model participates in seating decisions.

## 1. Conversational host

Add a provider-neutral interface returning typed conversation actions. Keep reservation tools separate from prompts. Model output proposes actions; the backend validates them. Clarify dates against an injected reference time and restaurant timezone. Preserve an explicit confirmation state and require fresh confirmation when any booking details change.

Acceptance: scripted cases for ambiguous Fridays, changed party sizes, invalid dates, unavailable tables, accommodation requests, prompt injection, tool errors, and requests for a human. Every claimed booking maps to a successful tool result. Relative dates are not silently guessed.

## 2. Voice

Add microphone capture, streaming ASR, conversational reasoning, and streaming TTS. Support interruption, discard interrupted response audio, and handle partial transcription. Add a separate speech-to-speech adapter and use the same reservation tools. Implement a staff handoff destination and an unavailable-staff fallback.

Acceptance: interruptible playback, reconnect behavior, user consent for microphone access, measured end-of-turn to first-audio latency, and no duplicate tool effects on retries.

## 3. Evaluation and observability

Version scenarios and prompts. Use deterministic checks for booking state, date correctness, confirmation, and tool errors. Use human-calibrated judges for tone and conversational quality. Record redacted traces with provider/model, prompt version, latency spans, tool results, token or audio usage, and pricing assumptions.

Publish p50/p95 latency, task success, correct transfers, unsupported claims, and cost per successful call. Label simulated traffic. Add stable session assignment for experiments only after offline regression gates pass. A local benchmark is not production evidence.

## 4. Deployment and model experiments

Move persistence to PostgreSQL before a multi-instance Cloud Run deployment. Add migrations, staff authentication, rate limits, retention controls, structured logs, and service health monitoring. Keep credentials in secret management. Use Vertex AI for a controlled provider experiment with budget limits.

Benchmark one hosted open-weight endpoint against another hosted model on identical scenarios. Report infrastructure, latency, cost, and quality assumptions. If practical, serve a small open-weight model separately to demonstrate deployment and operational tradeoffs.

## 5. Fine-tuning

Use synthetic and consented, de-identified data for a narrow reservation-field extraction task. Split by scenario family to avoid template leakage. Compare baseline prompting, improved prompting, and a tuned model against the same untouched test set. Report errors, latency, cost, and whether tuning is justified. Do not claim improvement without measured evidence.

## Implemented foundation: speech-quality lab

The transcript-quality workbench now provides synthetic fixtures, repeatable WER/CER reports, coverage slices, a heuristic review queue, provenance-aware annotations, and development-only exports. See [speech-quality methodology](speech-quality.md). Actual audio characterization, calibrated ASR outputs, active-learning experiments, model adaptation, and representative benchmarks remain future work.

## Implemented foundation: conversational booking

The `/host` page now has persistent draft states, local English extraction, restaurant-local relative dates, ambiguity clarification, explicit revision-bound confirmation, transactional booking, and safe turn retries. A reusable CLI runs 10 synthetic conversation scenarios. See [conversation design](conversation.md). Hosted model adapters, general language understanding, actual human transfer, and voice remain future work.
