// Mirrors of the engine's pydantic models (src/spriteguru/spec.py, jobs.py) and API payloads (api.py).

export type StyleKind = 'pixel' | 'hd-cartoon' | 'painted' | 'vector';
export type EngineName = 'phaser' | 'pixi' | 'godot' | 'unity' | 'gamemaker';
export type Facing = 'E' | 'W' | 'N' | 'S';
export type View = 'side' | 'top-down';
export type Motion = 'in-place' | 'root-motion';
export type Level = 'fail' | 'warn' | 'info';
export type SubjectKind = 'character' | 'vehicle' | 'machine' | 'effect';
export const SUBJECT_KINDS: SubjectKind[] = ['character', 'vehicle', 'machine', 'effect'];
/** "add": an effect drawn on black and composited additively (a glow); "normal": ordinary alpha. */
export type Blend = 'normal' | 'add';
export type Effort = 'none' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export const EFFORTS: Effort[] = ['none', 'low', 'medium', 'high', 'xhigh', 'max'];

export const ENGINES: EngineName[] = ['phaser', 'pixi', 'godot', 'unity', 'gamemaker'];

export interface Style {
  kind: StyleKind;
  pixel_height: number | null;
  palette_size: number;
  outline: 'dark' | 'selective' | 'none';
}

export interface SpriteSpec {
  character: {
    description: string;
    refs: string[];
    palette: string[];
    heads_tall: number | null;
    mirrorable: boolean;
    kind?: SubjectKind; // absent in reports written before subject kinds existed
    blend?: Blend;
  };
  view: View;
  facing: Facing;
  action: string;
  frames: number;
  loop: boolean;
  motion: Motion;
  style: Style;
  key: string | null;
  fps: number;
  engine: EngineName;
}

export interface Settings {
  session_cap_usd: number;
  job_cap_usd: number;
  sol_effort: Effort;
  luna_effort: Effort;
  provider_mode: 'live' | 'synthetic';
  judge_enabled: boolean;
  judge_disabled?: string[]; // judge issue types switched off by `spriteguru eval judge`
  image_models?: Record<string, string>; // image task -> model; unset tasks use the default
}

/** A model the user can pick for an image task (Settings > Image models). */
export interface ImageModelInfo {
  id: string;
  label: string;
  maker: string;
  provider: string;
  mask: boolean;
  price: string;
}

export interface ImageTaskInfo {
  task: string;
  label: string;
  default: string;
  model: string;
}

export interface ProjectConfig {
  name: string;
  style: Style;
  engine: EngineName;
  fps: number;
  asset_folder: string | null;
  settings: Settings;
}

export interface KeyStatus {
  configured: boolean;
  source: string | null;
  env: string;
}

export interface LedgerSummary {
  total_usd: number;
  calls: number;
  cache_hits: number;
  by_model: Record<string, number>;
  session_usd: number;
  unknown_outcomes: number;
}

export interface ProjectInfo {
  name: string;
  root: string;
  path: string; // absolute project folder
  active_character: string | null; // null when unset or the character no longer exists
  config: ProjectConfig;
  keys: Record<string, KeyStatus>;
  mode: 'live' | 'synthetic' | string;
  models: {
    embeddings: { model: string; path: string; present: boolean; backend: string };
    matte: { available: boolean; model: string };
  };
  roles: Record<string, string>;
  image_models: { tasks: ImageTaskInfo[]; models: ImageModelInfo[] };
  ledger: LedgerSummary;
  version: string;
}

/** One project in the library gallery (GET /api/library). */
export interface ProjectCard {
  id: string;
  name: string;
  path: string;
  style: StyleKind | null; // null when the folder is missing or project.json is unreadable
  engine: EngineName | null;
  characters: number;
  animations: number;
  thumbnail: string | null; // /api/library/projects/{id}/thumbnail
  updated: string; // ISO
  last_opened: string | null; // ISO
  missing: boolean; // the folder is gone; cannot be opened
  error: string | null; // project.json is unreadable
  current: boolean;
}

