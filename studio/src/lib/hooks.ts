import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { host, subscribeHost } from '../host';

export interface Async<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (d: T | null) => void;
}

/** Runs `fn` when `key` changes; `reload` re-runs it. A null key skips loading. */
export function useAsync<T>(fn: () => Promise<T>, key: string | null): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(key !== null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const keyRef = useRef(key);
  keyRef.current = key;

  const reload = useCallback(async () => {
    if (keyRef.current === null) return;
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (my === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (my === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (key === null) {
      setData(null);
      setLoading(false);
      return;
    }
    setData(null);
    setError(null);
    void reload();
  }, [key, reload]);

  return { data, error, loading, reload, setData };
}

/** Debounced callback: collapses bursts of engine events into one refetch. */
export function useDebounced(fn: () => void, ms: number): () => void {
  const ref = useRef(fn);
  ref.current = fn;
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  return useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      ref.current();
    }, ms);
  }, [ms]);
}

/** Loads an image element for canvas drawing. */
export function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`could not load ${src.split('?')[0]}`));
    img.src = src;
  });
}

export function useElementSize<T extends HTMLElement>(): [(el: T | null) => void, { w: number; h: number }] {
  // A callback ref, so elements that mount later (after data loads) are still observed.
  const [el, setEl] = useState<T | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, size];
}

/** "pywebview" inside the desktop app (native folder dialogs, reveal), "browser" otherwise. */
export function useHostKind(): 'pywebview' | 'browser' {
  return useSyncExternalStore(subscribeHost, () => host.kind);
}
