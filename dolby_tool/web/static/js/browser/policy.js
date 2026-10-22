const MiB = 1024 ** 2;
const GiB = 1024 ** 3;
export const POLICY = Object.freeze({
  autoBytes: 512 * MiB, autoMs: 30_000,
  deeperBytes: 2 * GiB, deeperMs: 120_000,
  maxReadBytes: 16 * MiB, cacheBytes: 32 * MiB, maxMoovBytes: 256 * MiB,
  windowSamples: 8, windowCount: 16, sweepBatchBytes: 4 * MiB,
  reportIntervalMs: 200,
});