export interface Library {
  root: string; // the library folder, where new projects go by default
  current: string | null; // the open project's path
  projects: ProjectCard[]; // most recently opened first, then most recently updated
}

/** POST /api/library/projects. */
export interface ProjectIn {
  name: string;
  style: { kind: StyleKind; pixel_height?: number; palette_size?: number };
  engine: EngineName;
  fps?: number;
  location?: string; // absolute parent folder; the library root when omitted
}

export interface CharacterRecord {
  name: string;
  description: string;
  style: Style;
  mirrorable: boolean;
  kind: SubjectKind;
  blend: Blend;
  source_image: string | null; // uploaded reference image, project-relative
  description_source: 'text' | 'image';
  facing_in_source: 'left' | 'right' | 'front' | 'unknown';
  palette: string[];
  height_px: number | null;
  heads_tall: number | null;
  views: Record<string, string>; // front, side-e, side-w, back; effects: key; image-as-view: side-e (+ side-w)
  turnaround: string | null;
  turnaround_index: number; // which turnaround candidate the views were cropped from
  view_warnings: string[]; // e.g. views that touched on the turnaround and may be clipped
  svg: string | null;
  approved: boolean;
  turnaround_candidates: string[];
}

/** POST /api/characters (CharacterIn). */
export interface CharacterIn {
  name: string;
  description: string; // may be empty when an image is given: the engine describes the image
  mirrorable: boolean;
  style?: Style;
  candidates: number;
  seed?: number;
  kind: SubjectKind;
  blend?: Blend;
  image?: string; // data URL or bare base64; PNG, JPEG, WebP or GIF (first frame), up to 20 MB
  use_image_as_view?: boolean; // skip the turnaround: the image becomes the side view (needs `image`)
}

export interface ActionInfo {
  action: string;
  kind: SubjectKind;
  frames: number;
  loop: boolean;
  title: string; // e.g. "a walk cycle"
}

export interface TopFinding {
  metric: string;
  level: Level;
  message: string;
  frames: number[];
  remedy: string;
}

export interface Candidate {
  id: string;
  round: number;
  kind: string; // generate, reroll, repair, inbetween, layout
  parent: string | null;
  key: string | null;
  key_exclude?: string[];
  sheet?: string;
  source_frames?: string;
  svg?: string;
  video?: string;
  meta?: string;
  score?: number;
  accepted?: boolean;
  frames?: string;
  report?: string;
  html?: string;
  matte?: string;
  removed?: string;
  top?: TopFinding[];
  chosen?: boolean;
}

export interface JobStep {
  name: string;
  status: 'running' | 'done' | string;
  started?: string;
  finished?: string;
  info?: Record<string, unknown>;
}

export interface Decision {
  action: string;
  frames: number[];
  reason: string;
  finding: Finding | null;
  key_exclude: string[];
  candidate: string;
  score: number;
}

export interface JobResult {
  winner?: string;
  score?: number;
  accepted?: boolean;
  final?: string;
  files?: string[];
  copied_to?: string | null;
  spend?: number;
}

export type JobStateName = 'queued' | 'running' | 'awaiting_approval' | 'done' | 'failed' | 'cancelled';

export interface Approval {
  model: string;
  op: string;
  estimate: number;
  reason: string;
  session_spend: number;
  job_spend: number;
}

export interface JobState {
  id: string;
  kind: string;
  anim_id: string | null;
  character: string | null;
  state: JobStateName;
  step: string | null;
  steps: JobStep[];
  route: string | null;
  key: string | null;
  candidates: Candidate[];
  winner: string | null;
  attempts: Record<string, number>;
  decisions: Decision[];
  spend: number;
  error: string | null;
  dir: string;
  created: string;
  updated: string;
  result: JobResult;
  approval: Approval | null;
  seed: number;
}

