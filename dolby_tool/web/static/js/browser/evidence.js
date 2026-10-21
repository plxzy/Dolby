export const STATES = Object.freeze(['checking', 'signaled', 'observed', 'not_found_in_scope', 'absent', 'unknown', 'unsupported', 'conflicting']);
export const VIDEO_FIELDS = Object.freeze(['video.dolby_vision', 'video.hdr10.mdcv', 'video.hdr10.cll', 'video.hdr10_plus', 'video.transfer']);
export const positive = finding => finding.state === 'signaled' || finding.state === 'observed';

export class EvidenceStore {
  constructor() { this.fields = new Map(); this.terminal = false; }
  field(id) {
    if (!this.fields.has(id)) this.fields.set(id, { evidence: [], limitations: [], next: null });
    return this.fields.get(id);
  }
  add(id, entry) {
    if (!['signaled', 'observed', 'not_found_in_scope', 'absent', 'unsupported'].includes(entry.kind)) throw new TypeError('Invalid evidence kind');
    this.field(id).evidence.push({ ranges: [], coverage: null, note: '', ...entry });
  }
  limit(id, text) { const field = this.field(id); if (!field.limitations.includes(text)) field.limitations.push(text); }
  setNext(id, text) { this.field(id).next = text; }
  conclude(id) {
    const { evidence } = this.field(id);
    const positives = evidence.filter(e => e.value && (e.kind === 'signaled' || e.kind === 'observed'));
    const absent = evidence.find(e => e.kind === 'absent');
    let state, value = null, selected;
    if (positives.length && absent) { state = 'conflicting'; selected = positives[0]; }
    else if (positives.length) {
      selected = positives.findLast(e => e.kind === 'observed') || positives.at(-1);
      state = selected.kind;
      // Keep the structural configuration when packet evidence only supplies a boolean.
      value = positives.find(e => typeof e.value === 'object')?.value ?? selected.value;
    } else if (absent) { state = 'absent'; value = false; selected = absent; }
    else if ((selected = evidence.findLast(e => e.kind === 'not_found_in_scope'))) state = 'not_found_in_scope';
    else if ((selected = evidence.findLast(e => e.kind === 'unsupported'))) state = 'unsupported';
    else state = this.terminal ? 'unknown' : 'checking';
    return { value, state, summary: state === 'conflicting' ? 'Positive stream evidence conflicts with an absence in the configuration' : selected?.note || selected?.coverage || (this.terminal ? 'No conclusive evidence' : 'Checking available evidence') };
  }
  snapshot() {
    return Object.fromEntries([...this.fields].map(([id, field]) => [id, { ...this.conclude(id), evidence: [...field.evidence], limitations: [...field.limitations], next: field.next }]));
  }
}
