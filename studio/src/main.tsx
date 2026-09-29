import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/bricolage-grotesque/standard.css';
import './styles/tokens.css';
import './styles/app.css';
import './styles/screens.css';
import { App } from './App';
import favicon from '../../brand/svg/logo-mark.svg';

// SpritePlay-era preferences carry over once; SpriteGuru values always win.
try {
  for (const k of ['theme', 'currentAnim']) {
    const legacy = localStorage.getItem(`spriteplay.${k}`);
    if (legacy !== null && localStorage.getItem(`spriteguru.${k}`) === null) {
      localStorage.setItem(`spriteguru.${k}`, legacy);
    }
    localStorage.removeItem(`spriteplay.${k}`);
  }
} catch {
  /* storage unavailable: nothing to carry over */
}

// set here rather than in index.html: an imported asset resolves in dev and in the build alike
const icon = document.createElement('link');
icon.rel = 'icon';
icon.type = 'image/svg+xml';
icon.href = favicon;
document.head.appendChild(icon);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
