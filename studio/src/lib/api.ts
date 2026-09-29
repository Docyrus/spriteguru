// HTTP transport to the local engine. The per-launch token arrives once in the URL query,
// moves to sessionStorage, and is stripped from the address bar.

import type {
  ActionInfo,
  AnimationSummary,
  CharacterIn,
  CharacterRecord,
  Edit,
  Estimate,
  ExportResult,
  FindingLabel,
  InbetweenResult,
  JobState,
  KeyStatus,
  LedgerEntry,
  Library,
  LedgerSummary,
  ProjectCard,
  ProjectConfig,
  ProjectIn,
  ProjectInfo,
  RemedyCosts,
  RepairResult,
  Report,
  Candidate,
  SubjectKind,
} from './types';

const TOKEN_KEY = 'spriteguru.token';

function captureToken(): string {
  let token = '';
  try {
    const url = new URL(window.location.href);
    const fromUrl = url.searchParams.get('token');
    if (fromUrl) {
      token = fromUrl;
      try {
        sessionStorage.setItem(TOKEN_KEY, fromUrl);
      } catch {
        /* storage blocked: keep the token in memory */
      }
      url.searchParams.delete('token');
      const clean = url.pathname + (url.searchParams.toString() ? `?${url.searchParams}` : '') + url.hash;
      window.history.replaceState(window.history.state, '', clean);
    } else {
      token = sessionStorage.getItem(TOKEN_KEY) ?? '';
    }
  } catch {
    /* ignore */
  }
  return token;
}

export const token = captureToken();

/** The engine's 409 detail when a project-scoped request arrives with no project open. */
export const NO_PROJECT = 'no project open';

/** Called when any request finds that no project is open. */
export const noProjectListeners = new Set<() => void>();

export class ApiError extends Error {
  status: number;
  /** The machine-readable reason supplied by an engine error. */
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function codeOf(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'error' in body) {
    const e = (body as { error: unknown }).error;
    if (e && typeof e === 'object' && 'code' in e) return String((e as { code: unknown }).code);
  }
  return undefined;
}

export const isNoProject = (e: unknown) => e instanceof ApiError && e.status === 409 && e.message === NO_PROJECT;

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      // pydantic validation errors (422): "sol_effort: Input should be 'none', 'low', …"
      return d
        .map((x) => {
          if (!x || typeof x !== 'object' || !('msg' in x)) return String(x);
          const e = x as { msg: unknown; loc?: unknown };
          const loc = Array.isArray(e.loc) ? e.loc.filter((l) => l !== 'body').join('.') : '';
          return loc ? `${loc}: ${String(e.msg)}` : String(e.msg);
        })
        .join('; ');
    }
  }
  return fallback;
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-SpriteGuru-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'The engine is not responding. Check that SpriteGuru is still running.');
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401) throw new ApiError(401, 'The studio token is missing or expired. Reopen the studio from SpriteGuru.');
    const detail = detailOf(data, `${method} ${path} failed with ${res.status}`);
    if (res.status === 409 && detail === NO_PROJECT) noProjectListeners.forEach((l) => l());
    throw new ApiError(res.status, detail, codeOf(data));
  }
  return data as T;
}

const enc = (s: string) => encodeURIComponent(s);
const encPath = (rel: string) => rel.split('/').map(encodeURIComponent).join('/');

/** URL for a project-relative file served by the engine; `bust` defeats image caches after re-exports. */
export function fileUrl(rel: string | null | undefined, bust?: string | number): string {
  if (!rel) return '';
  const base = rel.startsWith('/api/') ? rel : `/api/files/${encPath(rel)}`;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}token=${enc(token)}${bust !== undefined && bust !== '' ? `&v=${enc(String(bust))}` : ''}`;
}

export function eventsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/api/events?token=${enc(token)}`;
}

