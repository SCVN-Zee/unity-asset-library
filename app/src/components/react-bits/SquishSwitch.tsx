/**
 * Adapted from React Bits SquishSwitch (TypeScript + CSS).
 * Revision: 5c73f3d065d4c074ae3c5fa66b4918e07fc999fa
 * https://github.com/DavidHDev/react-bits/blob/5c73f3d065d4c074ae3c5fa66b4918e07fc999fa/src/ts-default/Micro/SquishSwitch
 * MIT + Commons Clause; full notice in ./LICENSE.
 * Local changes: the interactive element is a native checkbox input with
 * role="switch" overlaying the visual track (keyboard, disabled and label
 * association stay native; no nested button), theme-token default colors,
 * calmer defaults, pointer-drag removed in favour of native toggling while
 * the upstream velocity-stretch and swell springs are preserved.
 */
import React, { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { animate, motion, useMotionValue, useSpring, useTransform, useVelocity } from 'motion/react';
import { useReducedMotion } from './useReducedMotion';

import './SquishSwitch.css';

export interface SquishSwitchProps {
  checked?: boolean;
  defaultChecked?: boolean;
  onChange?: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  trackColor?: string;
  trackOnColor?: string;
  thumbColor?: string;
  thumbOnColor?: string;
  width?: number;
  height?: number;
  radius?: number;
  speed?: number;
  stretch?: number;
  hoverScale?: number;
  colorDuration?: number;
  ariaLabel?: string;
  className?: string;
  id?: string;
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const FLOW_SPRING = { stiffness: 320, damping: 40, mass: 0.6 };
const SWELL_SPRING = { stiffness: 520, damping: 34, mass: 0.6 };
const MAX_STRETCH = 0.4;
const STRETCH_SPEED = 600;

const SquishSwitch: React.FC<SquishSwitchProps> = ({
  checked,
  defaultChecked = false,
  onChange,
  label = '',
  disabled = false,
  trackColor,
  trackOnColor,
  thumbColor,
  thumbOnColor,
  width = 64,
  height = 32,
  radius = 16,
  speed = 50,
  stretch = 24,
  hoverScale = 1.02,
  colorDuration = 320,
  ariaLabel,
  className = '',
  id
}) => {
  const reduce = useReducedMotion();
  const inset = Math.max(3, Math.round(height * 0.11));
  const thumb = height - inset * 2;
  const min = inset;
  const max = width - inset - thumb;

  const isControlled = checked !== undefined;
  const [inner, setInner] = useState(defaultChecked);
  const on = isControlled ? checked : inner;
  const onRef = useRef(on);
  onRef.current = on;
  const autoId = useId();
  const buttonId = id ?? autoId;
  const swellRef = useRef<HTMLSpanElement>(null);

  const x = useMotionValue(on ? max : min);
  const flow = useSpring(useVelocity(x), FLOW_SPRING);
  const swell = useSpring(1, SWELL_SPRING);
  const gain = reduce ? 0 : clamp(stretch, 0, 100) / 100;
  const stretchOf = (v: number) => 1 + Math.min(MAX_STRETCH, Math.abs(v) / STRETCH_SPEED) * gain;
  const scaleX = useTransform([flow, swell], ([v, h]: number[]) => stretchOf(v) * h);
  const scaleY = useTransform([flow, swell], ([v, h]: number[]) => h / stretchOf(v));

  const commit = (next: boolean) => {
    if (next === onRef.current) return;
    onRef.current = next;
    if (!isControlled) setInner(next);
    onChange?.(next);
  };

  useEffect(() => {
    const target = on ? max : min;
    if (reduce) {
      x.jump(target);
      flow.jump(0);
      swell.jump(1);
      return undefined;
    }
    if (x.get() === target && x.getVelocity() === 0) return undefined;
    const controls = animate(x, target, {
      type: 'spring',
      stiffness: 170 - (50 - clamp(speed, 0, 100)) * 1.1,
      damping: 21.5,
      mass: 0.9,
      restDelta: 0.001,
      restSpeed: 0.01
    });
    return () => controls.stop();
  }, [on, min, max, speed, reduce, x, flow, swell]);

  const trackRadius = Math.min(radius, height / 2);
  const thumbRadius = Math.max(2, trackRadius - inset);

  const cssVars = {
    ...(trackColor ? { '--ss-track': trackColor } : null),
    ...(trackOnColor ? { '--ss-track-on': trackOnColor } : null),
    ...(thumbColor ? { '--ss-thumb-color': thumbColor } : null),
    ...(thumbOnColor ? { '--ss-thumb-on': thumbOnColor } : null),
    '--ss-w': `${width}px`,
    '--ss-h': `${height}px`,
    '--ss-inset': `${inset}px`,
    '--ss-thumb': `${thumb}px`,
    '--ss-r': `${trackRadius}px`,
    '--ss-thumb-r': `${thumbRadius}px`,
    '--ss-fade': `${colorDuration}ms`
  } as CSSProperties;

  return (
    <span className={`squish-switch-root${className ? ` ${className}` : ''}`}>
      <span
        ref={swellRef}
        className="squish-switch"
        data-on={on ? '' : undefined}
        style={cssVars}
        // Hover swell lives on a noninteractive span; the input overlay owns
        // all activation, so clicks and keys stay native.
        onPointerEnter={e => {
          if (e.pointerType === 'mouse' && !disabled && !reduce) swell.set(hoverScale);
        }}
        onPointerLeave={() => swell.set(1)}
      >
        <span className="squish-switch__track" aria-hidden="true">
          <motion.span className="squish-switch__thumb" style={{ x, scaleX, scaleY }} />
        </span>
        <input
          id={buttonId}
          type="checkbox"
          role="switch"
          className="squish-switch__input"
          checked={on}
          disabled={disabled}
          aria-checked={on}
          aria-label={ariaLabel}
          onChange={e => commit(e.target.checked)}
        />
      </span>
      {label ? (
        <label htmlFor={buttonId} className="squish-switch__label">
          {label}
        </label>
      ) : null}
    </span>
  );
};

export default SquishSwitch;
