// Shared studio data: the library, the open project with its characters, animations and jobs, and
// the active character every tab follows. Refreshed from engine events; reset on project switches.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, isNoProject, noProjectListeners, token } from './api';
import { useEngineEvent } from './events';
import { useDebounced } from './hooks';
import { clearReportCache } from './reports';
import type { ActionInfo, AnimationSummary, CharacterRecord, JobState, Library, ProjectCard, ProjectIn, ProjectInfo, SubjectKind } from './types';

export interface Toast {
  id: number;
  kind: 'info' | 'ok' | 'error';
  text: string;
}

interface Store {
  project: ProjectInfo | null;
  projectAt: number; // engine-clock seconds when the ledger summary was read
  projectError: string | null; // the engine is unreachable (not: no project open)
  noProject: boolean; // the engine answered, and no project is open
  library: Library | null;
  libraryError: string | null;
  characters: CharacterRecord[] | null;
  animations: AnimationSummary[] | null;
  jobs: JobState[] | null;
  /** The character every tab follows; may name a record that has not loaded yet. */
  activeCharacter: string | null;
  setActiveCharacter: (name: string | null) => Promise<boolean>;
  openProject: (target: { id: string } | { path: string }) => Promise<ProjectInfo>;
  createProject: (body: ProjectIn) => Promise<{ project: ProjectInfo; card: ProjectCard }>;
  currentAnim: string | null;
  setCurrentAnim: (id: string | null) => void;
  reloadProject: () => Promise<void>;
  reloadLibrary: () => Promise<void>;
  reloadCharacters: () => Promise<void>;
  reloadAnimations: () => Promise<void>;
  reloadJobs: () => Promise<void>;
  toasts: Toast[];
  toast: (text: string, kind?: Toast['kind']) => void;
  dismissToast: (id: number) => void;
}

const Ctx = createContext<Store | null>(null);
const ANIM_KEY = 'spriteguru.currentAnim';
export const BUILDER_JOB_KEY = 'spriteguru.builderJob';

function readAnim(): string | null {
  try {
    return localStorage.getItem(ANIM_KEY);
  } catch {
    return null;
  }
}

let toastSeq = 0;

const actionsCache = new Map<SubjectKind, Promise<ActionInfo[]>>();

