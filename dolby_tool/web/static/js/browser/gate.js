import { POLICY } from './policy.js';

export class CancelledError extends Error {
  constructor() { super('Analysis cancelled'); this.name = 'CancelledError'; }
}

export class Gate {
  constructor({ policy = {}, onState = () => {} } = {}) {
    this.policy = { ...POLICY, ...policy };
    this.onState = onState;
    this.state = 'running';
    this.limitBytes = this.policy.autoBytes;
    this.limitMs = this.policy.autoMs;
    this.bytesSinceStart = 0;
    this.elapsed = 0;
    this.started = performance.now();
    this.waiters = [];
    this.cancelled = false;
  }
  get activeMs() { return this.elapsed + (this.state === 'running' ? performance.now() - this.started : 0); }
  transition(state) {
    if (state === this.state) return;
    this.elapsed = this.activeMs;
    this.state = state;
    if (state === 'running') this.started = performance.now();
    this.onState(state);
    if (state === 'running' || this.cancelled) this.waiters.splice(0).forEach(resolve => resolve());
  }
  async admit(bytes) {
    for (;;) {
      if (this.cancelled) throw new CancelledError();
      if (this.state === 'running') {
        if (this.bytesSinceStart + bytes <= this.limitBytes && this.activeMs <= this.limitMs) {
          this.bytesSinceStart += bytes;
          return;
        }
        this.transition('paused-at-limit');
        continue;
      }
      await new Promise(resolve => this.waiters.push(resolve));
    }
  }
  pause() { if (!this.cancelled && this.state === 'running') this.transition('paused'); }
  resume() { if (!this.cancelled) this.transition('running'); }
  deeper() {
    this.limitBytes += this.policy.deeperBytes;
    this.limitMs += this.policy.deeperMs;
    this.resume();
  }
  cancel() { this.cancelled = true; this.waiters.splice(0).forEach(resolve => resolve()); }
}
