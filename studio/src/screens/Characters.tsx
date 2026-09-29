import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEvents } from '../lib/events';
import { fileSize, KIND_LABEL, kindLabel, thumbOf } from '../lib/format';
import { navigate, redirect, useRoute } from '../lib/router';
import { animationsOf, errText, useStore } from '../lib/store';
import { SUBJECT_KINDS, type Blend, type CharacterRecord, type SubjectKind } from '../lib/types';
import { Button, Empty, ErrorNote, Field, IconButton, Segmented, Spinner, Swatch, Toggle, WarnNote } from '../ui/controls';
import { Icon } from '../ui/Icon';
import { CharacterAnimations } from './CharacterAnimations';

/** Every view a subject can have, in sheet order: the turnaround's four, or an effect's one design view. */
const VIEWS: { key: string; label: string }[] = [
  { key: 'front', label: 'Front' },
  { key: 'side-e', label: 'Side E' },
  { key: 'side-w', label: 'Side W' },
  { key: 'back', label: 'Back' },
  { key: 'key', label: 'Design' },
];

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
const MAX_IMAGE = 20 * 1024 * 1024; // the engine's upload limit

/** Reads a reference image as a data URL, failing early on what the engine would reject anyway. */
function readImage(file: File): Promise<string> {
  if (file.type && !IMAGE_TYPES.includes(file.type)) {
    return Promise.reject(new Error(`${file.name} is not a PNG, JPEG, WebP or GIF image.`));
  }
  if (file.size > MAX_IMAGE) {
    return Promise.reject(new Error(`${file.name} is ${fileSize(file.size)}; reference images can be up to 20 MB.`));
  }
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    r.readAsDataURL(file);
  });
}

/** Views that a warning says touched a neighbour on the turnaround and may be clipped. */
function flaggedViews(rec: CharacterRecord): Set<string> {
  const out = new Set<string>();
  for (const w of rec.view_warnings) {
    if (!/touching|clipped/.test(w)) continue;
    const words = w.split(/[\s,;:]+/);
    for (const v of Object.keys(rec.views)) if (words.includes(v)) out.add(v);
  }
  return out;
}

/** The subject's views on one ground line, with a rule at every head height for characters. Only the
 * views it has are drawn: effects have one floating design view, image-as-view subjects their sides. */
