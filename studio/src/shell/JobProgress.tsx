import { useEffect, useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEngineEvent, useEvents } from '../lib/events';
import { DECISION_LABEL, isActive, q, routeLabel, stepLabel, usd } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { EngineEvent, JobState } from '../lib/types';
import { Button, ErrorNote, QScore, Spinner, StatePill } from '../ui/controls';
import { useAdditive } from './AnimHeader';
import { Icon } from '../ui/Icon';

function eventLine(e: EngineEvent): string | null {
  switch (e.type) {
    case 'step_started':
      return `${stepLabel(String(e.step))} started`;
    case 'step_done':
      return `${stepLabel(String(e.step))} done`;
    case 'candidate_ready':
      return `Candidate ${e.candidate} received`;
    case 'candidate_scored':
      return `Candidate ${e.candidate} scored Q ${q(e.score)}${e.accepted ? ', accepted' : ''}`;
    case 'decision':
      return `Decision: ${DECISION_LABEL[String(e.action)] ?? e.action}${e.reason ? `. ${e.reason}` : ''}`;
    case 'spend':
      return `${e.model}: ${e.cached ? 'cache hit, free' : usd(e.cost)}`;
    case 'retry':
      return `Retrying ${e.provider} (attempt ${e.attempt})`;
    case 'approval_required':
      return `Waiting for approval: ${e.reason}`;
    case 'job_done':
      return 'Job finished';
    case 'job_failed':
      return `Job failed: ${e.error}`;
    case 'job_cancelled':
      return 'Job cancelled';
    default:
      return null;
  }
}

export function JobProgress({ jobId, compact }: { jobId: string; compact?: boolean }) {
  const { toast } = useStore();
  const ev = useEvents();
  const job = useAsync<JobState>(() => api.job(jobId), jobId);
  const soon = useDebounced(() => void job.reload(), 250);
  const [cancelling, setCancelling] = useState(false);

  useEngineEvent((e) => {
    if (e.job === jobId) soon();
  });

  // A slow poll backs up the event stream while the job is active.
  const st = job.data;
  const additive = useAdditive(st?.anim_id);
  const reload = job.reload;
  const activeNow = st ? isActive(st) : false;
  useEffect(() => {
    if (!activeNow) return;
    const t = window.setInterval(() => void reload(), 4000);
    return () => window.clearInterval(t);
  }, [activeNow, reload]);

  if (!st) {
    return job.error ? <ErrorNote>{job.error}</ErrorNote> : <Spinner label="Loading job…" />;
  }

  const log = ev.log.filter((e) => e.job === jobId).map((e) => ({ e, text: eventLine(e) })).filter((x) => x.text).slice(-8);
  const active = isActive(st);
  const expectExport = !st.steps.some((s) => s.name === 'export');

  const cancel = async () => {
    setCancelling(true);
    try {
      await api.cancelJob(st.id);
      toast('Cancel requested; the job stops after its current step.');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className={`jobprog${compact ? ' is-compact' : ''}`} data-testid="job-progress" data-job={st.id} data-state={st.state}>
      <div className="jobprog-head">
        <StatePill state={st.state} testid="job-progress-state" />
        <span className="jobprog-route">{routeLabel(st.route)}</span>
        {st.key ? (
          <span className="jobprog-key" title={`Chroma key ${st.key}`}>
            <span className="swatch" style={{ background: st.key }} /> {st.key}
          </span>
        ) : null}
        <span className="jobprog-spend" data-testid="job-progress-spend">
          Spent <strong className="num">{usd(st.spend)}</strong>
        </span>
        <span className="spacer" />
        {active ? (
          <Button size="sm" variant="danger" icon="stop" busy={cancelling} onClick={() => void cancel()} data-testid="job-progress-cancel">
            Cancel
          </Button>
        ) : null}
      </div>

      <ol className="steps" data-testid="job-progress-steps">
        {st.steps.map((s) => (
          <li key={s.name} className={`step step-${s.status}`} data-testid="job-progress-step" data-step={s.name} data-status={s.status}>
            {s.status === 'done' ? <Icon name="check" size={13} /> : <span className="spinner" aria-hidden="true" />}
            {stepLabel(s.name)}
          </li>
        ))}
        {active && expectExport ? <li className="step step-pending">Export</li> : null}
      </ol>

      {st.error ? <ErrorNote testid="job-progress-error">{st.error}</ErrorNote> : null}

      {st.candidates.length ? (
        <div className="jobprog-cands" data-testid="job-progress-candidates">
          {st.candidates.map((c) => (
            <div key={c.id} className={`jp-cand${st.winner === c.id ? ' is-winner' : ''}`} data-testid="job-progress-candidate" data-candidate={c.id}>
              <div className={`jp-cand-img ${additive ? 'additive' : 'checker'}`}>
                {c.sheet ? (
                  <img src={fileUrl(c.sheet)} alt={`Candidate ${c.id}`} loading="lazy" />
                ) : c.frames ? (
                  <img src={fileUrl(`${c.frames}/000.png`)} alt={`Candidate ${c.id}, first frame`} loading="lazy" />
                ) : (
                  <span className="muted">…</span>
                )}
              </div>
              <div className="jp-cand-foot">
                <span>{c.id}</span>
                {typeof c.score === 'number' ? <QScore score={c.score} accepted={c.accepted} size="sm" /> : <span className="muted">scoring</span>}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {!compact && st.decisions.length ? (
        <ul className="decisions" data-testid="job-progress-decisions">
          {st.decisions.map((d, i) => (
            <li key={i} data-testid="job-progress-decision">
              <span className={`badge ${d.action === 'accept' ? 'badge-ok' : d.action === 'best_effort' ? 'badge-warn' : 'badge-accent'}`}>
                {DECISION_LABEL[d.action] ?? d.action}
              </span>
              <span className="decision-reason">{d.reason}</span>
              <span className="num muted">Q {q(d.score)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {!compact && log.length ? (
        <ul className="evlog" data-testid="job-progress-log" aria-label="Recent activity">
          {log.map(({ e, text }, i) => (
            <li key={`${e.ts}-${i}`}>
              <span className="num muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
              <span>{text}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {st.state === 'done' && st.anim_id ? (
        <div className="jobprog-done" data-testid="job-progress-done">
          <span>
            Finished with <strong>Q {q(st.result?.score)}</strong>
            {st.result?.accepted ? ', auto-accepted.' : '. Review it before exporting.'}
          </span>
          <Button size="sm" icon="candidates" onClick={() => navigate('candidates', st.anim_id, { job: st.id })} data-testid="job-progress-open-candidates">
            Review candidates
          </Button>
          <Button size="sm" icon="frames" onClick={() => navigate('frames', st.anim_id)} data-testid="job-progress-open-frames">
            Open frames
          </Button>
        </div>
      ) : null}
    </div>
  );
}
