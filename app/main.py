import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.reservations import Reservations, ReservationError, ZONE


def create_app(database=None, clock=None):
    app = FastAPI(title='Maître d’', version='0.1.0')
    service = Reservations(database or os.getenv('DATABASE_PATH', 'data/reservations.sqlite3'), clock)

    @app.exception_handler(ReservationError)
    async def reservation_error(request: Request, error: ReservationError):
        return JSONResponse(status_code=409, content={'detail': str(error)})

    @app.get('/')
    def home():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'local-demo'}

    @app.get('/api/restaurant')
    def restaurant():
        return {'name': 'Juniper & Rye', 'timezone': str(ZONE),
                'today': (clock() if clock else datetime.now(ZONE)).astimezone(ZONE).date().isoformat(),
                'booking_window_days': 60, 'mode': 'local-demo'}

    @app.get('/api/availability')
    def availability(date: str, party_size: int):
        return service.availability(date, party_size)

    class Booking(BaseModel):
        date: str
        time: str
        party_size: int = Field(strict=True, ge=1, le=6)
        guest_name: str = Field(min_length=1, max_length=100)
        notes: str = Field(default='', max_length=500)
        request_id: UUID
        confirmed: bool = Field(strict=True)

    @app.post('/api/reservations')
    def book(body: Booking):
        return service.book(body.date, body.time, body.party_size, body.guest_name,
                            body.notes, str(body.request_id), body.confirmed)

    @app.delete('/api/reservations/{confirmation}')
    def cancel(confirmation: UUID):
        return service.cancel(str(confirmation))

    return app


app = create_app()
