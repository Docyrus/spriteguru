import type { AnimationSummary, CharacterRecord, EngineName, JobState, Level, Library, ProjectCard, ProjectInfo, StyleKind, SubjectKind } from './types';

export function usd(n: number | null | undefined, digits?: number): string {
  const v = typeof n === 'number' && Number.isFinite(n) ? n : 0;
  const d = digits ?? (v !== 0 && Math.abs(v) < 0.1 ? 3 : 2);
  return `$${v.toFixed(d)}`;
}

export function q(n: number | null | undefined): string {
  if (typeof n !== 'number') return '–';
  const r = Math.round(n * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

export const METRIC_LABEL: Record<string, string> = {
  frame_count: 'Frame count',
  layout_confidence: 'Grid detection',
  clipping: 'Clipped frame',
  extraneous_marks: 'Stray marks',
  background: 'Background',
  key_contamination: 'Key colour bleed',
  scale: 'Scale drift',
  baseline: 'Ground line',
  facing: 'Facing',
  identity: 'Character drift',
  pose: 'Pose match',
  order: 'Frame order',
  duplicates: 'Duplicate frames',
  gaps: 'Motion gap',
  loop_seam: 'Loop seam',
  gait: 'Gait',
  sharpness: 'Sharpness',
  video_compression: 'Video compression',
  pixel_fidelity: 'Pixel grid',
  semantics: 'Visual check',
  vector_rig: 'Vector rig',
  motion_spec: 'Motion spec',
  idle_motion: 'Idle motion',
  registration: 'Alignment',
  frames_touching: 'Touching frames',
};

export const metricLabel = (m: string) => METRIC_LABEL[m] ?? m.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

export const REMEDY_LABEL: Record<string, string> = {
  reroll: 'Re-roll the sheet',
  reroll_next_key: 'Re-roll with the next key colour',
  reroll_1080p: 'Re-roll at 1080p',
  frame_repair: 'Repair the frame',
  regenerate_frame: 'Regenerate the frame',
  identity_fix: 'Fix character drift',
  pose_fix: 'Fix the pose',
  confirm_layout: 'Confirm the grid',
  ml_matte: 'ML matte',
  inbetween: 'Insert an in-between',
  auto_fixed: 'Fixed automatically',
  pixel_refit: 'Refit the pixel grid',
  fix_round: 'Vector fix round',
  none: 'No action needed',
};

export const remedyLabel = (r: string) => REMEDY_LABEL[r] ?? r.replace(/_/g, ' ');

/** Button text for a one-frame remedy, e.g. "Fix the pose of frame 3". */
export function frameRemedyLabel(r: string, frame: number): string {
  const n = frame + 1;
  if (r === 'pose_fix') return `Fix the pose of frame ${n}`;
  if (r === 'identity_fix') return `Fix drift in frame ${n}`;
  return `Repair frame ${n}`;
}

/** Maps a finding remedy onto a one-click action the studio can run. */
export type RemedyAction =
  | { kind: 'repair'; repair: 'pose' | 'identity' | 'frame'; costKey: string }
  | { kind: 'reroll'; costKey: string }
  | { kind: 'layout'; costKey: string }
  | { kind: 'inbetween'; costKey: string }
  | { kind: 'none' };

export function remedyAction(remedy: string): RemedyAction {
  if (remedy === 'frame_repair' || remedy === 'regenerate_frame') return { kind: 'repair', repair: 'frame', costKey: 'frame_repair' };
  if (remedy === 'pose_fix') return { kind: 'repair', repair: 'pose', costKey: 'pose_fix' };
  if (remedy === 'identity_fix') return { kind: 'repair', repair: 'identity', costKey: 'identity_fix' };
  if (remedy.startsWith('reroll')) return { kind: 'reroll', costKey: 'reroll' };
  if (remedy === 'confirm_layout') return { kind: 'layout', costKey: 'confirm_layout' };
  if (remedy === 'inbetween') return { kind: 'inbetween', costKey: 'inbetween' };
  return { kind: 'none' };
}

export const LEVEL_LABEL: Record<Level, string> = { fail: 'Fail', warn: 'Warn', info: 'Info' };

export const ROUTE_LABEL: Record<string, string> = {
  guided: 'Guided sheet',
  'guided-pixel': 'Guided pixel sheet',
  'rd-loop': 'Pixel loop',
  'video-loop': 'Video loop',
  'vector-idle': 'Vector idle',
  'vector-motion': 'Vector motion',
  'vector-anim': 'Vector animation',
};

export const routeLabel = (r: string | null | undefined) => (r ? ROUTE_LABEL[r] ?? r : '–');

export const KIND_LABEL: Record<SubjectKind, string> = {
  character: 'Character',
  vehicle: 'Vehicle',
  machine: 'Machine',
  effect: 'Effect',
};

export const STYLE_LABEL: Record<StyleKind, string> = {
  pixel: 'Pixel',
  'hd-cartoon': 'HD cartoon',
  painted: 'Painted',
  vector: 'Vector',
};

export const ENGINE_LABEL: Record<EngineName, string> = {
  phaser: 'Phaser',
  pixi: 'PixiJS',
  godot: 'Godot 4',
  unity: 'Unity',
  gamemaker: 'GameMaker',
};

/** The picture that stands for a subject in thumbnails: its east side view, an effect's design, any view, the reference. */
export function thumbOf(rec: CharacterRecord): string | null {
  return rec.views['side-e'] ?? rec.views.key ?? rec.views.front ?? Object.values(rec.views)[0] ?? rec.source_image ?? null;
}

/** The gallery's cards, with the open project first when the library does not list it (the engine was
 * started with --project, which does not record it). Such a card has no id: it cannot be removed. */
export function cardsWithCurrent(library: Library | null, project: ProjectInfo | null, counts?: { characters: number; animations: number }): ProjectCard[] {
  const cards = library?.projects ?? [];
  if (!project || cards.some((c) => c.current || c.path === project.path)) return cards;
  const current: ProjectCard = {
    id: '',
    name: project.name,
    path: project.path,
    style: project.config.style.kind,
    engine: project.config.engine,
    characters: counts?.characters ?? 0,
    animations: counts?.animations ?? 0,
    thumbnail: null,
    updated: '',
    last_opened: null,
    missing: false,
    error: null,
    current: true,
  };
  return [current, ...cards];
}

export const kindLabel = (k: string | null | undefined) => (k ? KIND_LABEL[k as SubjectKind] ?? k : KIND_LABEL.character);

/** An additive effect (a glow drawn on black): the exported blend wins over the planned one. */
export function isAdditive(a: Pick<AnimationSummary, 'spec' | 'final'> | null | undefined): boolean {
  return (a?.final?.blend ?? a?.spec?.character?.blend) === 'add';
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  const gb = bytes / 1024 ** 3;
  return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1)} GB`;
}

export const JOB_STATE_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  awaiting_approval: 'Needs approval',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const isActive = (j: Pick<JobState, 'state'>) =>
  j.state === 'queued' || j.state === 'running' || j.state === 'awaiting_approval';

export function stepLabel(name: string): string {
  const m = /^([a-z]+)-(\d+)$/.exec(name);
  const base: Record<string, string> = {
    compile: 'Compile',
    generate: 'Generate',
    reroll: 'Re-roll',
    repair: 'Repair',
    inbetween: 'In-between',
    analyze: 'Analyze',
    decide: 'Decide',
    export: 'Export',
  };
  if (!m) return base[name] ?? name;
  return `${base[m[1]!] ?? m[1]} ${Number(m[2]) + 1}`;
}

export const DECISION_LABEL: Record<string, string> = {
  accept: 'Accepted',
  best_effort: 'Kept best effort',
  reroll: 'Re-roll',
  repair: 'Repair frame',
  inbetween: 'Insert in-between',
  fix_round: 'Fix round',
};

export function shortJob(id: string): string {
  const at = id.indexOf('@');
  if (at < 0) return id;
  const stamp = id.slice(at + 1);
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(-\d+)?$/.exec(stamp);
  if (!m) return stamp;
  return `${m[2]}/${m[3]} ${m[4]}:${m[5]}:${m[6]}${m[7] ?? ''}`;
}

export function timeAgo(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 2 * 86400) return 'yesterday';
  if (s < 30 * 86400) return `${Math.floor(s / 86400)} days ago`;
  return new Date(t).toLocaleDateString();
}

export function frameList(frames: number[]): string {
  if (!frames.length) return 'whole sheet';
  return frames.map((f) => f + 1).join(', ');
}

export function pad3(i: number) {
  return String(i).padStart(3, '0');
}
