from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.conversation import Conversations, DemoParser, Extraction
from app.main import create_app
from app.reservations import Reservations, ReservationError, ZONE

NOW = datetime(2026, 11, 15, 12, tzinfo=ZONE)


@pytest.fixture
def host(tmp_path):
    return Conversations(Reservations(tmp_path / 'host.sqlite3', clock=lambda: NOW))


def say(host, session, text):
    return host.turn(session, str(uuid4()), text)


def ready(host, party='four'):
    session = host.create()['session_id']
    say(host, session, f'A table for {party} tomorrow at 6:30 pm')
    state = say(host, session, 'My name is Richa')
    assert state['can_confirm']
    return state


def test_no_booking_until_explicit_action_and_safe_confirmation_retry(host):
    state = ready(host)
    session = state['session_id']
    assert state['receipt'] is None
    state = say(host, session, 'yes')
    assert state['status'] == 'ready'
    assert state['receipt'] is None
    key = str(uuid4())
    booked = host.turn(session, key, confirm_revision=state['revision'])
    assert booked['status'] == 'booked'
    assert booked['receipt']['date'] == '2026-11-16'
    assert booked == host.turn(session, key, confirm_revision=state['revision'])
    with host.reservations.connect() as db:
        assert db.execute('SELECT count(*) FROM bookings').fetchone()[0] == 1
    assert host.get(session)['receipt'] == booked['receipt']


def test_changed_details_and_pause_invalidate_previous_confirmation(host):
    state = ready(host)
    changed = say(host, state['session_id'], 'Make that five')
    assert changed['draft']['party_size'] == 5
    with pytest.raises(ReservationError, match='changed'):
        host.turn(state['session_id'], str(uuid4()), confirm_revision=state['revision'])
    paused = say(host, state['session_id'], "Don't book")
    assert paused['status'] == 'collecting'
    with pytest.raises(ReservationError):
        host.turn(state['session_id'], str(uuid4()), confirm_revision=paused['revision'])


def test_ambiguous_weekday_and_time_clear_old_fields(host):
    state = ready(host)
    state = say(host, state['session_id'], 'Next Friday at seven')
    assert state['status'] == 'collecting'
    assert 'date' not in state['draft'] and 'time' not in state['draft']
    assert '2026-11-20' in state['messages'][-1]['text']
    assert '2026-11-27' in state['messages'][-1]['text']
    state = say(host, state['session_id'], '2026-11-20 at 8 pm')
    assert state['can_confirm']


def test_multiple_dates_require_clarification(host):
    state = ready(host)
    state = say(host, state['session_id'], '2026-11-20 or 2026-11-21')
    assert not state['can_confirm']
    assert 'date' not in state['draft']


def test_handoff_does_not_claim_connected_or_booked(host):
    state = ready(host)
    state = say(host, state['session_id'], 'I want a human')
    assert state['status'] == 'handoff'
    assert state['handoff_connected'] is False
    assert state['receipt'] is None
    assert 'no transfer has been made' in state['messages'][-1]['text']


def test_accommodation_is_recorded_without_guarantee(host):
    state = ready(host)
    state = say(host, state['session_id'], 'One guest has a severe nut allergy')
    assert state['draft']['notes'] == 'One guest has a severe nut allergy'
    assert 'not guaranteed' in state['messages'][-1]['text']


def test_capacity_rechecked_after_review(host):
    first = ready(host, 'six')
    second = ready(host, 'six')
    assert host.turn(first['session_id'], str(uuid4()), confirm_revision=first['revision'])['status'] == 'booked'
    response = host.turn(second['session_id'], str(uuid4()), confirm_revision=second['revision'])
    assert response['status'] == 'collecting'
    assert response['receipt'] is None
    assert 'No booking was made' in response['messages'][-1]['text']


