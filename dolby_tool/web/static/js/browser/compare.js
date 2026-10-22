/** Native compare weights and components, with unresolved evidence kept as intervals. */
import { toSpec } from './report.js';

export const WEIGHTS = Object.freeze({ dv_present: 25, dv_fourcc_ok: 25, hdr10_plus: 10, atmos: 15, bit_rate_mbps: 20, resolution: 10, subtitles: 5 });
const DV_FOURCCS = new Set(['dvh1', 'dvhe', 'dvav', 'dva1']);

// Python round(x, 1): toFixed uses the same binary value, except exact ties go to even.
export function roundPoint(value) {
  const lower = Math.floor(value * 10);
  return Number.isInteger(value * 4) && Math.abs(value * 4) % 2 === 1
    ? (lower + Math.abs(lower % 2)) / 10 : Number(value.toFixed(1));
}
const interval = (min, max = min) => ({ min, max });
const flag = (value, weight) => value === true ? interval(weight) : value === false ? interval(0) : interval(0, weight);
export const resolutionPoints = (height, weight) => height >= 2000 ? weight : height >= 1000 ? weight * 0.5 : height > 0 ? weight * 0.2 : 0;

// Python 3.12+ sums floats with compensation; keep decimal custom weights in parity.
function sumPoints(values) {
  let sum = 0, correction = 0;
  for (const value of values) {
    const next = sum + value;
    correction += Math.abs(sum) >= Math.abs(value) ? (sum - next) + value : (value - next) + sum;
    sum = next;
  }
  return sum + correction;
}

/** Null evidence stays an interval; a confirmed no-video file has no native score. */
export function scoreSpec(spec, weights = WEIGHTS, { tracksComplete = false } = {}) {
  const w = { ...WEIGHTS, ...weights }, v = spec.video;
  if (v.present === false) return null;
  const dv = v.dolby_vision.present;
  const fourcc = v.fourcc ? DV_FOURCCS.has(v.fourcc) : tracksComplete ? false : null;
  const atmos = spec.audio.some(t => t.atmos === true) ? true :
    tracksComplete && spec.audio.every(t => t.atmos === false) ? false : null;
  const parts = {
    dv_present: flag(dv, w.dv_present),
    dv_fourcc_ok: flag(dv === false || fourcc === false ? false : dv === true && fourcc === true ? true : null, w.dv_fourcc_ok),
    hdr10_plus: flag(v.hdr10_plus, w.hdr10_plus),
    atmos: flag(atmos, w.atmos),
    bit_rate_mbps: v.bit_rate_mbps == null ? interval(0, w.bit_rate_mbps) : interval(roundPoint(Math.min(v.bit_rate_mbps / 80, 1) * w.bit_rate_mbps)),
    resolution: v.height == null ? interval(0, w.resolution) : interval(roundPoint(resolutionPoints(v.height, w.resolution))),
    subtitles: tracksComplete ? interval(roundPoint(Math.min(spec.subtitles.length / 5, 1) * w.subtitles)) : interval(roundPoint(Math.min(spec.subtitles.length / 5, 1) * w.subtitles), roundPoint(w.subtitles)),
  };
  const min = roundPoint(sumPoints(Object.values(parts).map(part => part.min)));
  const max = roundPoint(sumPoints(Object.values(parts).map(part => part.max)));
  return { min, max, components: parts, resolved: min === max, ceiling: sumPoints(Object.values(w)) };
}

export function scoreReport(report, weights = WEIGHTS) {
  return scoreSpec(toSpec(report), weights, { tracksComplete: report.structureComplete === true });
}

/** Null scores are unranked. Strict separation wins; exact resolved ties share best. */
export function rankScores(scores, { scanning = false } = {}) {
  const eligible = scores.filter(Boolean);
  const allResolved = eligible.every(s => s.resolved);
  const best = Math.max(...eligible.map(s => s.min));
  const winners = scores.flatMap((score, index) =>
    score && (allResolved ? score.min === best : scores.every((other, j) => !other || j === index || score.min > other.max)) ? [index] : []);
  const order = scores.flatMap((score, i) => score ? [i] : []).sort((a, b) => scores[b].min - scores[a].min || scores[b].max - scores[a].max || a - b);
  const ranks = scores.map(score => score ? 1 + eligible.filter(other => other.min > score.max).length : null);
  const overlaps = scores.map((score, i) => scores.flatMap((other, j) =>
    score && other && j !== i && score.min <= other.max && other.min <= score.max ? [j] : []));
  return { order, ranks, overlaps, winners, provisional: scanning || !allResolved };
}