function ModelSheet({ rec, bust }: { rec: CharacterRecord; bust: string }) {
  const effect = rec.kind === 'effect';
  const heads = rec.kind === 'character' && rec.heads_tall && rec.heads_tall > 1 ? rec.heads_tall : null;
  const rules = heads ? Array.from({ length: Math.floor(heads) }, (_, i) => i + 1) : [];
  const views = [
    ...VIEWS.filter((v) => rec.views[v.key]),
    ...Object.keys(rec.views)
      .filter((k) => !VIEWS.some((v) => v.key === k))
      .map((k) => ({ key: k, label: k })),
  ];
  const flagged = flaggedViews(rec);
  return (
    <div
      className={`model-sheet ${rec.blend === 'add' ? 'additive' : 'checker'}${effect ? ' is-floating' : ''}`}
      style={{ gridTemplateColumns: `repeat(${views.length}, minmax(0, 1fr))`, maxWidth: views.length < 4 ? views.length * 220 + 62 : undefined }}
      data-testid="character-model-sheet"
      data-views={views.length}
    >
      <div className="figure-zone" aria-hidden="true">
        {rules.map((k) => (
          <span key={k} className="head-rule" style={{ bottom: `${(k / heads!) * 100}%` }}>
            <span>{k}</span>
          </span>
        ))}
        {effect ? null : <span className="ground-rule" />}
      </div>
      {views.map((v) => (
        <figure
          key={v.key}
          className={`model-view${flagged.has(v.key) ? ' is-flagged' : ''}`}
          data-testid={`character-view-${v.key}`}
          data-flagged={flagged.has(v.key) ? 'true' : undefined}
        >
          <div className="model-view-img">
            <img src={fileUrl(rec.views[v.key], bust)} alt={`${rec.name}, ${v.label.toLowerCase()} view`} draggable={false} />
          </div>
          <figcaption>
            {flagged.has(v.key) ? <Icon name="warn" size={12} title="May be clipped" /> : null}
            {v.label}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

const hasViewsOf = (rec: CharacterRecord) => Object.keys(rec.views).length > 0;

function CharacterRow({ rec, onChanged }: { rec: CharacterRecord; onChanged: () => void }) {
  const { toast } = useStore();
  const ev = useEvents();
  const ta = ev.turnaround[rec.name];
  const [busy, setBusy] = useState<string | null>(null);
  // the engine records which candidate the current views were cropped from
  const chosen = hasViewsOf(rec) ? rec.turnaround_index : null;
  const [bust, setBust] = useState(() => String(Date.now()));
  const [replaced, setReplaced] = useState(false);
  const replaceInput = useRef<HTMLInputElement>(null);
  const running = ta?.state === 'running';
  const hasViews = Object.keys(rec.views).length > 0;
  const effect = rec.kind === 'effect';
  const additive = rec.blend === 'add';
  // views and the reference are rewritten in place, so a finished turnaround also refreshes them
  const imgBust = `${bust}-${ta?.ts ?? 0}`;
  const fromImage = hasViews && !!rec.source_image && rec.turnaround === rec.source_image;

  const run = async (label: string, fn: () => Promise<unknown>, ok: string): Promise<boolean> => {
    setBusy(label);
    try {
      await fn();
      toast(ok, 'ok');
      setBust(String(Date.now()));
      onChanged();
      return true;
    } catch (e) {
      toast(errText(e), 'error');
      return false;
    } finally {
      setBusy(null);
    }
  };

  const regenerate = async () => {
    const ok = await run(
      'regen',
      () => api.regenerateTurnaround(rec.name, Math.max(1, rec.turnaround_candidates.length || 1), Math.floor(Math.random() * 1e6)),
      'Drawing a new turnaround.',
    );
    if (ok) setReplaced(false);
  };

  const replaceImage = async (file: File | undefined) => {
    if (!file) return;
    let data: string;
    try {
      data = await readImage(file);
    } catch (e) {
      toast(errText(e), 'error');
      return;
    }
    const ok = await run('replace', () => api.replaceCharacterImage(rec.name, data), `Replaced the reference image of ${rec.name}.`);
    if (ok) setReplaced(true);
  };

  return (
    <article className="char-row panel" data-testid="character-row" data-name={rec.name} data-kind={rec.kind} data-blend={rec.blend}>
      <header className="char-head">
        <h2 className="char-name" data-testid="character-name">
          {rec.name}
        </h2>
        <span className="badge" data-testid="character-kind-badge" data-kind={rec.kind}>
          {kindLabel(rec.kind)}
        </span>
        {rec.approved ? (
          <span className="badge badge-ok" data-testid="character-approved-badge">
            Approved
          </span>
        ) : hasViews ? (
          <span className="badge badge-warn" data-testid="character-pending-badge">
            Needs approval
          </span>
        ) : null}
        <span className="char-facts">
          {rec.kind === 'character' && rec.heads_tall ? (
            <span data-testid="character-heads-tall">
              <strong className="num">{rec.heads_tall.toFixed(1)}</strong> heads tall
            </span>
          ) : null}
          {rec.height_px ? (
            <span>
              <strong className="num">{rec.height_px}</strong> px
            </span>
          ) : null}
          {effect ? <span data-testid="character-blend-label">{additive ? 'Additive glow' : 'Normal blend'}</span> : null}
          <span>{rec.mirrorable ? 'Mirrorable' : 'Not mirrorable'}</span>
          <span>{rec.style.kind}</span>
        </span>
        <span className="spacer" />
        {rec.approved ? (
          <Button size="sm" variant="quiet" icon="builder" data-testid="character-animate" onClick={() => navigate('builder')}>
            Animate
          </Button>
        ) : null}
      </header>

      <div className="char-intro">
        <div className="char-intro-text">
          {rec.description ? (
            <p className="char-desc">{rec.description}</p>
          ) : (
            <p className="muted">{running ? 'Describing the reference image…' : 'No description yet.'}</p>
          )}
          {rec.description_source === 'image' && rec.description ? (
            <p className="muted small-print" data-testid="character-description-source">
              Written from the reference image.
            </p>
          ) : null}
        </div>
        {rec.source_image ? (
          <figure className="char-ref" data-testid="character-source-image">
            <div className={`char-ref-img ${additive ? 'additive' : 'checker'}`}>
              <img src={fileUrl(rec.source_image, imgBust)} alt={`Reference image of ${rec.name}`} draggable={false} />
            </div>
            <figcaption>Reference</figcaption>
          </figure>
        ) : null}
      </div>

      {running ? (
        <div className="char-progress" data-testid="character-turnaround-progress">
          <Spinner label={effect ? 'Drawing the design…' : 'Drawing the turnaround…'} />
          <span className="indeterminate" aria-hidden="true" />
        </div>
      ) : null}
      {ta?.state === 'failed' ? (
        <ErrorNote testid="character-turnaround-error">Turnaround failed: {ta.error}</ErrorNote>
      ) : null}

      {replaced ? (
        <div className="char-offer" role="status" data-testid="character-replaced-offer">
          <Icon name="image" size={16} />
          <span>New reference saved. Draw a new turnaround so the views follow it{rec.approved ? '; this clears the approval' : ''}.</span>
          <Button
            size="sm"
            variant="primary"
            icon="refresh"
            disabled={running || busy !== null}
            busy={busy === 'regen'}
            data-testid="character-replaced-regenerate"
            onClick={() => void regenerate()}
          >
            Draw new turnaround
          </Button>
          <IconButton icon="x" label="Dismiss" onClick={() => setReplaced(false)} data-testid="character-replaced-dismiss" />
        </div>
      ) : null}

      {hasViews && rec.view_warnings.length ? (
        <WarnNote testid="character-view-warnings">
          <strong>
            Check {effect ? 'the design' : 'these views'}
            {rec.approved ? '' : ' before approving'}
          </strong>
          <ul>
            {rec.view_warnings.map((w, i) => (
              <li key={i} data-testid="character-view-warning">
                {w}
              </li>
            ))}
          </ul>
        </WarnNote>
      ) : null}
      {hasViews ? <ModelSheet rec={rec} bust={imgBust} /> : null}
      {fromImage && !effect ? (
        <p className="muted small-print" data-testid="character-image-view-note">
          Cut straight from the reference image: side views only, so top-down animations are unavailable.
        </p>
      ) : null}
      {!hasViews && !running && ta?.state !== 'failed' ? (
        <p className="muted">
          {effect ? 'No design yet. Draw one to lock this effect’s look.' : `No turnaround yet. Draw one to lock this ${kindLabel(rec.kind).toLowerCase()}’s look.`}
        </p>
      ) : null}

      {rec.palette.length ? (
        <div className="palette" data-testid="character-palette" aria-label="Palette">
          {rec.palette.map((h) => (
            <Swatch key={h} hex={h} testid="character-swatch" />
          ))}
        </div>
      ) : null}

      {rec.turnaround_candidates.length > 1 || (!hasViews && rec.turnaround_candidates.length > 0) ? (
        <div className="ta-cands" data-testid="character-turnaround-candidates">
          <div className="ta-cands-title">Turnaround candidates</div>
          <div className="ta-cands-row">
            {rec.turnaround_candidates.map((c, i) => (
              <div
                key={c}
                className={`ta-cand${chosen === i ? ' is-chosen' : ''}${effect ? ' is-single' : ''}`}
                data-testid="character-turnaround-candidate"
                data-index={i}
                data-chosen={chosen === i ? 'true' : 'false'}
              >
                <img src={fileUrl(c, imgBust)} alt={`Turnaround candidate ${i + 1}`} />
                <div className="ta-cand-foot">
                  <span>Candidate {i + 1}</span>
                  <Button
                    size="sm"
                    variant={chosen === i ? 'quiet' : 'default'}
                    busy={busy === `choose-${i}`}
                    disabled={busy !== null || running || chosen === i}
                    data-testid="character-choose-turnaround"
                    onClick={() =>
                      void run(`choose-${i}`, () => api.chooseTurnaround(rec.name, i), `Using candidate ${i + 1}. Approve it to lock the character.`)
                    }
                  >
                    {chosen === i ? 'In use' : 'Use this'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <footer className="char-actions">
        {!rec.approved ? (
          <Button
            variant="primary"
            icon="check"
            disabled={!hasViews || running || busy !== null}
            busy={busy === 'approve'}
            data-testid="character-approve"
            onClick={() => void run('approve', () => api.approveCharacter(rec.name), `${rec.name} is approved and ready to animate.`)}
          >
            Approve {kindLabel(rec.kind).toLowerCase()}
          </Button>
        ) : null}
        <Button
          variant="quiet"
          icon="refresh"
          disabled={running || busy !== null}
          busy={busy === 'regen'}
          data-testid="character-regenerate"
          onClick={() => void regenerate()}
        >
          New turnaround
        </Button>
        <input
          ref={replaceInput}
          type="file"
          accept={IMAGE_TYPES.join(',')}
          hidden
          data-testid="character-replace-image-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = '';
            void replaceImage(f);
          }}
        />
        <Button
          variant="quiet"
          icon="image"
          disabled={running || busy !== null}
          busy={busy === 'replace'}
          data-testid="character-replace-image"
          onClick={() => replaceInput.current?.click()}
        >
          {rec.source_image ? 'Replace reference image' : 'Add reference image'}
        </Button>
        {rec.approved ? <span className="muted">Drawing a new turnaround clears the approval.</span> : null}
      </footer>
    </article>
  );
}

const PLACEHOLDER: Record<SubjectKind, { name: string; description: string }> = {
  character: { name: 'knight', description: 'An armoured knight with a blue tabard and a round shield.' },
  vehicle: { name: 'tank', description: 'An olive green battle tank with a long cannon and wide treads.' },
  machine: { name: 'generator', description: 'A rusty yellow steam generator with a big gear and a red warning light.' },
  effect: { name: 'fireball', description: 'A blue glowing energy ball with a streaming tail.' },
};

const KIND_HINT: Record<SubjectKind, string> = {
  character: 'A figure with a body: heroes, enemies, creatures.',
  vehicle: 'A rigid body that drives, fires and gets destroyed: tanks, cars, ships.',
  machine: 'A rigid object that works, activates and breaks: generators, turrets, doors.',
  effect: 'Projectiles, impacts, explosions and auras: one design view, no ground line.',
};

const DESCRIPTION_HINT: Record<SubjectKind, string> = {
  character: 'One to three sentences of visual facts: build, clothing, colours, props.',
  vehicle: 'One to three sentences of visual facts: shape, colours, parts.',
  machine: 'One to three sentences of visual facts: shape, colours, parts.',
  effect: 'One to three sentences of visual facts: colour, shape, glow, trail.',
};

interface RefImage {
  url: string; // data URL, sent as-is
  name: string;
  size: number;
  w?: number;
  h?: number;
}

function NewCharacterForm({ onCreated, onClose }: { onCreated: (name: string) => void; onClose?: () => void }) {
  const { toast, characters } = useStore();
  const [kind, setKind] = useState<SubjectKind>('character');
  const [blend, setBlend] = useState<Blend>('add');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [image, setImage] = useState<RefImage | null>(null);
  const [imageAsView, setImageAsView] = useState(false);
  const [over, setOver] = useState(false);
  const [mirrorable, setMirrorable] = useState(true);
  const [candidates, setCandidates] = useState(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const noun = KIND_LABEL[kind].toLowerCase();

  const takeFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      const url = await readImage(file);
      setImage({ url, name: file.name, size: file.size });
    } catch (e) {
      setError(errText(e));
    }
  };
  const clearImage = () => {
    setImage(null);
    setImageAsView(false);
  };

  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!Array.from(e.dataTransfer.types).includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setOver(true);
  };
  const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    void takeFile(e.dataTransfer.files[0]);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim() || (!description.trim() && !image)) {
      setError(image ? `Give the ${noun} a name.` : `Give the ${noun} a name and a short visual description, or add a reference image.`);
      return;
    }
    // the engine silently replaces a subject created under an existing name, approval and all
    const taken = (characters ?? []).find((c) => c.name.toLowerCase() === name.trim().toLowerCase());
    if (taken) {
      setError(`${taken.name} already exists. Pick another name, or redraw or re-reference it from its card.`);
      return;
    }
    setBusy(true);
    try {
      const rec = await api.createCharacter({
        name: name.trim(),
        description: description.trim(),
        mirrorable,
        candidates,
        kind,
        ...(kind === 'effect' ? { blend } : {}),
        ...(image ? { image: image.url, use_image_as_view: imageAsView } : {}),
      });
      toast(
        image && imageAsView
          ? `Created ${rec.name}. Cutting its views from the image…`
          : `Created ${rec.name}. Drawing ${candidates === 1 ? 'a turnaround' : `${candidates} turnarounds`}…`,
        'ok',
      );
      setName('');
      setDescription('');
      clearImage();
      onCreated(rec.name);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel new-char" onSubmit={submit} data-testid="character-form" data-kind={kind}>
      <div className="panel-head">
        <h2>New {noun}</h2>
        <span className="spacer" />
        {onClose ? <IconButton icon="x" label="Close the form" onClick={onClose} data-testid="character-form-close" /> : null}
      </div>
      <div className="panel-body form-stack">
        <Field label="Kind" hint={KIND_HINT[kind]}>
          <Segmented<SubjectKind>
            value={kind}
            onChange={setKind}
            testid="character-kind"
            label="Kind"
            options={SUBJECT_KINDS.map((k) => ({ value: k, label: KIND_LABEL[k] }))}
          />
        </Field>
        {kind === 'effect' ? (
          <Field
            label="Blend"
            hint={
              blend === 'add'
                ? 'Drawn on black and added like light in the game: fire, magic, energy.'
                : 'Drawn with transparency like any sprite: smoke, dust, water.'
            }
          >
            <Segmented<Blend>
              value={blend}
              onChange={setBlend}
              testid="character-blend"
              label="Blend"
              options={[
                { value: 'add', label: 'Additive glow on black' },
                { value: 'normal', label: 'Normal' },
              ]}
            />
          </Field>
        ) : null}
        <Field label="Name" htmlFor="nc-name" hint="Lowercase works best; it becomes part of file names.">
          <input
            id="nc-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={PLACEHOLDER[kind].name}
            data-testid="character-form-name"
            autoComplete="off"
          />
        </Field>
        <Field label="Reference image" hint="Optional. The turnaround follows its look. PNG, JPEG, WebP or GIF, up to 20 MB.">
          <div
            className={`drop-zone${over ? ' is-over' : ''}${image ? ' has-image' : ''}`}
            data-testid="character-image"
            data-state={image ? 'set' : 'empty'}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
          >
            <input
              ref={fileInput}
              type="file"
              accept={IMAGE_TYPES.join(',')}
              hidden
              data-testid="character-image-input"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                void takeFile(f);
              }}
            />
            {image ? (
              <>
                <span className={`drop-zone-thumb ${kind === 'effect' && blend === 'add' ? 'additive' : 'checker'}`}>
                  <img
                    src={image.url}
                    alt="Reference image preview"
                    data-testid="character-image-preview"
                    onLoad={(e) => {
                      const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                      setImage((i) => (i && i.url === image.url && i.w !== w ? { ...i, w, h } : i));
                    }}
                  />
                </span>
                <span className="drop-zone-meta">
                  <span className="drop-zone-name" title={image.name}>
                    {image.name}
                  </span>
                  <span className="muted num">
                    {image.w ? `${image.w} × ${image.h} px, ` : ''}
                    {fileSize(image.size)}
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => fileInput.current?.click()} data-testid="character-image-change">
                    Change
                  </Button>
                </span>
                <IconButton icon="x" label="Remove image" onClick={clearImage} data-testid="character-image-remove" />
              </>
            ) : (
              <button type="button" className="drop-zone-empty" onClick={() => fileInput.current?.click()} data-testid="character-image-pick">
                <Icon name="upload" size={20} />
                <span>
                  <strong>Drop an image here</strong> or choose a file
                </span>
              </button>
            )}
          </div>
        </Field>
        <Toggle
          checked={imageAsView}
          onChange={setImageAsView}
          disabled={!image}
          label={kind === 'effect' ? 'Use this image as the design (skip turnaround)' : 'Use this image as the side view (skip turnaround)'}
          hint={
            !image
              ? 'Add a reference image first.'
              : kind === 'effect'
                ? 'The image becomes the effect’s one design view.'
                : 'Side views only, so top-down animations are unavailable.'
          }
          testid="character-use-image-as-view"
        />
        <Field
          label={image ? <>Description <span className="muted">(optional)</span></> : 'Description'}
          htmlFor="nc-desc"
          hint={image ? 'Leave it empty to describe the image, or add facts the image does not show.' : DESCRIPTION_HINT[kind]}
        >
          <textarea
            id="nc-desc"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={image ? 'Leave empty and it will be described from the image.' : PLACEHOLDER[kind].description}
            data-testid="character-form-description"
          />
        </Field>
        <Toggle
          checked={mirrorable}
          onChange={setMirrorable}
          label="Mirrorable"
          hint="Symmetric design: the west-facing view is a flip of the east one."
          testid="character-form-mirrorable"
        />
        <Field
          label="Turnaround candidates"
          htmlFor="nc-n"
          hint={image && imageAsView ? 'Not used: the image is the view.' : 'More candidates cost more but give you a choice.'}
        >
          <div className="stepper">
            <input
              id="nc-n"
              type="number"
              min={1}
              max={4}
              value={candidates}
              disabled={!!image && imageAsView}
              onChange={(e) => setCandidates(Math.max(1, Math.min(4, Number(e.target.value) || 1)))}
              data-testid="character-form-candidates"
            />
          </div>
        </Field>
        {error ? <ErrorNote testid="character-form-error">{error}</ErrorNote> : null}
        <Button type="submit" variant="primary" icon="sparkle" busy={busy} data-testid="character-form-submit">
          {image && imageAsView ? 'Create from image' : kind === 'effect' ? 'Create and draw design' : 'Create and draw turnaround'}
        </Button>
      </div>
    </form>
  );
}

/** Another character of the project, compact: picking it makes it the active character. */
function CharacterCard({ rec, onPick }: { rec: CharacterRecord; onPick: () => void }) {
  const ev = useEvents();
  const ta = ev.turnaround[rec.name];
  const src = thumbOf(rec);
  const hasViews = Object.keys(rec.views).length > 0;
  return (
    <button
      type="button"
      className="char-card"
      onClick={onPick}
      data-testid="character-card"
      data-name={rec.name}
      data-kind={rec.kind}
      title={`Work on ${rec.name}: every tab follows it`}
    >
      <span className={`char-card-thumb ${rec.blend === 'add' ? 'additive' : 'checker'}`}>
        {src ? <img src={fileUrl(src, ta?.ts)} alt="" draggable={false} /> : <Icon name="characters" size={22} />}
      </span>
      <span className="char-card-body">
        <span className="char-card-name">{rec.name}</span>
        <span className="char-card-badges">
          <span className="badge" data-testid="character-kind-badge" data-kind={rec.kind}>
            {kindLabel(rec.kind)}
          </span>
          {ta?.state === 'running' ? (
            <span className="badge badge-accent">Drawing…</span>
          ) : rec.approved ? (
            <span className="badge badge-ok">Approved</span>
          ) : hasViews ? (
            <span className="badge badge-warn">Needs approval</span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

export function Characters() {
  const { characters, animations, reloadCharacters, activeCharacter, setActiveCharacter } = useStore();
  const route = useRoute();
  const list = characters ?? [];
  const active = list.find((c) => c.name === activeCharacter) ?? null;
  const others = list.filter((c) => c !== active);
  const firstRun = characters !== null && list.length === 0;
  const [formOpen, setFormOpen] = useState(false);
  const [focusForm, setFocusForm] = useState(0); // bumped to scroll to and focus the name field
  const showForm = formOpen || firstRun;
  const [focus, setFocus] = useState<string | null>(null);

  const openForm = () => {
    setFormOpen(true);
    setFocusForm((n) => n + 1);
  };
  // `#/characters?new=1` (the header's "New character…") opens the form
  useEffect(() => {
    if (route.query.get('new')) {
      openForm();
      redirect('characters');
    }
  }, [route]);
  useEffect(() => {
    if (!focusForm) return;
    const input = document.getElementById('nc-name');
    input?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    input?.focus({ preventScroll: true });
  }, [focusForm]);
  useEffect(() => {
    if (!focus) return;
    const el = document.querySelector(`[data-testid="character-row"][data-name="${CSS.escape(focus)}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setFocus(null);
    }
  }, [focus, characters]);

  return (
    <div className="screen" data-testid="characters-screen">
      <div className="screen-head">
        <h1 className="screen-title">Characters</h1>
        <span className="muted" data-testid="characters-count">
          {characters ? `${list.filter((c) => c.approved).length} of ${list.length} approved` : ''}
        </span>
        <div className="screen-actions">
          <Button size="sm" icon="plus" data-testid="characters-new" onClick={openForm}>
            New character
          </Button>
        </div>
      </div>
      <div className={`screen-body chars-layout${showForm ? ' has-form' : ''}`}>
        <section className="char-list" data-testid="character-list" aria-label="Characters">
          {characters === null ? <Spinner label="Loading characters…" /> : null}
          {firstRun ? (
            <Empty title="No characters yet" testid="characters-empty">
              Describe your first character, vehicle, machine or effect, or start from a reference image. The studio draws a turnaround you
              approve before animating, and every tab then follows the character you pick in the header.
            </Empty>
          ) : null}
          {active ? <CharacterRow key={active.name} rec={active} onChanged={() => void reloadCharacters()} /> : null}
          {active && (active.approved || animationsOf(animations, active.name).length) ? (
            <CharacterAnimations key={`animations-${active.name}`} rec={active} />
          ) : null}
          {activeCharacter && !active && list.length ? <Spinner label={`Loading ${activeCharacter}…`} /> : null}
          {!activeCharacter && list.length ? (
            <p className="muted" data-testid="characters-choose">
              Choose the character to work on. Every tab follows it.
            </p>
          ) : null}
          {others.length ? (
            <section className="char-others" data-testid="character-others" aria-label={active ? 'Other characters' : 'Characters'}>
              <h2 className="char-others-title">{active ? 'Other characters' : 'Characters'}</h2>
              <div className="char-cards">
                {others.map((c) => (
                  <CharacterCard key={c.name} rec={c} onPick={() => void setActiveCharacter(c.name)} />
                ))}
              </div>
            </section>
          ) : null}
        </section>
        {showForm ? (
          <aside className="chars-side">
            <NewCharacterForm
              onClose={firstRun ? undefined : () => setFormOpen(false)}
              onCreated={(n) => {
                // a new character is what the user works on next
                void setActiveCharacter(n);
                setFormOpen(false);
                setFocus(n);
                void reloadCharacters();
              }}
            />
          </aside>
        ) : null}
      </div>
    </div>
  );
}
