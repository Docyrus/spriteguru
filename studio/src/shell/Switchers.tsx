// The header's project switcher and active-character switcher. Every tab follows the character
// picked here; the project switcher reaches recent projects and the gallery.

import { useEffect, useState } from 'react';
import { fileUrl } from '../lib/api';
import { useEvents } from '../lib/events';
import { cardsWithCurrent, ENGINE_LABEL, kindLabel, STYLE_LABEL, thumbOf } from '../lib/format';
import { navigate } from '../lib/router';
import { errText, useStore } from '../lib/store';
import type { CharacterRecord, ProjectCard } from '../lib/types';
import { Menu, type MenuEntry } from '../ui/controls';
import { Icon } from '../ui/Icon';

const OPEN_CHARACTERS = 'spriteplay:open-character-switcher';

/** Opens the header's character dropdown, e.g. from the builder's "Change" button. */
export function openCharacterSwitcher() {
  window.dispatchEvent(new Event(OPEN_CHARACTERS));
}

const RECENT = 8;

function ProjectItem({ card }: { card: ProjectCard }) {
  return (
    <span className="menu-label">
      <span className="menu-title">{card.name}</span>
      <span className="menu-sub">
        {[card.style ? STYLE_LABEL[card.style] : null, card.engine ? ENGINE_LABEL[card.engine] : null].filter(Boolean).join(' · ')}
      </span>
    </span>
  );
}

export function ProjectSwitcher() {
  const { project, noProject, library, reloadLibrary, openProject, toast } = useStore();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (open) void reloadLibrary();
  }, [open, reloadLibrary]);

  const recent = cardsWithCurrent(library, project).filter((p) => !p.missing && !p.error).slice(0, RECENT);
  const pick = async (card: ProjectCard) => {
    if (card.current && project) return;
    try {
      await openProject({ id: card.id });
      toast(`Opened ${card.name}.`, 'ok');
      navigate('characters');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const entries: MenuEntry[] = [
    ...recent.map((card) => ({
      key: card.id || card.path,
      label: <ProjectItem card={card} />,
      checked: card.current,
      testid: 'header-project-item',
      data: { id: card.id },
      title: card.path,
      onSelect: () => void pick(card),
    })),
    { key: 'home', label: 'All projects…', divider: recent.length > 0, testid: 'header-project-home', onSelect: () => navigate('projects') },
    { key: 'new', label: 'New project…', testid: 'header-project-new', onSelect: () => navigate('projects', null, { new: '1' }) },
  ];
  return (
    <Menu
      label="Projects"
      testid="header-project"
      className="switcher switcher-project"
      header={recent.length ? 'Recent projects' : undefined}
      title={project?.path}
      data={{ path: project?.path }}
      open={open}
      onOpenChange={setOpen}
      entries={entries}
      trigger={
        <>
          <span className="switcher-name topbar-project" data-testid="topbar-project-name">
            {project?.name ?? (noProject ? 'Choose a project' : 'Opening project…')}
          </span>
          <Icon name="chevron" size={14} />
        </>
      }
    />
  );
}

function Thumb({ rec, bust }: { rec: CharacterRecord; bust?: number }) {
  const src = thumbOf(rec);
  return (
    <span className={`switcher-thumb ${rec.blend === 'add' ? 'additive' : 'checker'}`} aria-hidden="true">
      {src ? <img src={fileUrl(src, bust)} alt="" draggable={false} /> : <Icon name="characters" size={14} />}
    </span>
  );
}

function CharacterItem({ rec, bust, running }: { rec: CharacterRecord; bust?: number; running: boolean }) {
  const hasViews = Object.keys(rec.views).length > 0;
  return (
    <>
      <Thumb rec={rec} bust={bust} />
      <span className="menu-label">
        <span className="menu-title is-name">{rec.name}</span>
        <span className="menu-sub">
          <span className="badge">{kindLabel(rec.kind)}</span>
          {running ? (
            <span className="badge badge-accent">Drawing…</span>
          ) : rec.approved ? (
            <span className="badge badge-ok">Approved</span>
          ) : hasViews ? (
            <span className="badge badge-warn">Needs approval</span>
          ) : (
            <span>No views yet</span>
          )}
        </span>
      </span>
    </>
  );
}

export function CharacterSwitcher() {
  const { project, characters, activeCharacter, setActiveCharacter } = useStore();
  const ev = useEvents();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const l = () => setOpen(true);
    window.addEventListener(OPEN_CHARACTERS, l);
    return () => window.removeEventListener(OPEN_CHARACTERS, l);
  }, []);
  if (!project) return null;

  const list = characters ?? [];
  const rec = list.find((c) => c.name === activeCharacter) ?? null;
  const entries: MenuEntry[] = [
    ...list.map((c) => ({
      key: c.name,
      label: <CharacterItem rec={c} bust={ev.turnaround[c.name]?.ts} running={ev.turnaround[c.name]?.state === 'running'} />,
      checked: c.name === activeCharacter,
      testid: 'header-character-item',
      data: { name: c.name, kind: c.kind },
      onSelect: () => {
        if (c.name !== activeCharacter) void setActiveCharacter(c.name);
      },
    })),
    {
      key: 'new',
      label: (
        <>
          <Icon name="plus" size={14} /> New character…
        </>
      ),
      divider: list.length > 0,
      testid: 'header-character-new',
      onSelect: () => navigate('characters', null, { new: '1' }),
    },
  ];
  return (
    <Menu
      label="Characters"
      testid="header-character"
      className={`switcher switcher-character${activeCharacter ? '' : ' is-empty'}`}
      title={activeCharacter ? `Every tab follows ${activeCharacter}. Click to switch.` : 'Choose the character every tab follows'}
      data={{ name: activeCharacter ?? undefined, kind: rec?.kind }}
      open={open}
      onOpenChange={setOpen}
      entries={entries}
      header={list.length ? 'Every tab follows this character' : undefined}
      trigger={
        <>
          {rec ? <Thumb rec={rec} bust={ev.turnaround[rec.name]?.ts} /> : null}
          <span className="switcher-name">{activeCharacter ?? 'Choose a character'}</span>
          {rec ? <span className="badge switcher-kind">{kindLabel(rec.kind)}</span> : null}
          <Icon name="chevron" size={14} />
        </>
      }
    />
  );
}
