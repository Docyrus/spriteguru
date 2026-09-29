// The asset library (cloud plan 8): items in the Personal and team workspaces, by kind and search,
// imported into the open project; and the dialog that publishes a character or an animation.

import { useEffect, useMemo, useState } from 'react';
import { cloudBlobUrl, request } from '../lib/api';
import { BILLING, useCloud } from '../lib/cloud';
import { fileSize, timeAgo } from '../lib/format';
import { errText, useStore } from '../lib/store';
import { Button, ErrorNote, Field, Modal, Segmented, Spinner } from '../ui/controls';
import { Icon } from '../ui/Icon';

export interface LibraryItem {
  id: string;
  ownerId: string;
  name: string;
  kind: string;
  description: string | null;
  thumbnailSha: string | null;
  fileCount: number;
  totalBytes: number;
  creatorName: string | null;
  updatedAt: string;
  workspace: { type: string; id: string; name: string };
}

const KINDS = [
  { value: '', label: 'All' },
  { value: 'character', label: 'Characters' },
  { value: 'animation', label: 'Animations' },
  { value: 'effect', label: 'Effects' },
  { value: 'vehicle', label: 'Vehicles' },
  { value: 'machine', label: 'Machines' },
  { value: 'tileset', label: 'Tilesets' },
  { value: 'other', label: 'Other' },
];

