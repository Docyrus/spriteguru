// The SpritePlay account (cloud plan 4 and 5): signed in or not, the plan, and the access that gates
// generation. The engine holds the tokens and talks to spriteplay.com; the studio reads this status
// and opens website pages (sign-in, billing, machines) in the browser through the host adapter.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { host } from '../host';
import { api, token, type SyncChoice } from './api';
import { useEngineEvent } from './events';
import { errText, useStore } from './store';
import type { CloudStatus, SyncStatus } from './types';

interface Cloud {
  /** The open project's sync state; null with no project open or before it loads. */
  sync: SyncStatus | null;
  reloadSync: () => Promise<void>;
  syncNow: () => Promise<void>;
  link: (owner: string) => Promise<boolean>;
  unlink: () => Promise<void>;
  resolve: (choices: SyncChoice[]) => Promise<void>;
  answerCopied: (keep: boolean) => Promise<void>;
  setAutoSync: (on: boolean) => Promise<void>;
  status: CloudStatus | null;
  error: string | null;
  busy: boolean;
  reload: (refresh?: boolean) => Promise<void>;
  /** Starts a browser sign-in and opens the page. */
  signIn: () => Promise<void>;
  /** Opens the pending sign-in's page again (the tab was closed, C1). */
  reopenSignIn: () => void;
  cancelSignIn: () => Promise<void>;
  signOut: () => Promise<void>;
  /** Asks the server again now: after buying in the browser (C36). */
  checkAgain: () => Promise<void>;
  renameMachine: (name: string) => Promise<boolean>;
  /** Opens a spriteplay.com page (`/app/personal/billing`, `/app/devices`, …) in the browser. */
  openSite: (path: string) => void;
}

const Ctx = createContext<Cloud | null>(null);
const POLL_MS = 60_000;

