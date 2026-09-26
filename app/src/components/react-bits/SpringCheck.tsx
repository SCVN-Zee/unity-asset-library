/**
 * Adapted from React Bits SpringCheck (TypeScript + CSS).
 * Revision: 5c73f3d065d4c074ae3c5fa66b4918e07fc999fa
 * https://github.com/DavidHDev/react-bits/blob/5c73f3d065d4c074ae3c5fa66b4918e07fc999fa/src/ts-default/Micro/SpringCheck
 * MIT + Commons Clause; full notice in ./LICENSE.
 * Local changes: real native checkbox input overlay (keyboard, forms and
 * checked/onChange consumers stay native), outer className carries caller
 * classes (e.g. "import-check"),
 * title prop, stopPropagation so clicks never reach a parent selectable card,
 * calm default bounce, theme-token default colors, lucide "Check" stroke path
 * in place of the hugeicons tick (path from lucideISC-licensed
 * https://lucide.dev/icons/check) to avoid the hugeicons dependency.
 */
import React, { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { animate, useMotionValue, useMotionValueEvent } from 'motion/react';
import { useReducedMotion } from './useReducedMotion';

import './SpringCheck.css';

export type StrikeSide = 'left' | 'center' | 'right' | 'none';

export interface SpringCheckProps {
  label?: ReactNode;
  checked?: boolean;
  defaultChecked?: boolean;
  onChange?: (checked: boolean, event: React.ChangeEvent<HTMLInputElement>) => void;
  disabled?: boolean;
  color?: string;
  fillColor?: string;
  checkColor?: string;
  boxSize?: number;
  boxRadius?: number;
  fontSize?: number;
  bounce?: number;
  strikeLag?: number;
  doneOpacity?: number;
  strike?: StrikeSide;
  ariaLabel?: string;
  /** Class on the outer <label> control. */
  className?: string;
  title?: string;
}

const VISUAL_DURATION = 0.2;
const RULE_END = 0.84;
const SWELL = 0.35;
// lucide "Check" outline path (ISC license) — replaces the upstream
// @hugeicons/core-free-icons Tick02Icon to avoid the extra dependency.
const TICK_PATH = 'M20 6 9 17l-5-5';
const ORIGIN: Record<StrikeSide, string> = {
  left: 'left center',
  center: 'center',
  right: 'right center',
  none: 'left center'
};

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));
const zetaOf = (bounce: number) =>
  bounce <= 0 ? 1 : -Math.log(bounce) / Math.sqrt(Math.PI ** 2 + Math.log(bounce) ** 2);

const readings = (t: number, doneOpacity: number, strikeLag: number) => {
  const held = clamp01(t);
  return {
    fill: `scale(${Math.max(t, 0)})`,
    box: `scale(${1 + SWELL * Math.max(0, t - 1)})`,
    tick: 1 - held,
    word: 1 - (1 - doneOpacity) * held,
    rule: `scaleX(${clamp01((held - strikeLag) / (RULE_END - strikeLag))})`
  };
};