export interface FinalMeta {
  name: string;
  action: string;
  frames: number;
  size: [number, number];
  pivot: [number, number];
  durations: number[];
  loop: boolean;
  fps: number;
  engine: EngineName;
  rects: number[][];
  padding: number;
  extrude: number;
  pixel: boolean;
  root_motion: unknown;
  blend?: Blend; // "add" for additive effects; absent in exports made before blending existed
}

export interface AnimationSummary {
  id: string;
  character: string | null;
  spec: SpriteSpec;
  route: string;
  final: FinalMeta | null;
  latest_job: JobState | null;
  jobs: string[];
}

export interface CostLine {
  model: string;
  what: string;
  usd: number;
}

export interface Estimate {
  anim_id: string;
  compiled: {
    route: string;
    key: string | null;
    key_distance?: number;
    facing_generated?: string;
    flip_output?: boolean;
    plan: null | { frames: number; cols: number; rows: number; cell_w: number; cell_h: number; width: number; height: number; strip?: boolean };
    choreo?: string;
    notes: string[];
    prompt?: unknown;
  };
  estimate_usd: number;
  lines: CostLine[];
  spec: SpriteSpec;
  guide_url?: string;
}

export interface Finding {
  metric: string;
  level: Level;
  severity: number;
  frames: number[];
  value: number | null;
  threshold: number | null;
  message: string;
  remedy: string;
  auto_fixed: boolean;
  source: 'cv' | 'judge';
  corroborated: boolean;
  issue: string | null;
}

export type Box = [number, number, number, number];

export interface FrameInfo {
  index: number;
  box: Box;
  region: Box;
  anchor: [number, number];
  offset: [number, number];
  airborne: boolean;
  clipped: boolean;
  scale: number;
  flipped: boolean;
  duration_ms: number;
  source: string | null;
}

export interface Report {
  spec: SpriteSpec | null;
  source: string | null;
  score: number;
  accepted: boolean;
  findings: Finding[];
  frames: FrameInfo[];
  canvas: [number, number];
  pivot: [number, number];
  layout: {
    hypothesis?: string;
    confidence?: number;
    frames?: { index: number; region: Box; box: Box; clipped?: boolean; touching?: boolean }[];
    labels_removed?: number;
    strays?: number;
    [k: string]: unknown;
  };
  matte: Record<string, unknown>;
  temporal: Record<string, unknown>;
  pixel: Record<string, unknown>;
  order: number[];
  timings_ms: Record<string, number>;
}

export type RemedyCosts = Record<string, number>;

export interface ExportResult {
  dir: string;
  files: string[];
  bundle: string;
  copied_to: string | null;
  meta: FinalMeta;
}

export interface RepairResult {
  job: string;
  candidate: string;
  before: number;
  after: number;
  kept: boolean;
  export: ExportResult;
}

export interface InbetweenResult {
  job: string;
  candidate: string;
  frames: number;
  score: number | null;
  export: ExportResult;
}

export interface FindingLabel {
  job: string;
  candidate: string;
  index: number;
  metric: string;
  source: string;
  issue: string | null;
  correct: boolean;
}

export interface LedgerEntry {
  call_id?: string;
  job?: string | null;
  model?: string;
  op?: string;
  provider?: string;
  purpose?: string;
  status?: string;
  cost?: number;
  est_cost?: number;
  latency_ms?: number;
  time?: number;
  [k: string]: unknown;
}

export interface EngineEvent {
  type: string;
  ts: number;
  job?: string;
  anim?: string;
  character?: string;
  step?: string;
  candidate?: string;
  score?: number;
  accepted?: boolean;
  cost?: number;
  model?: string;
  error?: string;
  action?: string;
  reason?: string;
  [k: string]: unknown;
}

export type Edit =
  | { op: 'nudge'; frame: number; dx: number; dy: number }
  | { op: 'flip'; frame: number }
  | { op: 'duration'; frame: number; ms: number }
  | { op: 'pivot'; x: number; y: number }
  | { op: 'reorder'; order: number[] }
  | { op: 'delete'; frame: number }
  | { op: 'loop'; value: boolean };