export function LibraryScreen() {
  const cloud = useCloud();
  const { project, toast } = useStore();
  const s = cloud.status;
  const spaces = useMemo(() => {
    if (!s?.signed_in) return [];
    return [{ id: s.personal?.id ?? 'me', name: 'Personal', library: !!s.personal?.library }, ...s.teams.map((t) => ({ id: t.id, name: t.name, library: t.active }))];
  }, [s]);
  const [space, setSpace] = useState<string>('');
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState<string | null>(null);

  useEffect(() => {
    if (!space && spaces.length) setSpace(spaces[0].id);
  }, [spaces, space]);

  useEffect(() => {
    if (!space) return;
    let live = true;
    const t = window.setTimeout(() => {
      const qs = new URLSearchParams({ owner: space, ...(kind ? { kind } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
      request<{ items: LibraryItem[] }>('GET', `/api/cloud/library?${qs}`)
        .then((r) => live && (setItems(r.items), setError(null)))
        .catch((e) => live && setError(errText(e)));
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [space, kind, q]);

  const current = spaces.find((x) => x.id === space);
  const doImport = async (it: LibraryItem) => {
    setImporting(it.id);
    try {
      const r = await request<{ path: string; name: string }>('POST', `/api/cloud/library/${encodeURIComponent(it.id)}/import`);
      toast(`Imported ${r.name} into ${r.path}`, 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setImporting(null);
    }
  };

  return (
    <div className="screen" data-testid="library-screen">
      <div className="screen-head">
        <h1 className="screen-title">Library</h1>
        <span className="muted">Characters, animations and effects kept outside projects</span>
      </div>
      <div className="screen-body library-body">
        {!s ? <Spinner label="Reading the account…" /> : null}
        {s && !s.signed_in ? (
          <div className="access-banner" data-testid="library-signed-out">
            <Icon name="stop" size={16} />
            <span className="access-banner-text">Sign in to use the library in your workspaces.</span>
            <span className="access-banner-actions">
              <Button size="sm" variant="primary" onClick={() => void cloud.signIn()} data-testid="library-sign-in">
                Sign in
              </Button>
            </span>
          </div>
        ) : null}
        {spaces.length ? (
          <>
            <div className="library-controls">
              <Segmented<string>
                value={space}
                onChange={setSpace}
                testid="library-workspace"
                label="Workspace"
                options={spaces.map((x) => ({ value: x.id, label: x.name }))}
              />
              <Segmented<string> value={kind} onChange={setKind} testid="library-kind" label="Kind" options={KINDS} />
              <input
                type="search"
                className="library-search"
                placeholder="Search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                data-testid="library-search"
              />
            </div>
            {current && !current.library ? (
              <div className="access-banner is-info" data-testid="library-plan">
                <Icon name="sparkle" size={16} />
                <span className="access-banner-text">
                  {current.name === 'Personal' ? 'The library is part of Pro.' : `${current.name} has no active Team plan, so its library is read-only.`}
                </span>
                {current.name === 'Personal' ? (
                  <span className="access-banner-actions">
                    <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.pro)} data-testid="library-upgrade">
                      Upgrade to Pro
                    </Button>
                  </span>
                ) : null}
              </div>
            ) : null}
            {error ? <ErrorNote testid="library-error">{error}</ErrorNote> : null}
            {items && !items.length ? (
              <p className="muted" data-testid="library-empty">
                Nothing here yet. Add a character from the Characters screen or an animation from Export.
              </p>
            ) : null}
            <div className="project-grid" data-testid="library-grid">
              {(items ?? []).map((it) => (
                <article key={it.id} className="project-card library-card" data-testid="library-item" data-id={it.id} data-kind={it.kind} data-name={it.name}>
                  <span className={`project-thumb ${it.thumbnailSha ? 'checker' : 'ph-none'}`}>
                    {it.thumbnailSha ? (
                      <img src={cloudBlobUrl(it.ownerId, it.thumbnailSha)} alt="" draggable={false} data-testid="library-item-thumb" />
                    ) : (
                      <span className="project-thumb-mark" aria-hidden="true">
                        <Icon name="library" size={30} />
                      </span>
                    )}
                  </span>
                  <span className="project-card-body">
                    <span className="project-card-name">{it.name}</span>
                    <span className="project-card-badges">
                      <span className="badge">{KINDS.find((k) => k.value === it.kind)?.label.replace(/s$/, '') ?? it.kind}</span>
                    </span>
                    <span className="project-card-meta">
                      {it.fileCount} file{it.fileCount === 1 ? '' : 's'} · {fileSize(it.totalBytes)}
                    </span>
                    <span className="project-card-when">
                      {it.creatorName ? `${it.creatorName} · ` : ''}
                      {timeAgo(it.updatedAt)}
                    </span>
                    <Button
                      size="sm"
                      icon="download"
                      disabled={!project || !!importing}
                      busy={importing === it.id}
                      title={project ? undefined : 'Open a project to import into'}
                      onClick={() => void doImport(it)}
                      data-testid="library-item-import"
                    >
                      Import into {project ? project.name : 'a project'}
                    </Button>
                  </span>
                </article>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

/** "Add to library" for a character folder or an animation (cloud plan 8). */
export function PublishDialog({ prefix, name, kind, onClose }: { prefix: string; name: string; kind: string; onClose: () => void }) {
  const cloud = useCloud();
  const { toast } = useStore();
  const s = cloud.status;
  const spaces = s?.signed_in
    ? [{ id: 'me', name: 'Personal', ok: !!s.personal?.library }, ...s.teams.map((t) => ({ id: t.id, name: t.name, ok: t.active }))]
    : [];
  const [title, setTitle] = useState(name);
  const [itemKind, setItemKind] = useState(kind);
  const [owner, setOwner] = useState(cloud.sync?.linked && cloud.sync.owner_id && spaces.some((x) => x.id === cloud.sync?.owner_id) ? cloud.sync.owner_id : 'me');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = spaces.find((x) => x.id === owner);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await request<{ from_project: boolean }>('POST', '/api/project/library/publish', { prefix, name: title.trim(), kind: itemKind, owner });
      toast(`Added ${title.trim()} to ${target?.name ?? 'the library'}${r.from_project ? '' : ' (uploaded)'}.`, 'ok');
      onClose();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Add to library"
      onClose={onClose}
      testid="publish-dialog"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          {target && !target.ok && target.id === 'me' ? (
            <Button variant="primary" onClick={() => cloud.openSite(BILLING.pro)} data-testid="publish-upgrade">
              Upgrade to Pro
            </Button>
          ) : (
            <Button variant="primary" busy={busy} disabled={!title.trim() || !s?.signed_in} onClick={() => void submit()} data-testid="publish-submit">
              Add to library
            </Button>
          )}
        </>
      }
    >
      {!s?.signed_in ? (
        <p>Sign in to add assets to your library.</p>
      ) : (
        <div className="form-stack">
          <Field label="Name" htmlFor="pub-name">
            <input id="pub-name" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} data-testid="publish-name" />
          </Field>
          <Field label="Kind" htmlFor="pub-kind">
            <select id="pub-kind" value={itemKind} onChange={(e) => setItemKind(e.target.value)} data-testid="publish-kind">
              {KINDS.filter((k) => k.value).map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label.replace(/s$/, '')}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Workspace" htmlFor="pub-owner" hint={target && !target.ok ? (target.id === 'me' ? 'The library is part of Pro.' : `${target.name} has no active Team plan.`) : undefined}>
            <select id="pub-owner" value={owner} onChange={(e) => setOwner(e.target.value)} data-testid="publish-owner">
              {spaces.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </Field>
          <p className="muted small-print">
            From <code>{prefix}</code>
          </p>
          {error ? <ErrorNote testid="publish-error">{error}</ErrorNote> : null}
        </div>
      )}
    </Modal>
  );
}

