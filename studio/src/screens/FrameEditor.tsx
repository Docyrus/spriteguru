import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { useEvents } from '../lib/events';
import { isAdditive, pad3, usd } from '../lib/format';
import { loadImage, useAsync, useElementSize } from '../lib/hooks';
import { useReport } from '../lib/reports';
import { href } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { AnimationSummary, Edit, FinalMeta, Finding, RemedyCosts } from '../lib/types';
import { AnimHeader, pickJob, useAnimJobs } from '../shell/AnimHeader';
import { Button, Empty, ErrorNote, Field, IconButton, Modal, Segmented, Spinner, Toggle } from '../ui/controls';
import { Icon } from '../ui/Icon';

interface Item {
  src: number;
  dx: number;
  dy: number;
  flip: boolean;
  ms: number;
}

interface EditState {
  items: Item[];
  pivot: [number, number];
  loop: boolean;
}

type Bg = 'checker' | 'light' | 'dark';

const PX_PER_MS = 0.9;
const MIN_CELL = 64;

function initialState(meta: FinalMeta): EditState {
  return {
    items: Array.from({ length: meta.frames }, (_, i) => ({ src: i, dx: 0, dy: 0, flip: false, ms: meta.durations[i] ?? 83 })),
    pivot: [meta.pivot[0], meta.pivot[1]],
    loop: meta.loop,
  };
}

/** Translate the edited frame list into the engine's sequential edit ops (edits.py). */
export function buildEdits(meta: FinalMeta, s: EditState): Edit[] {
  const out: Edit[] = [];
  for (const it of s.items) {
    // flip first, then nudge: the engine result shift(flip(f)) matches the on-canvas preview
    if (it.flip) out.push({ op: 'flip', frame: it.src });
    if (it.dx || it.dy) out.push({ op: 'nudge', frame: it.src, dx: it.dx, dy: it.dy });
    if (it.ms !== (meta.durations[it.src] ?? 83)) out.push({ op: 'duration', frame: it.src, ms: it.ms });
  }
  const present = new Set(s.items.map((i) => i.src));
  const all = Array.from({ length: meta.frames }, (_, i) => i);
  for (const d of all.filter((i) => !present.has(i)).sort((a, b) => b - a)) out.push({ op: 'delete', frame: d });
  const remaining = all.filter((i) => present.has(i));
  const order = s.items.map((it) => remaining.indexOf(it.src));
  if (order.some((v, i) => v !== i)) out.push({ op: 'reorder', order });
  if (Math.abs(s.pivot[0] - meta.pivot[0]) > 1e-6 || Math.abs(s.pivot[1] - meta.pivot[1]) > 1e-6) {
    out.push({ op: 'pivot', x: +s.pivot[0].toFixed(5), y: +s.pivot[1].toFixed(5) });
  }
  if (s.loop !== meta.loop) out.push({ op: 'loop', value: s.loop });
  return out;
}

function tinted(img: HTMLImageElement, color: string): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext('2d')!;
  ctx.drawImage(img, 0, 0);
  ctx.globalCompositeOperation = 'source-atop';
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, c.width, c.height);
  return c;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

