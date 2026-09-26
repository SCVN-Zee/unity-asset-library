import { useSyncExternalStore } from "react";

// Motion's hook snapshots this preference at mount. Share one live media query
// so existing controls also stop animating when the OS preference changes.
const media = window.matchMedia("(prefers-reduced-motion: reduce)");
const listeners = new Set<() => void>();
const notify = () => { for (const listener of listeners) listener(); };
const subscribe = (listener: () => void) => {
  if (!listeners.size) media.addEventListener("change", notify);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) media.removeEventListener("change", notify);
  };
};
const snapshot = () => media.matches;
export function useReducedMotion() {
  return useSyncExternalStore(subscribe, snapshot);
}
