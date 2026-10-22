/** One worker at a time, including while paused: memory is bounded as well as CPU. */
import { startSession } from './session.js';

export class CompareSessions {
  constructor({ onChange, start = startSession }) {
    this.onChange = onChange;
    this.start = start;
    this.items = [];
    this.active = null;
    this.hostHidden = false;
  }
  add(file) {
    const item = { id: crypto.randomUUID(), file, report: null, session: null, requested: false, userPaused: false, hostPaused: false };
    this.items.push(item);
    return item;
  }
  run() {
    for (const item of this.items) if (!item.report?.terminal) item.requested = true;
    this.pump();
  }
  pump() {
    if (this.active || this.hostHidden) return;
    const item = this.items.find(i => i.requested && !i.report?.terminal);
    if (!item) return;
    this.active = item;
    try {
      item.session = this.start(item.file, { onReport: report => {
        if (!this.items.includes(item)) return;
        item.report = report;
        if (report.terminal) {
          this.active = null;
          item.session = null;
          queueMicrotask(() => this.pump());
        }
        this.onChange(item);
      } });
    } catch (error) {
      item.report = { state: 'done', terminal: { reason: 'failed', message: error.message }, activity: error.message };
      this.active = null;
      queueMicrotask(() => this.pump());
    }
    this.onChange(item);
  }
  command(item, command) {
    if (!item.session || item.report?.terminal || (this.hostHidden && command !== 'stop')) return;
    item.userPaused = command === 'pause';
    item.hostPaused = false;
    item.session[command]();
  }
  remove(item) {
    this.items = this.items.filter(i => i !== item);
    item.session?.stop();
    if (this.active === item) this.active = null;
    this.pump();
  }
  clear() {
    const items = this.items;
    this.items = [];
    this.active = null;
    for (const item of items) item.session?.stop();
  }
  pauseFromHost() {
    this.hostHidden = true;
    for (const item of this.items) {
      if (!item.session || item.userPaused || (item.report && item.report.state !== 'running')) continue;
      item.hostPaused = true;
      item.session.pause();
    }
    this.onChange();
  }
  resumeFromHost() {
    this.hostHidden = false;
    for (const item of this.items) {
      if (item.session && item.hostPaused && !item.userPaused) {
        item.hostPaused = false;
        // Never resume a threshold pause without the visitor choosing Continue deeper.
        if (!item.report || item.report.state === 'paused' || item.report.state === 'running') item.session.resume();
      }
    }
    this.pump();
    this.onChange();
  }
}
