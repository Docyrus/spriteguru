import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, fileUrl } from '../lib/api';
import { q } from '../lib/format';
import { loadImage, useAsync, useElementSize } from '../lib/hooks';
import { useReport } from '../lib/reports';
import { href, navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { Box, Candidate, JobState } from '../lib/types';
import { AnimHeader, pickJob, useAdditive, useAnimJobs } from '../shell/AnimHeader';
import { Button, Empty, ErrorNote, IconButton, QScore, Spinner, Toggle } from '../ui/controls';

type Edge = 'l' | 't' | 'r' | 'b';
type Drag =
  | { kind: 'move'; idx: number; x0: number; y0: number; orig: Box[] }
  | { kind: 'edge'; idx: number; edge: Edge; x0: number; y0: number; orig: Box[]; linked: [number, Edge][] }
  | null;

const EDGE_I: Record<Edge, number> = { l: 0, t: 1, r: 2, b: 3 };
const MIN = 8;

/** Edges of other cells on the same cut line as (idx, edge): in a grid, a whole column or row boundary.
 * Detected regions hug each frame, so a column's edges scatter by a few pixels; the tolerance is ~1.2%
 * of the sheet along that axis (never under 3 px). */
function linkedEdges(cells: Box[], idx: number, edge: Edge, W: number, H: number): [number, Edge][] {
  const v = cells[idx]![EDGE_I[edge]];
  const vertical = edge === 'l' || edge === 'r';
  const axis: Edge[] = vertical ? ['l', 'r'] : ['t', 'b'];
  const tol = Math.max(3, 0.012 * (vertical ? W : H));
  const out: [number, Edge][] = [];
  cells.forEach((o, j) => {
    if (j === idx) return;
    for (const e of axis) {
      if (Math.abs(o[EDGE_I[e]] - v) <= tol) out.push([j, e]);
    }
  });
  return out;
}

function clampBox(b: Box, W: number, H: number): Box {
  let [x0, y0, x1, y1] = b.map((v) => Math.round(v)) as Box;
  x0 = Math.max(0, Math.min(W - MIN, x0));
  y0 = Math.max(0, Math.min(H - MIN, y0));
  x1 = Math.max(x0 + MIN, Math.min(W, x1));
  y1 = Math.max(y0 + MIN, Math.min(H, y1));
  return [x0, y0, x1, y1];
}

function RemovedOverlay({ src }: { src: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let alive = true;
    loadImage(src)
      .then((img) => {
        const cv = ref.current;
        if (!alive || !cv) return;
        cv.width = img.naturalWidth;
        cv.height = img.naturalHeight;
        const ctx = cv.getContext('2d');
        if (!ctx) return;
        ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, cv.width, cv.height);
        const px = data.data;
        for (let i = 0; i < px.length; i += 4) {
          const l = px[i]!;
          px[i] = 240;
          px[i + 1] = 60;
          px[i + 2] = 200;
          px[i + 3] = l > 0 ? 220 : 0;
        }
        ctx.putImageData(data, 0, 0);
      })
      .catch(() => alive && setErr(true));
    return () => {
      alive = false;
    };
  }, [src]);
  if (err) return null;
  return <canvas ref={ref} className="sheet-layer" data-testid="sheet-removed-overlay" />;
}

