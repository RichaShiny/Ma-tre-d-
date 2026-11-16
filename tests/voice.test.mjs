import assert from 'node:assert/strict';
import test from 'node:test';
import {createVoice} from '../app/voice.mjs';

function fixture() {
  let recognition;
  const events = [];
  class Recognition {
    constructor() { recognition = this; }
    start() { this.onstart(); }
    stop() { events.push('stop'); }
    abort() { events.push('abort'); }
  }
  const browser = {
    webkitSpeechRecognition: Recognition,
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    speechSynthesis: {speak: utterance => events.push(utterance.text), cancel: () => events.push('cancel')},
  };
  const voice = createVoice(browser, {
    onTranscript: text => events.push(['draft', text]),
    onStatus: (...args) => events.push(args),
    onPhase: phase => events.push(phase),
  });
  return {voice, events, get recognition() { return recognition; }};
}

test('stopping waits for final text and only updates the draft', () => {
  const f = fixture();
  f.voice.start();
  f.voice.stop();
  f.recognition.onresult({results: [
    Object.assign([{transcript: 'partial'}], {isFinal: false}),
    Object.assign([{transcript: ' A table for four ' }], {isFinal: true}),
  ]});
  f.recognition.onend();
  assert.deepEqual(f.events.filter(event => Array.isArray(event) && event[0] === 'draft'),
    [['draft', 'A table for four']]);
  assert.ok(f.events.includes('finishing'));
  assert.equal(f.events.at(-1), 'idle');
});

test('starting the mic interrupts playback and abort ignores late results', () => {
  const f = fixture();
  f.voice.speak('Welcome');
  f.voice.start();
  f.voice.speak('This should not play');
  f.voice.abort();
  f.recognition.onresult({results: [Object.assign([{transcript: 'late'}], {isFinal: true})]});
  assert.deepEqual(f.events.slice(0, 3), ['Welcome', 'cancel', 'listening']);
  assert.ok(f.events.includes('abort'));
  assert.ok(!f.events.some(event => event?.[0] === 'draft' || event === 'This should not play'));
});

test('permission denial leaves microphone idle and allows another attempt', () => {
  const f = fixture();
  f.voice.start();
  const first = f.recognition;
  first.onerror({error: 'not-allowed'});
  first.onend();
  assert.equal(f.events.at(-1), 'idle');
  assert.ok(f.events.some(event => Array.isArray(event) && event[0].includes('denied')));
  f.voice.start();
  assert.notEqual(f.recognition, first);
});

test('unsupported browsers can initialize without touching speech features', () => {
  const voice = createVoice({}, {});
  assert.equal(voice.canListen, false);
  assert.equal(voice.canSpeak, false);
  voice.start();
  voice.speak('Welcome');
});
