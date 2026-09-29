import { useMemo, useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEngineEvent } from '../lib/events';
import { frameList, frameRemedyLabel, metricLabel, pad3, remedyAction, remedyLabel, usd } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { useReport } from '../lib/reports';
import { href, navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { Finding, FindingLabel, JobState, Level, RemedyCosts } from '../lib/types';
import { host } from '../host';
import { AnimHeader, pickJob, useAdditive, useAnimJobs } from '../shell/AnimHeader';
import { Button, Empty, ErrorNote, IconButton, LevelPill, QScore, Segmented, Spinner } from '../ui/controls';

type Filter = 'all' | Level | 'fixed';

function fmtVal(v: number | null): string {
  if (v === null || v === undefined) return '–';
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2).replace(/\.?0+$/, '');
  return v.toFixed(4).replace(/\.?0+$/, '');
}

export function Findings({ anim, job: wanted }: { anim: string | null; job: string | null }) {
  const { toast, reloadJobs, reloadAnimations } = useStore();
  const jobs = useAnimJobs(anim);
  const additive = useAdditive(anim);
  const picked = pickJob(wanted ? jobs : jobs.filter((j) => j.state === 'done'), wanted) ?? pickJob(jobs, null);
  const jobId = picked?.id ?? wanted ?? null;
  const job = useAsync<JobState>(() => api.job(jobId!), jobId);
  const soon = useDebounced(() => void job.reload(), 300);
  useEngineEvent((e) => {
    if (e.job === jobId) soon();
  });
  const costs = useAsync<RemedyCosts>(() => api.remedyCosts(anim!), anim);
  const st = job.data;
  const winner = st?.candidates.find((c) => c.id === st.winner) ?? null;
  // repairs and in-betweens act on the job being viewed, and need it finished
  const repairable = st?.state === 'done';
  const labels = useAsync<FindingLabel[]>(() => api.findingLabels(), anim ? `labels|${anim}` : null);
  const [localLabels, setLocalLabels] = useState<Record<string, boolean>>({});
  const { report, error: reportError } = useReport(st?.id ?? null, winner);
  const [filter, setFilter] = useState<Filter>('all');
  const [frameFilter, setFrameFilter] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const findings = report?.findings ?? [];
  const counts = useMemo(() => {
    const c = { fail: 0, warn: 0, info: 0, fixed: 0 };
    for (const f of findings) {
      if (f.auto_fixed) c.fixed++;
      c[f.level]++;
    }
    return c;
  }, [findings]);

  const perFrame = useMemo(() => {
    const m = new Map<number, { fail: number; warn: number; info: number }>();
    for (const f of findings) {
      if (f.auto_fixed) continue;
      for (const i of f.frames) {
        const e = m.get(i) ?? { fail: 0, warn: 0, info: 0 };
        e[f.level]++;
        m.set(i, e);
      }
    }
    return m;
  }, [findings]);

  const indexed = findings.map((f, idx) => ({ f, idx }));
  const shown = indexed.filter(({ f }) => {
    if (filter === 'fixed' && !f.auto_fixed) return false;
    if (filter !== 'all' && filter !== 'fixed' && f.level !== filter) return false;
    if (frameFilter !== null && !f.frames.includes(frameFilter)) return false;
    return true;
  });

  const cost = (k: string) => (costs.data && typeof costs.data[k] === 'number' ? costs.data[k]! : null);

  const doRepair = async (f: Finding, frame: number, kind: 'pose' | 'identity' | 'frame', tag: string) => {
    if (!anim) return;
    setBusy(tag);
    try {
      const r = await api.repair(anim, frame, kind, undefined, jobId);
      toast(
        r.kept ? `Frame ${frame + 1} repaired: Q ${r.before} to ${r.after}.` : `Repaired frame ${frame + 1} scored lower (Q ${r.after}); kept the previous version.`,
        r.kept ? 'ok' : 'info',
      );
      void reloadJobs();
      void reloadAnimations();
      if (r.job !== jobId) navigate('findings', anim, { job: r.job });
      else void job.reload();
    } catch (e) {
      toast(`${metricLabel(f.metric)}: ${errText(e)}`, 'error');
    } finally {
      setBusy(null);
    }
  };

  /** Insert a generated in-between after playback position `after`. */
  const doInbetween = async (after: number, tag: string) => {
    if (!anim) return;
    setBusy(tag);
    try {
      const r = await api.inbetween(anim, after, jobId);
      toast(`Inserted an in-between after frame ${after + 1}: ${r.frames} frames now, Q ${r.score ?? '–'}.`, 'ok');
      void reloadJobs();
      void reloadAnimations();
      if (r.job !== jobId) navigate('findings', anim, { job: r.job });
      else void job.reload();
    } catch (e) {
      toast(`In-between: ${errText(e)}`, 'error');
    } finally {
      setBusy(null);
    }
  };

  const labelKey = (idx: number) => `${st?.id}|${winner?.id}|${idx}`;
  const labelOf = (idx: number): boolean | null => {
    const k = labelKey(idx);
    if (k in localLabels) return localLabels[k]!;
    const hit = (labels.data ?? []).filter((l) => `${l.job}|${l.candidate}|${l.index}` === k).pop();
    return hit ? hit.correct : null;
  };
  const doLabel = async (idx: number, correct: boolean) => {
    if (!st || !winner) return;
    try {
      await api.labelFinding(st.id, winner.id, idx, correct);
      setLocalLabels((m) => ({ ...m, [labelKey(idx)]: correct }));
      toast(correct ? 'Marked as a real problem.' : 'Marked as a false alarm.', 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  /** Playback position to insert after: before the gap's frame, or the last frame for a loop seam. */
  const inbetweenAfter = (f: Finding): number | null => {
    const order = report?.order?.length ? report.order : Array.from({ length: report?.frames.length ?? 0 }, (_, i) => i);
    const n = order.length;
    if (!n) return null;
    if (f.metric === 'loop_seam' || !f.frames.length) return n - 1;
    const pos = order.indexOf(f.frames[0]!);
    return pos < 0 ? null : (pos - 1 + n) % n;
  };

  const doReroll = async (tag: string) => {
    if (!anim) return;
    setBusy(tag);
    try {
      const nj = await api.startJob(anim, Math.floor(Math.random() * 1e6));
      toast('Started a re-roll with a new seed.', 'ok');
      void reloadJobs();
      navigate('candidates', anim, { job: nj.id });
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const nFrames = report?.frames.length ?? 0;
  const blocked = counts.fail > 0;

  return (
    <div className="screen" data-testid="findings-screen">
      <AnimHeader screen="findings" title="Findings" anim={anim} job={jobId} showJob>
        {winner?.html ? (
          <Button
            size="sm"
            variant="quiet"
            icon="report"
            data-testid="findings-open-report"
            onClick={async () => {
              const r = await host.openExternal(fileUrl(winner.html));
              if (!r.ok && r.message) toast(r.message, 'error');
            }}
          >
            HTML report
          </Button>
        ) : null}
      </AnimHeader>
      <div className="screen-body">
        {!jobId && jobs.length === 0 ? (
          <Empty title="No analysis yet" testid="findings-empty">
            Findings appear once a job for this animation has been analyzed.
          </Empty>
        ) : null}
        {job.error ? <ErrorNote>{job.error}</ErrorNote> : null}
        {st && !winner ? (
          <Empty title="No winner to report on" testid="findings-no-winner">
            This job has no scored candidate yet.
          </Empty>
        ) : null}
        {reportError && winner ? <ErrorNote testid="findings-error">{reportError}</ErrorNote> : null}
        {winner && !report && !reportError ? <Spinner label="Loading the report…" /> : null}

        {report && winner ? (
          <>
            <section className="q-hero" data-testid="findings-summary">
              <QScore score={report.score} accepted={report.accepted} size="lg" testid="findings-q" />
              <div className="q-hero-text">
                <div className="q-verdict" data-testid="findings-verdict">
                  {report.accepted
                    ? 'Auto-accepted'
                    : blocked
                      ? `Blocked by ${counts.fail} failure${counts.fail === 1 ? '' : 's'}`
                      : 'Below the 85 needed to auto-accept'}
                </div>
                <div className="muted">
                  Winner <strong>{winner.id}</strong> of job {st?.id.split('@')[1]}. Q starts at 100 and each finding subtracts its weight times
                  its severity; any failure blocks acceptance.
                </div>
                <div className="q-counts">
                  <span className="level level-fail">{counts.fail} fail</span>
                  <span className="level level-warn">{counts.warn} warn</span>
                  <span className="level level-info">{counts.info} info</span>
                  <span className="badge badge-ok">{counts.fixed} fixed automatically</span>
                </div>
              </div>
            </section>

            {nFrames && winner.frames ? (
              <div className="thumbs" data-testid="findings-thumbs">
                {Array.from({ length: nFrames }, (_, i) => {
                  const b = perFrame.get(i);
                  const tone = b?.fail ? 'fail' : b?.warn ? 'warn' : b?.info ? 'info' : null;
                  return (
                    <button
                      type="button"
                      key={i}
                      className={`thumb${frameFilter === i ? ' is-on' : ''}`}
                      onClick={() => setFrameFilter((f) => (f === i ? null : i))}
                      data-testid="findings-thumb"
                      data-frame={i}
                      aria-pressed={frameFilter === i}
                      title={frameFilter === i ? 'Show all frames' : `Show findings for frame ${i + 1}`}
                    >
                      <span className={`thumb-img ${additive ? 'additive' : 'checker'}`}>
                        <img src={fileUrl(`${winner.frames}/${pad3(i)}.png`)} alt={`Frame ${i + 1}`} loading="lazy" />
                      </span>
                      <span className="thumb-num num">{i + 1}</span>
                      {tone && b ? (
                        <span className={`thumb-badge lv-${tone}`} data-testid="findings-thumb-badge">
                          {b.fail + b.warn + b.info}
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ) : null}

            <div className="findings-filter">
              <Segmented<Filter>
                value={filter}
                onChange={setFilter}
                testid="findings-filter"
                label="Filter findings"
                options={[
                  { value: 'all', label: `All ${findings.length}` },
                  { value: 'fail', label: `Fail ${counts.fail}` },
                  { value: 'warn', label: `Warn ${counts.warn}` },
                  { value: 'info', label: `Info ${counts.info}` },
                  { value: 'fixed', label: `Fixed ${counts.fixed}` },
                ]}
              />
              {frameFilter !== null ? (
                <Button size="sm" variant="ghost" icon="x" onClick={() => setFrameFilter(null)} data-testid="findings-clear-frame">
                  Frame {frameFilter + 1} only
                </Button>
              ) : null}
            </div>

            {findings.length === 0 ? (
              <p className="muted" data-testid="findings-none">
                No findings: this candidate passed every check.
              </p>
            ) : shown.length === 0 ? (
              <p className="muted" data-testid="findings-filter-empty">
                Nothing matches this filter.
              </p>
            ) : null}
            <div className="findings-table" role="table" aria-label="Findings" data-testid="findings-list" hidden={shown.length === 0}>
              <div className="ft-row ft-head" role="row">
                <span role="columnheader">Level</span>
                <span role="columnheader">Check</span>
                <span role="columnheader">What was found</span>
                <span role="columnheader">Frames</span>
                <span role="columnheader">Value</span>
                <span role="columnheader">Remedy</span>
              </div>
              {shown.map(({ f, idx: i }) => {
                const act = f.auto_fixed ? { kind: 'none' as const } : remedyAction(f.remedy);
                const c = act.kind !== 'none' ? cost(act.costKey) : null;
                const after = act.kind === 'inbetween' ? inbetweenAfter(f) : null;
                const lab = f.source === 'judge' ? labelOf(i) : null;
                return (
                  <div className="ft-row" role="row" key={i} data-testid="finding-row" data-metric={f.metric} data-level={f.level}>
                    <span role="cell">
                      <LevelPill level={f.level} testid="finding-level" />
                    </span>
                    <span role="cell" className="ft-metric">
                      <strong>{metricLabel(f.metric)}</strong>
                      <span className="muted">
                        {f.metric}
                        {f.source === 'judge' ? `, judge${f.corroborated ? ' (corroborated)' : ''}` : ''}
                      </span>
                    </span>
                    <span role="cell" className="ft-msg">
                      <span data-testid="finding-message">{f.message}</span>
                      {f.source === 'judge' ? (
                        <span className="label-btns" role="group" aria-label="Is this finding right?">
                          <IconButton
                            icon="thumbUp"
                            label="Real problem"
                            className="lbl-up"
                            active={lab === true}
                            onClick={() => void doLabel(i, true)}
                            data-testid="finding-label-up"
                          />
                          <IconButton
                            icon="thumbDown"
                            label="False alarm"
                            className="lbl-down"
                            active={lab === false}
                            onClick={() => void doLabel(i, false)}
                            data-testid="finding-label-down"
                          />
                        </span>
                      ) : null}
                    </span>
                    <span role="cell" className="ft-frames">
                      {f.frames.length ? (
                        f.frames.map((fr) => (
                          <button
                            key={fr}
                            type="button"
                            className="frame-chip num"
                            onClick={() => setFrameFilter(fr)}
                            data-testid="finding-frame-chip"
                            title={`Show frame ${fr + 1}`}
                          >
                            {fr + 1}
                          </button>
                        ))
                      ) : (
                        <span className="muted">{frameList([])}</span>
                      )}
                    </span>
                    <span role="cell" className="ft-val num" data-testid="finding-value">
                      {fmtVal(f.value)}
                      {f.threshold !== null && f.threshold !== undefined ? <span className="muted"> / {fmtVal(f.threshold)}</span> : null}
                    </span>
                    <span role="cell" className="ft-remedy">
                      {f.auto_fixed ? (
                        <span className="badge badge-ok" data-testid="finding-auto-fixed">
                          Fixed automatically
                        </span>
                      ) : act.kind === 'repair' ? (
                        f.frames.length ? (
                          f.frames.slice(0, 3).map((fr) => {
                            const tag = `${i}-${fr}`;
                            return (
                              <Button
                                key={fr}
                                size="sm"
                                icon="sparkle"
                                busy={busy === tag}
                                disabled={busy !== null || !repairable}
                                title={repairable ? undefined : 'Only finished jobs can be repaired.'}
                                onClick={() => void doRepair(f, fr, act.repair, tag)}
                                data-testid="finding-remedy"
                                data-remedy={f.remedy}
                              >
                                {frameRemedyLabel(f.remedy, fr)}
                                {c !== null ? ` (${usd(c)})` : ''}
                              </Button>
                            );
                          })
                        ) : (
                          <span className="muted">{remedyLabel(f.remedy)}</span>
                        )
                      ) : act.kind === 'inbetween' ? (
                        <Button
                          size="sm"
                          icon="inbetween"
                          busy={busy === `ib-${i}`}
                          disabled={busy !== null || !repairable || after === null}
                          title={repairable ? undefined : 'Only finished jobs can take an in-between.'}
                          onClick={() => after !== null && void doInbetween(after, `ib-${i}`)}
                          data-testid="finding-inbetween"
                          data-remedy={f.remedy}
                        >
                          Insert in-between after frame {(after ?? 0) + 1}
                          {c !== null ? ` (${usd(c)})` : ''}
                        </Button>
                      ) : act.kind === 'reroll' ? (
                        <Button
                          size="sm"
                          icon="refresh"
                          busy={busy === `r-${i}`}
                          disabled={busy !== null}
                          onClick={() => void doReroll(`r-${i}`)}
                          data-testid="finding-remedy"
                          data-remedy={f.remedy}
                        >
                          {remedyLabel(f.remedy)}
                          {c !== null ? ` (${usd(c)})` : ''}
                        </Button>
                      ) : act.kind === 'layout' ? (
                        <a
                          className="btn btn-sm"
                          href={href('sheet', anim, { job: jobId, cand: winner.id })}
                          data-testid="finding-remedy"
                          data-remedy={f.remedy}
                        >
                          {remedyLabel(f.remedy)} (free)
                        </a>
                      ) : (
                        <span className="muted" data-testid="finding-remedy-text">
                          {remedyLabel(f.remedy)}
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
