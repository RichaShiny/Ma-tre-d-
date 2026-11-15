from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.reservations import Reservations, ReservationError, ZONE

NOW = datetime(2026, 11, 15, 12, tzinfo=ZONE)


@pytest.fixture
def service(tmp_path):
    return Reservations(tmp_path / 'test.sqlite3', clock=lambda: NOW)


def book(service, key=None, **changes):
    data = dict(day='2026-11-16', slot='18:30', party=6, guest='Richa', notes='',
                request_id=key or str(uuid4()), confirmed=True)
    data.update(changes)
    return service.book(**data)


def test_confirmation_and_retry(service):
    with pytest.raises(ReservationError, match='confirmation'):
        book(service, confirmed=False)
    key = str(uuid4())
    first = book(service, key)
    assert book(service, key) == first
    with pytest.raises(ReservationError, match='already used'):
        book(service, key, guest='Someone else')


def test_capacity_cancellation_and_replay(service):
    key = str(uuid4())
    first = book(service, key)
    assert '18:30' not in service.availability('2026-11-16', 6)['slots']
    with pytest.raises(ReservationError, match='filled up'):
        book(service)
    assert service.cancel(first['confirmation'])['status'] == 'cancelled'
    assert service.cancel(first['confirmation'])['status'] == 'cancelled'
    assert '18:30' in service.availability('2026-11-16', 6)['slots']
    with pytest.raises(ReservationError, match='already used'):
        book(service, key)


def test_concurrent_callers_cannot_double_book(service):
    def attempt(_):
        try:
            return book(service)['status']
        except ReservationError:
            return 'full'
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))
    assert outcomes.count('confirmed') == 1
    assert outcomes.count('full') == 7


@pytest.mark.parametrize('changes', [dict(day='2026-11-14'), dict(day='2027-05-01'),
    dict(day='2026-02-30'), dict(slot='19:00'), dict(party=7), dict(party=0), dict(guest=' ')])
def test_invalid_requests(service, changes):
    with pytest.raises(ReservationError):
        book(service, **changes)


def test_accommodation_notice_does_not_promise_safety(service):
    result = book(service, notes='Severe nut allergy; wheelchair access')
    assert 'not guaranteed' in result['notice']
    assert 'speak with restaurant staff' in result['notice']


def test_api_journey(tmp_path):
    client = TestClient(create_app(tmp_path / 'api.sqlite3', clock=lambda: NOW))
    assert client.get('/').status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/api/availability', params={'date': '2026-11-16', 'party_size': 4}).json()['slots']
    payload = dict(date='2026-11-16', time='18:30', party_size=4, guest_name='Richa',
                   request_id=str(uuid4()), confirmed=True)
    response = client.post('/api/reservations', json=payload)
    assert response.status_code == 200
    token = response.json()['confirmation']
    assert client.post('/api/reservations', json=payload).json()['confirmation'] == token
    assert client.delete('/api/reservations/' + token).json()['status'] == 'cancelled'
    assert client.post('/api/reservations', json={**payload, 'confirmed': False}).status_code == 409
    assert client.delete('/api/reservations/not-a-code').status_code == 422
    assert client.post('/api/reservations', json={**payload, 'party_size': 2.5}).status_code == 422
