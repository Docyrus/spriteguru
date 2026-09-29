import { useEffect, useRef } from 'react';
import { token } from './lib/api';
import { startEvents } from './lib/events';
import { href, redirect, useRoute, type Route } from './lib/router';
import { pickAnimation, StoreProvider, useStore } from './lib/store';
import { Approvals } from './shell/Approvals';
import { NavRail } from './shell/NavRail';
import { openCharacterSwitcher } from './shell/Switchers';
import { TopBar } from './shell/TopBar';
import { Button, Empty, Spinner } from './ui/controls';
import { Icon } from './ui/Icon';
import { Characters } from './screens/Characters';
import { Builder } from './screens/Builder';
import { Candidates } from './screens/Candidates';
import { SheetReview } from './screens/SheetReview';
import { FrameEditor } from './screens/FrameEditor';
import { Findings } from './screens/Findings';
import { ExportScreen } from './screens/Export';
import { ProjectsScreen } from './screens/Projects';
import { SettingsScreen } from './screens/Settings';

const ANIM_SCREENS = new Set(['candidates', 'sheet', 'frames', 'findings', 'export']);

const ANIM_TITLE: Record<string, string> = {
  candidates: 'Candidates',
  sheet: 'Sheet review',
  frames: 'Frame editor',
  findings: 'Findings',
  export: 'Export',
};

/**
 * Keeps the route and the active character in step:
 * - nothing active yet: an animation link picks its character, else the first approved one does;
 * - the user picks another character on an animation tab: that tab moves to their newest animation;
 * - a link names another character's animation (an old link, a job in the jobs list): that
 *   character becomes active;
 * - an animation tab without an animation opens the active character's newest one.
 */
function useFollowActive(route: Route) {
  const { project, characters, animations, activeCharacter, setActiveCharacter, currentAnim } = useStore();
  const prevActive = useRef<string | null | undefined>(undefined);
  const prevParam = useRef<string | null | undefined>(undefined);
  const autoFor = useRef<string | null>(null); // the project an automatic pick was made for

  useEffect(() => {
    if (!project || !characters || !animations) return;
    const onAnim = ANIM_SCREENS.has(route.screen);
    const routeAnim = onAnim && route.param ? animations.find((a) => a.id === route.param) : undefined;
    const routeChar = routeAnim?.character && characters.some((c) => c.name === routeAnim.character) ? routeAnim.character : null;
    const activeChanged = prevActive.current != null && prevActive.current !== activeCharacter;
    const paramChanged = prevParam.current !== route.param;
    prevActive.current = activeCharacter;
    prevParam.current = route.param;

    if (!activeCharacter) {
      if (!characters.length || autoFor.current === project.path) return;
      autoFor.current = project.path;
      void setActiveCharacter(routeChar ?? (characters.find((c) => c.approved) ?? characters[0])!.name);
      return;
    }
    if (!onAnim) return;
    if (routeChar && routeChar !== activeCharacter) {
      if (activeChanged && !paramChanged) {
        const next = pickAnimation(animations, activeCharacter, currentAnim);
        redirect(route.screen, next?.id ?? null);
      } else {
        void setActiveCharacter(routeChar);
      }
      return;
    }
    if (!route.param) {
      const next = pickAnimation(animations, activeCharacter, currentAnim);
      if (next) redirect(route.screen, next.id, Object.fromEntries(route.query));
    }
  }, [project, characters, animations, activeCharacter, setActiveCharacter, currentAnim, route]);
}

/** An animation tab when the active character has no animation (or no character is active). */
function NoAnimations({ screen }: { screen: string }) {
  const { activeCharacter } = useStore();
  return (
    <div className="screen" data-testid="anim-empty-screen" data-screen={screen}>
      <div className="screen-head">
        <h1 className="screen-title">{ANIM_TITLE[screen] ?? screen}</h1>
      </div>
      <div className="screen-body">
        {activeCharacter ? (
          <Empty
            title={`No animations for ${activeCharacter} yet`}
            testid="anim-empty"
            action={
              <a className="btn btn-primary" href={href('builder')} data-testid="anim-empty-builder">
                Open the builder
              </a>
            }
          >
            Plan one in the builder. Its candidates, sheet, frames, findings and export appear on these tabs.
          </Empty>
        ) : (
          <Empty
            title="Choose a character"
            testid="anim-empty-no-character"
            action={
              <Button variant="primary" icon="characters" onClick={openCharacterSwitcher} data-testid="anim-empty-choose">
                Choose a character
              </Button>
            }
          >
            Every tab follows the character picked in the header.
          </Empty>
        )}
      </div>
    </div>
  );
}