export function FrameEditor({ anim }: { anim: string | null }) {
  const { toast, reloadAnimations } = useStore();
  const ev = useEvents();
  const exportedTs = anim ? ev.exported[anim] : undefined;
  const [localBust, setLocalBust] = useState(() => Date.now());
  const bust = `${exportedTs ?? 0}-${localBust}`;
  const animQ = useAsync<AnimationSummary>(() => api.animation(anim!), anim ? `${anim}|${bust}` : null);
  const meta = animQ.data?.final ?? null;
  // additive effects are glows drawn on black: dark checker ground, frames added like light
  const additive = isAdditive(animQ.data);
  const costs = useAsync<RemedyCosts>(() => api.remedyCosts(anim!), anim);

  // winner report for per-frame badges
  const jobs = useAnimJobs(anim);
  const doneJob = pickJob(jobs.filter((j) => j.state === 'done'), null);
  const winnerCand = doneJob?.candidates.find((c) => c.id === doneJob.winner) ?? null;
  const { report } = useReport(doneJob?.id ?? null, winnerCand);

  const [images, setImages] = useState<HTMLImageElement[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [state, setState] = useState<EditState | null>(null);
  const [past, setPast] = useState<EditState[]>([]);
  const [future, setFuture] = useState<EditState[]>([]);
  const [cur, setCur] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [onion, setOnion] = useState(true);
  const [showPivot, setShowPivot] = useState(true);
  const [bg, setBg] = useState<Bg>('checker');
  const [zoom, setZoom] = useState<'fit' | number>('fit');
  const [applying, setApplying] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [repairOpen, setRepairOpen] = useState(false);
  const [repairKind, setRepairKind] = useState<'pose' | 'identity' | 'frame'>('pose');
  const [repairNote, setRepairNote] = useState('');
  const [repairing, setRepairing] = useState(false);
  const [inserting, setInserting] = useState(false);
  const [themeTick, setThemeTick] = useState(0);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const [stageRef, stage] = useElementSize<HTMLDivElement>();
  const tints = useRef<Map<string, HTMLCanvasElement>>(new Map());
  const dragRef = useRef<null | { kind: 'pivot' | 'offset'; x0: number; y0: number; start: EditState }>(null);

  // load frames whenever the export changes
  useEffect(() => {
    if (!meta || !anim) return;
    let alive = true;
    setImages(null);
    setLoadErr(null);
    tints.current.clear();
    Promise.all(Array.from({ length: meta.frames }, (_, i) => loadImage(fileUrl(`animations/${anim}/final/frames/${pad3(i)}.png`, bust))))
      .then((imgs) => {
        if (!alive) return;
        setImages(imgs);
        setState(initialState(meta));
        setPast([]);
        setFuture([]);
        setCur((c) => Math.min(c, meta.frames - 1));
      })
      .catch((e) => alive && setLoadErr(errText(e)));
    return () => {
      alive = false;
    };
  }, [meta, anim, bust]);

  // redraw onion tints when the theme changes
  useEffect(() => {
    const mo = new MutationObserver(() => {
      tints.current.clear();
      setThemeTick((t) => t + 1);
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => mo.disconnect();
  }, []);

  // Handlers are recreated every render, so the closure `state` is current.
  const commit = (fn: (s: EditState) => EditState) => {
    if (!state) return;
    const next = fn(state);
    if (next === state) return;
    setPast((p) => [...p.slice(-99), state]);
    setFuture([]);
    setState(next);
  };
  const undo = () => {
    if (!past.length || !state) return;
    setFuture([state, ...future]);
    setState(past[past.length - 1]!);
    setPast(past.slice(0, -1));
  };
  const redo = () => {
    if (!future.length || !state) return;
    setPast([...past, state]);
    setState(future[0]!);
    setFuture(future.slice(1));
  };

  const items = state?.items ?? [];
  const n = items.length;
  const curItem = items[Math.min(cur, n - 1)];
  const w = meta?.size[0] ?? 1;
  const h = meta?.size[1] ?? 1;
  const edits = useMemo(() => (meta && state ? buildEdits(meta, state) : []), [meta, state]);

  const fitZoom = stage.w && stage.h ? Math.max(0.05, Math.min((stage.w - 32) / w, (stage.h - 32) / h)) : 0.5;
  let scale = zoom === 'fit' ? fitZoom : zoom;
  if (zoom === 'fit' && meta?.pixel && scale > 1) scale = Math.floor(scale);

  // ---- canvas drawing
  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv || !images || !state || !curItem) return;
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(w * scale);
    const ch = Math.round(h * scale);
    if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(ch * dpr)) {
      cv.width = Math.round(cw * dpr);
      cv.height = Math.round(ch * dpr);
      cv.style.width = `${cw}px`;
      cv.style.height = `${ch}px`;
    }
    const ctx = cv.getContext('2d')!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0);
    ctx.imageSmoothingEnabled = !(meta?.pixel ?? false);

    const drawItem = (it: Item, source: CanvasImageSource, alpha: number) => {
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, w, h);
      ctx.clip();
      ctx.globalAlpha = alpha;
      if (additive) ctx.globalCompositeOperation = 'lighter';
      ctx.translate(it.dx, it.dy);
      if (it.flip) {
        ctx.translate(w, 0);
        ctx.scale(-1, 1);
      }
      ctx.drawImage(source, 0, 0);
      ctx.restore();
    };
    const tint = (src: number, which: 'prev' | 'next') => {
      const k = `${src}-${which}`;
      let t = tints.current.get(k);
      if (!t) {
        t = tinted(images[src]!, cssVar(which === 'prev' ? '--red' : '--accent'));
        tints.current.set(k, t);
      }
      return t;
    };
    const idx = Math.min(cur, n - 1);
    if (onion && !playing && n > 1) {
      const prevI = idx > 0 ? idx - 1 : state.loop ? n - 1 : -1;
      const nextI = idx < n - 1 ? idx + 1 : state.loop ? 0 : -1;
      if (prevI >= 0 && prevI !== idx) drawItem(items[prevI]!, tint(items[prevI]!.src, 'prev'), 0.3);
      if (nextI >= 0 && nextI !== idx && nextI !== prevI) drawItem(items[nextI]!, tint(items[nextI]!.src, 'next'), 0.3);
    }
    drawItem(curItem, images[curItem.src]!, 1);

    if (showPivot) {
      const px = state.pivot[0] * w;
      const py = state.pivot[1] * h;
      const u = 1 / scale;
      ctx.save();
      ctx.lineWidth = u;
      ctx.strokeStyle = cssVar('--red');
      ctx.setLineDash([4 * u, 4 * u]);
      ctx.beginPath();
      ctx.moveTo(0, py);
      ctx.lineTo(w, py);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1.5 * u;
      ctx.beginPath();
      ctx.arc(px, py, 8 * u, 0, Math.PI * 2);
      ctx.moveTo(px - 14 * u, py);
      ctx.lineTo(px + 14 * u, py);
      ctx.moveTo(px, py - 14 * u);
      ctx.lineTo(px, py + 14 * u);
      ctx.stroke();
      ctx.restore();
    }
  }, [images, state, curItem, cur, n, items, w, h, scale, onion, playing, showPivot, meta?.pixel, additive]);

  useEffect(() => {
    draw();
  }, [draw, themeTick]);

  // ---- playback on per-frame durations
  const timeRef = useRef({ idx: 0, t: 0 });
  useEffect(() => {
    timeRef.current = { idx: cur, t: 0 };
  }, [cur, playing]);
  const offsets = useMemo(() => {
    const o: number[] = [];
    let acc = 0;
    for (const it of items) {
      o.push(acc);
      acc += Math.max(MIN_CELL, it.ms * PX_PER_MS);
    }
    return { starts: o, total: acc };
  }, [items]);

  useEffect(() => {
    const ph = playheadRef.current;
    if (ph && !playing && offsets.starts[cur] !== undefined) {
      ph.style.transform = `translateX(${offsets.starts[cur]}px)`;
    }
  }, [cur, playing, offsets]);

  useEffect(() => {
    if (!playing || !state || n === 0) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = now - last;
      last = now;
      const tr = timeRef.current;
      tr.t += dt;
      let changed = false;
      while (tr.t >= (state.items[tr.idx]?.ms ?? 83)) {
        tr.t -= state.items[tr.idx]?.ms ?? 83;
        if (tr.idx >= n - 1 && !state.loop) {
          tr.t = 0;
          setPlaying(false);
          return;
        }
        tr.idx = (tr.idx + 1) % n;
        changed = true;
      }
      if (changed) setCur(tr.idx);
      const ph = playheadRef.current;
      if (ph) {
        const it = state.items[tr.idx];
        const cellW = it ? Math.max(MIN_CELL, it.ms * PX_PER_MS) : 0;
        const x = (offsets.starts[tr.idx] ?? 0) + (it ? (tr.t / it.ms) * cellW : 0);
        ph.style.transform = `translateX(${x}px)`;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, state, n, offsets]);

  // ---- edit helpers
  const updateCur = (fn: (it: Item) => Item) =>
    commit((s) => ({ ...s, items: s.items.map((it, i) => (i === Math.min(cur, s.items.length - 1) ? fn(it) : it)) }));
  const nudge = (dx: number, dy: number) => updateCur((it) => ({ ...it, dx: it.dx + dx, dy: it.dy + dy }));
  const move = (from: number, to: number) => {
    if (from === to) return;
    commit((s) => {
      const arr = [...s.items];
      const [x] = arr.splice(from, 1);
      arr.splice(to, 0, x!);
      return { ...s, items: arr };
    });
    setCur(to);
  };
  const del = () => {
    if (n <= 2) {
      toast('An animation needs at least two frames.', 'error');
      return;
    }
    commit((s) => ({ ...s, items: s.items.filter((_, i) => i !== cur) }));
    setCur((c) => Math.max(0, Math.min(c, n - 2)));
  };

  // ---- keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (repairOpen || !state) return;
      const mod = e.metaKey || e.ctrlKey;
      const step = e.shiftKey ? 10 : 1;
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      switch (e.key) {
        case ' ':
          e.preventDefault();
          setPlaying((p) => !p);
          break;
        case ',':
        case '[':
          setPlaying(false);
          setCur((c) => (c - 1 + n) % n);
          break;
        case '.':
        case ']':
          setPlaying(false);
          setCur((c) => (c + 1) % n);
          break;
        case 'ArrowLeft':
          e.preventDefault();
          nudge(-step, 0);
          break;
        case 'ArrowRight':
          e.preventDefault();
          nudge(step, 0);
          break;
        case 'ArrowUp':
          e.preventDefault();
          nudge(0, -step);
          break;
        case 'ArrowDown':
          e.preventDefault();
          nudge(0, step);
          break;
        case 'f':
        case 'F':
          updateCur((it) => ({ ...it, flip: !it.flip }));
          break;
        case 'o':
        case 'O':
          setOnion((o) => !o);
          break;
        case 'Delete':
        case 'Backspace':
          e.preventDefault();
          del();
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // ---- pointer: drag the pivot, or drag the frame to offset it
  const toFrame = (e: React.PointerEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * w, y: ((e.clientY - r.top) / r.height) * h };
  };
  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!state) return;
    setPlaying(false);
    const p = toFrame(e);
    const px = state.pivot[0] * w;
    const py = state.pivot[1] * h;
    const near = showPivot && Math.hypot(p.x - px, p.y - py) * scale < 16;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { kind: near ? 'pivot' : 'offset', x0: p.x, y0: p.y, start: state };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const p = toFrame(e);
    if (d.kind === 'pivot') {
      const x = Math.max(0, Math.min(1, p.x / w));
      const y = Math.max(0, Math.min(1, p.y / h));
      setState((s) => (s ? { ...s, pivot: [x, y] } : s));
    } else {
      const dx = Math.round(p.x - d.x0);
      const dy = Math.round(p.y - d.y0);
      const idx = Math.min(cur, d.start.items.length - 1);
      setState((s) =>
        s ? { ...s, items: s.items.map((it, i) => (i === idx ? { ...it, dx: d.start.items[idx]!.dx + dx, dy: d.start.items[idx]!.dy + dy } : it)) } : s,
      );
    }
  };
  const onUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    if (d && state && state !== d.start) {
      setPast((p) => [...p.slice(-99), d.start]);
      setFuture([]);
    }
  };

  // ---- apply / repair
  const apply = async () => {
    if (!anim || !edits.length) return;
    setApplying(true);
    try {
      await api.edits(anim, edits);
      toast(`Applied ${edits.length} edit${edits.length === 1 ? '' : 's'} and re-exported.`, 'ok');
      setLocalBust(Date.now());
      void reloadAnimations();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setApplying(false);
    }
  };

  const repair = async () => {
    if (!anim || !curItem) return;
    setRepairing(true);
    try {
      const r = await api.repair(anim, curItem.src, repairKind, repairNote.trim() || undefined, doneJob?.id);
      toast(
        r.kept
          ? `Frame ${curItem.src + 1} regenerated: Q ${r.before} to ${r.after}.`
          : `Regenerated frame ${curItem.src + 1}, but it scored lower (Q ${r.after}), so the previous version stays.`,
        r.kept ? 'ok' : 'info',
      );
      setRepairOpen(false);
      setRepairNote('');
      setLocalBust(Date.now());
      void reloadAnimations();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setRepairing(false);
    }
  };

  const insertInbetween = async () => {
    if (!anim || !curItem) return;
    setInserting(true);
    try {
      const r = await api.inbetween(anim, curItem.src, doneJob?.id);
      toast(`Inserted an in-between after frame ${curItem.src + 1}: ${r.frames} frames now.`, 'ok');
      setCur(curItem.src + 1);
      setLocalBust(Date.now());
      void reloadAnimations();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setInserting(false);
    }
  };

  // per-frame findings badges (analysis frame indices match an unedited export)
  const badges = useMemo(() => {
    const m = new Map<number, Finding[]>();
    if (!report || !meta || report.frames.length !== meta.frames) return m;
    for (const f of report.findings) {
      if (f.auto_fixed || f.level === 'info') continue;
      for (const i of f.frames) m.set(i, [...(m.get(i) ?? []), f]);
    }
    return m;
  }, [report, meta]);

  const totalMs = items.reduce((a, b) => a + b.ms, 0);
  const regenCost = costs.data?.regenerate_frame ?? costs.data?.frame_repair;
  const ibCost = costs.data?.inbetween;

  return (
    <div className="screen fill" data-testid="frames-screen">
      <AnimHeader screen="frames" title="Frame editor" anim={anim}>
        {edits.length ? (
          <span className="badge badge-warn" data-testid="frames-pending-count">
            {edits.length} unsaved edit{edits.length === 1 ? '' : 's'}
          </span>
        ) : null}
        <IconButton icon="left" label="Undo (Cmd+Z)" disabled={!past.length} onClick={undo} data-testid="frames-undo" />
        <IconButton icon="right" label="Redo (Shift+Cmd+Z)" disabled={!future.length} onClick={redo} data-testid="frames-redo" />
        <Button
          size="sm"
          variant="ghost"
          disabled={!edits.length || applying}
          onClick={() => meta && (setState(initialState(meta)), setPast([]), setFuture([]))}
          data-testid="frames-discard"
        >
          Discard
        </Button>
        <Button size="sm" variant="primary" icon="check" disabled={!edits.length} busy={applying} onClick={() => void apply()} data-testid="frames-apply">
          Apply and re-export
        </Button>
      </AnimHeader>

      {animQ.error ? (
        <div className="screen-body">
          <ErrorNote testid="frames-error">{animQ.error}</ErrorNote>
        </div>
      ) : null}
      {animQ.data && !meta ? (
        <div className="screen-body">
          <Empty
            title="Nothing exported yet"
            testid="frames-no-export"
            action={
              <a className="btn btn-primary" href={href('builder')}>
                Open the builder
              </a>
            }
          >
            The frame editor works on the exported frame set. Finish a job for this animation first.
          </Empty>
        </div>
      ) : null}
      {loadErr ? (
        <div className="screen-body">
          <ErrorNote testid="frames-load-error">{loadErr}</ErrorNote>
        </div>
      ) : null}

      {meta && state && images ? (
        <div className="fe-layout">
          <div className="fe-stage-wrap">
            <div className="fe-toolbar" data-testid="frames-toolbar">
              <IconButton icon="prev" label="Previous frame (,)" onClick={() => (setPlaying(false), setCur((c) => (c - 1 + n) % n))} data-testid="frames-prev" />
              <IconButton
                icon={playing ? 'pause' : 'play'}
                label={playing ? 'Pause (Space)' : 'Play (Space)'}
                active={playing}
                onClick={() => setPlaying((p) => !p)}
                data-testid="frames-play"
              />
              <IconButton icon="next" label="Next frame (.)" onClick={() => (setPlaying(false), setCur((c) => (c + 1) % n))} data-testid="frames-next" />
              <span className="fe-counter num" data-testid="frames-current">
                {Math.min(cur, n - 1) + 1} / {n}
              </span>
              <span className="tb-sep" />
              <IconButton icon="onion" label="Onion skin (O)" active={onion} onClick={() => setOnion((o) => !o)} data-testid="frames-onion" />
              <IconButton icon="crosshair" label="Show pivot" active={showPivot} onClick={() => setShowPivot((p) => !p)} data-testid="frames-show-pivot" />
              <IconButton
                icon="loop"
                label={state.loop ? 'Loop on' : 'Loop off'}
                active={state.loop}
                onClick={() => commit((s) => ({ ...s, loop: !s.loop }))}
                data-testid="frames-loop"
              />
              <span className="tb-sep" />
              <Segmented<Bg>
                value={bg}
                onChange={setBg}
                testid="frames-bg"
                label="Background"
                options={[
                  { value: 'checker', label: 'Grid' },
                  { value: 'light', label: 'Light' },
                  { value: 'dark', label: 'Dark' },
                ]}
              />
              <span className="spacer" />
              <div className="zoom" role="group" aria-label="Zoom">
                <IconButton icon="minus" label="Zoom out" onClick={() => setZoom(Math.max(0.1, scale / 1.25))} data-testid="frames-zoom-out" />
                <button type="button" className="zoom-val num" onClick={() => setZoom('fit')} title="Fit" data-testid="frames-zoom-fit">
                  {zoom === 'fit' ? 'Fit' : `${Math.round(scale * 100)}%`}
                </button>
                <IconButton icon="plus" label="Zoom in" onClick={() => setZoom(Math.min(8, scale * 1.25))} data-testid="frames-zoom-in" />
                <Button size="sm" variant="ghost" onClick={() => setZoom(1)} data-testid="frames-zoom-100">
                  1:1
                </Button>
              </div>
            </div>
            <div className={`fe-stage bg-${bg}`} ref={stageRef} data-testid="frames-stage">
              <canvas
                ref={canvasRef}
                className={`fe-canvas${bg === 'checker' ? (additive ? ' additive' : ' checker') : ''}`}
                data-blend={additive ? 'add' : 'normal'}
                onPointerDown={onDown}
                onPointerMove={onPointerMove}
                onPointerUp={onUp}
                onPointerCancel={onUp}
                data-testid="frames-canvas"
                aria-label={`Frame ${cur + 1} of ${n}. Drag to offset the frame, drag the crosshair to move the pivot.`}
              />
            </div>
            <div className="fe-hint muted">
              Arrows nudge 1 px (Shift for 10). Drag the frame to move it, drag the crosshair to move the pivot. <kbd>,</kbd> <kbd>.</kbd> step,{' '}
              <kbd>Space</kbd> plays, <kbd>F</kbd> flips, <kbd>O</kbd> toggles onion skin.
            </div>
          </div>

          <aside className="fe-inspector" data-testid="frames-inspector">
            {curItem ? (
              <section className="insp-sec">
                <h3>
                  Frame {Math.min(cur, n - 1) + 1}
                  {curItem.src !== cur ? <span className="muted"> (was {curItem.src + 1})</span> : null}
                </h3>
                <div className="insp-grid">
                  <Field label="Offset x" htmlFor="fe-dx">
                    <input
                      id="fe-dx"
                      type="number"
                      value={curItem.dx}
                      onChange={(e) => updateCur((it) => ({ ...it, dx: Math.round(Number(e.target.value) || 0) }))}
                      data-testid="frames-offset-x"
                    />
                  </Field>
                  <Field label="Offset y" htmlFor="fe-dy">
                    <input
                      id="fe-dy"
                      type="number"
                      value={curItem.dy}
                      onChange={(e) => updateCur((it) => ({ ...it, dy: Math.round(Number(e.target.value) || 0) }))}
                      data-testid="frames-offset-y"
                    />
                  </Field>
                  <div className="dpad" role="group" aria-label="Nudge">
                    <IconButton icon="up" label="Nudge up" onClick={() => nudge(0, -1)} data-testid="frames-nudge-up" className="dp-up" />
                    <IconButton icon="left" label="Nudge left" onClick={() => nudge(-1, 0)} data-testid="frames-nudge-left" className="dp-left" />
                    <IconButton icon="right" label="Nudge right" onClick={() => nudge(1, 0)} data-testid="frames-nudge-right" className="dp-right" />
                    <IconButton icon="down" label="Nudge down" onClick={() => nudge(0, 1)} data-testid="frames-nudge-down" className="dp-down" />
                  </div>
                </div>
                <div className="insp-grid">
                  <Field label="Duration (ms)" htmlFor="fe-ms">
                    <input
                      id="fe-ms"
                      type="number"
                      min={10}
                      step={1}
                      value={curItem.ms}
                      onChange={(e) => updateCur((it) => ({ ...it, ms: Math.max(10, Math.round(Number(e.target.value) || 10)) }))}
                      data-testid="frames-duration"
                    />
                  </Field>
                  <Button
                    size="sm"
                    variant="quiet"
                    onClick={() => commit((s) => ({ ...s, items: s.items.map((it) => ({ ...it, ms: curItem.ms })) }))}
                    data-testid="frames-duration-all"
                  >
                    Use for all
                  </Button>
                </div>
                <div className="insp-row">
                  <Button size="sm" variant={curItem.flip ? 'primary' : 'quiet'} icon="flip" onClick={() => updateCur((it) => ({ ...it, flip: !it.flip }))} data-testid="frames-flip">
                    {curItem.flip ? 'Flipped' : 'Flip'}
                  </Button>
                  <IconButton icon="left" label="Move earlier" disabled={cur === 0} onClick={() => move(cur, cur - 1)} data-testid="frames-move-left" />
                  <IconButton icon="right" label="Move later" disabled={cur >= n - 1} onClick={() => move(cur, cur + 1)} data-testid="frames-move-right" />
                  <Button size="sm" variant="danger" icon="trash" disabled={n <= 2} onClick={del} data-testid="frames-delete">
                    Delete
                  </Button>
                </div>
                {badges.get(curItem.src)?.length ? (
                  <ul className="insp-findings" data-testid="frames-frame-findings">
                    {badges.get(curItem.src)!.map((f, i) => (
                      <li key={i} className={`lv-${f.level}`}>
                        {f.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <Button size="sm" icon="sparkle" onClick={() => setRepairOpen(true)} data-testid="frames-regenerate">
                  Regenerate frame{typeof regenCost === 'number' ? ` (${usd(regenCost)})` : ''}
                </Button>
                <Button
                  size="sm"
                  icon="inbetween"
                  busy={inserting}
                  disabled={inserting || applying}
                  title="Generates a new frame between this one and the next, then re-exports. Edits you have not applied are lost."
                  onClick={() => void insertInbetween()}
                  data-testid="frames-inbetween"
                >
                  Insert in-between after this frame{typeof ibCost === 'number' ? ` (${usd(ibCost)})` : ''}
                </Button>
              </section>
            ) : null}

            <section className="insp-sec">
              <h3>Animation</h3>
              <div className="insp-grid">
                <Field label="Pivot x" htmlFor="fe-px" hint={`${Math.round(state.pivot[0] * w)} px`}>
                  <input
                    id="fe-px"
                    type="number"
                    step={0.01}
                    min={0}
                    max={1}
                    value={+state.pivot[0].toFixed(3)}
                    onChange={(e) => commit((s) => ({ ...s, pivot: [Math.max(0, Math.min(1, Number(e.target.value) || 0)), s.pivot[1]] }))}
                    data-testid="frames-pivot-x"
                  />
                </Field>
                <Field label="Pivot y" htmlFor="fe-py" hint={`${Math.round(state.pivot[1] * h)} px`}>
                  <input
                    id="fe-py"
                    type="number"
                    step={0.01}
                    min={0}
                    max={1}
                    value={+state.pivot[1].toFixed(3)}
                    onChange={(e) => commit((s) => ({ ...s, pivot: [s.pivot[0], Math.max(0, Math.min(1, Number(e.target.value) || 0))] }))}
                    data-testid="frames-pivot-y"
                  />
                </Field>
              </div>
              <Toggle checked={state.loop} onChange={(v) => commit((s) => ({ ...s, loop: v }))} label="Loop" testid="frames-loop-toggle" />
              <dl className="kv small">
                <dt>Frame size</dt>
                <dd className="num">
                  {w} × {h} px
                </dd>
                <dt>Length</dt>
                <dd className="num" data-testid="frames-total-ms">
                  {totalMs} ms
                </dd>
                <dt>Base rate</dt>
                <dd className="num">{meta.fps} fps</dd>
                {additive ? (
                  <>
                    <dt>Blend</dt>
                    <dd data-testid="frames-blend">Additive</dd>
                  </>
                ) : null}
              </dl>
            </section>
          </aside>

          <div className="fe-timeline" data-testid="frames-timeline">
            <div className="tl-scroll">
              <div className="tl-inner" style={{ width: offsets.total + 2 }}>
                <div className="tl-ruler" aria-hidden="true">
                  {(() => {
                    const marks: React.ReactNode[] = [];
                    let acc = 0;
                    items.forEach((it, i) => {
                      marks.push(
                        <span key={i} className="tl-tick" style={{ left: offsets.starts[i] }}>
                          {acc} ms
                        </span>,
                      );
                      acc += it.ms;
                    });
                    return marks;
                  })()}
                </div>
                <ol className="tl-cells" onDragOver={(e) => e.preventDefault()}>
                  {items.map((it, i) => {
                    const cellW = Math.max(MIN_CELL, it.ms * PX_PER_MS);
                    const bs = badges.get(it.src) ?? [];
                    const fails = bs.filter((f) => f.level === 'fail').length;
                    const warns = bs.filter((f) => f.level === 'warn').length;
                    return (
                      <li
                        key={`${it.src}`}
                        className={`tl-cell${i === cur ? ' is-cur' : ''}${dropAt === i && dragFrom !== null && dragFrom !== i ? ' is-drop' : ''}`}
                        style={{ width: cellW }}
                        draggable
                        onDragStart={(e) => {
                          setDragFrom(i);
                          e.dataTransfer.effectAllowed = 'move';
                          e.dataTransfer.setData('text/plain', String(i));
                        }}
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDropAt(i);
                        }}
                        onDragEnd={() => {
                          setDragFrom(null);
                          setDropAt(null);
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          const from = dragFrom ?? Number(e.dataTransfer.getData('text/plain'));
                          if (Number.isFinite(from)) move(from, i);
                          setDragFrom(null);
                          setDropAt(null);
                        }}
                        onClick={() => (setPlaying(false), setCur(i))}
                        data-testid="frames-timeline-cell"
                        data-index={i}
                        data-src={it.src}
                        aria-current={i === cur ? 'true' : undefined}
                        title={`Frame ${i + 1}, ${it.ms} ms. Drag to reorder.`}
                      >
                        <div className={`tl-thumb ${additive ? 'additive' : 'checker'}`}>
                          <img
                            src={fileUrl(`animations/${anim}/final/frames/${pad3(it.src)}.png`, bust)}
                            alt=""
                            draggable={false}
                            style={{ transform: it.flip ? 'scaleX(-1)' : undefined }}
                          />
                          {fails || warns ? (
                            <span className={`tl-badge ${fails ? 'lv-fail' : 'lv-warn'}`} data-testid="frames-timeline-badge" title={bs.map((f) => f.message).join('\n')}>
                              {fails + warns}
                            </span>
                          ) : null}
                        </div>
                        <div className="tl-meta">
                          <span className="tl-num num">{i + 1}</span>
                          <span className="tl-ms num">{it.ms}</span>
                          {it.flip ? <Icon name="flip" size={12} title="Flipped" /> : null}
                          {it.dx || it.dy ? <span className="tl-off num" title="Offset">{`${it.dx},${it.dy}`}</span> : null}
                        </div>
                      </li>
                    );
                  })}
                </ol>
                <div className="tl-playhead" ref={playheadRef} aria-hidden="true" />
              </div>
            </div>
          </div>
        </div>
      ) : meta && !loadErr ? (
        <div className="screen-body">
          <Spinner label="Loading frames…" />
        </div>
      ) : null}

      {repairOpen && curItem ? (
        <Modal
          title={`Regenerate frame ${curItem.src + 1}`}
          testid="frames-repair-modal"
          onClose={() => !repairing && setRepairOpen(false)}
          actions={
            <>
              <Button variant="ghost" onClick={() => setRepairOpen(false)} disabled={repairing} data-testid="frames-repair-cancel">
                Cancel
              </Button>
              <Button variant="primary" icon="sparkle" busy={repairing} onClick={() => void repair()} data-testid="frames-repair-submit">
                Regenerate{typeof regenCost === 'number' ? ` (${usd(regenCost)})` : ''}
              </Button>
            </>
          }
        >
          <p className="modal-lede">
            Redraws this frame of the winning sheet with a masked edit, re-scores it and keeps it only if Q does not drop. The export is rebuilt from
            the sheet, so edits you have not applied are lost.
          </p>
          <Field label="What is wrong">
            <Segmented
              value={repairKind}
              onChange={setRepairKind}
              testid="frames-repair-kind"
              label="Repair kind"
              options={[
                { value: 'pose', label: 'Pose' },
                { value: 'identity', label: 'Character drift' },
                { value: 'frame', label: 'Other' },
              ]}
            />
          </Field>
          <Field label="Note for the model (optional)" htmlFor="fe-note">
            <input
              id="fe-note"
              type="text"
              value={repairNote}
              onChange={(e) => setRepairNote(e.target.value)}
              placeholder="the sword should be in the right hand"
              data-testid="frames-repair-note"
            />
          </Field>
        </Modal>
      ) : null}
    </div>
  );
}
