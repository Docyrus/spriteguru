import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { spendSince, useEvents } from '../lib/events';
import { isActive, shortJob, stepLabel, usd } from '../lib/format';
import { navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import { BrandMark, Icon } from '../ui/Icon';
import { IconButton, StatePill } from '../ui/controls';
import { CharacterSwitcher, ProjectSwitcher } from './Switchers';

type Theme = 'light' | 'dark';

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const t = localStorage.getItem('spriteguru.theme');
      if (t === 'light' || t === 'dark') return t;
    } catch {
      /* ignore */
    }
    return systemTheme();
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    try {
      localStorage.setItem('spriteguru.theme', next);
    } catch {
      /* ignore */
    }
  };
  return [theme, toggle];
}

export function TopBar() {
  const { project, projectAt, jobs, toast, reloadJobs } = useStore();
  const ev = useEvents();
  const [theme, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const mode = project?.mode ?? '…';
  const session = (project?.ledger.session_usd ?? 0) + spendSince(ev, projectAt);
  const cap = project?.config.settings.session_cap_usd ?? 0;
  const pct = cap > 0 ? Math.min(100, (session / cap) * 100) : 0;
  const active = (jobs ?? []).filter(isActive);
  const recent = (jobs ?? []).slice(0, 12);

  const cancel = async (id: string) => {
    try {
      await api.cancelJob(id);
      toast('Cancel requested; the job stops after its current step.');
      void reloadJobs();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  return (
    <header className="topbar" data-testid="topbar">
      <div className="brand">
        <BrandMark dark={theme === 'dark'} />
        <span className="brand-name">SpriteGuru</span>
      </div>
      <div className="topbar-context">
        <ProjectSwitcher />
        <CharacterSwitcher />
      </div>
      {project ? (
        <span
          className={`mode-pill mode-${mode}`}
          data-testid="topbar-provider-mode"
          title={mode === 'live' ? 'Requests go to the AI providers and cost money' : 'Offline simulator: no requests, no spend'}
        >
          {mode === 'live' ? 'Live' : mode === 'synthetic' ? 'Synthetic' : mode}
        </span>
      ) : null}
      <div className="topbar-spacer" />
      <span className={`conn conn-${ev.conn}`} data-testid="topbar-connection" title={`Engine events: ${ev.conn}`}>
        <span className="conn-dot" aria-hidden="true" />
        <span className="conn-text">{ev.conn === 'open' ? 'Connected' : ev.conn === 'connecting' ? 'Connecting' : 'Reconnecting'}</span>
      </span>
      {project ? (
        <div className="spend" data-testid="topbar-session-spend" title={`Session spend against the ${usd(cap)} session cap`}>
          <span className="spend-label">Session</span>
          <span className="spend-value" data-testid="topbar-session-spend-value">{usd(session)}</span>
          <span className="spend-meter" aria-hidden="true">
            <span style={{ width: `${pct}%` }} className={pct > 85 ? 'hot' : ''} />
          </span>
          <span className="spend-cap">of {usd(cap, 0)}</span>
        </div>
      ) : null}
      {project ? (
        <div className="jobs-anchor" ref={popRef}>
          <button
            type="button"
            className={`jobs-btn${active.length ? ' has-active' : ''}`}
            data-testid="topbar-jobs"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            <Icon name="jobs" size={17} />
            <span data-testid="topbar-jobs-count">{active.length ? `${active.length} running` : 'Jobs'}</span>
          </button>
          {open ? (
            <div className="jobs-pop" data-testid="jobs-popover">
              <div className="jobs-pop-head">Recent jobs</div>
              {recent.length === 0 ? <div className="jobs-pop-empty">No jobs yet. Generate one from the builder.</div> : null}
              <ul>
                {recent.map((j) => (
                  <li key={j.id} data-testid="jobs-popover-item" data-job={j.id}>
                    <button
                      type="button"
                      className="jobs-pop-row"
                      data-testid="jobs-popover-open"
                      onClick={() => {
                        setOpen(false);
                        if (j.anim_id) navigate('candidates', j.anim_id, { job: j.id });
                      }}
                    >
                      <span className="jobs-pop-anim">{j.anim_id ?? j.id}</span>
                      <span className="jobs-pop-meta">
                        <StatePill state={j.state} />
                        <span>{j.step ? stepLabel(j.step) : shortJob(j.id)}</span>
                        <span className="num">{usd(j.spend)}</span>
                      </span>
                    </button>
                    {isActive(j) ? (
                      <IconButton icon="stop" label="Cancel job" data-testid="jobs-popover-cancel" onClick={() => void cancel(j.id)} />
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      <IconButton
        icon={theme === 'dark' ? 'sun' : 'moon'}
        label={theme === 'dark' ? 'Use light theme' : 'Use dark theme'}
        data-testid="topbar-theme-toggle"
        onClick={toggleTheme}
      />
    </header>
  );
}