function Screen() {
  const route = useRoute();
  const { project, noProject, characters, animations, activeCharacter, currentAnim } = useStore();
  useFollowActive(route);

  // A bare launch URL lands on the gallery; make that visible in the address.
  useEffect(() => {
    if (!window.location.hash) redirect('projects');
  }, []);

  // Another window switched projects: an animation of the old project means nothing here.
  const shownPath = useRef<string | null>(null);
  useEffect(() => {
    const path = project?.path ?? null;
    if (shownPath.current && path && path !== shownPath.current && ANIM_SCREENS.has(route.screen) && route.param) {
      redirect(route.screen);
    }
    if (path) shownPath.current = path;
  }, [project?.path, route]);

  // Without an open project only the gallery renders; everything else goes home.
  useEffect(() => {
    if (noProject && route.screen !== 'projects') redirect('projects');
  }, [noProject, route.screen]);

  if (route.screen === 'projects') return <ProjectsScreen />;
  if (!project) {
    return noProject ? null : (
      <div className="screen-body">
        <Spinner label="Opening project…" />
      </div>
    );
  }
  if (ANIM_SCREENS.has(route.screen) && !route.param) {
    if (!characters || !animations) return null;
    // useFollowActive is about to open the active character's newest animation
    if (pickAnimation(animations, activeCharacter, currentAnim)) return null;
    return <NoAnimations screen={route.screen} />;
  }

  switch (route.screen) {
    case 'builder':
      return <Builder />;
    case 'candidates':
      return <Candidates anim={route.param} job={route.query.get('job')} />;
    case 'sheet':
      return <SheetReview anim={route.param} job={route.query.get('job')} cand={route.query.get('cand')} />;
    case 'frames':
      return <FrameEditor anim={route.param} />;
    case 'findings':
      return <Findings anim={route.param} job={route.query.get('job')} />;
    case 'export':
      return <ExportScreen anim={route.param} />;
    case 'settings':
      return <SettingsScreen />;
    default:
      return <Characters />;
  }
}

function Toasts() {
  const { toasts, dismissToast } = useStore();
  return (
    <div className="toasts" aria-live="polite" data-testid="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} data-testid={`toast-${t.kind}`}>
          <span>{t.text}</span>
          <button type="button" aria-label="Dismiss" onClick={() => dismissToast(t.id)} data-testid="toast-dismiss">
            <Icon name="x" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
  const { projectError, project, noProject } = useStore();
  if (!token) {
    return (
      <div className="gate" data-testid="gate-no-token">
        <h1>Open the studio from SpriteGuru</h1>
        <p>
          This page needs the launch token that <code>spriteguru studio</code> or <code>spriteguru serve</code> prints. Open the URL it gives you,
          which ends in <code>?token=…</code>.
        </p>
      </div>
    );
  }
  if (projectError && !project && !noProject) {
    return (
      <div className="gate" data-testid="gate-error">
        <h1>Can’t reach the engine</h1>
        <p>{projectError}</p>
      </div>
    );
  }
  return <>{children}</>;
}

function Shell() {
  const { project } = useStore();
  // Pixel-art projects scale sprites with nearest-neighbour sampling everywhere.
  const pixel = project?.config.style.kind === 'pixel';
  return (
    <div className="app" data-testid="app" data-pixel={pixel ? 'true' : undefined}>
      <TopBar />
      <NavRail />
      <main className="main" data-testid="main">
        <Gate>
          <Screen />
        </Gate>
      </main>
      <Approvals />
      <Toasts />
    </div>
  );
}

export function App() {
  useEffect(() => {
    if (token) startEvents();
  }, []);
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  );
}
