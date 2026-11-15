"""A persistent conversation state machine with a replaceable extraction provider."""
from datetime import timedelta
import json
import re
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.reservations import ReservationError, ZONE

WORDS = {word: number for number, word in enumerate(
    ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'))}
NUMBER = r'(?:\d{1,2}|' + '|'.join(WORDS) + r')\b'
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


class Extraction(BaseModel):
    """Providers propose fields only. They cannot confirm or execute tools."""
    model_config = ConfigDict(extra='forbid')
    date: str | None = None
    time: str | None = None
    party_size: int | None = Field(default=None, strict=True)
    guest_name: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=500)
    clear_date: bool = False
    clear_time: bool = False
    clear_party: bool = False
    clarification: str | None = None
    handoff: bool = False
    pause: bool = False


class ExtractionProvider(Protocol):
    name: str

    def extract(self, text: str, now) -> Extraction: ...


class DemoParser:
    """Intentionally limited English parser, not an LLM or a speech recognizer."""
    name = 'local-parser-v1'

    def extract(self, text, now):
        value = text.strip().casefold()
        result = Extraction()
        if re.search(r'\b(human|manager|staff|person|representative)\b', value):
            result.handoff = True
            return result
        if re.search(r"\b(cancel|stop|never mind|nevermind|do not book|don.t book)\b", value) or value in ('no', 'no thanks'):
            result.pause = True
            return result
        date_signals = re.findall(r'\b(?:\d{4}-\d{2}-\d{2}|today|tomorrow)\b', value)
        if len(date_signals) > 1:
            return Extraction(clear_date=True, clarification='Please send one exact date in YYYY-MM-DD format.')
        date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', value)
        if date_match:
            result.date = date_match[0]
        elif 'day after tomorrow' in value:
            result.date = (now.date() + timedelta(days=2)).isoformat()
        elif re.search(r'\bnot\s+(today|tomorrow)\b', value):
            result.clear_date = True
            result.clarification = 'Please send the date you want in YYYY-MM-DD format.'
        elif 'tomorrow' in value:
            result.date = (now.date() + timedelta(days=1)).isoformat()
        elif re.search(r'\btoday\b', value):
            result.date = now.date().isoformat()
        else:
            weekday = next((day for day in WEEKDAYS if re.search(rf'\b{day}\b', value)), None)
            if weekday:
                ahead = (WEEKDAYS.index(weekday) - now.weekday()) % 7 or 7
                first = now.date() + timedelta(days=ahead)
                second = first + timedelta(days=7)
                result.clear_date = True
                result.clarification = f'Did you mean {first.isoformat()} or {second.isoformat()}? Please send the exact YYYY-MM-DD date.'
        if not result.date and not result.clear_date and re.search(r'\b(next week|next month|weekend)\b', value):
            result.clear_date = True
            result.clarification = 'Please send one exact date in YYYY-MM-DD format.'
        party = re.search(rf'\b(?:table for|party of|for|make that)\s+({NUMBER})(?!\s*(?:am|pm|:))', value)
        if party:
            token = party[1]
            result.party_size = int(token) if token.isdigit() else WORDS[token]
        if re.search(rf'\b(?:for|party of|make that)\s+{NUMBER}\s+or\s+{NUMBER}', value):
            result.party_size = None
            result.clear_party = True
            result.clarification = 'Please choose one party size, for example: A table for four.'
        clock = re.search(r'\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b', value)
        military = re.search(r'\b([01]\d|2[0-3]):([0-5]\d)\b', value)
        if clock:
            hour = int(clock[1]) % 12 + (12 if clock[3] == 'pm' else 0)
            result.time = f'{hour:02}:{int(clock[2] or 0):02}'
        elif military:
            result.time = military[0]
        elif re.search(rf'\bat\s+(?:{NUMBER}|noon|midnight)', value):
            result.clear_time = True
            result.clarification = (result.clarification + ' ' if result.clarification else '') + 'Please choose a seating with an explicit time: 5 pm, 6:30 pm, or 8 pm.'
        if len(re.findall(r'\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:am|pm)\b', value)) > 1:
            result.time = None
            result.clear_time = True
            result.clarification = 'Please choose one seating: 5 pm, 6:30 pm, or 8 pm.'
        name = re.search(r"(?:my name is|name is|under the name|under)\s+([^.,;!?\n]+)", text, re.I)
        if name:
            # Require name at the end or separated by punctuation to avoid guessing.
            candidate = name[1].strip()
            if re.search(r'\b(?:for|tomorrow|today|at|on|table|party)\b', candidate, re.I):
                result.clarification = 'Please send the name separately, for example: My name is Richa.'
            else:
                result.guest_name = candidate[:100]
        if re.search(r'\b(allerg\w*|peanut\w*|nut\w*|wheelchair|accessib\w*|stroller)\b', value):
            result.notes = text[:500]
        return result


class Conversations:
    def __init__(self, reservations, provider=None):
        self.reservations = reservations
        self.provider = provider or DemoParser()
        with reservations.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id)
                );
            ''')

    def create(self):
        session = str(uuid4())
        state = {'revision': 0, 'status': 'collecting', 'draft': {'notes': ''},
                 'messages': [{'role': 'host', 'text': 'Welcome to Juniper & Rye. Tell me your date and party size. Try: A table for four tomorrow at 6:30 pm.'}],
                 'receipt': None}
        with self.reservations.connect() as db:
            db.execute('INSERT INTO conversations VALUES(?,?)', (session, json.dumps(state)))
        return self.view(session, state)

    def get(self, session):
        with self.reservations.connect() as db:
            row = db.execute('SELECT state FROM conversations WHERE id=?', (session,)).fetchone()
        if not row:
            raise ReservationError('Conversation not found. Start a new conversation.')
        return self.view(session, json.loads(row[0]))

    def view(self, session, state):
        return {'session_id': session, **state, 'provider': self.provider.name,
                'timezone': str(ZONE), 'can_confirm': state['status'] == 'ready',
                'handoff_connected': False}

    def turn(self, session, request_id, text='', confirm_revision=None):
        text = text.strip()
        if (not text and confirm_revision is None) or (text and confirm_revision is not None):
            raise ReservationError('Send a message or an explicit confirmation action, not both.')
        fingerprint = json.dumps([text, confirm_revision])
        with self.reservations.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM conversations WHERE id=?', (session,)).fetchone()
            if not row:
                raise ReservationError('Conversation not found. Start a new conversation.')
            cached = db.execute('SELECT fingerprint,response FROM conversation_turns WHERE session_id=? AND request_id=?', (session, request_id)).fetchone()
            if cached:
                if cached[0] != fingerprint:
                    raise ReservationError('This message ID was already used for different content.')
                return json.loads(cached[1])
            state = json.loads(row[0])
            if confirm_revision is not None:
                self.confirm(db, session, state, confirm_revision)
            elif state['status'] in ('booked', 'handoff'):
                state['messages'].append({'role': 'host', 'text': 'Start a new conversation for another request. Use the reservation studio to cancel an existing booking.'})
            else:
                state['messages'].append({'role': 'guest', 'text': text})
                self.advance(db, state, text)
            state['messages'] = state['messages'][-40:]
            response = self.view(session, state)
            db.execute('UPDATE conversations SET state=? WHERE id=?', (json.dumps(state), session))
            db.execute('INSERT INTO conversation_turns VALUES(?,?,?,?)',
                       (session, request_id, fingerprint, json.dumps(response)))
            return response

    def say(self, state, message):
        state['messages'].append({'role': 'host', 'text': message})

    def advance(self, db, state, text):
        now = self.reservations.clock().astimezone(ZONE)
        # Re-validate provider output; provider implementations never own state transitions.
        extraction = Extraction.model_validate(self.provider.extract(text, now))
        state['revision'] += 1  # Any new message invalidates an earlier confirmation.
        state['status'] = 'collecting'
        if extraction.handoff:
            state['status'] = 'handoff'
            self.say(state, 'I’ll stop the booking flow here. This demo cannot connect you to a person. Please contact restaurant staff directly; no transfer has been made.')
            return
        if extraction.pause:
            self.say(state, 'I’ve paused this draft. Nothing has been booked or cancelled. Tell me what to change, or use the reservation studio to cancel a confirmed reservation.')
            return
        draft = state['draft']
        for field in ('date', 'time', 'party_size', 'guest_name', 'notes'):
            value = getattr(extraction, field)
            if value is not None:
                draft[field] = value
        if extraction.clear_date:
            draft.pop('date', None)
        if extraction.clear_time:
            draft.pop('time', None)
        if extraction.clear_party:
            draft.pop('party_size', None)
        if extraction.clarification:
            self.say(state, extraction.clarification)
            return
        missing = [field for field in ('date', 'party_size', 'time', 'guest_name') if not draft.get(field)]
        if missing:
            prompts = {'date': 'What date would you like? Use YYYY-MM-DD, today, or tomorrow.',
                       'party_size': 'How many guests? For example: A table for four.',
                       'time': 'Choose a seating: 5 pm, 6:30 pm, or 8 pm.',
                       'guest_name': 'What name should I use? For example: My name is Richa.'}
            self.say(state, prompts[missing[0]])
            return
        try:
            self.reservations.validate(draft['date'], draft['time'], draft['party_size'])
        except ReservationError as error:
            self.say(state, str(error) + ' Offered seatings are 17:00, 18:30, and 20:00 (New York time).')
            return
        if not self.reservations.free_table(db, draft['date'], draft['time'], draft['party_size']):
            self.say(state, 'That seating has no table available for your party. Choose another time or date.')
            return
        state['status'] = 'ready'
        self.say(state, f"Please review: {draft['guest_name']}, {draft['party_size']} guests on {draft['date']} at {draft['time']} (America/New_York). Use Confirm reservation to book. Accommodation requests are recorded, not guaranteed; speak with restaurant staff about allergies or accessibility.")

    def confirm(self, db, session, state, revision):
        if state['status'] != 'ready' or revision != state['revision']:
            raise ReservationError('The draft changed or is not ready. Review the latest details before confirming.')
        draft = state['draft']
        try:
            receipt = self.reservations.book(draft['date'], draft['time'], draft['party_size'],
                draft['guest_name'], draft['notes'], f'conversation:{session}:{revision}', True, connection=db)
        except ReservationError as error:
            state['status'] = 'collecting'
            state['revision'] += 1
            self.say(state, str(error) + ' No booking was made. Please choose another date or time.')
            return
        state['receipt'] = receipt
        state['status'] = 'booked'
        self.say(state, f"Your table is confirmed. Save your confirmation code: {receipt['confirmation']}.")
