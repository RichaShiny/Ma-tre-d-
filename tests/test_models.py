from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import json
from threading import Event
from uuid import uuid4

import pytest

from app.conversation import Conversations
from app.models import OllamaExtractor
from app.reservations import Reservations, ZONE

NOW = datetime(2026, 11, 15, 12, tzinfo=ZONE)


def reply(fields):
    return BytesIO(json.dumps({'done': True, 'message': {'content': json.dumps(fields)}}).encode())


def test_model_fills_missing_fields_without_overriding_local_facts(tmp_path):
    def transport(request, timeout):
        body = json.loads(request.data)
        assert 'tools' not in body
        assert body['format']['additionalProperties'] is False
        assert body['stream'] is False
        return reply({'party_size': 6, 'guest_name': 'Richa'})
    provider = OllamaExtractor('test', transport=transport)
    service = Reservations(tmp_path / 'demo.sqlite3', clock=lambda: NOW)
    host = Conversations(service, provider)
    session = host.create()['session_id']
    result = host.turn(session, str(uuid4()), 'A table for four tomorrow at 6:30 pm. Book it for Richa.')
    assert result['status'] == 'ready'
    assert result['draft']['party_size'] == 4
    assert result['draft']['guest_name'] == 'Richa'
    assert result['receipt'] is None


@pytest.mark.parametrize('fields', [{'party_size': '4'}, {'confirm': True}, {'date': '2026-02-30'}])
def test_invalid_model_output_falls_back(fields):
    provider = OllamaExtractor('test', transport=lambda *a, **kw: reply(fields))
    result = provider.extract('A table for two tomorrow', NOW)
    assert result.party_size == 2
    assert result.date == '2026-11-16'
    assert result.time is None


def test_timeout_falls_back_without_exposing_message(caplog):
    def transport(*a, **kw):
        raise TimeoutError('secret upstream details')
    with caplog.at_level('INFO', logger='uvicorn.error'):
        result = OllamaExtractor('test', transport=transport).extract('My name is Richa', NOW)
    assert result.guest_name == 'Richa'
    assert 'latency_ms=' in caplog.text and 'outcome=fallback' in caplog.text
    assert 'Richa' not in caplog.text and 'secret' not in caplog.text


def test_clarification_skips_model():
    def unexpected(*a, **kw):
        pytest.fail('Should not call model')
    result = OllamaExtractor('test', transport=unexpected).extract('Next Friday at seven', NOW)
    assert result.clear_date and result.clear_time


def test_model_wait_does_not_block_other_database_writes(tmp_path):
    started, release = Event(), Event()

    def transport(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return reply({})

    service = Reservations(tmp_path / 'demo.sqlite3', clock=lambda: NOW)
    host = Conversations(service, OllamaExtractor('test', transport=transport))
    session = host.create()['session_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(host.turn, session, str(uuid4()), 'Tomorrow')
        try:
            assert started.wait(2)
            other = pool.submit(host.create)
            assert other.result(timeout=2)['session_id'] != session
        finally:
            release.set()
        assert slow.result(timeout=2)['draft']['date'] == '2026-11-16'
