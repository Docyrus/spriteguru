// Host adapter (plan section 18, rule 3): native folder dialogs, reveal-in-folder and external
// links. This is the only module allowed to touch `window.pywebview`; everything else in the
// studio talks to the engine over HTTP and WebSocket.

export interface HostResult {
  ok: boolean;
  message?: string;
}

export interface Host {
  readonly kind: 'pywebview' | 'browser';
  /** Ask for a folder; resolves to an absolute path, or null when cancelled. */
  pickFolder(initial?: string | null): Promise<string | null>;
  /** Show a file or folder in the OS file manager. */
  reveal(path: string): Promise<HostResult>;
  /** Open a URL (or an engine file URL) outside the studio. */
  openExternal(url: string): Promise<HostResult>;
}

interface PywebviewApi {
  open_folder_dialog?: (initial?: string | null) => Promise<string | string[] | null>;
  reveal?: (path: string) => Promise<unknown>;
  open_external?: (url: string) => Promise<unknown>;
}

function pywebviewApi(): PywebviewApi | null {
  const w = window as unknown as { pywebview?: { api?: PywebviewApi } };
  return w.pywebview?.api ?? null;
}

const absolute = (url: string) => new URL(url, window.location.href).href;

const browserHost: Host = {
  kind: 'browser',
  async pickFolder(initial) {
    const value = window.prompt('Folder path for exported game assets', initial ?? '');
    if (value === null) return null;
    const trimmed = value.trim();
    return trimmed || null;
  },
  async reveal(path) {
    try {
      await navigator.clipboard.writeText(path);
      return { ok: false, message: 'The browser cannot open folders, so the path was copied to the clipboard.' };
    } catch {
      return { ok: false, message: `The browser cannot open folders. Path: ${path}` };
    }
  },
  async openExternal(url) {
    // no 'noopener' feature: with it window.open always returns null, so a blocked tab can't be told
    // from an opened one; the opener link is cut by hand instead
    const win = window.open(absolute(url), '_blank');
    if (win === null) return { ok: false, message: 'The browser blocked the new tab.' };
    try {
      win.opener = null;
    } catch {
      /* cross-origin already */
    }
    return { ok: true };
  },
};

const pywebviewHost: Host = {
  kind: 'pywebview',
  async pickFolder(initial) {
    const fn = pywebviewApi()?.open_folder_dialog;
    if (!fn) return browserHost.pickFolder(initial);
    const res = await fn(initial ?? null);
    if (Array.isArray(res)) return res[0] ?? null;
    return res || null;
  },
  async reveal(path) {
    const fn = pywebviewApi()?.reveal;
    if (!fn) return browserHost.reveal(path);
    await fn(path);
    return { ok: true };
  },
  async openExternal(url) {
    const fn = pywebviewApi()?.open_external;
    if (!fn) return browserHost.openExternal(url);
    await fn(absolute(url));
    return { ok: true };
  },
};

/** Resolved per call: pywebview injects its API after page load (`pywebviewready`). */
export const host: Host = {
  get kind() {
    return pywebviewApi() ? 'pywebview' : 'browser';
  },
  pickFolder: (initial) => (pywebviewApi() ? pywebviewHost : browserHost).pickFolder(initial),
  reveal: (path) => (pywebviewApi() ? pywebviewHost : browserHost).reveal(path),
  openExternal: (url) => (pywebviewApi() ? pywebviewHost : browserHost).openExternal(url),
};

/** Calls `cb` when the native host appears: pywebview injects its API after page load. */
export function subscribeHost(cb: () => void): () => void {
  window.addEventListener('pywebviewready', cb);
  return () => window.removeEventListener('pywebviewready', cb);
}
