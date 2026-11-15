from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.quality import (aggregate, counts, dataset_hash, distance, load_samples,
                         normalize, report, review_queue, Reviews)


def test_known_edit_counts_and_normalization():
    assert normalize('  CAFÉ, Table!  ') == 'café table'
    assert distance(['a', 'b'], ['a', 'c', 'd']) == 2
    assert counts('table for four', 'table for two')['word_errors'] == 1
    assert counts('table for four', '')['word_errors'] == 3
    assert counts('a', 'a b c')['word_errors'] == 2  # WER may exceed 1.
    assert counts('Hi!', 'hi')['character_errors'] == 0
    with pytest.raises(ValueError):
        counts('!!!', 'hello')


def test_corpus_wer_is_weighted_by_reference_words():
    result = aggregate([{'reference': 'a', 'hypothesis': 'b'},
                        {'reference': 'a b c d', 'hypothesis': 'a b c d'}])
    assert result['wer'] == .2  # Not the per-sample average, .5.
    assert aggregate([])['wer'] is None


def test_report_is_reproducible_and_explicitly_synthetic():
    samples = load_samples()
    first = report(samples)
    assert first == report(load_samples())
    assert first['has_audio'] is False
    assert first['overall']['samples'] == 12
    assert first['dataset_sha256'] == dataset_hash(samples)
    assert sum(s['samples'] for s in first['slices']['split']) == 12
    assert any(s['low_support'] for s in first['slices']['language'])


def test_selection_is_label_free_deterministic_and_excludes_test():
    samples = load_samples()
    queue = review_queue(samples)
    assert all(s['split'] == 'development' for s in queue)
    assert queue == review_queue(list(reversed(samples)))
    assert queue[0]['priority'] >= queue[-1]['priority']
    changed = deepcopy(samples)
    for item in changed:
        item['reference'] = 'different hidden label'
    assert [s['id'] for s in queue] == [s['id'] for s in review_queue(changed)]
    assert queue[0]['id'] not in {s['id'] for s in review_queue(samples, [queue[0]['id']])}


def test_human_review_is_audited_and_does_not_change_test(tmp_path):
    samples = load_samples()
    reviews = Reviews(tmp_path / 'reviews.sqlite3')
    before = report(samples)
    reviews.add(samples, 'mixed-1', 'a table para cuatro mañana', 'reviewer-a', 'Text correction')
    reviews.add(samples, 'mixed-1', 'a table para cuatro mañana', 'reviewer-b', 'Second text review')
    assert len(reviews.all(dataset_hash(samples))) == 2
    exported = reviews.export(samples)
    assert len(exported) == 1
    assert exported[0]['annotation']['reviewer'] == 'reviewer-b'
    assert exported[0]['audio_verified'] is False
    assert exported[0]['split'] == 'development'
    assert report(samples) == before
    with pytest.raises(ValueError, match='Held-out'):
        reviews.add(samples, 'test-en-1', 'new text', 'reviewer', 'reason')
    with pytest.raises(ValueError, match='not found'):
        reviews.add(samples, 'missing', 'new text', 'reviewer', 'reason')
    with pytest.raises(ValueError):
        reviews.add(samples, 'mixed-1', '!!!', 'reviewer', 'reason')


def test_reviews_are_scoped_to_dataset_version(tmp_path):
    samples = load_samples()
    reviews = Reviews(tmp_path / 'reviews.sqlite3')
    reviews.add(samples, 'mixed-1', 'a table', 'reviewer', 'reason')
    changed = deepcopy(samples)
    changed[0]['model_version'] = 'another-fixture-version'
    assert reviews.export(changed) == []


def test_dataset_validation_rejects_duplicates_invalid_confidence_and_empty_reference(tmp_path):
    sample = load_samples()[0]
    path = tmp_path / 'samples.jsonl'
    path.write_text(json.dumps(sample) + '\n' + json.dumps(sample))
    with pytest.raises(ValueError, match='Duplicate'):
        load_samples(path)
    for update in ({'confidence': 1.2}, {'reference': '!!!'}, {'unexpected_field': True}):
        path.write_text(json.dumps({**sample, **update}))
        with pytest.raises(ValueError, match='Invalid dataset row'):
            load_samples(path)
    path.write_text('')
    with pytest.raises(ValueError, match='empty'):
        load_samples(path)


def test_quality_api_review_export_and_test_protection(tmp_path):
    client = TestClient(create_app(tmp_path / 'api.sqlite3'))
    assert client.get('/quality').status_code == 200
    initial = client.get('/api/quality/report').json()
    first = client.get('/api/quality/queue').json()['items'][0]
    payload = {'reference': first['reference'], 'reviewer': 'demo-reviewer', 'reason': 'Text reviewed'}
    result = client.post('/api/quality/reviews/' + first['id'], json=payload)
    assert result.status_code == 200
    assert result.json()['audio_verified'] is False
    assert first['id'] not in {s['id'] for s in client.get('/api/quality/queue').json()['items']}
    assert client.post('/api/quality/reviews/test-en-1', json=payload).status_code == 422
    assert client.post('/api/quality/reviews/missing', json=payload).status_code == 422
    assert client.post('/api/quality/reviews/' + first['id'], json={**payload, 'reviewer': ' '}).status_code == 422
    export = client.get('/api/quality/export')
    assert export.headers['content-type'].startswith('application/x-ndjson')
    rows = [json.loads(line) for line in export.text.splitlines()]
    assert len(rows) == 1
    assert all(row['split'] == 'development' for row in rows)
    assert client.get('/api/quality/report').json() == initial
