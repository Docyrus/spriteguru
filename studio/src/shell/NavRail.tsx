import { href, useRoute } from '../lib/router';
import { pickAnimation, useStore } from '../lib/store';
import { Icon } from '../ui/Icon';

const TOP = [
  { screen: 'characters', label: 'Characters', icon: 'characters' },
  { screen: 'builder', label: 'Builder', icon: 'builder' },
];

/** The animation tabs, in rail order; the Characters page links to them too. */
export const ANIM_TABS = [
  { screen: 'candidates', label: 'Candidates', icon: 'candidates' },
  { screen: 'sheet', label: 'Sheet', icon: 'sheet' },
  { screen: 'frames', label: 'Frames', icon: 'frames' },
  { screen: 'findings', label: 'Findings', icon: 'findings' },
  { screen: 'export', label: 'Export', icon: 'export' },
];

export function NavRail() {
  const route = useRoute();
  const { project, currentAnim, animations, activeCharacter } = useStore();
  // the animation tabs open the active character's animation; without one they show how to make it
  const anim = pickAnimation(animations, activeCharacter, currentAnim)?.id ?? null;
  const off = !project;

  const item = (screen: string, label: string, icon: string, param?: string | null, disabled?: boolean) => (
    <a
      key={screen}
      href={disabled ? undefined : href(screen, param)}
      className={`rail-item${route.screen === screen ? ' is-current' : ''}${disabled ? ' is-disabled' : ''}`}
      aria-current={route.screen === screen ? 'page' : undefined}
      aria-disabled={disabled || undefined}
      data-testid={`nav-${screen}`}
      title={disabled ? `${label}: open a project first` : label}
    >
      <Icon name={icon} size={20} />
      <span>{label}</span>
    </a>
  );

  return (
    <nav className="rail" aria-label="Studio screens" data-testid="nav-rail">
      {item('projects', 'Projects', 'projects')}
      {item('library', 'Library', 'library')}
      <span className="rail-rule" aria-hidden="true" />
      {TOP.map((i) => item(i.screen, i.label, i.icon, null, off))}
      <div className="rail-group" aria-label="Active character's animation">
        <span className="rail-rule" aria-hidden="true" />
        {ANIM_TABS.map((i) => item(i.screen, i.label, i.icon, anim, off))}
      </div>
      <div className="rail-fill" />
      {item('settings', 'Settings', 'settings', null, off)}
    </nav>
  );
}
