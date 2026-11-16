// Voice writes drafts only. The host owns all submissions and confirmation.
export function createVoice(browser, {onTranscript, onStatus, onPhase}) {
  const Recognition = browser.SpeechRecognition || browser.webkitSpeechRecognition;
  const synthesis = browser.speechSynthesis;
  let session = null;
  let utterance = null;
  const canSpeak = !!(synthesis && browser.SpeechSynthesisUtterance);

  function silence() {
    if (!utterance) return;
    utterance = null;
    synthesis.cancel();
  }

  function abort() {
    if (!session) return;
    const active = session;
    session = null;
    active.abort();
    onPhase('idle');
  }

  function start() {
    if (!Recognition || session) return;
    silence();
    const active = new Recognition();
    session = active;
    active.lang = 'en-US';
    active.continuous = false;
    active.interimResults = false;
    let captured = false;
    let failed = false;
    active.onstart = () => {
      if (session === active) onStatus('Listening…');
    };
    active.onresult = event => {
      if (session !== active || failed) return;
      const text = Array.from(event.results).filter(result => result.isFinal)
        .map(result => result[0].transcript.trim()).filter(Boolean).join(' ');
      if (!text) return;
      captured = true;
      onTranscript(text);
      onStatus('Review your transcript before sending.');
    };
    active.onerror = event => {
      if (session !== active) return;
      failed = true;
      const messages = {
        'not-allowed': 'Microphone permission was denied. You can still type.',
        'audio-capture': 'No microphone was available. You can still type.',
        'network': 'Speech recognition could not connect. You can still type.',
        'no-speech': 'No speech detected. Try again or type.',
      };
      onStatus(messages[event.error] || 'Speech recognition stopped. Try again or type.', true);
    };
    active.onend = () => {
      if (session !== active) return;
      session = null;
      onPhase('idle');
      if (!captured && !failed) onStatus('No transcript received. Try again or type.');
    };
    onPhase('listening');
    onStatus('Starting microphone…');
    try {
      active.start();
    } catch {
      session = null;
      onPhase('idle');
      onStatus('Could not start the microphone. You can still type.', true);
    }
  }

  function stop() {
    if (!session) return;
    onPhase('finishing');
    onStatus('Finishing transcription…');
    try {
      session.stop();
    } catch {
      abort();
      onStatus('Could not finish transcription. Try again or type.', true);
    }
  }

  function speak(text) {
    if (!canSpeak || session || !text) return;
    silence();
    const active = new browser.SpeechSynthesisUtterance(text);
    active.lang = 'en-US';
    utterance = active;
    active.onend = active.onerror = () => {
      if (utterance === active) utterance = null;
    };
    synthesis.speak(active);
  }

  return {canListen: !!Recognition, canSpeak, start, stop, abort, speak, silence};
}