/** The actions of one subject kind (GET /api/actions?kind=), fetched once per kind; failures retry. */
export function fetchActions(kind: SubjectKind): Promise<ActionInfo[]> {
  let p = actionsCache.get(kind);
  if (!p) {
    p = api.actions(kind);
    p.catch(() => actionsCache.delete(kind));
    actionsCache.set(kind, p);
  }
  return p;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [projectAt, setProjectAt] = useState(0);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [noProject, setNoProject] = useState(false);
  const [library, setLibrary] = useState<Library | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [characters, setCharacters] = useState<CharacterRecord[] | null>(null);
  const [animations, setAnimations] = useState<AnimationSummary[] | null>(null);
  const [jobs, setJobs] = useState<JobState[] | null>(null);
  const [activeCharacter, setActiveState] = useState<string | null>(null);
  const [currentAnim, setCurrentAnimState] = useState<string | null>(readAnim);
  const [toasts, setToasts] = useState<Toast[]>([]);

  // the folder whose characters, animations and jobs are loaded; null with no project open
  const pathRef = useRef<string | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeCharacter;
  const activePuts = useRef(0);

  const toast = useCallback((text: string, kind: Toast['kind'] = 'info') => {
    const id = ++toastSeq;
    setToasts((t) => [...t.slice(-3), { id, kind, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 9000 : 5000);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const setCurrentAnim = useCallback((id: string | null) => {
    setCurrentAnimState(id);
    try {
      if (id) localStorage.setItem(ANIM_KEY, id);
      else localStorage.removeItem(ANIM_KEY);
    } catch {
      /* per-viewer convenience only */
    }
  }, []);

  const reloadCharacters = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      setCharacters(await api.characters());
    } catch (e) {
      if (!isNoProject(e)) toast(e instanceof Error ? e.message : String(e), 'error');
    }
  }, [toast]);
  const reloadAnimations = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      setAnimations(await api.animations());
    } catch (e) {
      if (!isNoProject(e)) toast(e instanceof Error ? e.message : String(e), 'error');
    }
  }, [toast]);
  const reloadJobs = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      setJobs(await api.jobs());
    } catch {
      /* the jobs indicator is best effort */
    }
  }, []);
  const reloadLibrary = useCallback(async () => {
    try {
      setLibrary(await api.library());
      setLibraryError(null);
    } catch (e) {
      setLibraryError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  /** Adopts the engine's open project (or none). A different folder drops every per-project cache. */
  const applyProject = useCallback(
    (p: ProjectInfo | null) => {
      const path = p ? p.path ?? p.root : null;
      const prev = pathRef.current;
      pathRef.current = path;
      setProject(p);
      setNoProject(!p);
      if (activePuts.current === 0) setActiveState(p?.active_character ?? null);
      if (path === prev) return;
      actionsCache.clear();
      clearReportCache();
      setCharacters(null);
      setAnimations(null);
      setJobs(null);
      if (prev !== null) {
        // a switch, not the first load: the last animation and builder job belong to the old project
        setCurrentAnim(null);
        try {
          sessionStorage.removeItem(BUILDER_JOB_KEY);
        } catch {
          /* ignore */
        }
      }
      if (p) {
        void reloadCharacters();
        void reloadAnimations();
        void reloadJobs();
        fetchActions('character').catch(() => undefined); // warm the builder's default list
      }
    },
    [reloadCharacters, reloadAnimations, reloadJobs, setCurrentAnim],
  );

  const reloadProject = useCallback(async () => {
    try {
      const at = Date.now() / 1000;
      const p = await api.project();
      setProjectAt(at);
      setProjectError(null);
      applyProject(p);
    } catch (e) {
      if (isNoProject(e)) {
        setProjectError(null);
        applyProject(null);
      } else {
        setProjectError(e instanceof Error ? e.message : String(e));
      }
    }
  }, [applyProject]);

  const setActiveCharacter = useCallback(
    async (name: string | null): Promise<boolean> => {
      const prev = activeRef.current;
      setActiveState(name);
      activePuts.current += 1;
      try {
        const r = await api.setActiveCharacter(name);
        setActiveState(r.active_character);
        return true;
      } catch (e) {
        setActiveState(prev);
        toast(e instanceof Error ? e.message : String(e), 'error');
        return false;
      } finally {
        activePuts.current -= 1;
      }
    },
    [toast],
  );

  const openProject = useCallback(
    async (target: { id: string } | { path: string }) => {
      const p = await api.openProject(target);
      setProjectAt(Date.now() / 1000);
      setProjectError(null);
      applyProject(p);
      void reloadLibrary();
      return p;
    },
    [applyProject, reloadLibrary],
  );
  const createProject = useCallback(
    async (body: ProjectIn) => {
      const r = await api.createProject(body);
      setProjectAt(Date.now() / 1000);
      setProjectError(null);
      applyProject(r.project);
      void reloadLibrary();
      return r;
    },
    [applyProject, reloadLibrary],
  );

  useEffect(() => {
    if (!token) return;
    void reloadProject();
    void reloadLibrary();
  }, [reloadProject, reloadLibrary]);

  // Any request that meets "no project open" re-reads the project, which sends the studio home.
  useEffect(() => {
    const l = () => {
      if (pathRef.current) void reloadProject();
    };
    noProjectListeners.add(l);
    return () => {
      noProjectListeners.delete(l);
    };
  }, [reloadProject]);

  const jobsSoon = useDebounced(() => void reloadJobs(), 300);
  const animsSoon = useDebounced(() => void reloadAnimations(), 400);
  const charsSoon = useDebounced(() => void reloadCharacters(), 300);
  const projectSoon = useDebounced(() => void reloadProject(), 1500);

  useEngineEvent((e) => {
    switch (e.type) {
      case 'project_changed':
        // another window (or this one) opened, created or closed a project
        void reloadProject();
        void reloadLibrary();
        break;
      case 'active_character':
        setActiveState(typeof e.name === 'string' ? e.name : null);
        break;
      case 'job_created':
      case 'step_started':
      case 'step_done':
      case 'decision':
      case 'approval_required':
      case 'candidate_scored':
        jobsSoon();
        break;
      case 'job_done':
      case 'job_failed':
      case 'job_cancelled':
        jobsSoon();
        animsSoon();
        projectSoon();
        break;
      case 'exported':
        animsSoon();
        break;
      case 'turnaround_started':
      case 'turnaround_done':
      case 'turnaround_failed':
        charsSoon();
        if (e.type !== 'turnaround_started') projectSoon();
        break;
      case 'spend':
        projectSoon();
        break;
      case 'project_files_changed':
        // a pull brought files from another machine (C30): reload what they feed
        charsSoon();
        animsSoon();
        projectSoon();
        void reloadLibrary();
        break;
    }
  });

  const value = useMemo<Store>(
    () => ({
      project,
      projectAt,
      projectError,
      noProject,
      library,
      libraryError,
      characters,
      animations,
      jobs,
      activeCharacter,
      setActiveCharacter,
      openProject,
      createProject,
      currentAnim,
      setCurrentAnim,
      reloadProject,
      reloadLibrary,
      reloadCharacters,
      reloadAnimations,
      reloadJobs,
      toasts,
      toast,
      dismissToast,
    }),
    [project, projectAt, projectError, noProject, library, libraryError, characters, animations, jobs, activeCharacter,
      setActiveCharacter, openProject, createProject, currentAnim, setCurrentAnim, reloadProject, reloadLibrary,
      reloadCharacters, reloadAnimations, reloadJobs, toasts, toast, dismissToast],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error('useStore outside StoreProvider');
  return s;
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The active character's record, once characters have loaded. */
export function useActiveRecord(): CharacterRecord | null {
  const { characters, activeCharacter } = useStore();
  return (characters ?? []).find((c) => c.name === activeCharacter) ?? null;
}

/** Animations of one character, most recently worked on first. */
export function animationsOf(animations: AnimationSummary[] | null, character: string | null): AnimationSummary[] {
  if (!character) return [];
  const stamp = (a: AnimationSummary) => a.latest_job?.updated ?? a.latest_job?.created ?? '';
  return (animations ?? []).filter((a) => a.character === character).sort((a, b) => stamp(b).localeCompare(stamp(a)));
}

/** The animation a tab shows for a character: the last one opened if it is theirs, else their newest. */
export function pickAnimation(animations: AnimationSummary[] | null, character: string | null, current: string | null): AnimationSummary | null {
  const mine = animationsOf(animations, character);
  return mine.find((a) => a.id === current) ?? mine[0] ?? null;
}
