/**
 * React Bits GlideSelect, David Haz — MIT + Commons Clause; see ./LICENSE.
 * Source: https://github.com/DavidHDev/react-bits/tree/5c73f3d065d4c074ae3c5fa66b4918e07fc999fa/src/ts-default/Micro/GlideSelect
 * Local adaptations: theme defaults, accessible application controls, reduced motion.
 */
import React, { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Check, ChevronDown } from 'lucide-react';

import './GlideSelect.css';

export interface GlideSelectOption {
  value: string;
  label: ReactNode;
  tag?: string;
}

export interface GlideSelectProps {
  options?: (string | GlideSelectOption)[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string, option: GlideSelectOption) => void;
  placeholder?: string;
  showTags?: boolean;
  accentColor?: string;
  surfaceColor?: string;
  highlightColor?: string;
  textColor?: string;
  size?: 'sm' | 'md' | 'lg';
  radius?: number;
  menuWidth?: number;
  placement?: 'top' | 'bottom';
  align?: 'left' | 'right';
  popDuration?: number;
  glideDuration?: number;
  rememberPosition?: boolean;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}

type Phase = 'closed' | 'open' | 'closing';

const SIZES: Record<string, { chip: number; row: number; font: number }> = {
  sm: { chip: 28, row: 26, font: 12 },
  md: { chip: 32, row: 30, font: 13 },
  lg: { chip: 44, row: 40, font: 14 }
};
const PAD = 4;
const GAP = 1;
const MENU_GAP = 6;
const DEFAULT_OPTIONS: (string | GlideSelectOption)[] = ['One', 'Two', 'Three'];

const norm = (o: string | GlideSelectOption): GlideSelectOption => (typeof o === 'string' ? { value: o, label: o } : o);
const textOf = (it: GlideSelectOption) => (typeof it.label === 'string' ? it.label : it.value);
const typeaheadIndex = (items: GlideSelectOption[], from: number, ch: string) => {
  const c = ch.toLowerCase();
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const i = (from + k) % n;
    if (textOf(items[i]).toLowerCase().startsWith(c)) return i;
  }
  return from;
};

