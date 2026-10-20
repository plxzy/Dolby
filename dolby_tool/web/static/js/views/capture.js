/**
 * TV Capture view: stream TV.app's unified log over a WebSocket, or import a
 * saved log, and render what was actually decoded. Exactly one socket is live
 * at a time, and every handler ignores events from a socket that is no longer
 * current — so an old socket closing can never reset a newer capture.
 */

import { openSocket, postJson } from '../api.js';
import { emptyState, notice, withCode } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { $, el, replace } from '../dom.js';
import * as fmt from '../format.js';
import { renderSummary } from './capture-summary.js';
import { Timeline } from './capture-timeline.js';

const STATE_LABELS = {
  idle: 'Idle',
  connecting: 'Connecting…',
  capturing: 'Capturing',
  stopping: 'Finishing…',
  importing: 'Reading log…',
  stopped: 'Stopped',
  error: 'Error',
};
const BUSY = new Set(['connecting', 'capturing', 'stopping', 'importing']);

let ui;
let timeline;
let socket = null;
let state = 'idle';
let eventCount = 0;
let startedAt = 0;
let ticker = 0;
let liveCaptureMissing = false;

export function init({ capabilities }) {
  ui = {
    start: $('#capture-start'),
    stop: $('#capture-stop'),
    importButton: $('#capture-import'),
    file: $('#capture-file'),
    pill: $('#capture-status'),
    pillLabel: $('#capture-status-label'),
    counter: $('#capture-counter'),
    elapsed: $('#capture-elapsed'),
    platform: $('#capture-platform'),
    summary: $('#capture-summary'),
    timelineTitle: $('#timeline-title'),
  };
  timeline = new Timeline($('#capture-events'), { scroller: $('#capture-scroller'), jumpButton: $('#capture-jump') });

  ui.start.addEventListener('click', start);
  ui.stop.addEventListener('click', stop);
  ui.importButton.addEventListener('click', () => ui.file.click());
  ui.file.addEventListener('change', () => {
    importLog(ui.file.files[0]);
    ui.file.value = '';
  });
  capabilities.then((caps) => {
    liveCaptureMissing = Boolean(caps && !caps.tools.log);
    if (liveCaptureMissing) {
      replace(ui.platform, platformNote());
      // Importing is the way in here; Start stays available but stops being the headline action.
      ui.start.classList.remove('btn-primary');
      ui.importButton.classList.add('btn-primary');
    }
  });
  replace(ui.summary, idleState());
}

// ---------------------------------------------------------------------------
// State

function setState(next, label = STATE_LABELS[next]) {
  state = next;
  ui.pill.dataset.state = next;
  ui.pillLabel.textContent = label;
  const busy = BUSY.has(next);
  ui.start.hidden = busy;
  ui.stop.hidden = !busy;
  ui.stop.disabled = next !== 'capturing';
  ui.importButton.disabled = busy;
  clearInterval(ticker);
  if (next === 'capturing') {
    startedAt = Date.now();
    ticker = setInterval(renderElapsed, 1000);
    renderElapsed();
  }
}

function renderElapsed() {
  ui.elapsed.textContent = fmt.duration((Date.now() - startedAt) / 1000);
}

function setCount(count) {
  eventCount = count;
  ui.counter.textContent = fmt.plural(count, 'event');
}

function resetSession(title) {
  timeline.clear();
  setCount(0);
  ui.elapsed.textContent = '';
  ui.timelineTitle.textContent = title;
}

// ---------------------------------------------------------------------------
// Live capture

function start() {
  closeSocket();
  resetSession('Live events');
  replace(ui.summary, waitingState());
  setState('connecting');

  const ws = openSocket('/ws/tvlog');
  socket = ws;
  ws.addEventListener('open', () => {
    if (ws === socket) ws.send(JSON.stringify({ cmd: 'start' }));
  });
  ws.addEventListener('message', (message) => {
    if (ws === socket) handleMessage(message.data);
  });
  ws.addEventListener('error', () => {
    if (ws === socket) fail('The connection to the dolby-tool server failed.');
  });
  ws.addEventListener('close', () => {
    if (ws !== socket) return;
    socket = null;
    if (BUSY.has(state)) fail('The server closed the connection before the capture finished.');
  });
}

function stop() {
  if (socket?.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ cmd: 'stop' }));
  setState('stopping');
}

/** Detach first, so this socket's own close event is ignored. */
function closeSocket() {
  const ws = socket;
  socket = null;
  ws?.close();
}

function handleMessage(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    return; // Not ours to interpret; keep the capture running.
  }
  switch (message?.type) {
    case 'started':
      setState('capturing');
      break;
    case 'event':
      if (message.event) {
        timeline.add(message.event);
        setCount(eventCount + 1);
      }
      break;
    case 'summary':
      closeSocket();
      showSummary(message.summary, 'Live capture');
      setCount(message.summary?.event_count ?? eventCount);
      setState('stopped');
      break;
    case 'error':
      fail(message.message || 'The capture failed.');
      break;
    default:
      break; // "pong" and anything newer need no action.
  }
}

function fail(message) {
  closeSocket();
  setState('error');
  const hint = liveCaptureMissing
    ? 'Live capture needs macOS. You can still import a log saved on a Mac.'
    : 'Check the terminal running dolby-tool for details.';
  replace(ui.summary, notice({ tone: 'danger', title: 'Capture stopped', body: [message, ' ', hint] }));
}

// ---------------------------------------------------------------------------
// Import

async function importLog(file) {
  if (!file) return;
  closeSocket();
  resetSession(`Events from ${file.name}`);
  setState('importing');
  try {
    const { summary } = await postJson('/api/tvlog/parse', { text: await file.text() });
    timeline.load(summary.events ?? []);
    setCount(summary.event_count ?? 0);
    showSummary(summary, `Imported ${file.name}`);
    setState('stopped', 'Imported');
  } catch (error) {
    const message =
      error.status === 404 || error.status === 405
        ? 'This dolby-tool server can’t parse saved logs yet — update it to 0.2 or newer.'
        : error.message;
    setState('error');
    replace(ui.summary, notice({ tone: 'danger', title: 'Couldn’t import that log', body: message }));
  }
}

// ---------------------------------------------------------------------------
// Summary area states

function showSummary(summary, origin) {
  replace(ui.summary, summary ? renderSummary(summary, { origin }) : idleState());
}

function idleState() {
  return emptyState({
    iconName: 'capture',
    title: 'No capture yet',
    body:
      'Press Start, then play something in TV.app. When you stop, you’ll see what was actually decoded — ' +
      'Dolby Vision, FourCC, audio format and spatial rendering.',
  });
}

function waitingState() {
  return emptyState({
    iconName: 'play',
    title: 'Listening…',
    body: 'Play something in TV.app now. The summary appears here when you press Stop.',
  });
}

function platformNote() {
  return notice({
    tone: 'info',
    iconName: 'help',
    title: 'Live capture needs macOS',
    body: withCode(
      'It streams TV.app’s unified log with `log stream`, which only exists on macOS. ' +
        'Use “Import saved log…” to load one saved on a Mac and get the same summary.',
    ),
  });
}
