export function inspectSample(bytes, nalLengthSize = 4) {
  const result = { hdr10plus: false, dvRpu: false, mdcvSei: false, cllSei: false, malformedSamples: 0 };
  if (![1, 2, 3, 4].includes(nalLengthSize)) { result.malformedSamples = 1; return result; }
  for (let offset = 0; offset < bytes.length;) {
    if (offset + nalLengthSize > bytes.length) { result.malformedSamples = 1; break; }
    let size = 0;
    for (let i = 0; i < nalLengthSize; i++) size = size * 256 + bytes[offset + i];
    offset += nalLengthSize;
    if (size < 2 || offset + size > bytes.length) { result.malformedSamples = 1; break; }
    const type = (bytes[offset] >> 1) & 63;
    if (type === 62) result.dvRpu = true;
    if (type === 39) {
      const rbsp = new Uint8Array(size - 2);
      let n = 0, zeros = 0;
      for (let p = offset + 2; p < offset + size; p++) {
        const byte = bytes[p];
        if (zeros >= 2 && byte === 3) { zeros = 0; continue; }
        rbsp[n++] = byte;
        zeros = byte === 0 ? zeros + 1 : 0;
      }
      let p = 0;
      while (p < n && rbsp[p] !== 0x80) {
        let payloadType = 0, payloadSize = 0;
        while (p < n && rbsp[p] === 255) { payloadType += 255; p++; }
        if (p >= n) { result.malformedSamples = 1; break; } payloadType += rbsp[p++];
        while (p < n && rbsp[p] === 255) { payloadSize += 255; p++; }
        if (p >= n) { result.malformedSamples = 1; break; } payloadSize += rbsp[p++];
        if (p + payloadSize > n) { result.malformedSamples = 1; break; }
        if (payloadType === 4 && payloadSize >= 6 && [0xb5, 0, 0x3c, 0, 1, 4].every((b, i) => rbsp[p + i] === b)) result.hdr10plus = true;
        if (payloadType === 137) result.mdcvSei = true;
        if (payloadType === 144) result.cllSei = true;
        p += payloadSize;
      }
    }
    offset += size;
  }
  return result;
}
