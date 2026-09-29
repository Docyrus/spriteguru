import { useEffect, useState } from 'react';
import { api } from './api';
import type { Candidate, Report } from './types';

const cache = new Map<string, Promise<Report>>();

/** Forget every cached report: job ids are only unique within one project. */
export function clearReportCache() {
  cache.clear();
}

export function fetchReport(jobId: string, cand: Candidate, fresh = false): Promise<Report> {
  const k = `${jobId}/${cand.id}/${cand.report ?? ''}/${cand.score ?? ''}`;
  if (fresh || !cache.has(k)) {
    const p = api.report(jobId, cand.id);
    p.catch(() => cache.delete(k));
    cache.set(k, p);
  }
  return cache.get(k)!;
}

/** Reports of the given candidates, keyed by candidate id (missing until loaded). */
export function useReports(jobId: string | null, cands: Candidate[]): Record<string, Report> {
  const [out, setOut] = useState<Record<string, Report>>({});
  const sig = cands.map((c) => `${c.id}:${c.report ?? ''}:${c.score ?? ''}`).join('|');
  useEffect(() => {
    let alive = true;
    if (!jobId) return;
    for (const c of cands) {
      if (!c.report) continue;
      fetchReport(jobId, c)
        .then((r) => alive && setOut((o) => (o[c.id] === r ? o : { ...o, [c.id]: r })))
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, sig]);
  return out;
}

export function useReport(jobId: string | null, cand: Candidate | null): { report: Report | null; error: string | null } {
  const [state, setState] = useState<{ report: Report | null; error: string | null }>({ report: null, error: null });
  const k = jobId && cand ? `${jobId}/${cand.id}/${cand.report ?? ''}/${cand.score ?? ''}` : null;
  useEffect(() => {
    let alive = true;
    setState({ report: null, error: null });
    if (!jobId || !cand) return;
    if (!cand.report) {
      setState({ report: null, error: 'This candidate has no analysis report yet.' });
      return;
    }
    fetchReport(jobId, cand)
      .then((r) => alive && setState({ report: r, error: null }))
      .catch((e) => alive && setState({ report: null, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k]);
  return state;
}
