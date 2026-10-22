import { POLICY } from './policy.js';
import { Gate } from './gate.js';

export class FileUnavailableError extends Error {
  constructor(cause) { super('The file can no longer be read', { cause }); this.name = 'FileUnavailableError'; }
}

export class RangeReader {
  constructor(blob, { policy = {}, gate = new Gate({ policy }) } = {}) {
    this.blob = blob;
    this.policy = { ...POLICY, ...policy };
    this.gate = gate;
    this.metrics = { bytesRequested: 0, readCount: 0, repeatedBytes: 0, uniqueBytes: 0 };
    this.cache = new Map();
    this.cacheSize = 0;
    this.ranges = [];
  }
  coveredRanges() { return this.ranges.map(range => [...range]); }
  cover(start, end) {
    let overlap = 0;
    for (const [a, b] of this.ranges) overlap += Math.max(0, Math.min(b, end) - Math.max(a, start));
    this.metrics.repeatedBytes += overlap;
    this.metrics.uniqueBytes += end - start - overlap;
    const merged = [];
    for (const [a, b] of this.ranges) {
      if (b < start) merged.push([a, b]);
      else if (a > end) { merged.push([start, end]); start = a; end = b; }
      else { start = Math.min(a, start); end = Math.max(b, end); }
    }
    merged.push([start, end]);
    this.ranges = merged;
  }
  async read(offset, length, purpose = 'analysis') {
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(length) || offset < 0 || length < 0 || offset > this.blob.size) {
      throw new RangeError('Invalid file range');
    }
    length = Math.min(length, this.blob.size - offset);
    if (!length) return new Uint8Array();
    const key = `${offset}:${length}`;
    if (this.cache.has(key)) {
      await this.gate.admit(0);
      const hit = this.cache.get(key);
      this.cache.delete(key); this.cache.set(key, hit);
      return hit;
    }
    const chunks = [];
    for (let pos = offset; pos < offset + length;) {
      const count = Math.min(this.policy.maxReadBytes, offset + length - pos);
      await this.gate.admit(count);
      let bytes;
      try { bytes = new Uint8Array(await this.blob.slice(pos, pos + count).arrayBuffer()); }
      catch (error) {
        if (error.name === 'NotReadableError' || error.name === 'NotFoundError') throw new FileUnavailableError(error);
        throw error;
      }
      if (bytes.length !== count) throw new FileUnavailableError();
      this.metrics.bytesRequested += count;
      this.metrics.readCount++;
      this.cover(pos, pos + count);
      chunks.push(bytes);
      pos += count;
    }
    const result = chunks.length === 1 ? chunks[0] : new Uint8Array(length);
    if (chunks.length > 1) { let pos = 0; for (const chunk of chunks) { result.set(chunk, pos); pos += chunk.length; } }
    if (length <= this.policy.cacheBytes) {
      while (this.cacheSize + length > this.policy.cacheBytes && this.cache.size) {
        const oldest = this.cache.keys().next().value;
        this.cacheSize -= this.cache.get(oldest).length;
        this.cache.delete(oldest);
      }
      this.cache.set(key, result); this.cacheSize += length;
    }
    return result;
  }
}
