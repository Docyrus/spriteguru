import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { usd } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { errText, useStore } from '../lib/store';
import { EFFORTS, ENGINES, type Effort, type EngineName, type LedgerEntry, type LedgerSummary } from '../lib/types';
import { host } from '../host';
import { useCloud } from '../lib/cloud';
import { accessLabel } from '../shell/Account';
import { SyncSettings } from '../shell/Sync';
import { Button, ErrorNote, Field, Modal, Segmented, Spinner, Toggle } from '../ui/controls';


const PROVIDER_LABEL: Record<string, string> = {
  openai: 'OpenAI',
  fal: 'fal',
  retrodiffusion: 'Retro Diffusion',
  quiver: 'Quiver',
};

interface Draft {
  session_cap_usd: string;
  job_cap_usd: string;
  sol_effort: string;
  luna_effort: string;
  judge_enabled: boolean;
  asset_folder: string;
  engine: EngineName;
  fps: string;
}

/** This machine and the SpritePlay account it is signed in to (cloud plan 5). */
function AccountSection() {
  const cloud = useCloud();
  const s = cloud.status;
  const [name, setName] = useState('');
  const [renaming, setRenaming] = useState(false);
  useEffect(() => {
    if (s) setName(s.machine.name);
  }, [s?.machine.name]); // eslint-disable-line react-hooks/exhaustive-deps
  const rename = async () => {
    setRenaming(true);
    await cloud.renameMachine(name.trim());
    setRenaming(false);
  };
  return (
    <section className="panel" data-testid="settings-account">
      <div className="panel-head">
        <h2>Account</h2>
      </div>
      <div className="panel-body form-stack">
        {!s ? (
          <Spinner label="Reading the account…" />
        ) : s.signed_in ? (
          <div className="settings-account-row">
            <span data-testid="settings-account-who">
              Signed in as <b>{s.user?.email}</b> · {accessLabel(s.access, s)}
            </span>
            <Button size="sm" variant="quiet" busy={cloud.busy} onClick={() => void cloud.signOut()} data-testid="settings-account-sign-out">
              Sign out
            </Button>
          </div>
        ) : (
          <div className="settings-account-row">
            <span data-testid="settings-account-who">Not signed in. Sign in to start your 7-day trial.</span>
            <Button size="sm" variant="primary" busy={cloud.busy} onClick={() => void cloud.signIn()} data-testid="settings-account-sign-in">
              Sign in
            </Button>
          </div>
        )}
        {s ? (
          <Field label="This machine" hint="The name on the Machines page of your account." htmlFor="s-machine">
            <div className="inline-field">
              <input id="s-machine" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} data-testid="settings-machine-name" />
              <Button
                size="sm"
                disabled={!name.trim() || name.trim() === s.machine.name}
                busy={renaming}
                onClick={() => void rename()}
                data-testid="settings-machine-rename"
              >
                Rename
              </Button>
            </div>
          </Field>
        ) : null}
        <p className="muted small-print">Provider keys stay on this machine.</p>
      </div>
    </section>
  );
}

function EffortPicker({ value, onChange, testid, label }: { value: string; onChange: (v: string) => void; testid: string; label: string }) {
  const opts = EFFORTS.includes(value as Effort) ? EFFORTS : [value as Effort, ...EFFORTS];
  return (
    <Segmented<string>
      value={value}
      onChange={onChange}
      testid={testid}
      label={label}
      options={opts.map((e) => ({ value: e, label: e }))}
    />
  );
}

