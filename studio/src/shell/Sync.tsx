// Project sync in the studio (cloud plan 6.3, 6.7, 6.8, 6.10): the header's sync pill, the Sync panel
// with its conflict view, the question for a copied folder, and the workspace picker.

import { useEffect, useMemo, useState } from 'react';
import { cloudBlobUrl, fileUrl } from '../lib/api';
import { BILLING, useCloud } from '../lib/cloud';
import { fileSize, timeAgo } from '../lib/format';
import { useStore } from '../lib/store';
import type { CloudStatus, SyncConflictFile, SyncStatus } from '../lib/types';
import { Button, Modal, Toggle } from '../ui/controls';
import { Icon } from '../ui/Icon';

type PillState = 'syncing' | 'conflicts' | 'paused' | 'offline' | 'copied' | 'pending' | 'synced';

export function syncState(s: SyncStatus): PillState {
  if (s.copied) return 'copied';
  if (s.syncing) return 'syncing';
  if ((s.conflicts?.length ?? 0) > 0) return 'conflicts';
  if (s.offline) return 'offline';
  if (s.paused) return 'paused';
  if ((s.pending?.push ?? 0) > 0) return 'pending';
  return 'synced';
}

const conflictCount = (s: SyncStatus) => (s.conflicts ?? []).reduce((n, g) => n + g.files.length, 0);

function pillLabel(s: SyncStatus): string {
  const st = syncState(s);
  const n = conflictCount(s);
  switch (st) {
    case 'copied':
      return 'Linked elsewhere';
    case 'syncing':
      return 'Syncing…';
    case 'conflicts':
      return n === 1 ? '1 conflict' : `${n} conflicts`;
    case 'offline':
      return 'Offline';
    case 'paused':
      return s.paused?.code === 'removed' ? 'Removed from the cloud' : 'Sync paused';
    case 'pending':
      return s.pending?.push === 1 ? '1 change to sync' : `${s.pending?.push} changes to sync`;
    default:
      return 'Synced';
  }
}

/** The header's sync state for the open project; opens the Sync panel. */
export function SyncPill() {
  const { sync } = useCloud();
  const [open, setOpen] = useState(false);
  if (!sync?.linked) return null;
  const st = syncState(sync);
  return (
    <>
      <button
        type="button"
        className={`sync-pill sync-${st}`}
        onClick={() => setOpen(true)}
        data-testid="sync-pill"
        data-state={st}
        title={sync.last_pull ? `Last synced ${timeAgo(sync.last_pull)}` : 'Cloud sync'}
      >
        <Icon name={st === 'syncing' ? 'refresh' : st === 'synced' ? 'check' : 'warn'} size={14} />
        <span>{pillLabel(sync)}</span>
      </button>
      {open ? <SyncPanel onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function wsSlug(status: CloudStatus | null, owner?: string): string {
  const team = status?.teams.find((t) => t.id === owner);
  return team ? team.slug : 'personal';
}

/** What to do about a pause, in the studio's voice (5, 6.10). */
function PauseActions({ s, onUnlink }: { s: SyncStatus; onUnlink: () => void }) {
  const cloud = useCloud();
  const code = s.paused?.code;
  const team = cloud.status?.teams.find((t) => t.id === s.owner_id);
  const admin = team ? ['owner', 'admin'].includes(team.role) : true;
  if (code === 'removed') {
    return (
      <>
        <Button size="sm" onClick={() => cloud.openSite(`/app/${wsSlug(cloud.status, s.owner_id)}/projects/${s.project_id}`)} data-testid="sync-restore">
          Restore on spriteplay.com
        </Button>
        <Button size="sm" variant="quiet" onClick={onUnlink} data-testid="sync-stop">
          Stop syncing
        </Button>
      </>
    );
  }
  if (code === 'team_inactive' && team && admin) {
    return (
      <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.team(team.slug))} data-testid="sync-team-billing">
        Open team billing
      </Button>
    );
  }
  if (code === 'plan_required' || code === 'project_limit' || (code === 'quota_exceeded' && !team)) {
    return (
      <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.pro)} data-testid="sync-upgrade">
        Upgrade to Pro
      </Button>
    );
  }
  if (code === 'quota_exceeded' && team && admin) {
    return (
      <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.team(team.slug))} data-testid="sync-team-billing">
        Open team billing
      </Button>
    );
  }
  return null;
}

const IMAGE = /\.(png|gif|webp|jpe?g)$/i;

