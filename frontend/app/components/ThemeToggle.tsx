"use client";

import { useSyncExternalStore } from "react";
import { MoonIcon, SunIcon } from "./Icon";
import styles from "./AppHeader.module.css";

const STORAGE_KEY = "nutrition-assistant-theme";
const THEME_EVENT = "nutrition-assistant-theme-change";

type Theme = "light" | "dark";

/**
 * Light/dark toggle.
 *
 * *Applying* the theme happens in an inline script in `layout.tsx`, before first paint.
 * This component only reads the current value and flips it — if it also owned the initial
 * application, every dark-mode load would flash white while React hydrated.
 *
 * `useSyncExternalStore` rather than `useState` + `useEffect`, for two reasons. The theme
 * genuinely *is* external state: it lives on `<html>`'s dataset and in the OS preference,
 * neither of which React owns. And reading it in an effect would mean setting state during
 * one, which cascades an extra render — the thing `react-hooks/set-state-in-effect` is
 * there to catch. The subscription also means that a viewer who changes their OS theme
 * while the page is open sees the icon update, which the effect version missed.
 *
 * The server snapshot is `null` on purpose. There is no way to know a viewer's OS
 * preference while rendering on the server, so any icon chosen there is a coin flip that
 * hydration then has to correct — which React reports as a mismatch and the viewer sees as
 * the icon changing under them. An empty box for one frame is the quieter bug.
 */
function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  window.addEventListener(THEME_EVENT, onChange);
  return () => {
    media.removeEventListener("change", onChange);
    window.removeEventListener(THEME_EVENT, onChange);
  };
}

function getSnapshot(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getServerSnapshot(): Theme | null {
  return null;
}

export default function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Per-viewer convenience only. The page still works, it just forgets.
    }
    window.dispatchEvent(new Event(THEME_EVENT));
  }

  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      className={styles.iconButton}
      onClick={toggle}
      aria-label={label}
      title={label}
    >
      {theme === null ? null : theme === "dark" ? <SunIcon size={18} /> : <MoonIcon size={18} />}
    </button>
  );
}
