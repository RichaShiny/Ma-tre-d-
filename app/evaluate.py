"""Run deterministic conversation scenarios without network calls or real guests."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.conversation import Conversations
from app.reservations import Reservations
from app.models import configured_provider

DEFAULT_SCENARIOS = Path(__file__).resolve().parent.parent / 'datasets' / 'conversation_scenarios.json'


def evaluate(path=DEFAULT_SCENARIOS, provider=None):
    manifest = json.loads(Path(path).read_text())
    reference = datetime.fromisoformat(manifest['reference_time'])
    if reference.tzinfo is None:
        raise ValueError('Scenario reference time must include a timezone.')
    results = []
    for scenario in manifest['scenarios']:
        failures = []
        with TemporaryDirectory(prefix='maitre-eval-') as folder:
            service = Reservations(Path(folder) / 'eval.sqlite3', clock=lambda: reference)
            host = Conversations(service, provider)
            state = host.create()
            for index, step in enumerate(scenario['steps'], 1):
                try:
                    if step.get('confirm'):
                        state = host.turn(state['session_id'], str(uuid4()), confirm_revision=state['revision'])
                    else:
                        state = host.turn(state['session_id'], str(uuid4()), step['text'])
                    if state['status'] != step['status']:
                        failures.append(f'Step {index}: expected {step["status"]}, got {state["status"]}')
                    for key, expected in step.get('draft', {}).items():
                        if state['draft'].get(key) != expected:
                            failures.append(f'Step {index}: incorrect {key}')
                    for key in step.get('absent', []):
                        if key in state['draft']:
                            failures.append(f'Step {index}: {key} should require clarification')
                    if step.get('reply_contains', '').casefold() not in state['messages'][-1]['text'].casefold():
                        failures.append(f'Step {index}: required reply notice missing')
                    with service.connect() as db:
                        bookings = db.execute('SELECT count(*) FROM bookings').fetchone()[0]
                    if bookings != int(state['status'] == 'booked'):
                        failures.append(f'Step {index}: conversation/booking state mismatch')
                except Exception as error:
                    failures.append(f'Step {index}: {type(error).__name__}: {error}')
                    break
        results.append({'id': scenario['id'], 'passed': not failures, 'failures': failures})
    return {'suite_version': manifest['version'], 'reference_time': reference.isoformat(),
            'provider': provider.name if provider else 'local-parser-v1',
            'model_requests': getattr(provider, 'outcomes', {}),
            'scope': 'synthetic text scenarios; no voice benchmark',
            'passed': sum(item['passed'] for item in results), 'total': len(results), 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios', type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--configured-model', action='store_true', help='Use the model configured in the environment.')
    args = parser.parse_args()
    provider = configured_provider() if args.configured_model else None
    if args.configured_model and provider is None:
        parser.error('--configured-model requires MAITRE_MODEL')
    result = evaluate(args.scenarios, provider)
    content = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end='')
    raise SystemExit(0 if result['passed'] == result['total'] and result['total'] else 1)


if __name__ == '__main__':
    main()