function groupTitle(group: string): string {
  const m = /^(characters|animations)\/([^/]+)\/$/.exec(group);
  if (m) return `${m[1] === 'characters' ? 'Character' : 'Animation'} ${m[2]}`;
  if (group === 'labels/') return 'Finding labels';
  if (group === 'project.json') return 'Project settings';
  return group;
}

/** A short field diff of this machine's JSON against the cloud's. */
function JsonDiff({ file, owner }: { file: SyncConflictFile; owner: string }) {
  const [rows, setRows] = useState<{ key: string; mine: string; theirs: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    const get = async (url: string | null) => {
      if (!url) return null;
      const r = await fetch(url);
      return r.ok ? ((await r.json()) as Record<string, unknown>) : null;
    };
    void Promise.all([
      get(file.local ? fileUrl(file.path, file.local.sha256) : null),
      get(file.remote.sha256 ? cloudBlobUrl(owner, file.remote.sha256) : null),
    ]).then(([mine, theirs]) => {
      if (!live) return;
      const keys = [...new Set([...Object.keys(mine ?? {}), ...Object.keys(theirs ?? {})])];
      const show = (v: unknown) => (v === undefined ? '—' : JSON.stringify(v).slice(0, 80));
      setRows(
        keys
          .filter((k) => JSON.stringify(mine?.[k]) !== JSON.stringify(theirs?.[k]))
          .map((k) => ({ key: k, mine: show(mine?.[k]), theirs: show(theirs?.[k]) })),
      );
    });
    return () => {
      live = false;
    };
  }, [file, owner]);
  if (!rows) return <span className="muted">Comparing…</span>;
  if (!rows.length) return <span className="muted">Same fields; formatting differs.</span>;
  return (
    <table className="conflict-diff" data-testid="conflict-json-diff">
      <thead>
        <tr>
          <th>Field</th>
          <th>This machine</th>
          <th>Cloud</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 8).map((r) => (
          <tr key={r.key}>
            <td>{r.key}</td>
            <td>{r.mine}</td>
            <td>{r.theirs}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Side({ label, url, gone }: { label: string; url: string | null; gone: string }) {
  return (
    <figure className="conflict-side">
      <figcaption>{label}</figcaption>
      {url ? (
        <span className="conflict-img checker">
          <img src={url} alt={label} draggable={false} />
        </span>
      ) : (
        <span className="conflict-gone">{gone}</span>
      )}
    </figure>
  );
}

function ConflictFile({ file, owner, onPick, busy }: { file: SyncConflictFile; owner: string; onPick: (keep: 'mine' | 'theirs') => void; busy: boolean }) {
  const image = IMAGE.test(file.path);
  return (
    <li className="conflict-file" data-testid="conflict-file" data-path={file.path}>
      <div className="conflict-file-head">
        <code>{file.path}</code>
        <span className="muted">
          {file.local ? fileSize(file.local.size) : 'deleted here'} · {file.remote.deleted ? 'deleted in the cloud' : fileSize(file.remote.size)}
        </span>
      </div>
      {image ? (
        <div className="conflict-sides">
          <Side label="This machine" url={file.local ? fileUrl(file.path, file.local.sha256) : null} gone="Deleted on this machine" />
          <Side label="Cloud" url={file.remote.sha256 ? cloudBlobUrl(owner, file.remote.sha256) : null} gone="Deleted in the cloud" />
        </div>
      ) : file.path.endsWith('.json') && file.local && file.remote.sha256 ? (
        <JsonDiff file={file} owner={owner} />
      ) : null}
      <div className="conflict-actions">
        <Button size="sm" busy={busy} onClick={() => onPick('mine')} data-testid="conflict-keep-mine">
          Keep this machine’s
        </Button>
        <Button size="sm" busy={busy} onClick={() => onPick('theirs')} data-testid="conflict-keep-theirs">
          Keep the cloud’s
        </Button>
      </div>
    </li>
  );
}

function ConflictGroup({ group, files, owner }: { group: string; files: SyncConflictFile[]; owner: string }) {
  const cloud = useCloud();
  return (
    <section className="conflict-group" data-testid="conflict-group" data-group={group}>
      <div className="conflict-group-head">
        <h3>{groupTitle(group)}</h3>
        <span className="muted">{files.length === 1 ? '1 file' : `${files.length} files`} changed on both sides</span>
        <span className="spacer" />
        <Button size="sm" variant="primary" busy={cloud.busy} onClick={() => void cloud.resolve([{ group, keep: 'mine' }])} data-testid="conflict-group-mine">
          Keep this machine’s
        </Button>
        <Button size="sm" busy={cloud.busy} onClick={() => void cloud.resolve([{ group, keep: 'theirs' }])} data-testid="conflict-group-theirs">
          Keep the cloud’s
        </Button>
      </div>
      <ul className="conflict-files">
        {files.map((f) => (
          <ConflictFile key={f.path} file={f} owner={owner} busy={cloud.busy} onPick={(keep) => void cloud.resolve([{ path: f.path, keep }])} />
        ))}
      </ul>
    </section>
  );
}

/** C29: a folder that carries another folder's link. */
export function CopiedQuestion({ s }: { s: SyncStatus }) {
  const cloud = useCloud();
  if (!s.copied) return null;
  const where = s.workspace ?? 'the cloud';
  return (
    <div className="access-banner" role="status" data-testid="sync-copied">
      <Icon name="warn" size={16} />
      <span className="access-banner-text">
        This folder is linked to a cloud project in {where} from another folder (it was copied, cloned or restored). Keep syncing it here, or make it a
        separate project?
      </span>
      <span className="access-banner-actions">
        <Button size="sm" variant="primary" busy={cloud.busy} onClick={() => void cloud.answerCopied(true)} data-testid="sync-copied-keep">
          Keep syncing here
        </Button>
        <Button size="sm" busy={cloud.busy} onClick={() => void cloud.answerCopied(false)} data-testid="sync-copied-separate">
          Make it separate
        </Button>
      </span>
    </div>
  );
}

export function SyncPanel({ onClose }: { onClose: () => void }) {
  const cloud = useCloud();
  const s = cloud.sync;
  const [confirmStop, setConfirmStop] = useState(false);
  if (!s?.linked) return null;
  const owner = s.owner_id ?? '';
  const n = conflictCount(s);
  return (
    <Modal title="Cloud sync" onClose={onClose} testid="sync-panel" wide>
      <div className="sync-panel">
        <CopiedQuestion s={s} />
        <div className="sync-summary" data-testid="sync-summary">
          <div>
            <b>{s.workspace ?? 'Cloud'}</b>
            <span className="muted"> · revision {s.rev ?? 0}</span>
          </div>
          <div className="muted">
            {s.last_pull ? `Last synced ${timeAgo(s.last_pull)}` : 'Not synced yet'}
            {s.pending?.push ? ` · ${s.pending.push} local change${s.pending.push === 1 ? '' : 's'} to send` : ''}
          </div>
          <span className="spacer" />
          <Button size="sm" icon="refresh" busy={cloud.busy || s.syncing} disabled={!!s.copied} onClick={() => void cloud.syncNow()} data-testid="sync-now">
            Sync now
          </Button>
        </div>
        {s.offline ? (
          <div className="warn-note" data-testid="sync-offline">
            {s.offline_message}
          </div>
        ) : null}
        {s.paused && !s.offline ? (
          <div className="access-banner" data-testid="sync-paused" data-code={s.paused.code}>
            <Icon name="stop" size={16} />
            <span className="access-banner-text">{s.paused.message}</span>
            <span className="access-banner-actions">
              <PauseActions s={s} onUnlink={() => setConfirmStop(true)} />
            </span>
          </div>
        ) : null}
        {(s.findings ?? []).map((f, i) => (
          <div key={i} className="warn-note" data-testid="sync-finding" data-code={f.code}>
            {f.message}
          </div>
        ))}
        {n > 0 ? (
          <div className="sync-conflicts" data-testid="sync-conflicts">
            <p className="muted">
              These changed on this machine and in the cloud. Choose which version to keep; everything else keeps syncing meanwhile.
            </p>
            {(s.conflicts ?? []).map((g) => (
              <ConflictGroup key={g.group} group={g.group} files={g.files} owner={owner} />
            ))}
          </div>
        ) : null}
        <div className="sync-foot">
          <Toggle
            checked={s.auto !== false}
            onChange={(v) => void cloud.setAutoSync(v)}
            label="Sync automatically"
            testid="sync-auto"
          />
          <span className="spacer" />
          <Button size="sm" variant="quiet" onClick={() => setConfirmStop(true)} data-testid="sync-unlink">
            Stop syncing
          </Button>
        </div>
      </div>
      {confirmStop ? (
        <Modal
          title="Stop syncing this project?"
          onClose={() => setConfirmStop(false)}
          testid="sync-unlink-confirm"
          actions={
            <>
              <Button onClick={() => setConfirmStop(false)}>Keep syncing</Button>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirmStop(false);
                  void cloud.unlink().then(onClose);
                }}
                data-testid="sync-unlink-yes"
              >
                Stop syncing
              </Button>
            </>
          }
        >
          <p>The files stay on this machine, and the cloud copy stays on spriteplay.com, where it can be deleted.</p>
        </Modal>
      ) : null}
    </Modal>
  );
}

