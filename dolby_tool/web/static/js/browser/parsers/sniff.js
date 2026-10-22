export function sniff(head) {
  if (head[0] === 0x1a && head[1] === 0x45 && head[2] === 0xdf && head[3] === 0xa3) return 'mkv';
  if (head.length >= 8 && ['ftyp', 'moov', 'free', 'wide', 'mdat'].includes(String.fromCharCode(...head.subarray(4, 8)))) return 'mp4';
  return null;
}
