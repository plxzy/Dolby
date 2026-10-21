/**
 * Inline icon set: name -> inner markup for a 24×24 viewBox. The wrapper
 * (components/icon.js) draws a 1.75px round stroke in currentColor; glyphs
 * that need a fill set it on their own paths. Constant, trusted markup only.
 *
 * Attribution: the `container` (cube), `video` (film strip) and `hdr` (sun)
 * glyphs are adapted from SVG Repo icons (https://www.svgrepo.com), as used by
 * the previous dolby-tool UI. The remaining glyphs were drawn for dolby-tool.
 */
export const ICONS = {
  // Navigation
  inspect: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/><path d="M8 10.5h5M10.5 8v5"/>',
  compare: '<path d="M4 6.5h13M4 12h16M4 17.5h9"/>',
  capture:
    '<rect x="2.5" y="4" width="19" height="13" rx="2.5"/><path d="M8.5 20.5h7"/>' +
    '<circle cx="12" cy="10.5" r="2.6" fill="currentColor" stroke="none"/>',

  // Theme
  system: '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20h7M12 16.5V20"/>',
  light:
    '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2' +
    'M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  dark: '<path d="M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6Z"/>',

  // Media categories
  container:
    '<g transform="scale(0.4286)" fill="currentColor" stroke="none"><path d="M28 26.64 50.08 14.1c-.35-.35-.78-.63-1.29-.91' +
    'L32.27 3.77C30.81 2.95 29.41 2.5 28 2.5s-2.81.45-4.27 1.27L7.21 13.19c-.51.28-.94.56-1.29.91Zm-1.59 26.86V29.45L4.4 16.89' +
    'c-.12.52-.19 1.08-.19 1.78v18.26c0 3.4 1.2 4.66 3.38 5.91l18.35 10.45c.16.09.3.16.47.21Zm3.18 0c.17-.05.31-.12.47-.21' +
    'l18.35-10.45c2.18-1.25 3.38-2.51 3.38-5.91V18.67c0-.7-.07-1.26-.19-1.78L29.59 29.45Z"/></g>',
  video:
    '<path d="M7 5v14M17 5v14M3 8h4M17 8h4M3 16h4M17 16h4M3 12h4M17 12h4M6.2 20h11.6c1.12 0 1.68 0 2.11-.22a2 2 0 0 0 .87-.87' +
    'c.22-.43.22-.99.22-2.11V7.2c0-1.12 0-1.68-.22-2.11a2 2 0 0 0-.87-.87C19.48 4 18.92 4 17.8 4H6.2c-1.12 0-1.68 0-2.11.22' +
    'a2 2 0 0 0-.87.87C3 5.52 3 6.08 3 7.2v9.6c0 1.12 0 1.68.22 2.11a2 2 0 0 0 .87.87c.43.22.99.22 2.11.22Z"/>',
  hdr:
    '<g fill="currentColor" stroke="none"><path d="M12 17.75A5.75 5.75 0 1 1 17.75 12 5.76 5.76 0 0 1 12 17.75Zm0-10a4.25 4.25 ' +
    '0 1 0 4.25 4.25A4.26 4.26 0 0 0 12 7.75Z"/><path d="M12 5a.76.76 0 0 1-.75-.75v-1.5a.75.75 0 0 1 1.5 0v1.5A.76.76 0 0 1 ' +
    '12 5Zm0 17a.76.76 0 0 1-.75-.75v-1.5a.75.75 0 0 1 1.5 0v1.5A.76.76 0 0 1 12 22Zm9.25-9.25h-1.5a.75.75 0 0 1 0-1.5h1.5a' +
    '.75.75 0 0 1 0 1.5Zm-17 0h-1.5a.75.75 0 0 1 0-1.5h1.5a.75.75 0 0 1 0 1.5ZM6.5 7.25A.74.74 0 0 1 6 7L4.91 6a.75.75 0 0 1 ' +
    '1.09-1.09L7 6a.75.75 0 0 1 0 1 .74.74 0 0 1-.5.25Zm12.06 12.06a.74.74 0 0 1-.53-.22L17 18a.75.75 0 0 1 1-1l1.09 1a.75.75 ' +
    '0 0 1 0 1.06.73.73 0 0 1-.53.25ZM17.5 7.25A.74.74 0 0 1 17 7a.75.75 0 0 1 0-1l1-1.09A.75.75 0 0 1 19.09 6L18 7a.74.74 0 0 ' +
    '1-.5.25ZM5.44 19.31a.74.74 0 0 1-.53-.22.75.75 0 0 1 0-1.06L6 17a.75.75 0 0 1 1 1l-1 1.09a.75.75 0 0 1-.56.22Z"/></g>',
  color: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none"/>',
  dolbyVision:
    '<path d="m12 3.5 8.5 4.5L12 12.5 3.5 8Z"/><path d="m3.5 12 8.5 4.5 8.5-4.5"/><path d="m3.5 16 8.5 4.5 8.5-4.5"/>',
  audio: '<path d="M4 10v4M8 6.5v11M12 3.5v17M16 7.5v9M20 10.5v3"/>',
  atmos:
    '<circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/><path d="M8.3 8.3a5.2 5.2 0 0 0 0 7.4M15.7 8.3a5.2 5.2 0 0 1 0 7.4' +
    'M5.4 5.4a9.3 9.3 0 0 0 0 13.2M18.6 5.4a9.3 9.3 0 0 1 0 13.2"/>',
  subtitles: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 12.5h3.5M13.5 12.5H17M7 15.5h7"/>',
  resolution:
    '<path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>',
  tag: '<path d="M3.5 12.1V4.5a1 1 0 0 1 1-1h7.6l8.6 8.6a1.5 1.5 0 0 1 0 2.1l-6.5 6.5a1.5 1.5 0 0 1-2.1 0Z"/><circle cx="8" cy="8" r="1.4"/>',
  chip:
    '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6' +
    'M18 9.5h3.5M18 14.5h3.5"/>',
  stream: '<path d="M4.5 16.5a10.5 10.5 0 0 1 15 0M7.5 19.5a6 6 0 0 1 9 0"/><path d="M12 4v8M8.5 8.5 12 12l3.5-3.5"/>',
  package: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',

  // Files & actions
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="m10 11.5 4 2.5-4 2.5Z"/>',
  folder:
    '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5Z"/>',
  drop: '<path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5"/><path d="M4 14.5v3A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5v-3"/>',
  import:
    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5M12 11v6M9.5 14.5 12 17l2.5-2.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 5.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2h.5"/>',
  code: '<path d="M8.5 4H7.6A2.1 2.1 0 0 0 5.5 6.1v3.3L3.5 12l2 2.6v3.3A2.1 2.1 0 0 0 7.6 20h.9M15.5 4h.9a2.1 2.1 0 0 1 2.1 2.1v3.3l2 2.6-2 2.6v3.3a2.1 2.1 0 0 1-2.1 2.1h-.9"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  arrowDown: '<path d="M12 5v14M6.5 13.5 12 19l5.5-5.5"/>',
  sliders:
    '<path d="M4 6.5h8.5M16.5 6.5H20M4 12h2.5M10.5 12H20M4 17.5h10.5M18.5 17.5H20"/><circle cx="14.5" cy="6.5" r="2"/>' +
    '<circle cx="8.5" cy="12" r="2"/><circle cx="16.5" cy="17.5" r="2"/>',
  refresh:
    '<path d="M20 12a8 8 0 0 1-14.3 4.9M4 12a8 8 0 0 1 14.3-4.9"/><path d="M18.5 3.5v3.8h-3.8M5.5 20.5v-3.8h3.8"/>',
  play: '<path d="M8 5.6v12.8a.8.8 0 0 0 1.2.7l10.1-6.4a.8.8 0 0 0 0-1.4L9.2 4.9a.8.8 0 0 0-1.2.7Z" fill="currentColor"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/>',
  award: '<circle cx="12" cy="9" r="5.5"/><path d="m8.6 13.4-1.4 7.1L12 18l4.8 2.5-1.4-7.1"/>',

  // Status
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.8-4.9"/>',
  minusCircle: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12h7"/>',
  xCircle: '<circle cx="12" cy="12" r="8.5"/><path d="m9.3 9.3 5.4 5.4M14.7 9.3l-5.4 5.4"/>',
  alert:
    '<path d="M10.3 4.2 2.8 17.3A2 2 0 0 0 4.5 20.3h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4.2M12 16.8h.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6M12 16.8h.01"/>',
};
