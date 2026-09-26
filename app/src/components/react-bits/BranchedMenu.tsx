/**
 * Adapted from React Bits Branched Menu (TypeScript + CSS), David Haz.
 * Revision: 5c73f3d065d4c074ae3c5fa66b4918e07fc999fa
 * https://github.com/DavidHDev/react-bits/blob/5c73f3d065d4c074ae3c5fa66b4918e07fc999fa/src/ts-default/Micro/BranchedMenu/BranchedMenu.tsx
 * MIT + Commons Clause; full notice in ./LICENSE.
 * Local changes: arbitrary-depth recursive hierarchy, controlled single/multi
 * selection, independently selectable + collapsible parents, counts, theme
 * tokens, responsive width, inert collapsed subtrees and reduced-motion guards.
 */
import {
  isValidElement,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import "./BranchedMenu.css";

export interface BranchedMenuItem {
  value: string;
  label: string;
  count?: number;
  icon?: ReactNode;
  children?: BranchedMenuItem[];
}

export interface BranchedMenuProps {
  items: BranchedMenuItem[];
  /** Controlled selection. Single mode expects zero or one value; multi accepts any set. */
  selectedValues: string[];
  onSelect: (value: string) => void;
  /** Multi mode: rows report state with aria-pressed instead of aria-current. */
  multi?: boolean;
  ariaLabel?: string;
  className?: string;
  /** Optional pixel cap; the menu always stays within its parent's width. */
  width?: number;
  rowHeight?: number;
  indent?: number;
  trunk?: number;
  radius?: number;
  lineWidth?: number;
  fontSize?: number;
  color?: string;
  accentColor?: string;
  lineColor?: string;
  drawDuration?: number;
  foldDuration?: number;
}

const PAD = 6;
const MARK = 16;
const EMPTY_CHILDREN: BranchedMenuItem[] = [];

const descendantSelected = (item: BranchedMenuItem, selected: string[]): boolean =>
  selected.includes(item.value) || (item.children ?? []).some((kid) => descendantSelected(kid, selected));

interface Geometry {
  height: number;
  /** Measured center y of each immediate child row. */
  centers: Map<string, number>;
}

const EMPTY_GEOMETRY: Geometry = { height: PAD * 2, centers: new Map() };

/**
 * Measures the immediate child rows of one expanded parent and returns the
 * connector geometry. One ResizeObserver per expanded tree (not per item, and
 * no per-frame work): it only fires when a nested fold changes the tree size.
 */
function useTreeGeometry(
  treeRef: RefObject<HTMLDivElement | null>,
  rowRefs: RefObject<(HTMLDivElement | null)[]>,
  open: boolean,
  children: BranchedMenuItem[],
): Geometry {
  const [geometry, setGeometry] = useState<Geometry>(EMPTY_GEOMETRY);

  useLayoutEffect(() => {
    if (!open) {
      setGeometry(EMPTY_GEOMETRY);
      return;
    }
    const measure = () => {
      const wrappers = rowRefs.current.slice(0, children.length);
      if (!wrappers.length || wrappers.some(wrapper => !wrapper)) return;
      const centers = new Map<string, number>();
      wrappers.forEach((wrapper, index) => {
        const head = wrapper!.firstElementChild as HTMLDivElement;
        centers.set(children[index].value, wrapper!.offsetTop + head.offsetHeight / 2);
      });
      const last = wrappers[wrappers.length - 1]!;
      const height = last.offsetTop + (last.firstElementChild as HTMLDivElement).offsetHeight + PAD;
      setGeometry(previous => previous.height === height && previous.centers.size === centers.size &&
        [...centers].every(([value, y]) => previous.centers.get(value) === y) ? previous : { height, centers });
    };
    measure();
    // Nested sections folding/unfolding resize this tree; remeasure on settle.
    const observer = new ResizeObserver(measure);
    if (treeRef.current) observer.observe(treeRef.current);
    return () => observer.disconnect();
  }, [open, children, treeRef, rowRefs]);

  return geometry;
}

/** Upstream connector SVG: shared trunk, rounded branch elbows, accent draw-in. */
function TreeLines({
  kids,
  selectedValues,
  geometry,
  rowHeight,
  indent,
  trunk,
  radius,
}: {
  kids: BranchedMenuItem[];
  selectedValues: string[];
  geometry: Geometry;
  rowHeight: number;
  indent: number;
  trunk: number;
  radius: number;
}) {
  const r = Math.min(radius, rowHeight / 2 - 2);
  const endX = indent - 8;
  const centers = geometry.centers;
  const lastY = centers.get(kids[kids.length - 1]?.value) ?? PAD + rowHeight / 2;
  const elbow = (y: number) => `M ${trunk} ${y - r} A ${r} ${r} 0 0 0 ${trunk + r} ${y} H ${endX}`;
  const reach = (y: number) => `M ${trunk} 0 V ${y - r} A ${r} ${r} 0 0 0 ${trunk + r} ${y} H ${endX}`;
  const length = (y: number) => y - r + (Math.PI * r) / 2 + (endX - trunk - r);

  return (
    <svg
      className="branched-menu__lines"
      width={indent}
      height={Math.max(geometry.height, PAD * 2 + kids.length * rowHeight)}
      aria-hidden="true"
    >
      {centers.size > 0 && (
        <>
          <path className="branched-menu__base" d={`M ${trunk} 0 V ${lastY - r}`} />
          {kids.map(kid => {
            const y = centers.get(kid.value);
            return y === undefined ? null : <path key={kid.value} className="branched-menu__base" d={elbow(y)} />;
          })}
          {kids.map(kid => {
            const y = centers.get(kid.value);
            if (y === undefined) return null;
            const len = length(y);
            return (
              <path
                key={kid.value}
                className="branched-menu__reach"
                d={reach(y)}
                style={{
                  strokeDasharray: len,
                  strokeDashoffset: descendantSelected(kid, selectedValues) ? 0 : len,
                }}
              />
            );
          })}
        </>
      )}
    </svg>
  );
}

interface BranchProps {
  item: BranchedMenuItem;
  depth: number;
  open: Set<string>;
  toggle: (value: string) => void;
  selectedValues: string[];
  onSelect: (value: string) => void;
  multi: boolean;
  rowHeight: number;
  indent: number;
  trunk: number;
  radius: number;
  /** Registers this branch's wrapper as one of the parent's measured rows. */
  registerRow?: (el: HTMLDivElement | null) => void;
  /** Depth 0 only: registers the head row for the glide marker. */
  registerHead?: (el: HTMLButtonElement | null) => void;
}

function Branch({
  item,
  depth,
  open,
  toggle,
  selectedValues,
  onSelect,
  multi,
  rowHeight,
  indent,
  trunk,
  radius,
  registerRow,
  registerHead,
}: BranchProps) {
  const kids = item.children ?? EMPTY_CHILDREN;
  const hasChildren = kids.length > 0;
  const isOpen = hasChildren && open.has(item.value);
  const selected = selectedValues.includes(item.value);
  const childRefs = useRef<(HTMLDivElement | null)[]>([]);
  const treeRef = useRef<HTMLDivElement | null>(null);
  const geometry = useTreeGeometry(treeRef, childRefs, isOpen, kids);

  const selectItem = () => {
    onSelect(item.value);
    if (hasChildren && !open.has(item.value)) toggle(item.value);
  };

  return (
    <div ref={registerRow} className="branched-menu__branch">
      <div className="branched-menu__row">
        {hasChildren ? (
          <button
            type="button"
            className="branched-menu__toggle"
            aria-label={`${isOpen ? "Collapse" : "Expand"} ${item.label}`}
            aria-expanded={isOpen}
            onClick={() => toggle(item.value)}
          >
            {isOpen ? (
              <ChevronDown size={14} strokeWidth={2} aria-hidden="true" focusable={false} />
            ) : (
              <ChevronRight size={14} strokeWidth={2} aria-hidden="true" focusable={false} />
            )}
          </button>
        ) : (
          <span className="branched-menu__toggle-space" aria-hidden="true" />
        )}
        <button
          type="button"
          ref={registerHead}
          className={depth === 0 ? "branched-menu__item branched-menu__head" : "branched-menu__item"}
          title={item.label}
          {...(multi
            ? { "aria-pressed": selected }
            : { "aria-current": selected ? ("true" as const) : undefined })}
          data-active={selected ? "" : undefined}
          onClick={selectItem}
        >
          {item.icon ? (
            <span className="branched-menu__icon" aria-hidden="true">
              {isValidElement(item.icon) ? item.icon : null}
            </span>
          ) : null}
          <span className="branched-menu__label">{item.label}</span>
          {item.count !== undefined ? (
            <span className="branched-menu__count">{item.count.toLocaleString()}</span>
          ) : null}
        </button>
      </div>
      {hasChildren ? (
        <div className="branched-menu__body" data-open={isOpen ? "" : undefined} inert={!isOpen}>
          <div className="branched-menu__fold">
            <div ref={treeRef} className="branched-menu__tree">
              <TreeLines
                kids={kids}
                selectedValues={selectedValues}
                geometry={geometry}
                rowHeight={rowHeight}
                indent={indent}
                trunk={trunk}
                radius={radius}
              />
              {kids.map((kid, k) => (
                <Branch
                  key={kid.value}
                  item={kid}
                  depth={depth + 1}
                  open={open}
                  toggle={toggle}
                  selectedValues={selectedValues}
                  onSelect={onSelect}
                  multi={multi}
                  rowHeight={rowHeight}
                  indent={indent}
                  trunk={trunk}
                  radius={radius}
                  registerRow={(el) => {
                    childRefs.current[k] = el;
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

const BranchedMenu: React.FC<BranchedMenuProps> = ({
  items,
  selectedValues,
  onSelect,
  multi = false,
  ariaLabel,
  className = "",
  width,
  rowHeight = 32,
  indent = 24,
  trunk = 6,
  radius = 8,
  lineWidth = 1.5,
  fontSize = 12,
  color,
  accentColor,
  lineColor,
  drawDuration,
  foldDuration,
}) => {
  // Default all collapsed; selection is fully controlled from outside.
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const navRef = useRef<HTMLElement>(null);
  const headRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const markerRef = useRef<HTMLSpanElement>(null);
  const latest = useRef({ onSelect });
  latest.current = { onSelect };

  const toggle = (value: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  };

  // Glide marker tracks the top-level item holding the selection, including
  // selected top-level leaves that have no expandable section.
  const activeSection = items.findIndex((item) => descendantSelected(item, selectedValues));
  const markerShown = activeSection >= 0;
  useLayoutEffect(() => {
    const place = (glide: boolean) => {
      const marker = markerRef.current;
      const head = headRefs.current[activeSection];
      if (!marker) return;
      const on = markerShown && head;
      if (!glide) marker.style.transition = "none";
      if (on) marker.style.top = `${head!.offsetTop + (head!.offsetHeight - MARK) / 2}px`;
      marker.toggleAttribute("data-on", Boolean(on));
      if (!glide) {
        void marker.offsetHeight;
        marker.style.transition = "";
      }
    };
    place(true);
    const observer = new ResizeObserver(() => place(false));
    if (navRef.current) observer.observe(navRef.current);
    return () => observer.disconnect();
  }, [activeSection, markerShown, items]);

  return (
    <nav
      ref={navRef}
      className={`branched-menu${className ? ` ${className}` : ""}`}
      aria-label={ariaLabel}
      style={
        {
          ...(width !== undefined ? { "--bm-w": `${width}px` } : null),
          ...(color ? { "--bm-ink": color } : null),
          ...(accentColor ? { "--bm-accent": accentColor } : null),
          ...(lineColor ? { "--bm-line": lineColor } : null),
          "--bm-font": `${fontSize}px`,
          "--bm-row": `${rowHeight}px`,
          "--bm-indent": `${indent}px`,
          "--bm-line-w": lineWidth,
          "--bm-draw": `${drawDuration ?? 400}ms`,
          "--bm-fold": `${foldDuration ?? 300}ms`,
        } as CSSProperties
      }
    >
      <span ref={markerRef} className="branched-menu__marker" aria-hidden="true" />
      {items.map((item, i) => (
        <Branch
          key={item.value}
          item={item}
          depth={0}
          open={open}
          toggle={toggle}
          selectedValues={selectedValues}
          onSelect={(value) => latest.current.onSelect?.(value)}
          multi={multi}
          rowHeight={rowHeight}
          indent={indent}
          trunk={trunk}
          radius={radius}
          registerHead={(el) => {
            headRefs.current[i] = el;
          }}
        />
      ))}
    </nav>
  );
};

export default BranchedMenu;