/** The workspaces a project can be linked to (6.3): Personal when the plan syncs, and active teams. */
export function WorkspacePicker({ onDone }: { onDone?: () => void }) {
  const cloud = useCloud();
  const { status } = cloud;
  const options = useMemo(() => {
    if (!status?.signed_in) return [];
    const personal = status.personal;
    const out: { id: string; name: string; disabled: string | null }[] = [
      {
        id: 'me',
        name: 'Personal',
        disabled: personal?.cloud ? null : personal?.plan === 'license' ? 'Cloud sync is part of Pro and Team, not the License.' : 'Cloud sync is part of Pro and Team.',
      },
    ];
    for (const t of status.teams) out.push({ id: t.id, name: t.name, disabled: t.active ? null : `${t.name} has no active Team plan.` });
    return out;
  }, [status]);
  const [pick, setPick] = useState<string>('');
  useEffect(() => {
    if (!pick) setPick(options.find((o) => !o.disabled)?.id ?? '');
  }, [options, pick]);
  if (!status?.signed_in) {
    return (
      <div className="settings-account-row">
        <span>Sign in to sync this project across machines.</span>
        <Button size="sm" variant="primary" onClick={() => void cloud.signIn()} data-testid="sync-link-sign-in">
          Sign in
        </Button>
      </div>
    );
  }
  const noneOk = options.every((o) => o.disabled);
  return (
    <div className="workspace-picker" data-testid="sync-link-picker">
      <div className="workspace-options" role="radiogroup" aria-label="Workspace">
        {options.map((o) => (
          <label key={o.id} className={`workspace-option${o.disabled ? ' is-disabled' : ''}`} data-testid="sync-link-workspace" data-id={o.id}>
            <input type="radio" name="ws" value={o.id} checked={pick === o.id} disabled={!!o.disabled} onChange={() => setPick(o.id)} />
            <span>
              <b>{o.name}</b>
              {o.disabled ? <span className="muted"> · {o.disabled}</span> : null}
            </span>
          </label>
        ))}
      </div>
      <div className="workspace-actions">
        {noneOk ? (
          <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.pro)} data-testid="sync-link-upgrade">
            Upgrade to Pro
          </Button>
        ) : (
          <Button
            size="sm"
            variant="primary"
            icon="refresh"
            busy={cloud.busy}
            disabled={!pick}
            onClick={() => void cloud.link(pick).then((ok) => ok && onDone?.())}
            data-testid="sync-link-submit"
          >
            Sync to cloud
          </Button>
        )}
      </div>
    </div>
  );
}

