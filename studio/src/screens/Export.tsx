import { useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEvents } from '../lib/events';
import { ENGINE_LABEL, isAdditive, q } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { href } from '../lib/router';
import { errText, useStore } from '../lib/store';
import { ENGINES, type AnimationSummary, type EngineName, type ExportResult } from '../lib/types';
import { host } from '../host';
import { AnimHeader } from '../shell/AnimHeader';
import { Button, Empty, ErrorNote, Field, Spinner } from '../ui/controls';
import { Flipbook } from '../ui/Flipbook';
import { PublishDialog } from './Library';

const ENGINE_NOTE: Record<EngineName, string> = {
  phaser: 'PNG sheet with a TexturePacker-style JSON hash and an animations map.',
  pixi: 'PNG sheet with a TexturePacker-style JSON hash.',
  godot: 'PNG sheet with a SpriteFrames .tres resource: atlas regions, speed and loop.',
  unity: 'PNG sheet, JSON and an editor importer script that slices sprites and builds the clip.',
  gamemaker: 'A strip named name_stripN.png, which GameMaker slices automatically on import.',
};

function fileKind(f: string): string {
  if (f.startsWith('frames/')) return 'Frame';
  if (f.endsWith('.gif')) return 'Preview';
  if (f.endsWith('.png')) return 'Image';
  if (f.endsWith('.tres')) return 'Godot resource';
  if (f.endsWith('.cs')) return 'Unity script';
  if (f.endsWith('aseprite.json')) return 'Aseprite JSON';
  if (f.endsWith('.json')) return 'JSON';
  return 'File';
}