const GlideSelect: React.FC<GlideSelectProps> = ({
  options = DEFAULT_OPTIONS,
  value,
  defaultValue,
  onChange,
  placeholder = 'Select…',
  showTags = true,
  accentColor = 'var(--accent-text)',
  surfaceColor = 'var(--panel)',
  highlightColor = 'var(--accent-soft)',
  textColor = 'var(--text)',
  size = 'md',
  radius = 10,
  menuWidth = 176,
  placement = 'bottom',
  align = 'left',
  popDuration = 180,
  glideDuration = 160,
  rememberPosition = true,
  disabled = false,
  ariaLabel = 'Select',
  className = ''
}) => {
  const items = options.map(norm);
  const [inner, setInner] = useState(defaultValue ?? '');
  const current = value ?? inner;
  const selected = items.findIndex(it => it.value === current);
  const [phase, setPhase] = useState<Phase>('closed');
  const [active, setActive] = useState<number | null>(null);
  const [side, setSide] = useState<'top' | 'bottom'>(placement);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);
  const instant = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const consumedEscape = useRef(false);
  const id = useId();
  const S = SIZES[size] ?? SIZES.md;
  const step = S.row + GAP;
  const popOut = Math.round((popDuration * 2) / 3);

  useLayoutEffect(() => {
    if (phase !== 'open') return;
    const el = menuRef.current;
    const root = rootRef.current;
    if (!el || !root) return;
    const r = root.getBoundingClientRect();
    // Native top-layer popover escapes scroll clipping without leaving the dialog DOM.
    el.showPopover();
    const below = window.innerHeight - r.bottom - MENU_GAP - 8;
    const above = r.top - MENU_GAP - 8;
    const nextSide = placement === 'bottom' ? (below >= Math.min(320, el.scrollHeight) || below >= above ? 'bottom' : 'top') : (above >= Math.min(320, el.scrollHeight) || above >= below ? 'top' : 'bottom');
    setSide(nextSide);
    const height = Math.max(0, Math.min(320, nextSide === 'bottom' ? below : above));
    const width = Math.min(window.innerWidth - 16, Math.max(menuWidth, r.width));
    el.style.width = width + 'px';
    el.style.maxHeight = height + 'px';
    el.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, align === 'right' ? r.right - width : r.left)) + 'px';
    el.style.top = (nextSide === 'bottom' ? r.bottom + MENU_GAP : Math.max(8, r.top - MENU_GAP - Math.min(height, el.scrollHeight))) + 'px';
    el.style.transitionDuration = instant.current ? '0ms' : '';
    el.dataset.state = 'closed';
    void el.offsetHeight;
    el.dataset.state = 'open';
    const p = pillRef.current;
    if (p) {
      p.style.transition = 'none';
      p.style.transform = `translateY(${Math.max(0, selected) * step}px)`;
      p.style.opacity = '0';
      void p.offsetHeight;
      p.style.transition = '';
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  useLayoutEffect(() => {
    const p = pillRef.current;
    if (!p || phase !== 'open') return;
    if (active === null) {
      p.style.opacity = '0';
      return;
    }
    const jump = instant.current || p.style.opacity !== '1';
    p.style.transitionDuration = jump ? '0ms, 150ms' : '';
    p.style.transform = `translateY(${active * step}px)`;
    p.style.opacity = '1';
    if (instant.current) document.getElementById(id + '-' + active)?.scrollIntoView({ block: 'nearest' });
    instant.current = false;
  }, [active, phase, step]);

  const open = (viaKey: boolean) => {
    if (disabled) return;
    clearTimeout(closeTimer.current);
    instant.current = true;
    setActive(selected >= 0 ? selected : viaKey ? 0 : null);
    setPhase('open');
  };
  const close = (mode: 'instant' | 'pop') => {
    setActive(null);
    clearTimeout(closeTimer.current);
    const el = menuRef.current;
    if (mode === 'instant' || !el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setPhase('closed');
      return;
    }
    el.style.transitionDuration = '';
    el.dataset.state = 'closed';
    setPhase('closing');
    closeTimer.current = setTimeout(() => setPhase('closed'), popOut + 20);
  };
  const pick = (i: number, viaKey: boolean) => {
    const it = items[i];
    if (!it) {
      close('instant');
      return;
    }
    if (it.value !== current) {
      if (value === undefined) setInner(it.value);
      onChange?.(it.value, it);
      if (!viaKey && rootRef.current) rootRef.current.dataset.swap = '';
    }
    close('instant');
    triggerRef.current?.focus({ preventScroll: true });
  };

  const onTriggerKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const k = e.key;
    if (phase === 'open' && (k === 'Escape' || k === 'Tab')) {
      if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); consumedEscape.current = true; }
      close('instant');
      return;
    }
    const n = items.length;
    if (!n) return;
    const cur = active ?? Math.max(0, selected);
    if (phase !== 'open') {
      if (k === 'Enter' || k === ' ' || k === 'ArrowDown' || k === 'ArrowUp') {
        e.preventDefault();
        open(true);
      }
      return;
    }
    const go = (i: number) => {
      e.preventDefault();
      instant.current = true;
      setActive(Math.min(n - 1, Math.max(0, i)));
    };
    if (k === 'ArrowDown' || k === 'ArrowUp') go(active === null ? cur : cur + (k === 'ArrowDown' ? 1 : -1));
    else if (k === 'Home' || k === 'End') go(k === 'Home' ? 0 : n - 1);
    else if (k === 'Enter' || k === ' ') {
      e.preventDefault();
      pick(cur, true);
    } else if (k.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) go(typeaheadIndex(items, cur, k));
  };

  useEffect(() => {
    if (phase === 'closed') return undefined;
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close('pop');
    };
    const reposition = () => close('instant');
    const onScroll = (event: Event) => {
      if (!menuRef.current?.contains(event.target as Node)) close('instant');
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', reposition);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', reposition);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);
  useEffect(() => {
    if (disabled && phase !== 'closed') close('instant');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled]);
  useEffect(() => () => clearTimeout(closeTimer.current), []);

  const origin = `${side === 'bottom' ? 'top' : 'bottom'} ${align}`;
  return (
    <div
      ref={rootRef}
      className={`glide-select${className ? ` ${className}` : ''}`}
      data-size={size}
      data-disabled={disabled ? '' : undefined}
      style={
        {
          '--gs-accent': accentColor,
          '--gs-surface': surfaceColor,
          '--gs-highlight': highlightColor,
          '--gs-text': textColor,
          '--gs-radius': `${radius}px`,
          '--gs-inner-radius': `${Math.max(3, radius - 4)}px`,
          '--gs-chip': `${S.chip}px`,
          '--gs-row': `${S.row}px`,
          '--gs-font': `${S.font}px`,
          '--gs-menu-w': `${menuWidth}px`,
          '--gs-pop': `${popDuration}ms`,
          '--gs-pop-out': `${popOut}ms`,
          '--gs-glide': `${glideDuration}ms`,
          '--gs-origin': origin
        } as CSSProperties
      }
      onAnimationEnd={e => {
        if (e.animationName === 'gs-swap' && rootRef.current) delete rootRef.current.dataset.swap;
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={phase === 'open'}
        aria-controls={`${id}-list`}
        aria-activedescendant={active !== null ? `${id}-${active}` : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        className="glide-select__trigger"
        onClick={e => {
          if (disabled) return;
          e.currentTarget.focus({ preventScroll: true });
          if (phase === 'open') close('pop');
          else open(false);
        }}
        onKeyDown={onTriggerKey}
        onKeyUpCapture={e => {
          // Gluestack dismisses its containing dialog on keyup, not keydown.
          if (e.key === 'Escape' && consumedEscape.current) {
            e.stopPropagation(); consumedEscape.current = false;
          }
        }}
      >
        <span className="glide-select__label" key={current} data-empty={selected < 0 ? '' : undefined}>
          {selected >= 0 ? items[selected].label : placeholder}
        </span>
        <span className="glide-select__chevron" aria-hidden="true">
          <ChevronDown size={14} strokeWidth={2} />
        </span>
      </button>
      {phase !== 'closed' ? (
        <div ref={menuRef} popover="manual" className="glide-select__menu" data-state="open" data-side={side} data-align={align}>
          <div
            id={`${id}-list`}
            role="listbox"
            aria-label={ariaLabel}
            className="glide-select__list"
            data-live={active !== null ? '' : undefined}
            onPointerLeave={() => { if (!rememberPosition) setActive(null); }}

          >
            <span ref={pillRef} className="glide-select__pill" aria-hidden="true" />
            {items.map((it, i) => (
              <div
                key={it.value}
                id={`${id}-${i}`}
                role="option"
                aria-selected={i === selected}
                data-index={i}
                onPointerEnter={() => { instant.current = false; setActive(i); }}
                onPointerDown={e => e.preventDefault()}
                onClick={() => pick(i, false)}
                className="glide-select__option"
              >
                <span className="glide-select__name">{it.label}</span>
                {showTags && it.tag ? <span className="glide-select__tag">{it.tag}</span> : null}
                <span className="glide-select__check" data-on={i === selected ? '' : undefined} aria-hidden="true">
                  <Check size={14} strokeWidth={2} />
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default GlideSelect;
