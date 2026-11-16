# Conversational host

## Implemented behavior

The `/host` page provides a limited English text conversation backed by the real local reservation service. It gathers an absolute date, party size, offered seating, guest name, and optional accommodation request. Before booking, the host renders a complete draft and offers a separate confirmation action. A successful booking is claimed only after the reservation tool succeeds.

Architecture:

Browser → conversation API → extraction provider → typed field proposals → state machine → reservation tools → SQLite.

`ExtractionProvider` is a Python protocol. The built-in `DemoParser` is deterministic and uses no model, voice provider, network access, or API keys. Future model adapters can propose the same `Extraction` fields, but cannot mark a reservation confirmed or call booking tools themselves. The optional Ollama adapter below uses this interface.

## States and safety

- `collecting`: details are missing, ambiguous, invalid, unavailable, or paused.
- `ready`: details are complete, validated, and currently available. Confirmation is offered.
- `booked`: the booking tool returned a receipt. Start a new conversation for another booking.
- `handoff`: a human was requested. The demo stops booking but explicitly says it cannot connect to staff.

Any new guest message increments a revision. Confirmation must explicitly name the current revision while the state is ready. Old confirmations are rejected. Free-text “yes” requests another review; it never books. A pause invalidates the ready state. Capacity and booking-time rules are rechecked at confirmation.

Booking and conversation updates share one SQLite transaction. A failed response can therefore be retried without duplicating a booking. Each turn has a UUID request ID and a fingerprint: replaying the same request returns its recorded response; reusing that ID for different content fails. A unique active-table constraint remains the final seating safeguard. The browser saves unresolved requests in session storage and offers an explicit retry action after network interruption.

Sessions persist across service restarts. They are not user accounts. There is no multi-restaurant isolation or production authentication yet. Session IDs and reservation codes must be treated as secrets in this local prototype.

## Supported wording

- Dates: one `YYYY-MM-DD` date, today, tomorrow, or day after tomorrow. Relative dates use the injected server clock in America/New_York.
- Weekdays such as “next Friday”: the host suggests the next two dates and asks for an exact date. It does not guess which Friday was intended.
- Times: explicit `5 pm`, `6:30 pm`, `8 pm`, or 24-hour `17:00`, `18:30`, `20:00`. Ambiguous times such as “at seven” require clarification. Other explicit times are rejected by restaurant policy.
- Party size: “table for four,” “party of 4,” or “make that five.” Supported number words range from zero through twelve; the reservation service allows only 1–6 guests.
- Name: “My name is Richa” or “under the name Richa.” Send the name separately or as the last clause; complex sentences may need clarification.
- Accommodations: common allergy, nut, accessibility, wheelchair, and stroller terms record the current message as a request. These are not medical or accommodation guarantees.
- Human requests and cancel/stop wording pause or stop the chat flow without claiming an actual transfer or cancellation. Use the reservation studio and confirmation code to cancel a confirmed reservation.

The parser does not generally understand unrestricted language, multilingual speech, all negations, all date expressions, corrections to arbitrary fields, restaurant FAQs, or real staff availability. Accommodation notes from a later matching message replace the previous note rather than appending automatically. Always inspect the displayed draft. Chat does not edit an already confirmed reservation.

## API

- `POST /api/conversations`: start a draft.
- `GET /api/conversations/{session_id}`: retrieve current state.
- `POST /api/conversations/{session_id}/turns`: send `{request_id, text}` or `{request_id, confirm_revision}`; never both.

Responses include draft fields, revision, current status, recent messages, provider name, confirmation eligibility, and an optional receipt. Transcript display is capped at the latest 40 messages. Cached turn responses remain in SQLite for retry safety; production retention is not implemented.

## Evaluation

`datasets/conversation_scenarios.json` defines 10 fixed-clock synthetic text scenarios. `python -m app.evaluate` runs them in isolated temporary databases and checks state transitions, draft fields, clarification notices, and consistency between booking rows and conversation claims. Pass a custom manifest using `--scenarios`, or write a report using `--output`.

The pytest suite also checks stale confirmations, changed details, simultaneous confirmations, capacity races, timezone boundaries, session persistence, idempotent retries, API validation, and the replaceable provider interface. These are local parser and state-machine tests, not evidence of hosted model quality or voice latency.

## Next

Run live model comparisons, expand independent adversarial cases, then connect streaming voice to this state machine. Add authentication, consent, retention/deletion controls, and PostgreSQL before a public deployment.

## Optional model-assisted extraction

Set `MAITRE_MODEL` to a model already installed in a running Ollama server:

```bash
MAITRE_MODEL=your-installed-model python3 -m uvicorn app.main:app --reload
```

The server defaults to `http://127.0.0.1:11434`. An operator can set `MAITRE_MODEL_URL` to a trusted self-hosted Ollama endpoint. Only the current guest message and restaurant-local clock are sent; historical messages and existing bookings are not included. A remote endpoint receives the current message, so use only fictional guest data in the demo.

The adapter uses [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) and validates the response again locally. It fills missing draft fields while preserving deterministic parser results and explicit clarification. The model cannot return guest-facing replies, call tools, or confirm a booking. Network and validation errors use the local parser. Model requests happen outside the database write transaction; a draft changed mid-request must be reloaded before retrying. Server logs record prompt version, outcome and latency without message text.

Compare the local fixture baseline with the configured adapter:

```bash
python3 -m app.evaluate
MAITRE_MODEL=your-installed-model python3 -m app.evaluate --configured-model
```

Check `model_requests` in the report: fallback requests do not establish model quality. This smoke suite tests the combined parser/adapter, and live model accuracy, voice performance, and cost have not been measured.

## Browser voice preview

On `/host`, click **Start microphone**, speak, then click **Stop microphone** if recognition has not already ended. Final text goes into the message box for review and editing. Click **Send message** to submit. The microphone never submits a turn or confirms a reservation. Send or clear any unsent draft before confirming.

Use **Read latest reply** for on-demand playback or enable **Read new replies aloud**. **Stop speaking** stops playback; starting the microphone also stops it. Confirmation codes are displayed rather than read aloud. Typing works even when microphone access is denied or recognition is unavailable.

This preview uses the browser's [Web Speech API](https://webaudio.github.io/web-speech-api/). Support and processing depend on the browser; speech recognition may use the browser's remote service. Maître d’ does not upload audio itself. Only submitted text reaches the application backend. Use fictional details. This is a turn-based browser demo, not telephony, a provider-streaming integration, or full-duplex voice. Automated tests simulate browser speech events; actual microphone and playback behavior needs a manual browser pass.
