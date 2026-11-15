# Maître d’

A restaurant host that gets the details right. This project is growing toward real-time voice reservations with deterministic tools, provider-independent model routing, and measurable conversation quality.

## What works today

A responsive reservation studio for **Juniper & Rye**, a fictional restaurant. Guests can check real availability against a local SQLite database, review and explicitly confirm a booking, save a confirmation code, and cancel a reservation. The backend prevents duplicate bookings under concurrent requests and safely handles request retries.

This is a local product prototype. It does not connect to a real restaurant, use a language model, or support voice yet. Accommodation requests are recorded but never guaranteed. It includes a notice to speak with staff about allergies and accessibility; actual staff handoff is not implemented yet.

## Run locally

Requires Python 3.11 or newer. From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Interactive API documentation is at http://127.0.0.1:8000/docs. No API keys or paid services are required.

By default, reservations persist in `data/reservations.sqlite3`. Set `DATABASE_PATH` to change this path. Delete the database while the server is stopped to reset the fictional restaurant.

## Checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The initial suite covers the API journey, required confirmation, idempotent retries, changed retry payloads, concurrent booking attempts, capacity, cancellation, invalid dates, booking windows, party limits, and accommodation messaging. These are deterministic product checks, not conversation or model evaluations.

## Speech-quality lab

Open `/quality` to inspect the synthetic transcript benchmark, language and condition coverage, and ranked human-review queue. The workbench supports audited text annotations and a development-only JSONL export. Test references stay frozen.

Generate the same report without running the server:

```bash
python -m app.quality --output data/speech-quality-report.json
```

This lab uses 12 authored transcript fixtures, not audio or real model outputs. Its metrics and confidence values are illustrative. No ASR provider, training job, or production performance improvement is claimed. See [the speech-quality methodology](docs/speech-quality.md).

## Product rules

- Restaurant timezone: America/New_York.
- Booking window: today through 60 days ahead; only future seatings are offered.
- Fixed 90-minute seatings at 17:00, 18:30, and 20:00.
- Five tables: two for two guests, two for four, and one for six.
- One table per reservation; larger parties require restaurant assistance.
- Availability is advisory until the booking transaction succeeds.
- Request IDs make retries safe; confirmation codes let guests cancel.

## Layout

```text
app/          Reservation tools, speech-quality lab, API, and interfaces
datasets/     Versioned synthetic transcript fixtures
tests/        Deterministic regression checks
docs/         Architecture, data methodology, and build roadmap
```

## Next milestones

1. Natural-language host with an explicit conversation state machine, timezone-aware date clarification, and structured tool calls.
2. Streaming speech recognition, language model, and speech synthesis; interruption handling and staff handoff.
3. Speech-to-speech comparison, provider routing, trace capture, and scenario evaluations.
4. PostgreSQL migration, Cloud Run deployment, and a Vertex AI experiment.
5. Open-weight serving benchmark and a narrow fine-tuning experiment with held-out data.

See [the roadmap](docs/roadmap.md) for acceptance criteria.

## Before a public deployment

This version has no authentication, rate limiting, staff console, or production privacy controls. Confirmation codes act as cancellation secrets. Run it locally with fictional guest data. A public release needs access controls, retention rules, monitoring, and a durable managed database. Cloud Run's local filesystem is not the persistence layer for this SQLite version.
