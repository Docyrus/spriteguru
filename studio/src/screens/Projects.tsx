import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, cloudBlobUrl, fileUrl } from '../lib/api';
import { useCloud } from '../lib/cloud';
import { useEngineEvent } from '../lib/events';
import { cardsWithCurrent, ENGINE_LABEL, fileSize, STYLE_LABEL, timeAgo } from '../lib/format';
import { useHostKind } from '../lib/hooks';
import { navigate, redirect, useRoute } from '../lib/router';
import { errText, useStore } from '../lib/store';
import { ENGINES, type CloudProject, type EngineName, type ProjectCard, type StyleKind } from '../lib/types';
import { host } from '../host';
import { Button, ErrorNote, Field, IconButton, Menu, Modal, Segmented, Spinner } from '../ui/controls';
import { Icon } from '../ui/Icon';
import { AccessBanner, UpdateBanner } from '../shell/Account';

const STYLES: StyleKind[] = ['pixel', 'hd-cartoon', 'painted', 'vector'];

const STYLE_HINT: Record<StyleKind, string> = {
  pixel: 'Crisp pixel art on a fixed grid, with a limited palette.',
  'hd-cartoon': 'Clean high-resolution cartoon sprites with outlines.',
  painted: 'Painterly high-resolution sprites with soft shading.',
  vector: 'Resolution-free vector characters, animated by rig.',
};

