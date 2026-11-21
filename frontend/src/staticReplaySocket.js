// Backend-free replay player for the hosted demo (VITE_STATIC_DEMO=1).
//
// It mimics the small part of the WebSocket API that App.jsx uses (onopen,
// onmessage, onerror, onclose, send, close) and plays a recording exported by
// demos/replays/export_static_demo.py, so a replay renders exactly like a live run.

export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === '1';

const REPLAY_BASE = `${import.meta.env.BASE_URL}replays/`;

export async function fetchStaticReplays() {
  const res = await fetch(`${REPLAY_BASE}index.json`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export class StaticReplaySocket {
  constructor({ speed = 1 } = {}) {
    this.speed = speed > 0 ? speed : 1;
    this.closed = false;
    this.timer = null;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    setTimeout(() => {
      if (!this.closed) this.onopen?.();
    }, 0);
  }

  send(raw) {
    let replayId = null;
    try {
      replayId = JSON.parse(raw).replay_id;
    } catch {
      // ignore malformed payloads; handled below
    }
    if (!replayId || !/^[A-Za-z0-9._-]+$/.test(replayId)) {
      this.emit({
        event: 'error',
        content: 'This hosted demo can only replay recorded runs. Install VerumTrade locally to analyze any ticker.',
      });
      this.finish();
      return;
    }
    fetch(`${REPLAY_BASE}${replayId}.json`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((replay) => this.play(replay.events || [], 0))
      .catch((err) => {
        if (this.closed) return;
        this.onerror?.(err);
        this.finish();
      });
  }

  play(events, index) {
    if (this.closed) return;
    if (index >= events.length) {
      this.finish();
      return;
    }
    const event = events[index];
    const delay = Math.max(event.delay_ms || 0, 0) / this.speed;
    this.timer = setTimeout(() => {
      this.emit(event.payload);
      this.play(events, index + 1);
    }, delay);
  }

  emit(payload) {
    if (this.closed) return;
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  finish() {
    if (this.closed) return;
    this.closed = true;
    clearTimeout(this.timer);
    this.onclose?.();
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
  }
}
