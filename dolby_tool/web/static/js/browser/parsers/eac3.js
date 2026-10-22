// E-AC-3 BSI: ETSI TS 102 366, Annex E (syncframe/bsi syntax).
// https://www.etsi.org/deliver/etsi_ts/102300_102399/102366/01.04.01_60/ts_102366v010401p.pdf
// Extension type A signalling follows FFmpeg's eac3_parse_header:
// https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/ac3_parser.c
// MediaInfo also requires >=2 addbsi bytes and bit 0 of the first byte:
// https://github.com/MediaArea/MediaInfoLib/blob/master/Source/MediaInfo/Audio/File_Ac3.cpp
// This recognizes signalling, not decoded JOC objects or playable audio. `complete`
// means all supplied frame boundaries/BSI were inspected, NOT whole-track absence,
// CRC verification, or validation of audio blocks. Input must begin at a syncframe.
export function inspectEac3Sample(bytes) {
  const result = { joc: false, frames: 0, complete: false };
  if (!(bytes instanceof Uint8Array) || !bytes.length) return { ...result, reason: 'empty-or-invalid-input' };
  let offset = 0;
  try {
    while (offset < bytes.length) {
      if (bytes.length - offset < 7) throw new Error('truncated-header');
      if (bytes[offset] !== 0x0b || bytes[offset + 1] !== 0x77) throw new Error('invalid-syncword');
      const size = ((((bytes[offset + 2] & 7) << 8) | bytes[offset + 3]) + 1) * 2;
      if (size < 7) throw new Error('invalid-frame-size');
      if (size > bytes.length - offset) throw new Error('truncated-frame');
      let bit = (offset + 2) * 8;
      // Never read the final CRC word as BSI.
      const end = (offset + size - 2) * 8;
      const read = count => {
        if (bit + count > end) throw new Error('truncated-bsi');
        let value = 0;
        for (let i = 0; i < count; i++, bit++) value = value * 2 + ((bytes[bit >> 3] >> (7 - (bit & 7))) & 1);
        return value;
      };
      const skip = count => {
        if (bit + count > end) throw new Error('truncated-bsi');
        bit += count;
      };
      const type = read(2);
      if (type === 3) throw new Error('reserved-stream-type');
      read(3); // substreamid: every independent/dependent substream is inspected
      read(11); // frmsiz, already bounded above
      const fscod = read(2);
      let blocks;
      if (fscod === 3) {
        if (read(2) === 3) throw new Error('reserved-sample-rate');
        blocks = 6;
      } else blocks = [1, 2, 3, 6][read(2)];
      const acmod = read(3), lfe = read(1);
      if (read(5) !== 16) throw new Error('unsupported-bsid');
      const programs = acmod === 0 ? 2 : 1;
      for (let i = 0; i < programs; i++) { skip(5); if (read(1)) skip(8); }
      if (type === 1 && read(1)) skip(16); // chanmap
      if (read(1)) { // mixmdate
        if (acmod > 2) {
          skip(2);
          if (acmod & 1) skip(6);
          if (acmod & 4) skip(6);
        }
        if (lfe && read(1)) skip(5);
        if (type === 0) {
          for (let i = 0; i < programs; i++) if (read(1)) skip(6);
          if (read(1)) skip(6);
          const mixdef = read(2);
          if (mixdef === 1) skip(5);
          if (mixdef === 2) skip(12);
          if (mixdef === 3) skip((read(5) + 2) * 8);
          if (acmod < 2) for (let i = 0; i < programs; i++) if (read(1)) skip(14);
          if (read(1)) for (let i = 0; i < blocks; i++) if (blocks === 1 || read(1)) skip(5);
        }
      }
      if (read(1)) { // infomdate
        skip(5);
        if (acmod === 2) skip(4);
        if (acmod >= 6) skip(2);
        for (let i = 0; i < programs; i++) if (read(1)) skip(8);
        if (fscod !== 3) skip(1);
      }
      if (type === 0 && blocks !== 6) skip(1); // convsync
      if (type === 2 && (blocks === 6 || read(1))) skip(6); // blkid/frmsizecod
      let joc = false;
      if (read(1)) {
        const length = read(6) + 1;
        const first = read(8);
        if (first & 1) {
          if (length < 2) throw new Error('missing-joc-complexity');
          read(8); // complexity_index_type_a, retained only as presence evidence
          skip((length - 2) * 8);
          joc = true;
        } else skip((length - 1) * 8);
      }
      result.joc ||= joc;
      result.frames++;
      offset += size;
    }
    result.complete = true;
  } catch (error) { result.reason = error.message; }
  return result;
}
