import { useState } from 'react';
import { useEvents } from '../lib/events';
import { isAdditive, JOB_STATE_LABEL, timeAgo } from '../lib/format';
import { navigate } from '../lib/router';
import { animationsOf, useStore } from '../lib/store';
import type { AnimationSummary, CharacterRecord, FinalMeta, JobState } from '../lib/types';
import { ANIM_TABS } from '../shell/NavRail';
import { Button, Menu, QScore } from '../ui/controls';
import { Flipbook } from '../ui/Flipbook';
import { Icon } from '../ui/Icon';

const ACTIVE_STATES = new Set(['queued', 'running', 'awaiting_approval']);

/** A short fingerprint of an export's metadata: frame count, durations and cells change with every rewrite. */
function metaStamp(final: FinalMeta): string {
  const s = JSON.stringify([final.frames, final.durations, final.rects, final.size]);
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

function AnimationCard({
  anim,
  final,
  jobs,
  playing,
  onToggle,
}: {
  anim: AnimationSummary;
  final: FinalMeta;
  jobs: JobState[];
  playing: boolean;
  onToggle: () => void;
}) {
  const { setCurrentAnim } = useStore();
  const ev = useEvents();
  const additive = isAdditive(anim);
  const action = anim.spec.action.replace(/-/g, ' ');
  // the export's version: an export event in this session, the newest job and the metadata itself,
  // so frames rewritten by the CLI or another window are not served from the browser cache either
  const bust = `${ev.exported[anim.id] ?? 0}-${anim.latest_job?.updated ?? ''}-${metaStamp(final)}`;
  const scored = jobs.find((j) => j.state === 'done' && typeof j.result?.score === 'number');
  const busy = jobs.find((j) => ACTIVE_STATES.has(j.state));
  const when = anim.latest_job?.updated ?? anim.latest_job?.created;
  const seconds = final.durations.reduce((a, b) => a + b, 0) / 1000;

  const open = (screen: string) => {
    setCurrentAnim(anim.id);
    navigate(screen, anim.id);
  };

  return (
    <article
      className="anim-card"
      data-testid="character-animation"
      data-anim={anim.id}
      data-blend={additive ? 'add' : 'normal'}
      data-loop={final.loop ? 'true' : 'false'}
    >
      <button
        type="button"
        className={`anim-stage ${additive ? 'additive' : 'checker'}`}
        aria-pressed={playing}
        aria-label={`${playing ? 'Pause' : 'Play'} ${action}`}
        title={playing ? 'Pause' : 'Play'}
        onClick={onToggle}
        data-testid="character-animation-play"
      >
        <Flipbook
          key={bust}
          anim={anim.id}
          final={final}
          bust={bust}
          playing={playing}
          alt={`${anim.character ?? ''} ${action}`.trim()}
          testid="character-animation-player"
        />
        <span className="anim-stage-btn" aria-hidden="true">
          <Icon name={playing ? 'pause' : 'play'} size={14} />
        </span>
      </button>
      <div className="anim-card-body">
        <div className="anim-card-title">
          <span className="anim-card-name" data-testid="character-animation-name" title={anim.id}>
            {action}
          </span>
          <span className="badge" title={`Facing ${anim.spec.facing}`}>
            {anim.spec.facing}
          </span>
          <span className="spacer" />
          {scored ? <QScore score={scored.result.score} accepted={scored.result.accepted} size="sm" /> : null}
        </div>
        <div className="anim-card-facts muted num" data-testid="character-animation-facts">
          <span>{final.frames} frames</span>
          <span>{seconds.toFixed(seconds < 10 ? 2 : 1)} s</span>
          <span>
            <Icon name={final.loop ? 'loop' : 'right'} size={12} /> {final.loop ? 'Loops' : 'Plays once'}
          </span>
        </div>
        <div className="anim-card-foot">
          {busy ? (
            <span className="badge badge-accent" data-testid="character-animation-busy">
              {JOB_STATE_LABEL[busy.state] ?? busy.state}
            </span>
          ) : when ? (
            <span className="muted small-print">{timeAgo(when)}</span>
          ) : null}
          <span className="spacer" />
          <Menu
            label={`Open ${anim.id}`}
            align="end"
            className="btn btn-sm btn-default"
            testid="character-animation-open"
            menuTestid="character-animation-menu"
            data={{ anim: anim.id }}
            trigger={
              <>
                Open <Icon name="chevron" size={14} />
              </>
            }
            entries={ANIM_TABS.map((t) => ({
              key: t.screen,
              label: (
                <>
                  <Icon name={t.icon} size={16} />
                  {t.label}
                </>
              ),
              onSelect: () => open(t.screen),
              testid: 'character-animation-menu-item',
              data: { screen: t.screen },
            }))}
          />
        </div>
      </div>
    </article>
  );
}

/** The active character's finished animations: each plays in place and opens on any animation tab. */
export function CharacterAnimations({ rec }: { rec: CharacterRecord }) {
  const { animations, jobs } = useStore();
  const mine = animationsOf(animations, rec.name);
  const done = mine.filter((a): a is AnimationSummary & { final: FinalMeta } => !!a.final && a.final.frames > 0);
  const unfinished = mine.length - done.length;
  const [playing, setPlaying] = useState<Set<string>>(() => new Set());
  const allPlaying = done.length > 0 && done.every((a) => playing.has(a.id));

  const toggle = (id: string) =>
    setPlaying((p) => {
      const next = new Set(p);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  if (animations === null) return null;
  return (
    <section className="panel anim-shelf" data-testid="character-animations" data-character={rec.name} aria-label={`Animations of ${rec.name}`}>
      <div className="panel-head">
        <h2>Animations</h2>
        <span className="muted num" data-testid="character-animations-count">
          {done.length} finished{unfinished ? `, ${unfinished} not exported yet` : ''}
        </span>
        <span className="spacer" />
        {done.length > 1 ? (
          <Button
            size="sm"
            variant="quiet"
            icon={allPlaying ? 'pause' : 'play'}
            data-testid="character-animations-play-all"
            onClick={() => setPlaying(allPlaying ? new Set() : new Set(done.map((a) => a.id)))}
          >
            {allPlaying ? 'Pause all' : 'Play all'}
          </Button>
        ) : null}
        {rec.approved ? (
          <Button size="sm" variant="quiet" icon="builder" data-testid="character-animations-new" onClick={() => navigate('builder')}>
            New animation
          </Button>
        ) : null}
      </div>
      <div className="panel-body">
        {done.length ? (
          <div className="anim-cards">
            {done.map((a) => (
              <AnimationCard
                key={a.id}
                anim={a}
                final={a.final}
                jobs={(jobs ?? []).filter((j) => j.anim_id === a.id)}
                playing={playing.has(a.id)}
                onToggle={() => toggle(a.id)}
              />
            ))}
          </div>
        ) : (
          <p className="muted" data-testid="character-animations-empty">
            No finished animations for {rec.name} yet.{' '}
            {rec.approved ? 'Plan one in the builder; finished animations play here.' : 'Approve it to start animating.'}
          </p>
        )}
      </div>
    </section>
  );
}