export function CloudProvider({ children }: { children: ReactNode }) {
  const { toast, project } = useStore();
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const projectPath = project?.path ?? null;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const statusRef = useRef<CloudStatus | null>(null);
  statusRef.current = status;

  const reload = useCallback(async (refresh = false) => {
    try {
      setStatus(await api.cloudStatus(refresh));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  const openSite = useCallback(
    (path: string) => {
      const base = statusRef.current?.cloud_url ?? 'https://spriteplay.com';
      void host.openExternal(`${base}${path}`).then((r) => {
        if (!r.ok && r.message) toast(r.message, 'error');
      });
    },
    [toast],
  );

  const signIn = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.cloudSignIn();
      const opened = await host.openExternal(r.authorize_url);
      if (!opened.ok && opened.message) toast(opened.message, 'error');
      await reload();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(false);
    }
  }, [reload, toast]);

  const reopenSignIn = useCallback(() => {
    const url = statusRef.current?.pending_sign_in?.url;
    if (url) void host.openExternal(url);
  }, []);

  const cancelSignIn = useCallback(async () => {
    try {
      await api.cloudSignInCancel();
    } finally {
      await reload();
    }
  }, [reload]);

  const signOut = useCallback(async () => {
    setBusy(true);
    try {
      await api.cloudSignOut();
      toast('Signed out. Your projects stay on this machine.', 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(false);
      await reload();
    }
  }, [reload, toast]);

  const checkAgain = useCallback(async () => {
    setBusy(true);
    try {
      await api.cloudAccessRefresh();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(false);
      await reload();
    }
  }, [reload, toast]);

  const renameMachine = useCallback(
    async (name: string) => {
      try {
        await api.cloudRenameMachine(name);
        toast(`This machine is now “${name}”.`, 'ok');
        await reload();
        return true;
      } catch (e) {
        toast(errText(e), 'error');
        return false;
      }
    },
    [reload, toast],
  );

  const reloadSync = useCallback(async () => {
    if (!projectPath) {
      setSync(null);
      return;
    }
    try {
      setSync(await api.projectSync());
    } catch {
      /* no project open, or the engine is busy: the next event reloads */
    }
  }, [projectPath]);

  useEffect(() => {
    setSync(null);
    void reloadSync();
  }, [reloadSync]);

  const guarded = useCallback(
    async (fn: () => Promise<unknown>, ok?: string) => {
      setBusy(true);
      try {
        await fn();
        if (ok) toast(ok, 'ok');
        return true;
      } catch (e) {
        toast(errText(e), 'error');
        return false;
      } finally {
        setBusy(false);
        await Promise.all([reloadSync(), reload()]);
      }
    },
    [reload, reloadSync, toast],
  );

  const syncNow = useCallback(async () => {
    await guarded(() => api.syncNow());
  }, [guarded]);
  const link = useCallback((owner: string) => guarded(() => api.syncLink(owner), 'This project now syncs to the cloud.'), [guarded]);
  const unlink = useCallback(async () => {
    await guarded(() => api.syncUnlink(), 'Syncing stopped. The cloud copy stays on spriteplay.com.');
  }, [guarded]);
  const resolve = useCallback(
    async (choices: SyncChoice[]) => {
      await guarded(() => api.syncResolve(choices));
    },
    [guarded],
  );
  const answerCopied = useCallback(
    async (keep: boolean) => {
      await guarded(() => api.syncCopied(keep), keep ? 'This folder keeps syncing here.' : 'This folder is now a separate project.');
    },
    [guarded],
  );
  const setAutoSync = useCallback(
    async (on: boolean) => {
      await guarded(() => api.setAutoSync(on));
    },
    [guarded],
  );

  useEffect(() => {
    if (!token) return;
    void reload();
    // the engine answers from its cache and asks the server when that is old, so a trial that ends
    // while the studio is open shows without a restart (C35)
    const t = window.setInterval(() => void reload(), POLL_MS);
    const onFocus = () => {
      const s = statusRef.current;
      if (s?.signed_in && !s.access.allowed) void checkAgain();
      else void reload();
    };
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('focus', onFocus);
    };
  }, [reload, checkAgain]);

  useEngineEvent((e) => {
    if (e.type === 'cloud_account') {
      void reload();
      if (e.signed_in) toast('Signed in to SpritePlay.', 'ok');
      else if (e.reason === 'revoked') toast('This machine was signed out on spriteplay.com. Sign in again to keep creating.', 'error');
      else if (e.reason === 'expired') toast('Your sign-in has ended. Sign in again.', 'error');
    } else if (e.type === 'cloud_access') {
      void reload();
    } else if (
      e.type === 'sync_started' ||
      e.type === 'sync_done' ||
      e.type === 'sync_paused' ||
      e.type === 'sync_conflict' ||
      e.type === 'sync_unlinked' ||
      e.type === 'project_files_changed'
    ) {
      void reloadSync();
    }
  });

  const value = useMemo<Cloud>(
    () => ({
      sync,
      reloadSync,
      syncNow,
      link,
      unlink,
      resolve,
      answerCopied,
      setAutoSync,
      status,
      error,
      busy,
      reload,
      signIn,
      reopenSignIn,
      cancelSignIn,
      signOut,
      checkAgain,
      renameMachine,
      openSite,
    }),
    [sync, reloadSync, syncNow, link, unlink, resolve, answerCopied, setAutoSync, status, error, busy, reload, signIn,
      reopenSignIn, cancelSignIn, signOut, checkAgain, renameMachine, openSite],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCloud(): Cloud {
  const c = useContext(Ctx);
  if (!c) throw new Error('useCloud outside CloudProvider');
  return c;
}

/** Whether the open project can create sprites now: synthetic mode always can; live needs access. */
export function useGenerationLock(): { locked: boolean; status: CloudStatus | null } {
  const { project } = useStore();
  const { status } = useCloud();
  const live = project ? project.mode !== 'synthetic' : true;
  return { locked: live && !!status && !status.access.allowed, status };
}

export const BILLING = {
  license: '/app/personal/billing?plan=license',
  plans: '/app/personal/billing',
  pro: '/app/personal/billing?plan=pro',
  account: '/app',
  machines: '/app/devices',
  team: (slug: string) => `/app/${slug}/billing`,
};
