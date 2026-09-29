import { useEffect, useState } from 'react';
import { fileUrl } from '../lib/api';
import { pad3 } from '../lib/format';
import type { FinalMeta } from '../lib/types';

/** How long a one-shot's last frame stays up before it starts again, so it does not read as a cycle. */
const ONE_SHOT_HOLD_MS = 700;

/**
 * An exported animation played from its frame files at their per-frame durations. Until it first plays
 * only the first frame loads; once every frame has loaded it advances, so the first pass does not
 * flash blank frames. Pausing stops on the current frame. Parents key it by the export's version, so
 * a rewritten export starts over with fresh images.
 */
export function Flipbook({
  anim,
  final,
  bust,
  playing = true,
  alt,
  testid,
}: {
  anim: string;
  final: FinalMeta;
  bust: string;
  playing?: boolean;
  alt: string;
  testid?: string;
}) {
  const n = Math.max(1, final.frames);
  const [i, setI] = useState(0);
  const [started, setStarted] = useState(playing);
  const [loaded, setLoaded] = useState(0); // frames loaded (or failed), so a missing file cannot stall it
  const cur = i % n;
  const ready = loaded >= n;

  useEffect(() => {
    if (playing) setStarted(true);
  }, [playing]);

  useEffect(() => {
    if (!playing || !ready || n < 2) return;
    const ms = final.durations[cur] ?? Math.round(1000 / (final.fps || 12));
    const hold = !final.loop && cur === n - 1 ? ONE_SHOT_HOLD_MS : 0;
    const t = window.setTimeout(() => setI(cur + 1), ms + hold);
    return () => window.clearTimeout(t);
  }, [playing, ready, cur, n, final.durations, final.fps, final.loop]);

  const count = () => setLoaded((k) => k + 1);
  return (
    <div
      className="frames-preview"
      data-testid={testid}
      data-frame={cur}
      data-frames={n}
      data-playing={playing && n > 1 ? 'true' : 'false'}
      data-ready={ready ? 'true' : 'false'}
    >
      {Array.from({ length: started ? n : 1 }, (_, k) => (
        <img
          key={k}
          src={fileUrl(`animations/${anim}/final/frames/${pad3(k)}.png`, bust)}
          alt={k === cur ? alt : ''}
          style={{ visibility: k === cur ? 'visible' : 'hidden' }}
          draggable={false}
          onLoad={count}
          onError={count}
        />
      ))}
    </div>
  );
}
