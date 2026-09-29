import { useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEngineEvent } from '../lib/events';
import { DECISION_LABEL, frameList, isActive, metricLabel, q, routeLabel, usd } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { useReports } from '../lib/reports';
import { href, navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { Candidate, JobState, Report } from '../lib/types';
import { host } from '../host';
import { AnimHeader, pickJob, useAdditive, useAnimJobs } from '../shell/AnimHeader';
import { JobProgress } from '../shell/JobProgress';
import { Button, Empty, ErrorNote, LevelPill, QScore, Spinner } from '../ui/controls';

function kindLabel(c: Candidate): string {
  switch (c.kind) {
    case 'generate':
      return 'Generated';
    case 'reroll':
      return 'Re-roll';
    case 'repair':
      return c.parent ? `Repair of ${c.parent}` : 'Repair';
    case 'inbetween':
      return c.parent ? `In-between added to ${c.parent}` : 'In-between';
    case 'layout':
      return c.parent ? `Confirmed grid of ${c.parent}` : 'Confirmed grid';
    default:
      return c.kind;
  }
}

function FramesStrip({ cand, report }: { cand: Candidate; report?: Report }) {
  const n = report?.frames.length ?? 0;
  if (!cand.frames || !n) return <span className="muted">No preview</span>;
  return (
    <div className="frames-strip">
      {Array.from({ length: n }, (_, i) => (
        <img key={i} src={fileUrl(`${cand.frames}/${String(i).padStart(3, '0')}.png`)} alt={`Frame ${i + 1}`} loading="lazy" />
      ))}
    </div>
  );
}

function CandidateCard({
  job,
  cand,
  report,
  onChoose,
  choosing,
  additive,
}: {
  job: JobState;
  cand: Candidate;
  report?: Report;
  onChoose: () => void;
  choosing: boolean;
  additive: boolean;
}) {
  const { toast } = useStore();
  const winner = job.winner === cand.id;
  const handPicked = !!cand.chosen;
  const scored = typeof cand.score === 'number';
  const top = cand.top ?? [];
  return (
    <article
      className={`cand${winner ? ' is-winner' : ''}`}
      data-testid="candidate-card"
      data-candidate={cand.id}
      data-winner={winner ? 'true' : 'false'}
      data-chosen={handPicked ? 'true' : 'false'}
    >
      <header className="cand-head">
        <span className="cand-id">{cand.id}</span>
        <span className="muted">{kindLabel(cand)}</span>
        <span className="spacer" />
        {handPicked ? (
          <span className="badge" data-testid="candidate-chosen-badge" title="You chose this candidate; it outranks the automatic pick.">
            Picked by you
          </span>
        ) : null}
        {winner ? (
          <span className="badge badge-accent" data-testid="candidate-winner-badge">
            Winner
          </span>
        ) : null}
      </header>
      <div className={`cand-img ${additive ? 'additive' : 'checker'}`}>
        {cand.sheet ? (
          <img src={fileUrl(cand.sheet)} alt={`Sheet of candidate ${cand.id}`} data-testid="candidate-sheet" />
        ) : (
          <FramesStrip cand={cand} report={report} />
        )}
      </div>
      <div className="cand-score">
        <QScore score={cand.score} accepted={cand.accepted} testid="candidate-score" />
        {scored ? (
          cand.accepted ? (
            <span className="badge badge-ok" data-testid="candidate-accepted-badge">
              Accepted
            </span>
          ) : (
            <span className="badge" data-testid="candidate-not-accepted">
              Not accepted
            </span>
          )
        ) : (
          <Spinner label="Analyzing" />
        )}
        {report ? <span className="muted num">{report.frames.length} frames</span> : null}
      </div>
      {top.length ? (
        <ul className="cand-top" data-testid="candidate-top-findings">
          {top.slice(0, 3).map((f, i) => (
            <li key={i} data-testid="candidate-finding">
              <LevelPill level={f.level} />
              <span>
                <strong>{metricLabel(f.metric)}.</strong> {f.message}
                {f.frames.length ? <span className="muted"> (frames {frameList(f.frames)})</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : scored ? (
        <p className="muted cand-clean">No findings worth mentioning.</p>
      ) : null}
      <footer className="cand-actions">
        <Button
          variant={winner ? 'quiet' : 'primary'}
          icon={winner ? 'check' : undefined}
          size="sm"
          disabled={winner || !scored || choosing || isActive(job)}
          busy={choosing}
          onClick={onChoose}
          data-testid="candidate-use"
        >
          {winner ? 'In use' : 'Use this one'}
        </Button>
        {cand.sheet && cand.report ? (
          <a className="btn btn-sm btn-quiet" href={href('sheet', job.anim_id, { job: job.id, cand: cand.id })} data-testid="candidate-review-sheet">
            Review sheet
          </a>
        ) : null}
        {cand.html ? (
          <Button
            size="sm"
            variant="ghost"
            icon="report"
            data-testid="candidate-report-link"
            onClick={async () => {
              const r = await host.openExternal(fileUrl(cand.html));
              if (!r.ok && r.message) toast(r.message, 'error');
            }}
          >
            Report
          </Button>
        ) : null}
      </footer>
    </article>
  );
}

export function Candidates({ anim, job: wanted }: { anim: string | null; job: string | null }) {
  const { toast, reloadAnimations, reloadJobs, jobs: allJobs } = useStore();
  const jobs = useAnimJobs(anim);
  const additive = useAdditive(anim);
  const picked = pickJob(jobs, wanted);
  const jobId = picked?.id ?? wanted ?? null;
  const job = useAsync<JobState>(() => api.job(jobId!), jobId);
  const soon = useDebounced(() => void job.reload(), 300);
  const [choosing, setChoosing] = useState<string | null>(null);
  useEngineEvent((e) => {
    if (e.job === jobId) soon();
  });
  const st = job.data;
  const reports = useReports(st?.id ?? null, st?.candidates ?? []);

  const choose = async (cid: string) => {
    if (!st) return;
    setChoosing(cid);
    try {
      await api.chooseWinner(st.id, cid);
      toast(`Using ${cid}. The export was rewritten from it.`, 'ok');
      await job.reload();
      void reloadAnimations();
      void reloadJobs();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setChoosing(null);
    }
  };

  const rounds = new Map<number, Candidate[]>();
  for (const c of st?.candidates ?? []) {
    rounds.set(c.round, [...(rounds.get(c.round) ?? []), c]);
  }

  return (
    <div className="screen" data-testid="candidates-screen">
      <AnimHeader screen="candidates" title="Candidates" anim={anim} job={jobId} showJob>
        {st && !isActive(st) && anim ? (
          <Button
            size="sm"
            icon="refresh"
            variant="quiet"
            data-testid="candidates-reroll"
            onClick={async () => {
              try {
                const nj = await api.startJob(anim, Math.floor(Math.random() * 1e6));
                toast('Started a new job with a fresh seed.', 'ok');
                void reloadJobs();
                navigate('candidates', anim, { job: nj.id });
              } catch (e) {
                toast(errText(e), 'error');
              }
            }}
          >
            New job
          </Button>
        ) : null}
      </AnimHeader>
      <div className="screen-body">
        {!anim ? <Empty title="Pick an animation" testid="candidates-no-anim">Choose an animation above, or plan one in the builder.</Empty> : null}
        {anim && allJobs && !jobId ? (
          <Empty
            title="No jobs for this animation yet"
            testid="candidates-no-job"
            action={
              <a className="btn btn-primary" href={href('builder')}>
                Open the builder
              </a>
            }
          >
            Generate a job from the builder and its candidates appear here.
          </Empty>
        ) : null}
        {job.error ? <ErrorNote testid="candidates-error">{job.error}</ErrorNote> : null}
        {jobId && !st && !job.error ? <Spinner label="Loading job…" /> : null}
        {st ? (
          <>
            {isActive(st) ? (
              <div className="panel cand-live">
                <div className="panel-body">
                  <JobProgress jobId={st.id} compact />
                </div>
              </div>
            ) : (
              <div className="job-summary" data-testid="candidates-job-summary">
                <span className={`state state-${st.state}`}>{st.state === 'done' ? 'Done' : st.state}</span>
                <span>{routeLabel(st.route)}</span>
                <span>
                  Spent <strong className="num">{usd(st.spend)}</strong>
                </span>
                <span>
                  {st.candidates.length} candidate{st.candidates.length === 1 ? '' : 's'}
                </span>
                {st.decisions.length ? (
                  <span className="job-summary-decisions">
                    {st.decisions.map((d, i) => (
                      <span key={i} className="decision-chip" title={d.reason}>
                        {DECISION_LABEL[d.action] ?? d.action} <span className="num">Q {q(d.score)}</span>
                      </span>
                    ))}
                  </span>
                ) : null}
                {st.error ? <ErrorNote>{st.error}</ErrorNote> : null}
              </div>
            )}
            {st.candidates.length === 0 ? <p className="muted">No candidates yet.</p> : null}
            <div className="cand-rounds" data-testid="candidate-list">
              {[...rounds.entries()].map(([rnd, cands]) => (
                <section key={rnd} className="cand-round" data-testid="candidate-round" data-round={rnd}>
                  <div className="cand-round-label">{rnd === 0 ? 'First round' : `Round ${rnd + 1}`}</div>
                  <div className="cand-row">
                    {cands.map((c) => (
                      <CandidateCard
                        key={c.id}
                        job={st}
                        cand={c}
                        report={reports[c.id]}
                        choosing={choosing === c.id}
                        onChoose={() => void choose(c.id)}
                        additive={additive}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
