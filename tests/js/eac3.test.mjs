import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectEac3Sample } from '../../dolby_tool/web/static/js/browser/parsers/eac3.js';

// Syntax-only fixtures: BSI plus zero padding/CRC, not encoded playable audio.
function frame({ joc = false, type = 0, acmod = 2, blocks = 3, reduced = false,
  options = false, mixdef = 0, add = null, bsid = 16, size = 128 } = {}) {
  const bytes = new Uint8Array(size);
  let bit = 0;
  const put = (value, count) => {
    for (let i = count - 1; i >= 0; i--, bit++) bytes[bit >> 3] |= ((value >> i) & 1) << (7 - (bit & 7));
  };
  put(0x0b77, 16); put(type, 2); put(3, 3); put(size / 2 - 1, 11);
  put(reduced ? 3 : 0, 2); put(reduced ? 0 : blocks, 2);
  put(acmod, 3); put(options ? 1 : 0, 1); put(bsid, 5);
  const programs = acmod === 0 ? 2 : 1;
  for (let i = 0; i < programs; i++) { put(0, 5); put(options ? 1 : 0, 1); if (options) put(0x81, 8); }
  if (type === 1) { put(options ? 1 : 0, 1); if (options) put(0xa000, 16); }
  put(options ? 1 : 0, 1);
  if (options) {
    if (acmod > 2) { put(0, 2); if (acmod & 1) put(0, 6); if (acmod & 4) put(0, 6); }
    put(1, 1); put(0, 5); // lfemixlevcode/lfemixlevcod
    if (type === 0) {
      for (let i = 0; i < programs; i++) { put(1, 1); put(0, 6); }
      put(1, 1); put(0, 6); put(mixdef, 2);
      if (mixdef === 1) put(0, 5);
      if (mixdef === 2) put(0, 12);
      if (mixdef === 3) { put(0, 5); put(0, 16); }
      if (acmod < 2) for (let i = 0; i < programs; i++) { put(1, 1); put(0, 14); }
      put(1, 1);
      const count = reduced ? 6 : [1, 2, 3, 6][blocks];
      for (let i = 0; i < count; i++) { if (count !== 1) put(1, 1); put(0, 5); }
    }
  }
  put(options ? 1 : 0, 1);
  if (options) {
    put(0, 5); if (acmod === 2) put(0, 4); if (acmod >= 6) put(0, 2);
    for (let i = 0; i < programs; i++) { put(1, 1); put(0, 8); }
    if (!reduced) put(0, 1);
  }
  if (type === 0 && !reduced && blocks !== 3) put(1, 1);
  if (type === 2) { if (!reduced && blocks !== 3) put(1, 1); put(0, 6); }
  const additional = add ?? (joc ? [1, 16] : []);
  put(additional.length ? 1 : 0, 1);
  if (additional.length) { put(additional.length - 1, 6); for (const byte of additional) put(byte, 8); }
  assert.ok(bit <= (size - 2) * 8);
  return bytes;
}
const join = (...arrays) => Uint8Array.from(arrays.flatMap(a => [...a]));

test('JOC requires bounded addbsi extension type A, not text', () => {
  assert.deepEqual(inspectEac3Sample(frame({ joc: true })), { joc: true, frames: 1, complete: true });
  assert.deepEqual(inspectEac3Sample(frame()), { joc: false, frames: 1, complete: true });
  assert.deepEqual(inspectEac3Sample(frame({ add: [0, 16, 1] })), { joc: false, frames: 1, complete: true });
  const ascii = new TextEncoder().encode('EAC3 JOC Atmos');
  assert.equal(inspectEac3Sample(ascii).complete, false);
  const plain = frame(); plain.set(ascii, 64);
  assert.equal(inspectEac3Sample(plain).joc, false);
  assert.equal(inspectEac3Sample(frame({ add: [1] })).complete, false);
});

test('walks independent, dependent and converted frames with BSI options', () => {
  for (const type of [0, 1, 2]) for (const acmod of [0, 1, 2, 3, 4, 5, 6, 7]) {
    for (const blocks of [0, 1, 2, 3]) for (const mixdef of [0, 1, 2, 3]) {
      const bytes = frame({ type, acmod, blocks, mixdef, options: true, joc: true });
      assert.deepEqual(inspectEac3Sample(bytes), { joc: true, frames: 1, complete: true });
    }
    assert.equal(inspectEac3Sample(frame({ type, acmod, reduced: true, options: true, joc: true })).joc, true);
  }
  assert.deepEqual(inspectEac3Sample(join(frame(), frame({ type: 1, joc: true }), frame())),
    { joc: true, frames: 3, complete: true });
});

test('truncation, false sync and unsupported syntax cannot conclude absence', () => {
  const bytes = frame({ joc: true });
  for (let n = 0; n < bytes.length; n++) assert.equal(inspectEac3Sample(bytes.subarray(0, n)).complete, false);
  for (const bytes of [frame({ bsid: 8 }), frame({ type: 3 }), new Uint8Array([0x0b, 0x77, 0, 0, 0, 0, 0])]) {
    assert.equal(inspectEac3Sample(bytes).complete, false);
    assert.equal(inspectEac3Sample(bytes).joc, false);
  }
  assert.deepEqual(inspectEac3Sample(join(frame({ joc: true }), new Uint8Array([0]))),
    { joc: true, frames: 1, complete: false, reason: 'truncated-header' });
  const tiny = bytes.slice(0, 8); tiny[2] &= 0xf8; tiny[3] = 3;
  assert.equal(inspectEac3Sample(tiny).complete, false);
  const rate = frame(); rate[4] |= 0xf0;
  assert.equal(inspectEac3Sample(rate).reason, 'reserved-sample-rate');
});
