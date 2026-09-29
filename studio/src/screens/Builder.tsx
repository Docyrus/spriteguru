import { useEffect, useRef, useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEvents } from '../lib/events';
import { kindLabel, routeLabel, thumbOf, usd } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { href } from '../lib/router';
import { BUILDER_JOB_KEY, errText, fetchActions, useActiveRecord, useStore } from '../lib/store';
import type { ActionInfo, AnimationSummary, Estimate, Facing, Motion, SubjectKind, View } from '../lib/types';
import { JobProgress } from '../shell/JobProgress';
import { openCharacterSwitcher } from '../shell/Switchers';
import { Button, Empty, ErrorNote, Field, Segmented, Spinner, Toggle } from '../ui/controls';
import { useGenerationLock } from '../lib/cloud';
import { AccessBanner } from '../shell/Account';

interface Form {
  character: string;
  action: string;
  facing: Facing;
  view: View;
  frames: number;
  loop: boolean;
  motion: Motion;
}

const JOB_KEY = BUILDER_JOB_KEY;

/** The action a subject starts on: each kind's signature one when the engine lists it, else its first. */
const DEFAULT_ACTION: Record<SubjectKind, string> = { character: 'walk', vehicle: 'idle', machine: 'work', effect: 'projectile' };

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function Builder() {
  const { locked, status: lockStatus } = useGenerationLock();
  const { characters, animations, toast, setCurrentAnim, project, activeCharacter } = useStore();
  const ev = useEvents();
  // the builder animates the active character, picked in the header
  const active = useActiveRecord();

  const [form, setForm] = useState<Form>({
    character: '',
    action: '',
    facing: 'E',
    view: 'side',
    frames: 8,
    loop: true,
    motion: 'in-place',
  });
  const [planned, setPlanned] = useState<{ key: string; anim: AnimationSummary; est: Estimate; at: number } | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [seed, setSeed] = useState(0);
  const [starting, setStarting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(JOB_KEY);
    } catch {
      return null;
    }
  });

  const jobPanel = useRef<HTMLDivElement>(null);
  const [scrollToJob, setScrollToJob] = useState(false);
  useEffect(() => {
    if (scrollToJob && jobPanel.current) {
      jobPanel.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      setScrollToJob(false);
    }
  }, [scrollToJob, jobId]);

  // A new active character resets the action: the effect below picks its kind's default.
  const subject = active?.approved ? active : null;
  useEffect(() => {
    const name = subject?.name ?? '';
    setForm((f) => (f.character === name ? f : { ...f, character: name, action: '' }));
  }, [subject?.name]);

  // Only the active subject's kind of actions: a tank has no walk, a fireball no attack.
  const kind: SubjectKind = subject?.kind ?? 'character';
  const kindQ = useAsync<{ kind: SubjectKind; list: ActionInfo[] }>(
    () => fetchActions(kind).then((list) => ({ kind, list })),
    subject ? kind : null,
  );
  // tagged with its kind, so a list still loaded for the previous subject is never offered
  const actions = kindQ.data?.kind === kind ? kindQ.data.list : null;

  // A missing or foreign action (a new subject was picked) becomes that kind's default.
  useEffect(() => {
    if (!actions?.length) return;
    setForm((f) => {
      if (f.action && actions.some((x) => x.action === f.action)) return f;
      const a = actions.find((x) => x.action === DEFAULT_ACTION[kind]) ?? actions[0]!;
      return { ...f, action: a.action, frames: a.frames, loop: a.loop };
    });
  }, [actions, kind, form.action]);

  const key = JSON.stringify(form);
  const stale = planned !== null && planned.key !== key;
  const animId = form.character && form.action ? `${form.character}-${form.action}-${form.facing}` : null;
  const exists = animId ? (animations ?? []).some((a) => a.id === animId) : false;

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  const currentAction = actions?.find((a) => a.action === form.action) ?? null;

  const pickAction = (action: string) => {
    const a = actions?.find((x) => x.action === action);
    setForm((f) => ({ ...f, action, frames: a?.frames ?? f.frames, loop: a?.loop ?? f.loop }));
  };

  const pickView = (view: View) => {
    setForm((f) => ({ ...f, view, facing: view === 'side' && (f.facing === 'N' || f.facing === 'S') ? 'E' : f.facing }));
  };

  const plan = async () => {
    setPlanning(true);
    setPlanError(null);
    try {
      const anim = await api.createAnimation(form);
      const est = await api.estimate(anim.id);
      setPlanned({ key, anim, est, at: Date.now() });
      setCurrentAnim(anim.id);
    } catch (e) {
      setPlanError(errText(e));
    } finally {
      setPlanning(false);
    }
  };

  const generate = async () => {
    if (!planned) return;
    setStarting(true);
    try {
      const st = await api.startJob(planned.anim.id, seed);
      setJobId(st.id);
      setScrollToJob(true);
      try {
        sessionStorage.setItem(JOB_KEY, st.id);
      } catch {
        /* ignore */
      }
      toast(`Started ${planned.anim.id}.`, 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setStarting(false);
    }
  };

  if (characters && !subject) {
    const pending = active && !active.approved;
    return (
      <div className="screen" data-testid="builder-screen">
        <div className="screen-head">
          <h1 className="screen-title">Animation builder</h1>
        </div>
        <div className="screen-body">
          {!activeCharacter && characters.length ? (
            <Empty
              title="Choose a character"
              testid="builder-no-active"
              action={
                <Button variant="primary" icon="characters" onClick={openCharacterSwitcher} data-testid="builder-choose-character">
                  Choose a character
                </Button>
              }
            >
              The builder animates the character picked in the header.
            </Empty>
          ) : (
            <Empty
              title={pending ? `Approve ${active.name} first` : 'Approve a character first'}
              testid="builder-no-characters"
              action={
                <a className="btn btn-primary" href={href('characters')} data-testid="builder-go-characters">
                  Go to characters
                </a>
              }
            >
              Animations are drawn from a locked, approved character so every frame stays on model.
            </Empty>
          )}
        </div>
      </div>
    );
  }

  const est = planned?.est;
  const plan_ = est?.compiled.plan;
  const facingOpts: { value: Facing; label: string; disabled?: boolean; title?: string }[] = [
    { value: 'E', label: 'E', title: 'Facing east (right)' },
    { value: 'W', label: 'W', title: 'Facing west (left)' },
    { value: 'N', label: 'N', disabled: form.view === 'side', title: form.view === 'side' ? 'Side view uses E and W' : 'Facing north (away)' },
    { value: 'S', label: 'S', disabled: form.view === 'side', title: form.view === 'side' ? 'Side view uses E and W' : 'Facing south (toward camera)' },
  ];

  return (
    <div className="screen" data-testid="builder-screen">
      <div className="screen-head">
        <h1 className="screen-title">Animation builder</h1>
        {animId ? (
          <span className="muted" data-testid="builder-anim-id">
            {animId}
            {exists ? ' (replaces the current plan)' : ''}
          </span>
        ) : null}
      </div>
      <AccessBanner />
      <div className="screen-body builder-layout">
        <section className="panel builder-form" data-testid="builder-form" aria-label="Animation settings">
          <div className="panel-body form-stack">
            <Field label="Character" hint="The character picked in the header; every tab follows it.">
              <div className="subject-chip" data-testid="builder-character" data-kind={subject?.kind} data-name={subject?.name}>
                <span className={`subject-chip-thumb ${subject?.blend === 'add' ? 'additive' : 'checker'}`} aria-hidden="true">
                  {subject && thumbOf(subject) ? <img src={fileUrl(thumbOf(subject), ev.turnaround[subject.name]?.ts)} alt="" draggable={false} /> : null}
                </span>
                <span className="subject-chip-name">{subject?.name ?? '…'}</span>
                {subject ? <span className="badge">{kindLabel(subject.kind)}</span> : null}
                <span className="spacer" />
                <Button size="sm" variant="ghost" onClick={openCharacterSwitcher} data-testid="builder-character-change">
                  Change
                </Button>
              </div>
            </Field>
            <Field
              label="Action"
              htmlFor="b-action"
              hint={currentAction?.title ? `${sentence(currentAction.title)}.` : undefined}
            >
              <select
                id="b-action"
                value={form.action}
                onChange={(e) => pickAction(e.target.value)}
                disabled={!actions?.length}
                data-testid="builder-action"
                data-kind={kind}
              >
                {!actions ? <option value="">{kindQ.error ? 'Could not load actions' : 'Loading actions…'}</option> : null}
                {(actions ?? []).map((a) => (
                  <option key={a.action} value={a.action} title={a.title}>
                    {a.action} ({a.frames} frames{a.loop ? ', loop' : ''})
                  </option>
                ))}
              </select>
            </Field>
            {kindQ.error ? <ErrorNote testid="builder-actions-error">{kindQ.error}</ErrorNote> : null}
            <div className="field-row">
              <Field label="View">
                <Segmented<View>
                  value={form.view}
                  onChange={pickView}
                  testid="builder-view"
                  label="View"
                  options={[
                    { value: 'side', label: 'Side' },
                    { value: 'top-down', label: 'Top-down' },
                  ]}
                />
              </Field>
              <Field label="Facing">
                <Segmented<Facing> value={form.facing} onChange={(v) => set('facing', v)} testid="builder-facing" label="Facing" options={facingOpts} />
              </Field>
            </div>
            <Field label={<>Frames <strong className="num frames-val">{form.frames}</strong></>} htmlFor="b-frames">
              <div className="range-row">
                <input
                  id="b-frames"
                  type="range"
                  min={2}
                  max={16}
                  value={form.frames}
                  onChange={(e) => set('frames', Number(e.target.value))}
                  data-testid="builder-frames"
                />
                <input
                  type="number"
                  min={2}
                  max={16}
                  value={form.frames}
                  aria-label="Frame count"
                  onChange={(e) => set('frames', Math.max(2, Math.min(16, Number(e.target.value) || 2)))}
                  data-testid="builder-frames-input"
                />
              </div>
            </Field>
            <Field label="Motion">
              <Segmented<Motion>
                value={form.motion}
                onChange={(v) => set('motion', v)}
                testid="builder-motion"
                label="Motion"
                options={[
                  { value: 'in-place', label: 'In place' },
                  { value: 'root-motion', label: 'Root motion' },
                ]}
              />
            </Field>
            <Toggle checked={form.loop} onChange={(v) => set('loop', v)} label="Loop" hint="The last frame flows back into the first." testid="builder-loop" />
            {planError ? <ErrorNote testid="builder-plan-error">{planError}</ErrorNote> : null}
            <Button variant={planned && !stale ? 'default' : 'primary'} icon="grid" busy={planning} disabled={!form.character || !form.action} onClick={() => void plan()} data-testid="builder-plan">
              {planned && !stale ? 'Plan again' : 'Plan and estimate'}
            </Button>
          </div>
        </section>

        <section className="builder-main">
          {!planned && !planning ? (
            <Empty title="Plan before you spend" testid="builder-estimate-empty">
              Planning shows the route, the estimated cost and, for guided sheets, the exact guide canvas the model will draw on. Nothing is sent
              until you press Generate.
            </Empty>
          ) : null}
          {planning && !planned ? <Spinner label="Compiling the spec…" /> : null}
          {est ? (
            <div className={`panel estimate${stale ? ' is-stale' : ''}`} data-testid="builder-estimate">
              <div className="panel-head">
                <h2>Plan for {planned!.anim.id}</h2>
                <span className="badge badge-accent" data-testid="builder-route">
                  {routeLabel(est.compiled.route)}
                </span>
                {plan_ ? (
                  <span className="muted num" data-testid="builder-grid">
                    {plan_.cols} × {plan_.rows} grid, {plan_.cell_w} × {plan_.cell_h} px cells
                  </span>
                ) : null}
                <span className="spacer" />
                {stale ? (
                  <span className="badge badge-warn" data-testid="builder-estimate-stale">
                    Settings changed: plan again
                  </span>
                ) : null}
              </div>
              <div className="estimate-body">
                <div className="guide-stage checker" data-testid="builder-guide">
                  {est.guide_url ? (
                    <img src={fileUrl(est.guide_url, planned!.at)} alt="Guide canvas sent with the prompt" data-testid="builder-guide-image" />
                  ) : (
                    <p className="muted guide-none">This route has no guide canvas; the model animates from the reference view.</p>
                  )}
                </div>
                <div className="estimate-side">
                  <table className="cost-table" data-testid="builder-cost-lines">
                    <tbody>
                      {est.lines.map((l, i) => (
                        <tr key={i} data-testid="builder-cost-line">
                          <td>
                            <div>{l.what}</div>
                            <div className="muted">{l.model}</div>
                          </td>
                          <td className="num">{usd(l.usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td>Estimated total</td>
                        <td className="num" data-testid="builder-estimate-total">
                          {usd(est.estimate_usd)}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                  {project?.mode === 'synthetic' ? (
                    <p className="muted" data-testid="builder-synthetic-note">
                      Synthetic mode: the offline simulator answers every call, so nothing is spent.
                    </p>
                  ) : null}
                  {est.compiled.notes.length ? (
                    <ul className="notes" data-testid="builder-notes">
                      {est.compiled.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  ) : null}
                  <dl className="kv small">
                    <dt>Chroma key</dt>
                    <dd>
                      {est.compiled.key ? (
                        <>
                          <span className="swatch" style={{ background: est.compiled.key }} /> {est.compiled.key}
                        </>
                      ) : (
                        'auto'
                      )}
                    </dd>
                    {est.compiled.choreo ? (
                      <>
                        <dt>Choreography</dt>
                        <dd>{est.compiled.choreo}</dd>
                      </>
                    ) : null}
                    {est.compiled.flip_output ? (
                      <>
                        <dt>Facing</dt>
                        <dd>Drawn facing {est.compiled.facing_generated}, flipped on export</dd>
                      </>
                    ) : null}
                  </dl>
                  <div className="generate-row">
                    <Field label="Seed" htmlFor="b-seed">
                      <input id="b-seed" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value) || 0)} data-testid="builder-seed" />
                    </Field>
                    <Button
                      variant="primary"
                      icon="sparkle"
                      busy={starting}
                      disabled={stale || planning || locked}
                      title={locked ? lockStatus?.access.message : undefined}
                      onClick={() => void generate()}
                      data-testid="builder-generate"
                    >
                      Generate {est.estimate_usd > 0 ? `(${usd(est.estimate_usd)})` : ''}
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {jobId ? (
            <div className="panel" data-testid="builder-job" ref={jobPanel}>
              <div className="panel-head">
                <h2>Job</h2>
                <span className="muted">{jobId}</span>
                <span className="spacer" />
                <Button
                  size="sm"
                  variant="ghost"
                  icon="x"
                  onClick={() => {
                    setJobId(null);
                    try {
                      sessionStorage.removeItem(JOB_KEY);
                    } catch {
                      /* ignore */
                    }
                  }}
                  data-testid="builder-job-dismiss"
                >
                  Hide
                </Button>
              </div>
              <div className="panel-body">
                <JobProgress jobId={jobId} />
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