const SpringCheck: React.FC<SpringCheckProps> = ({
  label,
  checked,
  defaultChecked = false,
  onChange,
  disabled = false,
  color,
  fillColor,
  checkColor,
  boxSize = 24,
  boxRadius = 7,
  fontSize = 14,
  bounce = 0.08,
  strikeLag = 0.12,
  doneOpacity = 0.42,
  strike = 'left',
  ariaLabel,
  className = '',
  title
}) => {
  const controlled = checked !== undefined;
  const [inner, setInner] = useState(defaultChecked);
  const on = controlled ? checked : inner;
  const reduce = useReducedMotion();

  const t = useMotionValue(on ? 1 : 0);
  const viaPointer = useRef(false);
  const instant = useRef(false);
  const pressRef = useRef<HTMLSpanElement>(null);
  const boxRef = useRef<HTMLSpanElement>(null);
  const fillRef = useRef<HTMLSpanElement>(null);
  const tickRef = useRef<SVGPathElement>(null);
  const wordRef = useRef<HTMLSpanElement>(null);
  const ruleRef = useRef<HTMLSpanElement>(null);
  const cfg = useRef({ doneOpacity, strikeLag });
  cfg.current = { doneOpacity, strikeLag };

  const write = (value: number) => {
    const r = readings(value, cfg.current.doneOpacity, cfg.current.strikeLag);
    if (fillRef.current) fillRef.current.style.transform = r.fill;
    if (boxRef.current) boxRef.current.style.transform = r.box;
    if (tickRef.current) tickRef.current.style.strokeDashoffset = String(r.tick);
    if (wordRef.current) wordRef.current.style.opacity = String(r.word);
    if (ruleRef.current) ruleRef.current.style.transform = r.rule;
  };
  useMotionValueEvent(t, 'change', write);
  useLayoutEffect(() => {
    write(t.get());
  });

  useEffect(() => {
    const target = on ? 1 : 0;
    if (reduce || instant.current) {
      instant.current = false;
      t.jump(target);
      return undefined;
    }
    if (t.get() === target && t.getVelocity() === 0) return undefined;
    const controls = animate(t, target, {
      type: 'spring',
      visualDuration: VISUAL_DURATION,
      bounce: 1 - zetaOf(bounce)
    });
    return () => controls.stop();
  }, [on, reduce, bounce, t]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Keyboard and programmatic toggles skip the spring for an instant settle.
    instant.current = !viaPointer.current;
    viaPointer.current = false;
    const next = e.target.checked;
    if (!controlled) setInner(next);
    onChange?.(next, e);
  };

  const handlePointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || disabled) return;
    viaPointer.current = true;
    if (!reduce && pressRef.current) pressRef.current.dataset.pressed = '';
  };
  const handlePointerUp = () => {
    if (pressRef.current) delete pressRef.current.dataset.pressed;
  };
  const handlePointerCancel = () => {
    viaPointer.current = false;
    handlePointerUp();
  };

  // The native input owns semantics; the visual track stays noninteractive so
  // the control remains a single interactive element (no nested button).
  const r = readings(t.get(), doneOpacity, strikeLag);
  const ring = boxSize >= 24 ? 2 : 1.5;
  const gap = Math.min(16, Math.max(8, Math.round(boxSize * 0.43)));
  const ruleHeight = Math.max(1.5, Math.round(fontSize / 6) / 2);

  const cssVars = {
    ...(color ? { '--sc-ink': color } : null),
    ...(fillColor ? { '--sc-fill': fillColor } : null),
    ...(checkColor ? { '--sc-check': checkColor } : null),
    '--sc-box': `${boxSize}px`,
    '--sc-radius': `${boxRadius}px`,
    '--sc-font': `${fontSize}px`,
    '--sc-ring': `${ring}px`,
    '--sc-gap': `${gap}px`,
    '--sc-row': `${Math.max(32, boxSize + 14)}px`,
    '--sc-rule': `${ruleHeight}px`,
    '--sc-origin': ORIGIN[strike] || ORIGIN.left
  } as CSSProperties;

  return (
    <label
      className={`spring-check${className ? ` ${className}` : ''}`}
      style={cssVars}
      title={title}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onPointerLeave={handlePointerCancel}
      // Keep the click from bubbling into a parent selectable card.
      onClick={e => e.stopPropagation()}
    >
      <input
        type="checkbox"
        className="spring-check__input"
        checked={on}
        onChange={handleChange}
        onClick={e => e.stopPropagation()}
        disabled={disabled}
        aria-label={ariaLabel}
      />
      <span ref={pressRef} className="spring-check__press">
        <span ref={boxRef} className="spring-check__box" style={{ transform: r.box }}>
          <span className="spring-check__ring" aria-hidden="true" />
          <span ref={fillRef} className="spring-check__fill" style={{ transform: r.fill }} />
          <svg className="spring-check__tick" viewBox="0 0 24 24" aria-hidden="true">
            <path ref={tickRef} d={TICK_PATH} pathLength={1} strokeDasharray={1} style={{ strokeDashoffset: r.tick }} />
          </svg>
        </span>
      </span>
      {label !== undefined && label !== null ? (
        <span className="spring-check__label">
          <span ref={wordRef} className="spring-check__word" style={{ opacity: r.word }}>
            {label}
          </span>
          {strike !== 'none' ? (
            <span ref={ruleRef} className="spring-check__rule" aria-hidden="true" style={{ transform: r.rule }} />
          ) : null}
        </span>
      ) : null}
    </label>
  );
};

export default SpringCheck;
