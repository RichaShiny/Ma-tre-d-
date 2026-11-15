import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.reservations import Reservations, ReservationError, ZONE
from app.conversation import Conversations
from app.quality import load_samples, report, review_queue, dataset_hash, Reviews
import json


def create_app(database=None, clock=None):
    app = FastAPI(title='Maître d’', version='0.2.0')
    service = Reservations(database or os.getenv('DATABASE_PATH', 'data/reservations.sqlite3'), clock)

    conversations = Conversations(service)

    @app.get('/host')
    def host_page():
        return FileResponse(Path(__file__).with_name('host.html'))

    @app.post('/api/conversations')
    def new_conversation():
        return conversations.create()

    @app.get('/api/conversations/{session_id}')
    def get_conversation(session_id: UUID):
        return conversations.get(str(session_id))

    class Turn(BaseModel):
        request_id: UUID
        text: str = Field(default='', max_length=1000)
        confirm_revision: int | None = Field(default=None, strict=True, ge=0)

    @app.post('/api/conversations/{session_id}/turns')
    def conversation_turn(session_id: UUID, body: Turn):
        return conversations.turn(str(session_id), str(body.request_id), body.text, body.confirm_revision)

    samples = load_samples()
    reviews = Reviews(service.path)

    @app.get('/quality')
    def quality_page():
        return FileResponse(Path(__file__).with_name('quality.html'))

    @app.get('/api/quality/report')
    def quality_report():
        return report(samples)

    @app.get('/api/quality/queue')
    def queue():
        reviewed = {row['sample_id'] for row in reviews.all(dataset_hash(samples))}
        return {'items': review_queue(samples, reviewed), 'reviewed_samples': len(reviewed)}

    class Review(BaseModel):
        reference: str = Field(min_length=1, max_length=2000)
        reviewer: str = Field(min_length=1, max_length=80)
        reason: str = Field(min_length=1, max_length=500)

    @app.post('/api/quality/reviews/{sample_id}')
    def save_review(sample_id: str, body: Review):
        try:
            return reviews.add(samples, sample_id, body.reference, body.reviewer, body.reason)
        except ValueError as error:
            return JSONResponse(status_code=422, content={'detail': str(error)})

    @app.get('/api/quality/export')
    def export_reviews():
        rows = reviews.export(samples)
        content = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
        return Response(content, media_type='application/x-ndjson',
                        headers={'Content-Disposition': 'attachment; filename=reviewed-development.jsonl'})

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
