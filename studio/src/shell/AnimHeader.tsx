import { useEffect, type ReactNode } from 'react';
import { isAdditive, JOB_STATE_LABEL, q, shortJob } from '../lib/format';
import { navigate } from '../lib/router';
import { animationsOf, useStore } from '../lib/store';
import type { JobState } from '../lib/types';

/** Jobs of one animation, newest first. */
export function useAnimJobs(anim: string | null): JobState[] {
  const { jobs } = useStore();
  return (jobs ?? []).filter((j) => j.anim_id === anim);
}

/** Whether an animation is an additive effect, so its previews go on a dark ground, screen-blended. */
export function useAdditive(anim: string | null | undefined): boolean {
  const { animations } = useStore();
  return isAdditive((animations ?? []).find((a) => a.id === anim));
}

/** The job a screen should show: the one in the URL, else the newest finished one with a winner. */
export function pickJob(jobs: JobState[], wanted: string | null): JobState | null {
  if (wanted) return jobs.find((j) => j.id === wanted) ?? null;
  return jobs.find((j) => j.state === 'done' && j.winner) ?? jobs[0] ?? null;
}

export function AnimHeader({
  screen,
  title,
  anim,
  job,
  showJob,
  children,
}: {
  screen: string;
  title: string;
  anim: string | null;
  job?: string | null;
  showJob?: boolean;
  children?: ReactNode;
}) {
  const { animations, setCurrentAnim, activeCharacter } = useStore();
  const jobs = useAnimJobs(anim);
  // only the active character's animations: the header's character switcher picks whose
  const mine = animationsOf(animations, activeCharacter);

  useEffect(() => {
    if (anim) setCurrentAnim(anim);
  }, [anim, setCurrentAnim]);

  return (
    <div className="screen-head">
      <h1 className="screen-title">{title}</h1>
      <div className="context">
        <label className="context-field">
          <span>Animation</span>
          <select
            value={anim ?? ''}
            data-testid="context-animation-select"
            onChange={(e) => navigate(screen, e.target.value)}
          >
            {!anim ? <option value="">Choose…</option> : null}
            {mine.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id}
              </option>
            ))}
            {anim && !mine.some((a) => a.id === anim) ? <option value={anim}>{anim}</option> : null}
          </select>
        </label>
        {showJob ? (
          <label className="context-field">
            <span>Job</span>
            <select
              value={job ?? ''}
              data-testid="context-job-select"
              onChange={(e) => navigate(screen, anim, { job: e.target.value })}
              disabled={!jobs.length}
            >
              {!jobs.length ? <option value="">No jobs yet</option> : null}
              {job && !jobs.some((j) => j.id === job) ? <option value={job}>{shortJob(job)}</option> : null}
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {shortJob(j.id)} ({JOB_STATE_LABEL[j.state] ?? j.state}
                  {j.result?.score !== undefined ? `, Q ${q(j.result.score)}` : ''})
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <div className="screen-actions">{children}</div>
    </div>
  );
}
