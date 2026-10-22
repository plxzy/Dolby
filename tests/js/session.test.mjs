import test from 'node:test';
import assert from 'node:assert/strict';
import { startSession } from '../../dolby_tool/web/static/js/browser/session.js';

class TestWorker extends EventTarget {
  static instances = [];
  constructor(url, options) { super(); this.url = url; this.options = options; this.messages = []; this.terminated = false; TestWorker.instances.push(this); }
  postMessage(data) { this.messages.push(data); }
  terminate() { this.terminated = true; }
  receive(data) { this.dispatchEvent(new MessageEvent('message', { data })); }
}

test('session controls retain id, stop terminates, stale reports are ignored', () => {
  const original = globalThis.Worker; globalThis.Worker = TestWorker;
  try {
    const reports = [], session = startSession(new File(['test'], 'test.mp4'), { onReport: r => reports.push(r) });
    const worker = TestWorker.instances.at(-1);
    assert.equal(worker.options.type, 'module');
    assert.ok(worker.url.pathname.endsWith('/browser/worker.js'));
    worker.receive({ type: 'report', sessionId: 'stale', report: { state: 'running' } });
    assert.equal(reports.length, 0);
    const report = { sessionId: session.id, state: 'running', activity: 'Checking' };
    worker.receive({ type: 'report', sessionId: session.id, report });
    session.pause(); session.resume(); session.deeper();
    assert.deepEqual(worker.messages.slice(1).map(m => m.type), ['pause', 'resume', 'deeper']);
    assert.ok(worker.messages.every(m => m.sessionId === session.id));
    session.stop(); assert.equal(worker.terminated, true);
    assert.deepEqual(reports.at(-1).terminal, { reason: 'stopped', message: 'Stopped by you' });
    worker.receive({ type: 'report', sessionId: session.id, report });
    assert.equal(reports.length, 2);
  } finally { globalThis.Worker = original; }
});

test('completed workers are terminated on replacement; worker errors are terminal', () => {
  const original = globalThis.Worker; globalThis.Worker = TestWorker;
  try {
    const session = startSession(new File(['test'], 'test.mp4'), { onReport() {} });
    const worker = TestWorker.instances.at(-1);
    worker.receive({ type: 'report', sessionId: session.id, report: { state: 'done' } });
    session.stop(); assert.equal(worker.terminated, true);
    const reports = [], failed = startSession(new File(['test'], 'test.mp4'), { onReport: r => reports.push(r) });
    const second = TestWorker.instances.at(-1);
    const event = new Event('error'); event.message = 'Broken worker'; second.dispatchEvent(event);
    assert.equal(reports.at(-1).terminal.reason, 'failed');
    assert.equal(reports.at(-1).terminal.message, 'Broken worker');
    assert.equal(second.terminated, true); failed.stop();
  } finally { globalThis.Worker = original; }
});