const STYLE_ICON: Record<StyleKind, string> = {
  pixel: 'grid',
  'hd-cartoon': 'characters',
  painted: 'sparkle',
  vector: 'builder',
};

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`;

function ProjectTile({
  card,
  busy,
  canReveal,
  onOpen,
  onReveal,
  onRemove,
  onSync,
}: {
  card: ProjectCard;
  busy: boolean;
  canReveal: boolean;
  onOpen: () => void;
  onReveal: () => void;
  onRemove: () => void;
  onSync: () => void;
}) {
  const [thumbFailed, setThumbFailed] = useState(false);
  const blocked = card.missing || !!card.error;
  const hasImg = !!card.thumbnail && !thumbFailed && !blocked;
  const reason = card.missing ? 'Folder not found. It was moved or deleted.' : card.error;
  const when = card.last_opened ? `Opened ${timeAgo(card.last_opened)}` : card.updated ? `Never opened · updated ${timeAgo(card.updated)}` : 'Never opened';
  return (
    <article
      className={`project-card${card.current ? ' is-current' : ''}${blocked ? ' is-blocked' : ''}`}
      data-testid="project-card"
      data-id={card.id}
      data-name={card.name}
      data-current={card.current ? 'true' : 'false'}
      data-state={card.missing ? 'missing' : card.error ? 'error' : 'ok'}
    >
      <button type="button" className="project-card-open" disabled={blocked || busy} onClick={onOpen} data-testid="project-card-open" title={card.path}>
        <span className={`project-thumb ${hasImg ? 'checker' : `ph-${card.style ?? 'none'}`}`}>
          {hasImg && card.thumbnail ? (
            <img src={fileUrl(card.thumbnail, card.updated)} alt="" draggable={false} onError={() => setThumbFailed(true)} />
          ) : (
            <span className="project-thumb-mark" aria-hidden="true">
              <Icon name={blocked ? 'warn' : card.style ? STYLE_ICON[card.style] : 'projects'} size={30} />
            </span>
          )}
          {busy ? (
            <span className="project-thumb-busy">
              <Spinner label="Opening…" />
            </span>
          ) : null}
        </span>
        <span className="project-card-body">
          <span className="project-card-name">{card.name}</span>
          <span className="project-card-badges">
            {card.style ? <span className="badge">{STYLE_LABEL[card.style]}</span> : null}
            {card.engine ? <span className="badge">{ENGINE_LABEL[card.engine]}</span> : null}
            {card.current ? <span className="badge badge-accent">Open now</span> : null}
            {card.cloud ? <SyncBadge c={card.cloud} /> : null}
          </span>
          {blocked ? (
            <span className="project-card-reason" data-testid="project-card-reason">
              <Icon name="warn" size={13} />
              <span>{reason}</span>
            </span>
          ) : (
            <span className="project-card-meta">
              {plural(card.characters, 'character')} · {plural(card.animations, 'animation')}
            </span>
          )}
          <span className="project-card-when">{when}</span>
        </span>
      </button>
      <div className="project-card-menu">
        <Menu
          label={`${card.name} actions`}
          triggerLabel={`More actions for ${card.name}`}
          title="More actions"
          testid="project-card-menu"
          className="icon-btn"
          align="end"
          trigger={<Icon name="more" size={17} />}
          entries={[
            { key: 'open', label: 'Open', onSelect: onOpen, disabled: blocked, testid: 'project-card-menu-open' },
            { key: 'sync', label: card.cloud ? 'Cloud sync…' : 'Sync to cloud…', onSelect: onSync, disabled: blocked, testid: 'project-card-sync-menu' },
            ...(canReveal ? [{ key: 'reveal', label: 'Reveal in Finder', onSelect: onReveal, disabled: card.missing, testid: 'project-card-reveal' }] : []),
            {
              key: 'remove',
              label: 'Remove from list',
              onSelect: onRemove,
              divider: true,
              disabled: !card.id,
              testid: 'project-card-remove',
              title: card.id ? 'The folder and its files stay where they are' : 'Not in the library list yet',
            },
          ]}
        />
      </div>
    </article>
  );
}

function SyncBadge({ c }: { c: NonNullable<ProjectCard['cloud']> }) {
  const [label, warn] = c.conflicts
    ? [c.conflicts === 1 ? '1 conflict' : `${c.conflicts} conflicts`, true]
    : c.paused === 'removed'
      ? ['Removed from the cloud', true]
      : c.paused
        ? ['Sync paused', true]
        : ['Synced', false];
  return (
    <span className={`badge ${warn ? 'badge-warn' : 'badge-ok'}`} data-testid="project-card-sync" data-conflicts={c.conflicts}>
      {label}
    </span>
  );
}

/** Projects in the cloud that aren't on this machine yet (cloud plan 6.9). */
function CloudRow() {
  const cloud = useCloud();
  const hostKind = useHostKind();
  const { library, reloadProject, reloadLibrary, toast } = useStore();
  const [rows, setRows] = useState<CloudProject[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const signedIn = !!cloud.status?.signed_in;
  const libraryKey = library?.projects.map((p) => p.id).join(',');

  useEffect(() => {
    if (!signedIn) {
      setRows(null);
      return;
    }
    let live = true;
    api
      .cloudProjects()
      .then((r) => live && (setRows(r.projects), setError(null)))
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
  }, [signedIn, libraryKey]);

  useEngineEvent((e) => {
    if (e.type === 'cloud_download_progress' && downloading && e.project_id === downloading) {
      setProgress({ done: Number(e.done), total: Number(e.total) });
    }
  });

  if (!signedIn) return null;
  const remote = (rows ?? []).filter((r) => !r.local_path);
  const download = async (r: CloudProject, choose = false) => {
    let location: string | null = null;
    if (choose) {
      location = await host.pickFolder(library?.root ?? null);
      if (!location) return;
    }
    setDownloading(r.id);
    setProgress(null);
    try {
      const res = await api.cloudDownload(r.id, location);
      toast(`Downloaded ${r.name}.`, 'ok');
      await reloadLibrary();
      if (res.opened) {
        await reloadProject();
        navigate('characters');
      }
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setDownloading(null);
      setProgress(null);
    }
  };
  return (
    <section className="cloud-row" data-testid="cloud-row">
      <h2 className="cloud-row-title">In the cloud</h2>
      {error ? <ErrorNote testid="cloud-row-error">{error}</ErrorNote> : null}
      {rows && !remote.length ? (
        <p className="muted" data-testid="cloud-row-empty">
          Every cloud project you can open is already on this machine.
        </p>
      ) : null}
      <div className="project-grid">
        {remote.map((r) => (
          <article key={r.id} className="project-card cloud-card" data-testid="cloud-card" data-id={r.id} data-name={r.name}>
            <span className={`project-thumb ${r.thumbnailSha ? 'checker' : 'ph-none'}`}>
              {r.thumbnailSha ? (
                <img src={cloudBlobUrl(r.ownerId, r.thumbnailSha)} alt="" draggable={false} data-testid="cloud-card-thumb" />
              ) : (
                <span className="project-thumb-mark" aria-hidden="true">
                  <Icon name="projects" size={30} />
                </span>
              )}
            </span>
            <span className="project-card-body">
              <span className="project-card-name">{r.name}</span>
              <span className="project-card-badges">
                <span className="badge" data-testid="cloud-card-workspace">
                  {r.workspace.name}
                </span>
              </span>
              <span className="project-card-meta">
                {plural(r.fileCount, 'file')} · {fileSize(r.totalBytes)}
              </span>
              <span className="project-card-when">Synced {timeAgo(r.updatedAt)}</span>
              {downloading === r.id ? (
                <span className="cloud-card-progress" data-testid="cloud-card-progress">
                  {progress ? `${progress.done} of ${progress.total} files` : 'Starting…'}
                  <Button size="sm" variant="quiet" onClick={() => void api.cloudDownloadCancel(r.id)} data-testid="cloud-card-cancel">
                    Cancel
                  </Button>
                </span>
              ) : (
                <span className="cloud-card-actions">
                  <Button size="sm" icon="download" disabled={!!downloading} onClick={() => void download(r)} data-testid="cloud-card-download">
                    Download
                  </Button>
                  {hostKind === 'pywebview' ? (
                    <Button size="sm" variant="quiet" disabled={!!downloading} onClick={() => void download(r, true)} data-testid="cloud-card-download-to">
                      Download to…
                    </Button>
                  ) : null}
                </span>
              )}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

function NewProjectDialog({ root, onClose }: { root: string | null; onClose: () => void }) {
  const { createProject, project, toast } = useStore();
  const hostKind = useHostKind();
  const [name, setName] = useState('');
  const [style, setStyle] = useState<StyleKind>(project?.config.style.kind ?? 'hd-cartoon');
  const [pixelHeight, setPixelHeight] = useState(48);
  const [engine, setEngine] = useState<EngineName>(project?.config.engine ?? 'godot');
  const [location, setLocation] = useState(''); // empty: the library folder
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  // after the modal has focused itself
  useEffect(() => nameRef.current?.focus(), []);

  const choose = async () => {
    const picked = await host.pickFolder(location || root);
    if (picked) setLocation(picked);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError('Give the project a name.');
      return;
    }
    setBusy(true);
    try {
      const r = await createProject({
        name: name.trim(),
        style: { kind: style, ...(style === 'pixel' ? { pixel_height: pixelHeight } : {}) },
        engine,
        ...(location.trim() ? { location: location.trim() } : {}),
      });
      toast(`Created ${r.project.name}. Add its first character.`, 'ok');
      onClose();
      navigate('characters');
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="New project"
      testid="project-form-modal"
      wide
      onClose={busy ? undefined : onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy} data-testid="project-form-cancel">
            Cancel
          </Button>
          <Button type="submit" form="np-form" variant="primary" icon="plus" busy={busy} data-testid="project-form-submit">
            Create project
          </Button>
        </>
      }
    >
      <form id="np-form" className="form-stack" onSubmit={submit} data-testid="project-form">
        <Field label="Name" htmlFor="np-name" hint="Shown in the gallery; the project folder is named after it.">
          <input
            id="np-name"
            ref={nameRef}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My game"
            autoComplete="off"
            data-testid="project-form-name"
          />
        </Field>
        <Field label="Style" hint={STYLE_HINT[style]}>
          <Segmented<StyleKind>
            value={style}
            onChange={setStyle}
            testid="project-form-style"
            label="Style"
            options={STYLES.map((k) => ({ value: k, label: STYLE_LABEL[k] }))}
          />
        </Field>
        {style === 'pixel' ? (
          <Field label="Character height (px)" htmlFor="np-ph" hint="A standing character’s height on the pixel grid; sprites are drawn at this size.">
            <div className="stepper">
              <input
                id="np-ph"
                type="number"
                min={16}
                max={256}
                value={pixelHeight}
                onChange={(e) => setPixelHeight(Math.max(16, Math.min(256, Math.round(Number(e.target.value) || 48))))}
                data-testid="project-form-pixel-height"
              />
            </div>
          </Field>
        ) : null}
        <Field label="Game engine" htmlFor="np-engine" hint="Exports are written for it. You can change it later in Settings.">
          <select id="np-engine" value={engine} onChange={(e) => setEngine(e.target.value as EngineName)} data-testid="project-form-engine">
            {ENGINES.map((en) => (
              <option key={en} value={en}>
                {ENGINE_LABEL[en]}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Location"
          htmlFor="np-loc"
          hint={location.trim() ? 'The project folder is created inside this folder.' : 'The library folder, where the gallery finds your projects.'}
        >
          <div className="input-row">
            <input
              id="np-loc"
              type="text"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder={root ?? 'Library folder'}
              spellCheck={false}
              autoComplete="off"
              data-testid="project-form-location"
            />
            {hostKind === 'pywebview' ? (
              <Button onClick={() => void choose()} icon="folder" data-testid="project-form-location-choose">
                Choose…
              </Button>
            ) : null}
            {location ? <IconButton icon="x" label="Use the library folder" onClick={() => setLocation('')} data-testid="project-form-location-reset" /> : null}
          </div>
        </Field>
        {error ? <ErrorNote testid="project-form-error">{error}</ErrorNote> : null}
      </form>
    </Modal>
  );
}

export function ProjectsScreen() {
  const { library, libraryError, reloadLibrary, openProject, project, characters, animations, toast } = useStore();
  const route = useRoute();
  const hostKind = useHostKind();
  const [creating, setCreating] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);

  // fresh counts and thumbnails on every visit
  useEffect(() => {
    void reloadLibrary();
  }, [reloadLibrary]);
  // `#/projects?new=1` (the header's "New project…") opens the create dialog
  useEffect(() => {
    if (route.query.get('new')) {
      setCreating(true);
      redirect('projects');
    }
  }, [route]);

  const open = async (card: ProjectCard) => {
    if (card.missing || card.error) return;
    if (card.current && project) {
      navigate('characters');
      return;
    }
    setOpening(card.id);
    try {
      await openProject({ id: card.id });
      navigate('characters');
    } catch (e) {
      toast(errText(e), 'error');
      void reloadLibrary();
    } finally {
      setOpening(null);
    }
  };

  const openFolder = async () => {
    const path = await host.pickFolder(library?.root ?? null);
    if (!path) return;
    try {
      await openProject({ path });
      navigate('characters');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  const remove = async (card: ProjectCard) => {
    try {
      await api.removeProject(card.id);
      toast(`Removed ${card.name} from the list. Its folder was not touched.`, 'ok');
      void reloadLibrary();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  const reveal = async (card: ProjectCard) => {
    const r = await host.reveal(card.path);
    if (!r.ok && r.message) toast(r.message, 'info');
  };

  const cards = cardsWithCurrent(library, project, { characters: characters?.length ?? 0, animations: animations?.length ?? 0 });
  return (
    <div className="screen" data-testid="projects-screen">
      <div className="screen-head">
        <h1 className="screen-title">Projects</h1>
        {library ? (
          <span className="muted projects-root" title="New projects go here unless you choose another folder" data-testid="projects-root">
            {library.root}
          </span>
        ) : null}
        <div className="screen-actions">
          {hostKind === 'pywebview' ? (
            <Button size="sm" icon="folder" onClick={() => void openFolder()} data-testid="projects-open-folder">
              Open folder…
            </Button>
          ) : null}
        </div>
      </div>
      <div className="screen-body">
        <AccessBanner always />
        <UpdateBanner />
        {libraryError && !library ? <ErrorNote testid="projects-error">{libraryError}</ErrorNote> : null}
        {!library && !libraryError ? <Spinner label="Loading projects…" /> : null}
        {library ? (
          <div className="project-grid" data-testid="project-grid">
            <button type="button" className="project-new" onClick={() => setCreating(true)} data-testid="projects-new">
              <span className="project-new-mark" aria-hidden="true">
                <Icon name="plus" size={26} />
              </span>
              <span className="project-new-title">New project</span>
              <span className="muted">Pick a style and a game engine</span>
            </button>
            {cards.map((c) => (
              <ProjectTile
                key={c.id || c.path}
                card={c}
                busy={opening === c.id}
                canReveal={hostKind === 'pywebview'}
                onOpen={() => void open(c)}
                onReveal={() => void reveal(c)}
                onRemove={() => void remove(c)}
                onSync={() => void open(c).then(() => navigate('settings'))}
              />
            ))}
          </div>
        ) : null}
        {library && cards.length === 0 ? (
          <p className="muted projects-first" data-testid="projects-empty">
            No projects yet. Start one with a style and an engine; it opens on the Characters tab.
          </p>
        ) : null}
        <CloudRow />
      </div>
      {creating ? <NewProjectDialog root={library?.root ?? null} onClose={() => setCreating(false)} /> : null}
    </div>
  );
}