def test_concurrent_confirmations_create_one_booking(host):
    state = ready(host)
    def confirm(_):
        try:
            return host.turn(state['session_id'], str(uuid4()), confirm_revision=state['revision'])['status']
        except ReservationError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(confirm, range(4)))
    assert results.count('booked') == 1
    with host.reservations.connect() as db:
        assert db.execute('SELECT count(*) FROM bookings').fetchone()[0] == 1


def test_sessions_persist_across_service_restart(host):
    state = ready(host)
    restarted = Conversations(Reservations(host.reservations.path, clock=lambda: NOW))
    assert restarted.get(state['session_id']) == state


def test_request_id_reuse_with_different_content_is_rejected(host):
    session = host.create()['session_id']
    key = str(uuid4())
    host.turn(session, key, 'Tomorrow')
    with pytest.raises(ReservationError, match='different content'):
        host.turn(session, key, 'Today')


def test_invalid_or_unavailable_seatings_cannot_be_confirmed(host):
    state = ready(host)
    for text in ('2026-02-30', '2026-11-14', '2027-06-01', 'tomorrow at 7 pm', 'A table for twelve'):
        state = say(host, state['session_id'], text)
        assert not state['can_confirm']


def test_restaurant_timezone_controls_relative_dates(tmp_path):
    clock = lambda: datetime(2026, 11, 16, 2, tzinfo=timezone.utc)  # Still Nov 15 in NYC.
    host = Conversations(Reservations(tmp_path / 'zone.sqlite3', clock=clock))
    session = host.create()['session_id']
    assert say(host, session, 'Tomorrow')['draft']['date'] == '2026-11-16'


def test_provider_interface_only_proposes_fields(tmp_path):
    class StubProvider:
        name = 'test-provider'
        def extract(self, text, now):
            return Extraction(date='2026-11-16', time='18:30', party_size=4, guest_name='Demo')
    host = Conversations(Reservations(tmp_path / 'provider.sqlite3', clock=lambda: NOW), StubProvider())
    state = say(host, host.create()['session_id'], 'propose details')
    assert state['status'] == 'ready'
    assert state['receipt'] is None
    assert state['provider'] == 'test-provider'


def test_chat_api_bounds_and_booking_journey(tmp_path):
    client = TestClient(create_app(tmp_path / 'api.sqlite3', clock=lambda: NOW))
    assert client.get('/host').status_code == 200
    session = client.post('/api/conversations').json()['session_id']
    path = f'/api/conversations/{session}/turns'
    def post(**body):
        return client.post(path, json={'request_id': str(uuid4()), **body})
    assert post(text=' ').status_code == 409
    assert post(text='x' * 1001).status_code == 422
    assert post(text='hello', confirm_revision=0).status_code == 409
    assert post(confirm_revision=True).status_code == 422
    post(text='A table for four tomorrow at 6:30 pm')
    response = post(text='My name is Richa').json()
    booked = post(confirm_revision=response['revision'])
    assert booked.json()['status'] == 'booked'
    assert client.get(f'/api/conversations/{session}').json()['status'] == 'booked'


def test_offline_conversation_scenario_suite():
    from app.evaluate import evaluate
    result = evaluate()
    assert result['total'] == 10
    assert result['passed'] == result['total'], result['results']


@pytest.mark.parametrize('text,field', [('today or tomorrow', 'date'), ('not tomorrow','date'),
    ('next week', 'date'), ('at 5 pm or 8 pm', 'time'), ('at noon', 'time'),
    ('A table for four or five', 'party_size')])
def test_ambiguous_edits_never_reuse_old_details(host, text, field):
    state = ready(host)
    edited = say(host, state['session_id'], text)
    assert not edited['can_confirm']
    assert field not in edited['draft']


def test_host_voice_module_is_served_to_browser(tmp_path):
    client = TestClient(create_app(tmp_path / 'api.sqlite3'))
    page = client.get('/host')
    assert '/assets/voice.mjs' in page.text
    asset = client.get('/assets/voice.mjs')
    assert asset.status_code == 200
    assert asset.headers['content-type'].startswith('text/javascript')
