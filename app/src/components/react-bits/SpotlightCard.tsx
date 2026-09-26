/**
 * Adapted from React Bits Spotlight Card (TypeScript + CSS), David Haz.
 * Revision: 5c73f3d065d4c074ae3c5fa66b4918e07fc999fa
 * https://github.com/DavidHDev/react-bits/blob/5c73f3d065d4c074ae3c5fa66b4918e07fc999fa/src/ts-default/Components/SpotlightCard/SpotlightCard.tsx
 * MIT + Commons Clause; full notice in ./LICENSE.
 * Local changes: theme-owned styling, ordinary div props/ref, composed events,
 * fine-pointer/reduced-motion guards and no clipping of descendant focus rings.
 */
import type { ComponentProps, MouseEvent } from "react";
import "./SpotlightCard.css";

// A single live query follows preference changes without per-card listeners
// or allocating a MediaQueryList on every pointer move.
const pointerMotion = window.matchMedia("(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)");

type SpotlightCardProps = ComponentProps<"div"> & { enabled?: boolean };

export default function SpotlightCard({ children, className = "", enabled = true, onMouseMove, ...props }: SpotlightCardProps) {
  function trackPointer(event: MouseEvent<HTMLDivElement>) {
    onMouseMove?.(event);
    if (event.defaultPrevented || !pointerMotion.matches) return;
    const surface = event.currentTarget;
    const rect = surface.getBoundingClientRect();
    surface.style.setProperty("--mouse-x", (event.clientX - rect.left) + "px");
    surface.style.setProperty("--mouse-y", (event.clientY - rect.top) + "px");
  }

  return (
    <div {...props} className={"spotlight-card " + (enabled ? "" : "spotlight-card-disabled ") + className} onMouseMove={enabled ? trackPointer : onMouseMove}>
      {children}
    </div>
  );
}
