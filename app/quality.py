"""Offline transcript evaluation and human-review selection; no speech-model calls."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / 'datasets' / 'speech_samples.jsonl'
NORMALIZATION = 'unicode-nfkc-casefold-alphanumeric-v1'


class Sample(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9-]+$', max_length=80)
    split: Literal['development', 'test']
    source: Literal['synthetic']
    language: str = Field(min_length=1, max_length=40)
    condition: str = Field(min_length=1, max_length=40)
    domain: str = Field(min_length=1, max_length=40)
    speaker_group: str = Field(min_length=1, max_length=40)
    reference: str = Field(min_length=1, max_length=2000)
    hypothesis: str = Field(max_length=2000)
    alternate_hypothesis: str = Field(max_length=2000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    model_version: str = Field(min_length=1, max_length=100)


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return ' '.join(''.join(c if c.isalnum() or c.isspace() else ' ' for c in text).split())


def distance(left, right):
    """Levenshtein insertions + deletions + substitutions, each with unit cost."""
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def counts(reference, hypothesis):
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        raise ValueError('References must contain letters or numbers.')
    words = ref.split()
    # Spaces excluded from CER; punctuation and case normalized for both metrics.
    chars = ref.replace(' ', '')
    return {'word_errors': distance(words, hyp.split()), 'reference_words': len(words),
            'character_errors': distance(chars, hyp.replace(' ', '')), 'reference_characters': len(chars)}


def load_samples(path=DEFAULT_DATASET):
    samples = []
    seen = set()
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            sample = Sample.model_validate_json(line).model_dump()
            counts(sample['reference'], sample['hypothesis'])
        except (ValidationError, ValueError) as error:
            raise ValueError(f'Invalid dataset row {number}: {error}') from error
        if sample['id'] in seen:
            raise ValueError(f'Duplicate sample ID: {sample["id"]}')
        seen.add(sample['id'])
        samples.append(sample)
    if not samples:
        raise ValueError('Dataset cannot be empty.')
    return samples


def dataset_hash(samples):
    return hashlib.sha256(json.dumps(samples, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def aggregate(samples):
    total = Counter()
    for sample in samples:
        total.update(counts(sample['reference'], sample['hypothesis']))
    return {'samples': len(samples), **dict(total),
            'wer': total['word_errors'] / total['reference_words'] if samples else None,
            'cer': total['character_errors'] / total['reference_characters'] if samples else None}


def report(samples, minimum_slice_size=3):
    slices = {}
    # Report splits separately. Test examples never enter the review/adaptation pool.
    for field in ('split', 'language', 'condition', 'domain', 'speaker_group'):
        slices[field] = []
        for value in sorted({s[field] for s in samples}):
            group = [s for s in samples if s[field] == value]
            slices[field].append({'value': value, **aggregate(group),
                                  'low_support': len(group) < minimum_slice_size})
    return {'dataset_sha256': dataset_hash(samples), 'normalization': NORMALIZATION,
            'data_kind': 'synthetic-transcript-fixtures', 'has_audio': False,
            'minimum_slice_size': minimum_slice_size,
            'overall': aggregate(samples), 'slices': slices,
            'model_versions': sorted({s['model_version'] for s in samples}),
            'limitations': [
                'Synthetic transcripts only: no audio was recorded, transcribed, or scored.',
                'Confidence values and alternate hypotheses are authored fixtures, not calibrated model outputs.',
                'Slice coverage describes this dataset, not the population of restaurant callers.',
                'Combined development/test metrics are descriptive; use the test slice for a fixed benchmark.',
                'WER and CER do not measure booking correctness or accommodation safety.',
                'No accent, age, gender, or speaker identity is inferred from these examples.',
            ]}


def review_queue(samples, reviewed_ids=(), limit=10):
    pool = [s for s in samples if s['split'] == 'development']
    languages = Counter(s['language'] for s in pool)
    queue = []
    for sample in pool:
        if sample['id'] in reviewed_ids:
            continue
        primary = normalize(sample['hypothesis']).split()
        alternate = normalize(sample['alternate_hypothesis']).split()
        disagreement = distance(primary, alternate) / max(len(primary), len(alternate), 1)
        rarity = 1 / languages[sample['language']]
        uncertainty = 1 - sample['confidence']
        score = .5 * uncertainty + .3 * disagreement + .2 * rarity
        queue.append({**sample, 'priority': round(score, 6),
                      'signals': {'uncertainty': uncertainty, 'disagreement': disagreement,
                                  'language_rarity': rarity},
                      'review_note': 'Text-only review. No audio is available to verify what was spoken.'})
    return sorted(queue, key=lambda item: (-item['priority'], item['id']))[:limit]


class Reviews:
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS transcript_reviews (
                revision INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL,
                dataset_hash TEXT NOT NULL, reference TEXT NOT NULL,
                reviewer TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')

    def all(self, fingerprint):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute('SELECT * FROM transcript_reviews WHERE dataset_hash=? ORDER BY revision',
                              (fingerprint,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def add(self, samples, sample_id, reference, reviewer, reason):
        sample = next((s for s in samples if s['id'] == sample_id), None)
        if sample is None:
            raise ValueError('Sample not found.')
        if sample['split'] != 'development':
            raise ValueError('Held-out test samples cannot be annotated through the review queue.')
        reference, reviewer, reason = reference.strip(), reviewer.strip(), reason.strip()
        if not reference or not reviewer or not reason:
            raise ValueError('Reference, reviewer, and reason are required.')
        counts(reference, sample['hypothesis'])
        fingerprint = dataset_hash(samples)
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute('''INSERT INTO transcript_reviews
                (sample_id,dataset_hash,reference,reviewer,reason,created_at) VALUES(?,?,?,?,?,?)''',
                (sample_id, fingerprint, reference, reviewer, reason, datetime.now(timezone.utc).isoformat()))
            revision = cursor.lastrowid
        return {'sample_id': sample_id, 'revision': revision, 'status': 'text-reviewed',
                'audio_verified': False, 'dataset_sha256': fingerprint}

    def export(self, samples):
        latest = {row['sample_id']: row for row in self.all(dataset_hash(samples))}
        return [{**sample, 'reference': latest[sample['id']]['reference'],
                 'annotation': latest[sample['id']], 'audio_verified': False,
                 'usage': 'synthetic development experiments only'}
                for sample in samples if sample['split'] == 'development' and sample['id'] in latest]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(report(load_samples(args.dataset)), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + '\n')
    else:
        print(result)


if __name__ == '__main__':
    main()