function KeyRow({ provider, status, onSaved }: { provider: string; status: { configured: boolean; source: string | null; env: string }; onSaved: () => void }) {
  const { toast } = useStore();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState<'save' | 'del' | null>(null);
  const save = async () => {
    if (!value.trim()) return;
    setBusy('save');
    try {
      await api.putKey(provider, value.trim());
      setValue('');
      toast(`${PROVIDER_LABEL[provider] ?? provider} key saved to the keychain.`, 'ok');
      onSaved();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const remove = async () => {
    setBusy('del');
    try {
      await api.deleteKey(provider);
      toast(`${PROVIDER_LABEL[provider] ?? provider} key removed from the keychain.`, 'ok');
      onSaved();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const fromEnv = status.source?.startsWith('env:');
  return (
    <div className="key-row" data-testid="settings-key-row" data-provider={provider}>
      <div className="key-name">
        <strong>{PROVIDER_LABEL[provider] ?? provider}</strong>
        <span className={`badge ${status.configured ? 'badge-ok' : ''}`} data-testid="settings-key-status">
          {status.configured ? (fromEnv ? `From ${status.source!.slice(4)}` : 'In keychain') : 'Not set'}
        </span>
      </div>
      <form
        className="key-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={status.configured ? 'Replace key' : `Paste key (or set ${status.env})`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label={`${PROVIDER_LABEL[provider] ?? provider} API key`}
          data-testid="settings-key-input"
        />
        <Button type="submit" size="sm" disabled={!value.trim()} busy={busy === 'save'} data-testid="settings-key-save">
          Save
        </Button>
        {status.source === 'keychain' ? (
          <Button size="sm" variant="ghost" busy={busy === 'del'} onClick={() => void remove()} data-testid="settings-key-remove">
            Remove
          </Button>
        ) : null}
      </form>
    </div>
  );
}

export function SettingsScreen() {
  const { project, reloadProject, toast } = useStore();
  const ledger = useAsync<{ summary: LedgerSummary; entries: LedgerEntry[] }>(() => api.ledger(60), 'ledger');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmLive, setConfirmLive] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [dl, setDl] = useState(false);

  const base = useMemo<Draft | null>(() => {
    if (!project) return null;
    const c = project.config;
    return {
      session_cap_usd: String(c.settings.session_cap_usd),
      job_cap_usd: String(c.settings.job_cap_usd),
      sol_effort: c.settings.sol_effort,
      luna_effort: c.settings.luna_effort,
      judge_enabled: c.settings.judge_enabled,
      asset_folder: c.asset_folder ?? '',
      engine: c.engine,
      fps: String(c.fps),
    };
  }, [project]);

  useEffect(() => {
    if (base && draft === null) setDraft(base);
  }, [base, draft]);

  if (!project || !draft || !base) {
    return (
      <div className="screen" data-testid="settings-screen">
        <div className="screen-head">
          <h1 className="screen-title">Settings</h1>
        </div>
        <div className="screen-body">
          <Spinner label="Loading settings…" />
        </div>
      </div>
    );
  }

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const changed = (Object.keys(draft) as (keyof Draft)[]).filter((k) => draft[k] !== base[k]);
  const numOk = (s: string) => s.trim() !== '' && Number.isFinite(Number(s)) && Number(s) >= 0;
  const invalid =
    !numOk(draft.session_cap_usd) || !numOk(draft.job_cap_usd) || !numOk(draft.fps) || Number(draft.fps) < 1 || Number(draft.fps) > 60;

  const save = async () => {
    const body: Record<string, unknown> = {};
    for (const k of changed) {
      if (k === 'session_cap_usd' || k === 'job_cap_usd') body[k] = Number(draft[k]);
      else if (k === 'fps') body[k] = Math.round(Number(draft.fps));
      else if (k === 'asset_folder') body[k] = draft.asset_folder.trim();
      else body[k] = draft[k];
    }
    setSaving(true);
    setSaveError(null);
    try {
      await api.patchSettings(body);
      await reloadProject();
      setDraft(null);
      toast('Settings saved.', 'ok');
    } catch (e) {
      // 422 from the engine lists each rejected field
      setSaveError(e instanceof ApiError && e.status === 422 ? `The engine rejected these settings. ${e.message}` : errText(e));
    } finally {
      setSaving(false);
    }
  };

  const setMode = async (mode: 'live' | 'synthetic') => {
    setModeBusy(true);
    setSaveError(null);
    try {
      await api.patchSettings({ provider_mode: mode });
      await reloadProject();
      toast(mode === 'live' ? 'Live mode: calls now go to the providers and cost money.' : 'Synthetic mode: the offline simulator answers every call.', 'ok');
    } catch (e) {
      setSaveError(errText(e));
    } finally {
      setModeBusy(false);
      setConfirmLive(false);
    }
  };

  const browse = async () => {
    try {
      const p = await host.pickFolder(draft.asset_folder || null);
      if (p) set('asset_folder', p);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  const emb = project.models.embeddings;
  const lsum = ledger.data?.summary ?? project.ledger;

  return (
    <div className="screen" data-testid="settings-screen">
      <div className="screen-head">
        <h1 className="screen-title">Settings</h1>
        <span className="muted">{project.root}</span>
        <div className="screen-actions">
          {changed.length ? (
            <>
              <span className="badge badge-warn" data-testid="settings-dirty">
                {changed.length} unsaved
              </span>
              <Button size="sm" variant="ghost" onClick={() => (setDraft(base), setSaveError(null))} data-testid="settings-revert">
                Revert
              </Button>
            </>
          ) : null}
          <Button variant="primary" size="sm" icon="check" disabled={!changed.length || invalid} busy={saving} onClick={() => void save()} data-testid="settings-save">
            Save settings
          </Button>
        </div>
      </div>
      {saveError ? (
        <div className="settings-error">
          <ErrorNote testid="settings-save-error">{saveError}</ErrorNote>
        </div>
      ) : null}
      <div className="screen-body settings-grid">
        <AccountSection />
        <SyncSettings />
        <section className="panel" data-testid="settings-provider">
          <div className="panel-head">
            <h2>Providers</h2>
          </div>
          <div className="panel-body form-stack">
            <Field label="Mode" hint={project.mode === 'live' ? 'Requests go to OpenAI, fal, Retro Diffusion and Quiver with your keys.' : 'The offline simulator draws placeholder art; nothing is sent or spent.'}>
              <Segmented<'live' | 'synthetic'>
                value={project.mode === 'live' ? 'live' : 'synthetic'}
                onChange={(v) => (v === 'live' ? setConfirmLive(true) : void setMode('synthetic'))}
                testid="settings-mode"
                label="Provider mode"
                disabled={modeBusy}
                options={[
                  { value: 'synthetic', label: 'Synthetic' },
                  { value: 'live', label: 'Live' },
                ]}
              />
            </Field>
            <div className="keys" data-testid="settings-keys">
              {Object.entries(project.keys).map(([p, s]) => (
                <KeyRow key={p} provider={p} status={s} onSaved={() => void reloadProject()} />
              ))}
            </div>
            <p className="muted small-print">Keys live in the OS keychain; environment variables override them. The studio never shows a stored key.</p>
          </div>
        </section>

        <section className="panel" data-testid="settings-budget">
          <div className="panel-head">
            <h2>Budget and quality</h2>
          </div>
          <div className="panel-body form-stack">
            <div className="field-row">
              <Field label="Session cap (USD)" htmlFor="s-sess" hint="Calls past this ask for approval.">
                <input id="s-sess" type="number" min={0} step={0.5} value={draft.session_cap_usd} onChange={(e) => set('session_cap_usd', e.target.value)} data-testid="settings-session-cap" />
              </Field>
              <Field label="Job cap (USD)" htmlFor="s-job" hint="Per job; approving lifts it for that job.">
                <input id="s-job" type="number" min={0} step={0.25} value={draft.job_cap_usd} onChange={(e) => set('job_cap_usd', e.target.value)} data-testid="settings-job-cap" />
              </Field>
            </div>
            <Field label="GPT-6 Sol reasoning effort" hint="Vector motion authoring and fix rounds.">
              <EffortPicker value={draft.sol_effort} onChange={(v) => set('sol_effort', v)} testid="settings-sol-effort" label="GPT-6 Sol reasoning effort" />
            </Field>
            <Field label="GPT-6 Luna reasoning effort" hint="The QA judge that checks frames for visual defects.">
              <EffortPicker value={draft.luna_effort} onChange={(v) => set('luna_effort', v)} testid="settings-luna-effort" label="GPT-6 Luna reasoning effort" />
            </Field>
            <Toggle
              checked={draft.judge_enabled}
              onChange={(v) => set('judge_enabled', v)}
              label="Visual judge"
              hint="GPT-6 Luna reviews candidates that score 40 or more."
              testid="settings-judge"
            />
            <div className="judge-disabled" data-testid="settings-judge-disabled">
              <span className="field-label">Issue types switched off by calibration</span>
              {project.config.settings.judge_disabled?.length ? (
                <div className="chips">
                  {project.config.settings.judge_disabled.map((t) => (
                    <span key={t} className="badge" data-testid="settings-judge-disabled-item">
                      {t.replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="muted">None. Label judge findings in Findings, then run <code>spritekit eval judge</code>.</span>
              )}
            </div>
          </div>
        </section>

        <section className="panel" data-testid="settings-output">
          <div className="panel-head">
            <h2>Output</h2>
          </div>
          <div className="panel-body form-stack">
            <Field label="Game asset folder" htmlFor="s-folder" hint="Every export is copied here, one folder per animation.">
              <div className="input-row">
                <input id="s-folder" type="text" value={draft.asset_folder} placeholder="Not set" onChange={(e) => set('asset_folder', e.target.value)} data-testid="settings-asset-folder" />
                <Button size="sm" icon="folder" onClick={() => void browse()} data-testid="settings-asset-folder-browse">
                  Browse
                </Button>
                {draft.asset_folder ? (
                  <Button size="sm" variant="ghost" onClick={() => set('asset_folder', '')} data-testid="settings-asset-folder-clear">
                    Clear
                  </Button>
                ) : null}
              </div>
            </Field>
            <div className="field-row">
              <Field label="Engine" htmlFor="s-engine">
                <select id="s-engine" value={draft.engine} onChange={(e) => set('engine', e.target.value as EngineName)} data-testid="settings-engine">
                  {ENGINES.map((en) => (
                    <option key={en} value={en}>
                      {en}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Frame rate (fps)" htmlFor="s-fps" hint="Default timing for new animations.">
                <input id="s-fps" type="number" min={1} max={60} value={draft.fps} onChange={(e) => set('fps', e.target.value)} data-testid="settings-fps" />
              </Field>
            </div>
            {invalid ? <ErrorNote testid="settings-invalid">Caps must be zero or more and the frame rate between 1 and 60.</ErrorNote> : null}
          </div>
        </section>

        <section className="panel" data-testid="settings-models">
          <div className="panel-head">
            <h2>Local models</h2>
          </div>
          <div className="panel-body form-stack">
            <div className="model-row" data-testid="settings-model-embeddings">
              <div>
                <strong>Embeddings</strong> <span className="muted">{emb.model}</span>
                <div className="muted small-print">Character identity checks. Without it the studio falls back to edge descriptors.</div>
              </div>
              <span className={`badge ${emb.present ? 'badge-ok' : 'badge-warn'}`} data-testid="settings-embeddings-status">
                {emb.present ? `Ready (${emb.backend})` : `Missing (${emb.backend})`}
              </span>
              {!emb.present ? (
                <Button
                  size="sm"
                  icon="download"
                  busy={dl}
                  data-testid="settings-embeddings-download"
                  onClick={async () => {
                    setDl(true);
                    try {
                      const r = await api.downloadEmbeddings();
                      toast(r.ok ? 'Embeddings model downloaded.' : 'The download did not finish. Check the network and try again.', r.ok ? 'ok' : 'error');
                      void reloadProject();
                    } catch (e) {
                      toast(errText(e), 'error');
                    } finally {
                      setDl(false);
                    }
                  }}
                >
                  Download
                </Button>
              ) : (
                <Button size="sm" variant="ghost" icon="download" busy={dl} disabled data-testid="settings-embeddings-download">
                  Downloaded
                </Button>
              )}
            </div>
            <div className="model-row" data-testid="settings-model-matte">
              <div>
                <strong>Matting</strong> <span className="muted">{project.models.matte.model}</span>
                <div className="muted small-print">ML matte fallback for hard backgrounds.</div>
              </div>
              <span className={`badge ${project.models.matte.available ? 'badge-ok' : 'badge-warn'}`}>
                {project.models.matte.available ? 'Available' : 'Not installed'}
              </span>
            </div>
            <table className="roles" data-testid="settings-roles">
              <tbody>
                {Object.entries(project.roles).map(([role, model]) => (
                  <tr key={role}>
                    <td className="muted">{role.replace(/_/g, ' ')}</td>
                    <td>{model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel settings-ledger" data-testid="settings-ledger">
          <div className="panel-head">
            <h2>Ledger</h2>
            <span className="spacer" />
            <Button size="sm" variant="ghost" icon="refresh" onClick={() => void ledger.reload()} data-testid="settings-ledger-refresh">
              Refresh
            </Button>
          </div>
          <div className="panel-body">
            <div className="ledger-stats">
              <div>
                <span className="stat-n num" data-testid="settings-ledger-total">
                  {usd(lsum.total_usd)}
                </span>
                <span className="muted">all time</span>
              </div>
              <div>
                <span className="stat-n num">{usd(lsum.session_usd)}</span>
                <span className="muted">this session</span>
              </div>
              <div>
                <span className="stat-n num">{lsum.calls}</span>
                <span className="muted">paid calls</span>
              </div>
              <div>
                <span className="stat-n num">{lsum.cache_hits}</span>
                <span className="muted">cache hits</span>
              </div>
              {lsum.unknown_outcomes ? (
                <div>
                  <span className="stat-n num warn-text">{lsum.unknown_outcomes}</span>
                  <span className="muted">unknown outcomes</span>
                </div>
              ) : null}
            </div>
            <table className="ledger-models">
              <tbody>
                {Object.entries(lsum.by_model).map(([m, v]) => (
                  <tr key={m}>
                    <td>{m}</td>
                    <td className="num">{usd(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ledger.data ? (
              <details className="ledger-entries">
                <summary>Recent calls ({ledger.data.entries.length})</summary>
                <table data-testid="settings-ledger-entries">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Model</th>
                      <th>Purpose</th>
                      <th>Status</th>
                      <th className="num">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...ledger.data.entries].reverse().map((e, i) => (
                      <tr key={`${e.call_id ?? i}-${e.status}-${i}`}>
                        <td className="num">{e.time ? new Date(e.time * 1000).toLocaleString() : ''}</td>
                        <td>{e.model}</td>
                        <td>{e.purpose}</td>
                        <td>{e.status}</td>
                        <td className="num">{typeof e.cost === 'number' ? usd(e.cost) : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            ) : null}
          </div>
        </section>
      </div>

      {confirmLive ? (
        <Modal
          title="Switch to live mode?"
          testid="settings-live-confirm"
          onClose={() => setConfirmLive(false)}
          actions={
            <>
              <Button variant="ghost" onClick={() => setConfirmLive(false)} data-testid="settings-live-cancel">
                Stay synthetic
              </Button>
              <Button variant="danger" busy={modeBusy} onClick={() => void setMode('live')} data-testid="settings-live-confirm-button">
                Go live
              </Button>
            </>
          }
        >
          <p className="modal-lede">
            Jobs will call the AI providers with your keys and spend real money, up to the {usd(project.config.settings.session_cap_usd)} session cap
            before asking.
          </p>
        </Modal>
      ) : null}
    </div>
  );
}
