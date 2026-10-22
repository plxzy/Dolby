export function startSession(file, { onReport }) {
  const id = crypto.randomUUID();
  const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  let last = { sessionId: id, file: { name: file.name, size: file.size, lastModified: file.lastModified }, state: 'running', terminal: null, activity: 'Reading container structure', metrics: { bytesRequested: 0, uniqueBytes: 0, repeatedBytes: 0, readCount: 0, activeMs: 0, limitBytes: 0, limitMs: 0 }, container: { format: null, formatLongName: null, durationS: null, overallBitRateBps: null }, tracks: [], findings: {}, coverage: { video: { samplesInspected: 0, samplesTotal: null, windows: [], videoBytesInspected: 0, videoBytesTotal: null } } };
  let stopped = false;
  const finish = (reason, message) => { if (stopped) return; stopped = true; worker.terminate(); last = { ...last, state: 'done', terminal: { reason, message }, activity: message }; onReport(last); };
  worker.addEventListener('message', ({ data }) => {
    if (stopped || data.sessionId !== id) return;
    if (data.type === 'report') {
      last = data.report;
      if (last.state === 'done') { stopped = true; worker.terminate(); }
      onReport(last);
    }
    else if (data.type === 'error') finish('failed', data.message);
  });
  worker.addEventListener('error', event => finish('failed', event.message || 'Analysis worker failed'));
  worker.postMessage({ type: 'start', sessionId: id, file });
  const send = type => { if (!stopped && last.state !== 'done') worker.postMessage({ type, sessionId: id }); };
  return { id, pause: () => send('pause'), resume: () => send('resume'), deeper: () => send('deeper'), stop: () => finish('stopped', 'Stopped by you') };
}
