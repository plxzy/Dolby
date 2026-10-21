import { analyze } from './engine.js';
import { Gate } from './gate.js';

let gate, activeId;
self.addEventListener('message', ({ data }) => {
  if (data.type === 'start') {
    gate?.cancel();
    const { sessionId, file, policy } = data;
    activeId = sessionId;
    gate = new Gate({ policy });
    analyze(file, { sessionId, policy, gate, onReport: report => self.postMessage({ type: 'report', sessionId, report }) }).catch(error => self.postMessage({ type: 'error', sessionId, message: error.message }));
  } else if (data.sessionId === activeId && ['pause', 'resume', 'deeper'].includes(data.type)) gate?.[data.type]();
});
