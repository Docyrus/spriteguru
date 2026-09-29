import { useEffect, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { Icon } from './Icon';
import type { Level } from '../lib/types';
import { LEVEL_LABEL, q } from '../lib/format';

type BtnVariant = 'primary' | 'default' | 'ghost' | 'danger' | 'quiet';

export function Button({
  variant = 'default',
  icon,
  busy,
  size,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; icon?: string; busy?: boolean; size?: 'sm' | 'md' }) {
  return (
    <button
      type="button"
      className={`btn btn-${variant}${size === 'sm' ? ' btn-sm' : ''}${busy ? ' is-busy' : ''}${className ? ` ${className}` : ''}`}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <span className="spinner" aria-hidden="true" /> : icon ? <Icon name={icon} size={size === 'sm' ? 15 : 17} /> : null}
      {children ? <span>{children}</span> : null}
    </button>
  );
}

export function IconButton({
  icon,
  label,
  active,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string; active?: boolean }) {
  return (
    <button
      type="button"
      className={`icon-btn${active ? ' is-active' : ''}${className ? ` ${className}` : ''}`}
      aria-label={label}
      aria-pressed={active === undefined ? undefined : active}
      title={label}
      {...rest}
    >
      <Icon name={icon} size={17} />
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  testid,
  label,
  disabled,
}: {
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  onChange: (v: T) => void;
  testid: string;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label} data-testid={testid}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={value === o.value ? 'is-on' : ''}
          disabled={disabled || o.disabled}
          title={o.title}
          data-testid={`${testid}-${o.value}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  testid,
  disabled,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  testid: string;
  disabled?: boolean;
  hint?: ReactNode;
}) {
  return (
    <label className={`toggle${disabled ? ' is-disabled' : ''}`}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        data-testid={testid}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-text">
        {label}
        {hint ? <small>{hint}</small> : null}
      </span>
    </label>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint ? <div className="field-hint">{hint}</div> : null}
    </div>
  );
}

export function LevelPill({ level, testid }: { level: Level; testid?: string }) {
  return (
    <span className={`level level-${level}`} data-testid={testid}>
      {LEVEL_LABEL[level]}
    </span>
  );
}

export function StatePill({ state, testid }: { state: string; testid?: string }) {
  const label: Record<string, string> = {
    queued: 'Queued',
    running: 'Running',
    awaiting_approval: 'Needs approval',
    done: 'Done',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };
  return (
    <span className={`state state-${state}`} data-testid={testid}>
      {state === 'running' || state === 'queued' ? <span className="pulse" aria-hidden="true" /> : null}
      {label[state] ?? state}
    </span>
  );
}

/** The quality score Q (0–100); 85 and above with no fails is auto-accepted. */
export function QScore({ score, accepted, size = 'md', testid }: { score?: number; accepted?: boolean; size?: 'sm' | 'md' | 'lg'; testid?: string }) {
  const tone = typeof score !== 'number' ? 'none' : accepted ? 'ok' : score >= 60 ? 'warn' : 'fail';
  return (
    <span className={`qscore qscore-${size} tone-${tone}`} data-testid={testid} title="Quality score Q">
      <span className="qscore-q">Q</span>
      <span className="qscore-n">{q(score)}</span>
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-line" role="status">
      <span className="spinner" aria-hidden="true" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export function Empty({ title, children, testid, action }: { title: string; children?: ReactNode; testid?: string; action?: ReactNode }) {
  return (
    <div className="empty" data-testid={testid}>
      <div className="empty-title">{title}</div>
      {children ? <div className="empty-body">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorNote({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <div className="error-note" role="alert" data-testid={testid}>
      <Icon name="warn" size={16} />
      <span>{children}</span>
    </div>
  );
}

export function WarnNote({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <div className="warn-note" role="status" data-testid={testid}>
      <Icon name="warn" size={16} />
      <div>{children}</div>
    </div>
  );
}

export function Modal({
  title,
  children,
  onClose,
  actions,
  testid,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose?: () => void;
  actions?: ReactNode;
  testid: string;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // the latest onClose, so a parent re-render never re-runs the focus handling below
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && closeRef.current) closeRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      prev?.focus?.();
    };
  }, []);
  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && closeRef.current?.()}>
      <div
        className={`modal${wide ? ' modal-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testid}
        tabIndex={-1}
        ref={ref}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          {onClose ? <IconButton icon="x" label="Close" onClick={onClose} data-testid={`${testid}-close`} /> : null}
        </div>
        <div className="modal-body">{children}</div>
        {actions ? <div className="modal-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

const dataAttrs = (d?: Record<string, string | undefined>) => Object.fromEntries(Object.entries(d ?? {}).map(([k, v]) => [`data-${k}`, v]));

export interface MenuEntry {
  key: string;
  label: ReactNode;
  onSelect: () => void;
  checked?: boolean; // a radio-style entry: the current choice is checked
  disabled?: boolean;
  title?: string;
  testid?: string;
  data?: Record<string, string | undefined>; // extra data-* attributes
  divider?: boolean; // a separator above this entry
}

/** A menu button: click, Enter, Space or the arrow keys open it; arrows, Home and End move; Enter
 * picks; Escape closes and returns focus to the button. `open`/`onOpenChange` make it controllable. */
export function Menu({
  trigger,
  label,
  entries,
  testid,
  menuTestid,
  className,
  menuClassName,
  align = 'start',
  header,
  title,
  triggerLabel,
  data,
  disabled,
  open: openProp,
  onOpenChange,
}: {
  trigger: ReactNode;
  label: string; // the menu's accessible name
  triggerLabel?: string; // the button's accessible name, for icon-only triggers
  data?: Record<string, string | undefined>; // data-* attributes on the button
  entries: MenuEntry[];
  testid?: string;
  menuTestid?: string;
  className?: string;
  menuClassName?: string;
  align?: 'start' | 'end';
  header?: ReactNode;
  title?: string;
  disabled?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [openState, setOpenState] = useState(false);
  const open = openProp ?? openState;
  const setOpen = (v: boolean) => (onOpenChange ? onOpenChange(v) : setOpenState(v));
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const focusAt = useRef<'first' | 'last' | 'checked'>('checked');

  const items = () => Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('.menu-item:not(:disabled)') ?? []);

  useEffect(() => {
    if (!open) return;
    const list = items();
    const target =
      focusAt.current === 'last'
        ? list[list.length - 1]
        : (focusAt.current === 'checked' && list.find((b) => b.getAttribute('aria-checked') === 'true')) || list[0];
    target?.focus();
    focusAt.current = 'checked';
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  const onTriggerKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      focusAt.current = e.key === 'ArrowUp' ? 'last' : 'first';
      if (open) items()[e.key === 'ArrowUp' ? items().length - 1 : 0]?.focus();
      else setOpen(true);
    }
  };

  const onMenuKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    const move = (to: number) => {
      e.preventDefault();
      list[(to + list.length) % list.length]?.focus();
    };
    if (e.key === 'ArrowDown') move(i + 1);
    else if (e.key === 'ArrowUp') move(i < 0 ? list.length - 1 : i - 1);
    else if (e.key === 'Home') move(0);
    else if (e.key === 'End') move(list.length - 1);
    else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Tab') close(false);
  };

  return (
    <div className="menu-anchor" ref={wrap}>
      <button
        type="button"
        ref={button}
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={triggerLabel}
        title={title}
        disabled={disabled}
        data-testid={testid}
        {...dataAttrs(data)}
        onClick={() => {
          focusAt.current = 'checked';
          setOpen(!open);
        }}
        onKeyDown={onTriggerKey}
      >
        {trigger}
      </button>
      {open ? (
        <div
          ref={menu}
          role="menu"
          aria-label={label}
          className={`menu-pop menu-${align}${menuClassName ? ` ${menuClassName}` : ''}`}
          data-testid={menuTestid}
          onKeyDown={onMenuKey}
        >
          {header ? <div className="menu-head">{header}</div> : null}
          {entries.map((m) => [
            m.divider ? <div key={`${m.key}-sep`} role="separator" className="menu-sep" /> : null,
            <button
              key={m.key}
              type="button"
              role={m.checked === undefined ? 'menuitem' : 'menuitemradio'}
              aria-checked={m.checked}
              tabIndex={-1}
              className={`menu-item${m.checked ? ' is-checked' : ''}`}
              disabled={m.disabled}
              title={m.title}
              data-testid={m.testid}
              {...dataAttrs(m.data)}
              onClick={() => {
                close(true);
                m.onSelect();
              }}
            >
              {m.checked !== undefined ? (
                <span className="menu-check" aria-hidden="true">
                  {m.checked ? <Icon name="check" size={14} /> : null}
                </span>
              ) : null}
              {m.label}
            </button>,
          ])}
        </div>
      ) : null}
    </div>
  );
}

export function Swatch({ hex, testid }: { hex: string; testid?: string }) {
  return <span className="swatch" style={{ background: hex }} title={hex} data-testid={testid} />;
}