export const api = {
  /** 409 "no project open" when the engine has no project; see `isNoProject`. */
  project: () => request<ProjectInfo>('GET', '/api/project'),
  setActiveCharacter: (name: string | null) =>
    request<{ active_character: string | null }>('PUT', '/api/project/active-character', { name }),

  library: () => request<Library>('GET', '/api/library'),
  /** Creates `<location>/<slug>.sprites`, opens it and returns the new project with its card. */
  createProject: (body: ProjectIn) => request<{ project: ProjectInfo; card: ProjectCard }>('POST', '/api/library/projects', body),
  openProject: (target: { id: string } | { path: string }) => request<ProjectInfo>('POST', '/api/library/open', target),
  closeProject: () => request<{ ok: boolean }>('POST', '/api/library/close'),
  /** Removes the card from the gallery; no files are deleted. */
  removeProject: (id: string) => request<{ ok: boolean }>('DELETE', `/api/library/projects/${enc(id)}`),

  patchSettings: (body: Partial<Record<string, unknown>>) => request<ProjectConfig>('PATCH', '/api/settings', body),
  putKey: (provider: string, value: string) => request<KeyStatus>('PUT', `/api/keys/${enc(provider)}`, { value }),
  deleteKey: (provider: string) => request<KeyStatus>('DELETE', `/api/keys/${enc(provider)}`),
  downloadEmbeddings: () =>
    request<{ ok: boolean; present: boolean; backend: string }>('POST', '/api/models/embeddings/download'),

  characters: () => request<CharacterRecord[]>('GET', '/api/characters'),
  character: (name: string) => request<CharacterRecord>('GET', `/api/characters/${enc(name)}`),
  /** Starts the turnaround (or, with `use_image_as_view`, the image-as-view cut) in the background; its
   * progress arrives as turnaround_started / turnaround_done / turnaround_failed events. */
  createCharacter: (body: CharacterIn) => request<CharacterRecord>('POST', '/api/characters', body),
  /** Replaces the reference image (data URL or base64); regenerate the turnaround afterwards to use it. */
  replaceCharacterImage: (name: string, image: string) =>
    request<CharacterRecord>('PUT', `/api/characters/${enc(name)}/image`, { image }),
  regenerateTurnaround: (name: string, n: number, seed: number) =>
    request<{ started: boolean }>('POST', `/api/characters/${enc(name)}/turnaround`, { n, seed }),
  chooseTurnaround: (name: string, index: number) =>
    request<CharacterRecord>('POST', `/api/characters/${enc(name)}/choose`, { index }),
  approveCharacter: (name: string) => request<CharacterRecord>('POST', `/api/characters/${enc(name)}/approve`),

  /** Actions of one subject kind; the engine defaults to character actions. */
  actions: (kind?: SubjectKind) => request<ActionInfo[]>('GET', kind ? `/api/actions?kind=${enc(kind)}` : '/api/actions'),
  animations: () => request<AnimationSummary[]>('GET', '/api/animations'),
  animation: (id: string) => request<AnimationSummary>('GET', `/api/animations/${enc(id)}`),
  createAnimation: (body: {
    character: string;
    action: string;
    facing: string;
    frames: number;
    loop: boolean;
    view: string;
    motion: string;
  }) => request<AnimationSummary>('POST', '/api/animations', body),
  estimate: (id: string) => request<Estimate>('GET', `/api/animations/${enc(id)}/estimate`),
  startJob: (id: string, seed: number) => request<JobState>('POST', `/api/animations/${enc(id)}/jobs`, { seed }),
  edits: (id: string, edits: Edit[]) => request<ExportResult>('POST', `/api/animations/${enc(id)}/edits`, { edits }),
  exportAnim: (id: string, engine?: string) =>
    request<ExportResult>('POST', `/api/animations/${enc(id)}/export`, engine ? { engine } : {}),
  repair: (id: string, frame: number, kind: 'pose' | 'identity' | 'frame', note?: string, job?: string | null) =>
    request<RepairResult>('POST', `/api/animations/${enc(id)}/repair`, { frame, kind, note: note || null, job: job || null }),
  inbetween: (id: string, after: number, job?: string | null) =>
    request<InbetweenResult>('POST', `/api/animations/${enc(id)}/inbetween`, { after, job: job || null }),
  labelFinding: (job: string, candidate: string, index: number, correct: boolean) =>
    request<FindingLabel>('POST', '/api/findings/label', { job, candidate, index, correct }),
  /** Labels written so far (labels/findings.jsonl in the project), oldest first; empty when none. */
  findingLabels: async (): Promise<FindingLabel[]> => {
    const res = await fetch(fileUrl('labels/findings.jsonl', Date.now()));
    if (!res.ok) return [];
    const out: FindingLabel[] = [];
    for (const line of (await res.text()).split('\n')) {
      if (!line.trim()) continue;
      try {
        out.push(JSON.parse(line) as FindingLabel);
      } catch {
        /* skip a torn line */
      }
    }
    return out;
  },
  remedyCosts: (id: string) => request<RemedyCosts>('GET', `/api/animations/${enc(id)}/remedy-costs`),

  jobs: () => request<JobState[]>('GET', '/api/jobs'),
  job: (id: string) => request<JobState>('GET', `/api/jobs/${enc(id)}`),
  report: (jobId: string, cid: string) =>
    request<Report>('GET', `/api/jobs/${enc(jobId)}/candidates/${enc(cid)}/report`),
  cancelJob: (id: string) => request<{ cancelled: boolean }>('POST', `/api/jobs/${enc(id)}/cancel`),
  approveJob: (id: string, ok: boolean) => request<{ delivered: boolean }>('POST', `/api/jobs/${enc(id)}/approve`, { ok }),
  chooseWinner: (id: string, candidate: string) =>
    request<unknown>('POST', `/api/jobs/${enc(id)}/winner`, { candidate }),
  confirmLayout: (id: string, candidate: string, cells: number[][]) =>
    request<Candidate>('POST', `/api/jobs/${enc(id)}/layout`, { candidate, cells }),

  ledger: (limit = 200) => request<{ summary: LedgerSummary; entries: LedgerEntry[] }>('GET', `/api/ledger?limit=${limit}`),
};
