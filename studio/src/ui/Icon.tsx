// A small stroke icon set drawn for the studio (20px grid, 1.6 stroke).
import mark from '../../../brand/svg/logo-mark.svg';
import markOnDark from '../../../brand/svg/logo-mark-on-dark.svg';

const P: Record<string, string> = {
  characters: 'M10 3.2a2.3 2.3 0 1 1 0 4.6 2.3 2.3 0 0 1 0-4.6ZM6.2 17v-4.4L5 10.3c.5-1 1.9-1.8 5-1.8s4.5.8 5 1.8l-1.2 2.3V17M8.4 17v-3.6M11.6 17v-3.6',
  builder: 'M3.5 16.5 12 8M11 4.2l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6ZM15.5 9.2l.4 1 1 .4-1 .4-.4 1-.4-1-1-.4 1-.4ZM15 2.8l.3.7.7.3-.7.3-.3.7-.3-.7-.7-.3.7-.3Z',
  candidates: 'M2.8 4.5h6v11h-6zM11.2 4.5h6v11h-6zM13.2 10.2l1.2 1.2 2-2.4',
  sheet: 'M3 3.5h14v13H3zM3 10h14M7.7 3.5v13M12.3 3.5v13',
  frames: 'M2.5 5h15v10h-15zM2.5 7.5h15M2.5 12.5h15M6 5v2.5M10 5v2.5M14 5v2.5M6 12.5V15M10 12.5V15M14 12.5V15',
  findings: 'M4.5 17V3.5M4.5 4h9.5l-2 3 2 3H4.5',
  export: 'M10 3v9M6.5 8.5 10 12l3.5-3.5M4 12.5V16h12v-3.5',
  settings: 'M4 5.5h6M14 5.5h2M4 10h2M10 10h6M4 14.5h8M16 14.5h0M12 4v3M8 8.5v3M14 13v3',
  jobs: 'M2.5 10h3l2-5 3 10 2-5h5',
  sun: 'M10 6.4a3.6 3.6 0 1 1 0 7.2 3.6 3.6 0 0 1 0-7.2ZM10 2v1.6M10 16.4V18M2 10h1.6M16.4 10H18M4.3 4.3l1.2 1.2M14.5 14.5l1.2 1.2M4.3 15.7l1.2-1.2M14.5 5.5l1.2-1.2',
  moon: 'M15.8 12.6A6.5 6.5 0 0 1 7.4 4.2a6.5 6.5 0 1 0 8.4 8.4Z',
  play: 'M6.5 4.5v11l9-5.5z',
  pause: 'M6.5 4.5v11M13.5 4.5v11',
  prev: 'M14 5v10l-7-5zM5.5 5v10',
  next: 'M6 5v10l7-5zM14.5 5v10',
  flip: 'M10 2.5v15M8 5.5 3.5 14.5H8zM12 5.5l4.5 9H12z',
  trash: 'M4 6h12M8 6V4h4v2M5.5 6l.8 10.5h7.4L14.5 6M8.5 9v5M11.5 9v5',
  onion: 'M4 7.5h9v9H4zM7 4.5h9v9',
  crosshair: 'M10 5.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9ZM10 2v5M10 13v5M2 10h5M13 10h5',
  up: 'M10 4v12M5.5 8.5 10 4l4.5 4.5',
  down: 'M10 16V4M5.5 11.5 10 16l4.5-4.5',
  left: 'M4 10h12M8.5 5.5 4 10l4.5 4.5',
  right: 'M16 10H4M11.5 5.5 16 10l-4.5 4.5',
  check: 'M4 10.5 8 14.5 16 5.5',
  x: 'M5 5l10 10M15 5 5 15',
  external: 'M11.5 3.5h5v5M16.5 3.5 9 11M14 11.5V16.5H3.5V6H8.5',
  folder: 'M2.5 5.5V15.5h15V7.5H9.5L8 5.5z',
  refresh: 'M16 10a6 6 0 1 1-1.8-4.3M16 3.5v3h-3',
  key: 'M7.5 9.5a3.5 3.5 0 1 1 0 .1ZM10.8 10.8 17 17M14.5 14.5l1.8-1.8M12.8 12.8l1.5-1.5',
  download: 'M10 3v10M6 9l4 4 4-4M4 16.5h12',
  warn: 'M10 3 18 16.5H2zM10 8v4M10 14.2v.3',
  loop: 'M4 9.5V8.5a3 3 0 0 1 3-3h8.5M13 3l2.5 2.5L13 8M16 10.5v1a3 3 0 0 1-3 3H4.5M7 17l-2.5-2.5L7 12',
  grid: 'M3 3h14v14H3zM3 7.7h14M3 12.3h14M7.7 3v14M12.3 3v14',
  eye: 'M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10ZM10 7.6a2.4 2.4 0 1 1 0 4.8 2.4 2.4 0 0 1 0-4.8Z',
  plus: 'M10 4v12M4 10h12',
  minus: 'M4 10h12',
  stop: 'M5 5h10v10H5z',
  sparkle: 'M10 3l1.4 4.2L15.6 8.6 11.4 10 10 14.2 8.6 10 4.4 8.6 8.6 7.2z',
  library: 'M3.5 3.5h3.2v13H3.5zM8.4 3.5h3.2v13H8.4zM13.2 4.6l3-.9 2.9 11.9-3 .9z',
  report: 'M5 2.5h7l3.5 3.5v11.5H5zM12 2.5V6h3.5M7.5 10h5M7.5 13h5',
  drag: 'M7.5 5h.1M12.5 5h.1M7.5 10h.1M12.5 10h.1M7.5 15h.1M12.5 15h.1',
  fit: 'M3.5 7.5v-4h4M12.5 3.5h4v4M16.5 12.5v4h-4M7.5 16.5h-4v-4',
  thumbUp: 'M6.5 9v8h-3V9zM6.5 9l3-6c1.2 0 2 .8 2 2v3h4.2c.9 0 1.5.8 1.3 1.7l-1.3 6c-.2.8-.8 1.3-1.6 1.3H6.5',
  thumbDown: 'M6.5 11V3h-3v8zM6.5 11l3 6c1.2 0 2-.8 2-2v-3h4.2c.9 0 1.5-.8 1.3-1.7l-1.3-6c-.2-.8-.8-1.3-1.6-1.3H6.5',
  inbetween: 'M2.5 5h4.5v10H2.5zM13 5h4.5v10H13zM10 7.5v5M7.5 10h5',
  image: 'M3 4h14v12H3zM3 13.5l4-4 3.5 3.5 2-2 4.5 4.5M12.8 7.3h.1',
  upload: 'M10 13.5V3.5M6.5 7 10 3.5 13.5 7M4 12.5V16h12v-3.5',
  projects: 'M3.5 3.5h5.5v5.5H3.5zM11 3.5h5.5v5.5H11zM3.5 11h5.5v5.5H3.5zM11 11h5.5v5.5H11z',
  chevron: 'M6 8l4 4 4-4',
  more: 'M5 9.3a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4ZM10 9.3a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4ZM15 9.3a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4Z',
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 18, title }: { name: IconName | string; size?: number; title?: string }) {
  const d = P[name] ?? P.sparkle;
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
    >
      {title ? <title>{title}</title> : null}
      <path d={d} />
    </svg>
  );
}

/** The SpritePlay mark from the brand kit; the dark theme takes the white-tile variant. Drawn at
 * 32 px so every pixel of the S is exactly 4 px. */
export function BrandMark({ dark }: { dark: boolean }) {
  return (
    <img
      className="brand-mark"
      src={dark ? markOnDark : mark}
      width={32}
      height={32}
      alt="SpritePlay"
      draggable={false}
      data-testid="topbar-brand-mark"
      data-variant={dark ? 'on-dark' : 'light'}
    />
  );
}