export function ExportScreen({ anim }: { anim: string | null }) {
  const [publishing, setPublishing] = useState(false);
  const { project, toast, reloadAnimations } = useStore();
  const ev = useEvents();
  const bust = anim ? String(ev.exported[anim] ?? 0) : '0';
  const [localBust, setLocalBust] = useState(0);
  const a = useAsync<AnimationSummary>(() => api.animation(anim!), anim ? `${anim}|${bust}|${localBust}` : null);
  const projectEngine = (project?.config.engine ?? 'godot') as EngineName;
  const [engine, setEngine] = useState<EngineName | null>(null);
  const chosen = engine ?? projectEngine;
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ExportResult | null>(null);

  const final = a.data?.final ?? null;
  const additive = isAdditive(a.data);
  const lastDone = a.data?.latest_job?.state === 'done' ? a.data.latest_job : null;
  const files = result?.files ?? lastDone?.result?.files ?? [];
  const finalDir = result?.dir ?? (project && anim ? `${project.root}/animations/${anim}/final` : '');
  const nonFrames = files.filter((f) => !f.startsWith('frames/'));
  const frameCount = files.length - nonFrames.length;

  const run = async () => {
    if (!anim) return;
    setBusy(true);
    try {
      const r = await api.exportAnim(anim, chosen);
      setResult(r);
      setLocalBust(Date.now());
      toast(`Exported ${anim} for ${ENGINE_LABEL[chosen]}${r.copied_to ? ' and copied it to the game folder' : ''}.`, 'ok');
      void reloadAnimations();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const reveal = async (path: string) => {
    const r = await host.reveal(path);
    if (!r.ok && r.message) toast(r.message, 'info');
  };

  return (
    <div className="screen" data-testid="export-screen">
      <AnimHeader screen="export" title="Export" anim={anim} />
      <div className="screen-body">
        {a.error ? <ErrorNote testid="export-error">{a.error}</ErrorNote> : null}
        {!a.data && !a.error && anim ? <Spinner label="Loading…" /> : null}
        {a.data && !final ? (
          <Empty
            title="Nothing to export yet"
            testid="export-nothing"
            action={
              <a className="btn btn-primary" href={href('builder')}>
                Open the builder
              </a>
            }
          >
            Finish a job for this animation; its winner becomes the exported frame set.
          </Empty>
        ) : null}
        {a.data && final ? (
          <div className="export-layout">
            <section className="panel export-preview" data-testid="export-preview">
              <div className="panel-head">
                <h2>Preview</h2>
                <span className="muted num">
                  {final.frames} frames, {final.size[0]} × {final.size[1]} px, {final.loop ? 'loops' : 'plays once'}
                </span>
                {additive ? (
                  <span className="badge badge-accent" data-testid="export-additive-badge" title="Additive glow drawn on black">
                    Additive blend
                  </span>
                ) : null}
              </div>
              <div className={`gif-stage ${additive ? 'additive' : 'checker'}`} data-blend={additive ? 'add' : 'normal'}>
                {additive && anim ? (
                  // additive effects play from their frames: preview.gif is flattened onto a light checker
                  <Flipbook
                    key={`${bust}-${localBust}`}
                    anim={anim}
                    final={final}
                    bust={`${bust}-${localBust}`}
                    alt={`Animated preview of ${anim}`}
                    testid="export-preview-frames"
                  />
                ) : (
                  <img
                    src={fileUrl(`animations/${anim}/final/preview.gif`, `${bust}-${localBust}`)}
                    alt={`Animated preview of ${anim}`}
                    data-testid="export-preview-gif"
                  />
                )}
              </div>
              <div className={`panel-body sheet-thumb${additive ? ' additive' : ''}`}>
                <img src={fileUrl(`animations/${anim}/final/sheet.png`, `${bust}-${localBust}`)} alt="Packed sprite sheet" data-testid="export-sheet" />
              </div>
            </section>

            <section className="export-side">
              <div className="panel">
                <div className="panel-body form-stack">
                  <Field
                    label="Engine"
                    htmlFor="ex-engine"
                    hint={chosen !== projectEngine ? `The project uses ${ENGINE_LABEL[projectEngine]}; exporting also saves ${ENGINE_LABEL[chosen]} as this animation’s engine.` : ENGINE_NOTE[chosen]}
                  >
                    <select id="ex-engine" value={chosen} onChange={(e) => setEngine(e.target.value as EngineName)} data-testid="export-engine">
                      {ENGINES.map((en) => (
                        <option key={en} value={en}>
                          {ENGINE_LABEL[en]}
                          {en === projectEngine ? ' (project)' : ''}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <p className="muted">Every export also writes Aseprite JSON, a GIF preview and a zip of the whole set.</p>
                  {additive ? (
                    <p className="muted" data-testid="export-additive-note">
                      Additive effect: draw the sprite with Add blending in your engine so its black stays invisible and the glow brightens what is
                      behind it.
                    </p>
                  ) : null}
                  <Button variant="primary" icon="export" busy={busy} onClick={() => void run()} data-testid="export-run">
                    Export for {ENGINE_LABEL[chosen]}
                  </Button>
                  {project?.config.asset_folder ? (
                    <p className="muted" data-testid="export-asset-folder">
                      Copies to <code>{project.config.asset_folder}</code>
                    </p>
                  ) : (
                    <p className="muted">
                      Set a game asset folder in <a href={href('settings')}>Settings</a> to copy each export into your game.
                    </p>
                  )}
                </div>
              </div>

              <div className="panel" data-testid="export-files">
                <div className="panel-head">
                  <h2>{result ? 'Exported files' : 'Current files'}</h2>
                  <span className="spacer" />
                  <Button size="sm" variant="quiet" icon="library" disabled={!final} onClick={() => setPublishing(true)} data-testid="export-publish">
                    Add to library
                  </Button>
                  <Button size="sm" variant="quiet" icon="folder" disabled={!finalDir} onClick={() => void reveal(finalDir)} data-testid="export-reveal">
                    Show in folder
                  </Button>
                  {publishing && anim ? (
                    <PublishDialog
                      prefix={`animations/${anim}/final/`}
                      name={anim}
                      kind={a.data?.spec.character.kind === 'effect' ? 'effect' : 'animation'}
                      onClose={() => setPublishing(false)}
                    />
                  ) : null}
                </div>
                <ul className="file-list">
                  {nonFrames.map((f) => (
                    <li key={f} data-testid="export-file">
                      <span className="file-kind">{fileKind(f)}</span>
                      <a href={fileUrl(`animations/${anim}/final/${f}`, localBust || undefined)} target="_blank" rel="noreferrer" className="file-name">
                        {f}
                      </a>
                    </li>
                  ))}
                  {frameCount ? (
                    <li data-testid="export-file-frames">
                      <span className="file-kind">Frames</span>
                      <span className="file-name">
                        frames/000.png to frames/{String(frameCount - 1).padStart(3, '0')}.png ({frameCount})
                      </span>
                    </li>
                  ) : null}
                  {!files.length ? <li className="muted">Run an export to list its files.</li> : null}
                </ul>
                <div className="panel-body export-foot">
                  <a className="btn btn-sm" href={fileUrl(`animations/${anim}/final/export.zip`, localBust || undefined)} download={`${anim}.zip`} data-testid="export-download-zip">
                    Download zip
                  </a>
                  {result?.copied_to ? (
                    <Button size="sm" variant="quiet" icon="folder" onClick={() => void reveal(result.copied_to!)} data-testid="export-reveal-copy">
                      Show game copy
                    </Button>
                  ) : null}
                  {lastDone?.result?.score !== undefined ? <span className="muted">From winner {lastDone.result.winner}, Q {q(lastDone.result.score)}</span> : null}
                </div>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