/** Settings → Cloud sync: link, state, automatic sync (6.3, 6.8). */
export function SyncSettings() {
  const cloud = useCloud();
  const { project } = useStore();
  const [panel, setPanel] = useState(false);
  const s = cloud.sync;
  return (
    <section className="panel" data-testid="settings-sync">
      <div className="panel-head">
        <h2>Cloud sync</h2>
      </div>
      <div className="panel-body form-stack">
        {!project ? null : s?.linked ? (
          <>
            {s.copied ? <CopiedQuestion s={s} /> : null}
            <div className="settings-account-row">
              <span data-testid="settings-sync-state" data-state={syncState(s)}>
                Syncs to <b>{s.workspace ?? 'the cloud'}</b> · {pillLabel(s)}
              </span>
              <Button size="sm" onClick={() => setPanel(true)} data-testid="settings-sync-open">
                Open sync panel
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="muted small-print">Keep this project in a cloud workspace to open it on your other machines or share it with a team.</p>
            <WorkspacePicker />
          </>
        )}
        <Toggle
          checked={cloud.status ? s?.auto !== false : true}
          onChange={(v) => void cloud.setAutoSync(v)}
          label="Sync linked projects automatically on this machine"
          testid="settings-sync-auto"
        />
      </div>
      {panel ? <SyncPanel onClose={() => setPanel(false)} /> : null}
    </section>
  );
}
