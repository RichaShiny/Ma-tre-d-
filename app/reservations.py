"""Deterministic reservation tools. All seating decisions live here."""
import json
from contextlib import contextmanager, nullcontext
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/New_York')
SLOTS = ('17:00', '18:30', '20:00')  # Non-overlapping 90-minute seatings.
TABLES = ((1, 2), (2, 2), (3, 4), (4, 4), (5, 6))


class ReservationError(ValueError):
    pass


class Reservations:
    def __init__(self, path, clock=None):
        self.path = str(path)
        self.clock = clock or (lambda: datetime.now(ZONE))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bookings (
                    token TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, day TEXT NOT NULL, slot TEXT NOT NULL,
                    party INTEGER NOT NULL, table_id INTEGER NOT NULL,
                    guest TEXT NOT NULL, notes TEXT NOT NULL, cancelled INTEGER DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS occupied_table
                ON bookings(day, slot, table_id) WHERE cancelled = 0;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def validate(self, day, slot, party):
        if type(party) is not int or not 1 <= party <= 6:
            raise ReservationError('For parties larger than six, please contact the restaurant.')
        if slot not in SLOTS:
            raise ReservationError('Choose an offered seating time.')
        try:
            start = datetime.strptime(f'{day} {slot}', '%Y-%m-%d %H:%M').replace(tzinfo=ZONE)
        except ValueError:
            raise ReservationError('Use a valid date in YYYY-MM-DD format.') from None
        now = self.clock().astimezone(ZONE)
        if start <= now:
            raise ReservationError('Please choose a future seating.')
        if start.date() > now.date() + timedelta(days=60):
            raise ReservationError('Reservations open 60 days in advance.')

    @staticmethod
    def free_table(db, day, slot, party):
        occupied = {row[0] for row in db.execute(
            'SELECT table_id FROM bookings WHERE day=? AND slot=? AND cancelled=0', (day, slot))}
        return next((number for number, seats in TABLES if seats >= party and number not in occupied), None)

    def availability(self, day, party):
        result = []
        for slot in SLOTS:
            try:
                self.validate(day, slot, party)
            except ReservationError as error:
                # Earlier seatings today are omitted; invalid inputs still fail.
                if str(error) == 'Please choose a future seating.':
                    continue
                raise
            with self.connect() as db:
                if self.free_table(db, day, slot, party):
                    result.append(slot)
        # Validate even when every seating is already in the past.
        if not result:
            self.validate(day, SLOTS[-1], party)
        return {'date': day, 'party_size': party, 'slots': result, 'timezone': str(ZONE)}

    def book(self, day, slot, party, guest, notes, request_id, confirmed, *, connection=None):
        if not confirmed:
            raise ReservationError('Explicit guest confirmation is required before booking.')
        guest, notes = guest.strip(), notes.strip()
        if not guest or len(guest) > 100 or len(notes) > 500:
            raise ReservationError('Provide a guest name up to 100 characters and notes up to 500 characters.')
        fingerprint = json.dumps([day, slot, party, guest, notes])
        with (self.connect() if connection is None else nullcontext(connection)) as db:
            if connection is None:
                db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT token, fingerprint, cancelled FROM bookings WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                if existing[1] != fingerprint or existing[2]:
                    raise ReservationError('This request ID was already used. Start a new reservation.')
                return self.receipt(existing[0], day, slot, party, guest)
            self.validate(day, slot, party)
            table = self.free_table(db, day, slot, party)
            if table is None:
                raise ReservationError('That seating just filled up. Please check availability again.')
            token = str(uuid4())
            db.execute('INSERT INTO bookings(token,request_id,fingerprint,day,slot,party,table_id,guest,notes) VALUES(?,?,?,?,?,?,?,?,?)',
                       (token, request_id, fingerprint, day, slot, party, table, guest, notes))
        return self.receipt(token, day, slot, party, guest)

    @staticmethod
    def receipt(token, day, slot, party, guest):
        return {'confirmation': token, 'date': day, 'time': slot, 'party_size': party,
                'guest_name': guest, 'timezone': str(ZONE), 'status': 'confirmed',
                'notice': 'Accommodation requests are recorded, not guaranteed. For allergies or accessibility needs, speak with restaurant staff.'}

    def cancel(self, token):
        with self.connect() as db:
            found = db.execute('SELECT token FROM bookings WHERE token=?', (token,)).fetchone()
            if not found:
                raise ReservationError('Reservation not found. Check your confirmation code.')
            db.execute('UPDATE bookings SET cancelled=1 WHERE token=?', (token,))
        return {'status': 'cancelled', 'confirmation': token}
