// Engine event bus over WebSocket (/api/events). Reconnects with exponential backoff and drops the
// history the server replays on every (re)connect when it has already been seen.

import { useEffect, useRef, useSyncExternalStore } from 'react';
import { eventsUrl } from './api';
import type { EngineEvent } from './types';

export type ConnState = 'connecting' | 'open' | 'closed';

export interface TurnaroundStatus {
  state: 'running' | 'done' | 'failed';
  error?: string;
  ts: number;
}

interface Snapshot {
  conn: ConnState;
  log: EngineEvent[];
  turnaround: Record<string, TurnaroundStatus>;
  exported: Record<string, number>;
  spend: { ts: number; cost: number }[];
}

let snap: Snapshot = { conn: 'connecting', log: [], turnaround: {}, exported: {}, spend: [] };
const storeListeners = new Set<() => void>();
const eventListeners = new Set<(e: EngineEvent) => void>();
const seen = new Set<string>();
let ws: WebSocket | null = null;
let retry = 0;
let started = false;

function emitStore() {
  storeListeners.forEach((l) => l());
}

const keyOf = (e: EngineEvent) =>
  [e.ts, e.type, e.job ?? '', e.step ?? '', e.candidate ?? '', e.character ?? '', e.anim ?? '', e.model ?? ''].join('|');

function ingest(e: EngineEvent) {
  const k = keyOf(e);
  if (seen.has(k)) return;
  seen.add(k);
  if (seen.size > 5000) {
    const first = seen.values().next().value;
    if (first !== undefined) seen.delete(first);
  }
  const next: Snapshot = { ...snap, log: [...snap.log.slice(-299), e] };
  if (e.type.startsWith('turnaround_') && e.character) {
    const state = e.type === 'turnaround_started' ? 'running' : e.type === 'turnaround_done' ? 'done' : 'failed';
    next.turnaround = { ...snap.turnaround, [e.character]: { state, error: e.error, ts: e.ts } };
  }
  if (e.type === 'exported' && e.anim) next.exported = { ...snap.exported, [e.anim]: e.ts };
  if (e.type === 'spend' && typeof e.cost === 'number' && e.cost > 0) {
    next.spend = [...snap.spend.slice(-499), { ts: e.ts, cost: e.cost }];
  }
  snap = next;
  emitStore();
  eventListeners.forEach((l) => {
    try {
      l(e);
    } catch (err) {
      console.error(err);
    }
  });
}

function connect() {
  snap = { ...snap, conn: 'connecting' };
  emitStore();
  const sock = new WebSocket(eventsUrl());
  ws = sock;
  sock.onopen = () => {
    retry = 0;
    snap = { ...snap, conn: 'open' };
    emitStore();
  };
  sock.onmessage = (m) => {
    try {
      const data = JSON.parse(String(m.data)) as EngineEvent;
      if (data && typeof data.type === 'string') ingest(data);
    } catch {
      /* ignore malformed frames */
    }
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    ws = null;
    snap = { ...snap, conn: 'closed' };
    emitStore();
    const delay = Math.min(10000, 500 * 2 ** retry) + Math.random() * 250;
    retry += 1;
    window.setTimeout(connect, delay);
  };
  sock.onerror = () => sock.close();
}

export function startEvents() {
  if (started) return;
  started = true;
  connect();
}

function subscribeStore(l: () => void) {
  storeListeners.add(l);
  return () => storeListeners.delete(l);
}

export function useEvents(): Snapshot {
  return useSyncExternalStore(subscribeStore, () => snap);
}

/** Calls `fn` for every new engine event; the latest `fn` is always used. */
export function useEngineEvent(fn: (e: EngineEvent) => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const l = (e: EngineEvent) => ref.current(e);
    eventListeners.add(l);
    return () => {
      eventListeners.delete(l);
    };
  }, []);
}

/** Sum of live spend events newer than `sinceTs` (engine clock, seconds). */
export function spendSince(s: Snapshot, sinceTs: number): number {
  return s.spend.reduce((acc, x) => (x.ts > sinceTs ? acc + x.cost : acc), 0);
}