export function SheetReview({ anim, job: wanted, cand: wantedCand }: { anim: string | null; job: string | null; cand: string | null }) {
  const { toast, reloadAnimations, reloadJobs } = useStore();
  const jobs = useAnimJobs(anim);
  const picked = pickJob(jobs, wanted);
  const jobId = picked?.id ?? wanted ?? null;
  const job = useAsync<JobState>(() => api.job(jobId!), jobId);
  const st = job.data;
  const storeAdditive = useAdditive(anim);
  const sheetCands = (st?.candidates ?? []).filter((c) => c.sheet && c.report);
  const cand: Candidate | null =
    sheetCands.find((c) => c.id === wantedCand) ?? sheetCands.find((c) => c.id === st?.winner) ?? sheetCands[0] ?? null;
  const { report, error: reportError } = useReport(st?.id ?? null, cand);
  // an additive effect's sheet is drawn on black: show it (and its matte) on a dark ground, screen-blended
  const additive = storeAdditive || report?.spec?.character?.blend === 'add';

  const detected = useMemo<Box[]>(() => {
    if (!report) return [];
    const lf = report.layout?.frames;
    if (lf && lf.length) return lf.map((f) => [...f.region] as Box);
    return report.frames.map((f) => [...f.region] as Box);
  }, [report]);
  const boxes = useMemo<Box[]>(() => {
    if (!report) return [];
    const lf = report.layout?.frames;
    return lf && lf.length ? lf.map((f) => f.box) : report.frames.map((f) => f.box);
  }, [report]);

  const [cells, setCells] = useState<Box[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag>(null);
  const [showMatte, setShowMatte] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [showAnchors, setShowAnchors] = useState(true);
  const [showBoxes, setShowBoxes] = useState(true);
  const [link, setLink] = useState(true);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState<'fit' | number>('fit');
  const [confirming, setConfirming] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [stageRef, stage] = useElementSize<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    setCells(detected);
    setSel(null);
  }, [detected]);
  useEffect(() => {
    setNatural(null);
  }, [cand?.sheet]);

  const W = natural?.w ?? report?.canvas?.[0] ?? 1;
  const H = natural?.h ?? 1;
  const fit = natural && stage.w ? Math.min((stage.w - 24) / W, Math.max(240, stage.h - 24) / H) : 0.3;
  const scale = zoom === 'fit' ? fit : zoom;
  const dirty = JSON.stringify(cells) !== JSON.stringify(detected);

  const toSheet = useCallback(
    (clientX: number, clientY: number) => {
      const r = svgRef.current?.getBoundingClientRect();
      if (!r) return { x: 0, y: 0 };
      return { x: ((clientX - r.left) / r.width) * W, y: ((clientY - r.top) / r.height) * H };
    },
    [W, H],
  );

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = toSheet(e.clientX, e.clientY);
    const dx = p.x - drag.x0;
    const dy = p.y - drag.y0;
    const next = drag.orig.map((b) => [...b] as Box);
    if (drag.kind === 'move') {
      const b = drag.orig[drag.idx]!;
      const w = b[2] - b[0];
      const h = b[3] - b[1];
      const x0 = Math.max(0, Math.min(W - w, b[0] + dx));
      const y0 = Math.max(0, Math.min(H - h, b[1] + dy));
      next[drag.idx] = [Math.round(x0), Math.round(y0), Math.round(x0 + w), Math.round(y0 + h)];
    } else {
      const vertical = drag.edge === 'l' || drag.edge === 'r';
      const d = vertical ? dx : dy;
      const targets: [number, Edge][] = [[drag.idx, drag.edge], ...drag.linked];
      for (const [j, e] of targets) {
        const b = next[j]!;
        b[EDGE_I[e]] = drag.orig[j]![EDGE_I[e]] + d;
        next[j] = clampBox(b, W, H);
      }
    }
    setCells(next);
  };

  const startMove = (e: React.PointerEvent, idx: number) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    const p = toSheet(e.clientX, e.clientY);
    setSel(idx);
    setDrag({ kind: 'move', idx, x0: p.x, y0: p.y, orig: cells.map((b) => [...b] as Box) });
  };
  const startEdge = (e: React.PointerEvent, idx: number, edge: Edge) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    const p = toSheet(e.clientX, e.clientY);
    setSel(idx);
    setDrag({
      kind: 'edge',
      idx,
      edge,
      x0: p.x,
      y0: p.y,
      orig: cells.map((b) => [...b] as Box),
      linked: link ? linkedEdges(cells, idx, edge, W, H) : [],
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (sel === null) return;
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        setCells((c) => c.filter((_, i) => i !== sel));
        setSel(null);
      }
      const step = e.shiftKey ? 10 : 1;
      const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
      const mv = d[e.key];
      if (mv) {
        e.preventDefault();
        setCells((c) =>
          c.map((b, i) => {
            if (i !== sel) return b;
            // translate without resizing: stop at the sheet edge
            const dx = Math.max(-b[0], Math.min(W - b[2], mv[0]));
            const dy = Math.max(-b[1], Math.min(H - b[3], mv[1]));
            return [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy] as Box;
          }),
        );
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sel, W, H]);

  const addCell = () => {
    const last = cells[cells.length - 1];
    const w = last ? last[2] - last[0] : Math.round(W / 4);
    const h = last ? last[3] - last[1] : Math.round(H / 2);
    const b = clampBox([Math.round(W / 2 - w / 2), Math.round(H / 2 - h / 2), Math.round(W / 2 + w / 2), Math.round(H / 2 + h / 2)], W, H);
    setCells((c) => [...c, b]);
    setSel(cells.length);
  };

  const confirm = async () => {
    if (!st || !cand) return;
    setConfirming(true);
    try {
      const entry = await api.confirmLayout(st.id, cand.id, cells);
      toast(`Layout confirmed and exported as ${entry.id} (Q ${q(entry.score)}).`, 'ok');
      void reloadJobs();
      void reloadAnimations();
      // show the confirmed candidate (its cells are your cells), so leaving and coming back keeps them
      if (entry.id === wantedCand) void job.reload();
      else navigate('sheet', anim, { job: jobId, cand: entry.id });
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setConfirming(false);
    }
  };

  const useShown = async () => {
    if (!st || !cand) return;
    setChoosing(true);
    try {
      await api.chooseWinner(st.id, cand.id);
      toast(`Using ${cand.id}; the export was rewritten.`, 'ok');
      void reloadAnimations();
      void job.reload();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setChoosing(false);
    }
  };

  const hw = 1 / Math.max(scale, 0.01); // one screen pixel in sheet units
  const src = showMatte && cand?.matte ? fileUrl(cand.matte) : fileUrl(cand?.sheet);

  return (
    <div className="screen fill" data-testid="sheet-screen">
      <AnimHeader screen="sheet" title="Sheet review" anim={anim} job={jobId} showJob>
        {sheetCands.length > 1 ? (
          <label className="context-field">
            <span>Candidate</span>
            <select
              value={cand?.id ?? ''}
              data-testid="sheet-candidate-select"
              onChange={(e) => navigate('sheet', anim, { job: jobId, cand: e.target.value })}
            >
              {sheetCands.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id}
                  {c.id === st?.winner ? ' (winner)' : ''} Q {q(c.score)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </AnimHeader>

      {!jobId && jobs.length === 0 ? (
        <div className="screen-body">
          <Empty title="No sheet to review" testid="sheet-empty">
            Generate a job first; guided sheets show their detected grid here.
          </Empty>
        </div>
      ) : null}
      {st && !cand ? (
        <div className="screen-body">
          <Empty
            title="This job has no sheet"
            testid="sheet-no-sheet"
            action={
              <a className="btn" href={href('candidates', anim, { job: st.id })} data-testid="sheet-back-candidates">
                Back to candidates
              </a>
            }
          >
            Its frames came straight from a video or a vector render, so there is no grid to confirm.
          </Empty>
        </div>
      ) : null}
      {reportError && cand ? (
        <div className="screen-body">
          <ErrorNote testid="sheet-error">{reportError}</ErrorNote>
        </div>
      ) : null}

      {cand && report ? (
        <div className="sheet-layout">
          <div className="sheet-toolbar" data-testid="sheet-toolbar">
            <Toggle checked={showMatte} onChange={setShowMatte} label="Matte" testid="sheet-toggle-matte" disabled={!cand.matte} hint={!cand.matte ? 'not saved for this candidate' : undefined} />
            <Toggle
              checked={showRemoved}
              onChange={setShowRemoved}
              label="Removed marks"
              testid="sheet-toggle-removed"
              disabled={!cand.removed}
              hint={!cand.removed ? 'none recorded' : undefined}
            />
            <Toggle checked={showAnchors} onChange={setShowAnchors} label="Anchors" testid="sheet-toggle-anchors" />
            <Toggle checked={showBoxes} onChange={setShowBoxes} label="Sprite boxes" testid="sheet-toggle-boxes" />
            <Toggle checked={link} onChange={setLink} label="Move shared cuts together" testid="sheet-toggle-link" />
            <span className="spacer" />
            <div className="zoom" role="group" aria-label="Zoom">
              <IconButton icon="minus" label="Zoom out" data-testid="sheet-zoom-out" onClick={() => setZoom(Math.max(0.05, scale / 1.25))} />
              <button type="button" className="zoom-val num" onClick={() => setZoom('fit')} data-testid="sheet-zoom-fit" title="Fit to window">
                {zoom === 'fit' ? 'Fit' : `${Math.round(scale * 100)}%`}
              </button>
              <IconButton icon="plus" label="Zoom in" data-testid="sheet-zoom-in" onClick={() => setZoom(Math.min(4, scale * 1.25))} />
            </div>
          </div>

          <div className="sheet-stage" ref={stageRef} data-testid="sheet-stage">
            <div
              className={`sheet-canvas${additive ? ' additive' : showMatte ? ' checker' : ''}`}
              data-blend={additive ? 'add' : 'normal'}
              style={{ width: natural ? W * scale : undefined, height: natural ? H * scale : undefined }}
            >
              <img
                src={src}
                alt={`Sheet of candidate ${cand.id}`}
                className="sheet-img"
                data-testid="sheet-image"
                draggable={false}
                onLoad={(e) => {
                  const im = e.currentTarget;
                  if (!natural || natural.w !== im.naturalWidth) setNatural({ w: im.naturalWidth, h: im.naturalHeight });
                }}
              />
              {showRemoved && cand.removed ? <RemovedOverlay src={fileUrl(cand.removed)} /> : null}
              {natural ? (
                <svg
                  ref={svgRef}
                  className="sheet-overlay"
                  viewBox={`0 0 ${W} ${H}`}
                  onPointerMove={onMove}
                  onPointerUp={() => setDrag(null)}
                  onPointerCancel={() => setDrag(null)}
                  onPointerDown={() => setSel(null)}
                  data-testid="sheet-overlay"
                >
                  {showBoxes
                    ? boxes.map((b, i) => (
                        <rect
                          key={`box-${i}`}
                          x={b[0]}
                          y={b[1]}
                          width={b[2] - b[0]}
                          height={b[3] - b[1]}
                          className="ov-box"
                          vectorEffect="non-scaling-stroke"
                          data-testid="sheet-box"
                        />
                      ))
                    : null}
                  {cells.map((b, i) => (
                    <g key={`cell-${i}`} className={`ov-cell${sel === i ? ' is-sel' : ''}`} data-testid="sheet-cell" data-index={i}>
                      <rect
                        x={b[0]}
                        y={b[1]}
                        width={b[2] - b[0]}
                        height={b[3] - b[1]}
                        className="ov-cell-body"
                        vectorEffect="non-scaling-stroke"
                        onPointerDown={(e) => startMove(e, i)}
                      />
                      <text x={b[0] + 8 * hw} y={b[1] + 18 * hw} fontSize={13 * hw} className="ov-label">
                        {i + 1}
                      </text>
                      {(['l', 'r', 't', 'b'] as Edge[]).map((edge) => {
                        const [x1, y1, x2, y2] =
                          edge === 'l' ? [b[0], b[1], b[0], b[3]] : edge === 'r' ? [b[2], b[1], b[2], b[3]] : edge === 't' ? [b[0], b[1], b[2], b[1]] : [b[0], b[3], b[2], b[3]];
                        return (
                          <line
                            key={edge}
                            x1={x1}
                            y1={y1}
                            x2={x2}
                            y2={y2}
                            className={`ov-edge ov-edge-${edge === 'l' || edge === 'r' ? 'v' : 'h'}`}
                            vectorEffect="non-scaling-stroke"
                            onPointerDown={(e) => startEdge(e, i, edge)}
                            data-testid={`sheet-cell-edge-${edge}`}
                          />
                        );
                      })}
                    </g>
                  ))}
                  {showAnchors
                    ? report.frames.map((f, i) => (
                        <g key={`a-${i}`} className="ov-anchor" data-testid="sheet-anchor" transform={`translate(${f.anchor[0]} ${f.anchor[1]})`}>
                          <circle r={7 * hw} vectorEffect="non-scaling-stroke" />
                          <line x1={-12 * hw} x2={12 * hw} y1={0} y2={0} vectorEffect="non-scaling-stroke" />
                          <line y1={-12 * hw} y2={12 * hw} x1={0} x2={0} vectorEffect="non-scaling-stroke" />
                        </g>
                      ))
                    : null}
                </svg>
              ) : null}
            </div>
            {!natural ? <Spinner label="Loading sheet…" /> : null}
          </div>

          <aside className="sheet-side" data-testid="sheet-side">
            <div className="sheet-side-score">
              <QScore score={cand.score} accepted={cand.accepted} size="md" />
              <div>
                <div>
                  Candidate <strong>{cand.id}</strong>
                </div>
                <div className="muted">
                  {report.layout?.hypothesis === 'confirmed'
                    ? 'Cut along your confirmed cells'
                    : report.layout?.hypothesis
                      ? `Detected by ${report.layout.hypothesis}`
                      : 'Detected layout'}
                  {typeof report.layout?.confidence === 'number' ? `, confidence ${(report.layout.confidence * 100).toFixed(0)}%` : ''}
                </div>
              </div>
            </div>
            <dl className="kv small">
              <dt>Cells</dt>
              <dd className="num" data-testid="sheet-cell-count">
                {cells.length}
                {report.spec ? ` of ${report.spec.frames} expected` : ''}
              </dd>
              <dt>Sheet</dt>
              <dd className="num">
                {W} × {H} px
              </dd>
              {typeof report.layout?.labels_removed === 'number' ? (
                <>
                  <dt>Marks removed</dt>
                  <dd className="num">{report.layout.labels_removed + (report.layout.strays ?? 0)}</dd>
                </>
              ) : null}
              {sel !== null && cells[sel] ? (
                <>
                  <dt>Cell {sel + 1}</dt>
                  <dd className="num" data-testid="sheet-selected-cell">
                    {cells[sel]!.join(', ')}
                  </dd>
                </>
              ) : null}
            </dl>
            <p className="muted sheet-help">
              Drag a cell to move it or drag its edges to recut. With shared cuts on, neighbours follow. Arrow keys nudge the selected cell;
              Delete removes it. Cells are read in order, so frame 1 comes first.
            </p>
            <div className="sheet-actions">
              <Button size="sm" variant="quiet" icon="plus" onClick={addCell} data-testid="sheet-add-cell">
                Add cell
              </Button>
              <Button
                size="sm"
                variant="quiet"
                icon="trash"
                disabled={sel === null}
                onClick={() => {
                  if (sel === null) return;
                  setCells((c) => c.filter((_, i) => i !== sel));
                  setSel(null);
                }}
                data-testid="sheet-remove-cell"
              >
                Remove cell
              </Button>
              <Button size="sm" variant="ghost" icon="refresh" disabled={!dirty} onClick={() => setCells(detected)} data-testid="sheet-reset">
                Reset
              </Button>
            </div>
            <Button variant="primary" icon="check" busy={confirming} disabled={cells.length < 2} onClick={() => void confirm()} data-testid="sheet-confirm-layout">
              Confirm layout
            </Button>
            <p className="muted small-print">
              Re-cuts the frames exactly along your cells as a new candidate, uses it for the export and saves it as a labelled example. Free.
            </p>
            {cand && st && cand.id !== st.winner ? (
              <div className="confirm-result" data-testid="sheet-confirm-result">
                <div>
                  Showing <strong>{cand.id}</strong>
                  {cand.kind === 'layout' ? ' (your confirmed layout)' : ''}; the export uses{' '}
                  <strong>{st.winner ?? 'none yet'}</strong>.
                </div>
                <QScore score={cand.score} accepted={cand.accepted} size="sm" />
                <Button size="sm" variant="primary" busy={choosing} onClick={() => void useShown()} data-testid="sheet-use-confirmed">
                  Use this one
                </Button>
                <a className="btn btn-sm btn-quiet" href={href('candidates', anim, { job: jobId })}>
                  Compare
                </a>
              </div>
            ) : null}
          </aside>
        </div>
      ) : cand && !reportError ? (
        <div className="screen-body">
          <Spinner label="Loading the analysis…" />
        </div>
      ) : null}
    </div>
  );
}
