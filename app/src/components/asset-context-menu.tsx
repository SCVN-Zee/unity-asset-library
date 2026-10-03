import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMenu, useMenuItem } from "@react-aria/menu";
import { useOverlay } from "@react-aria/overlays";
import { mergeProps } from "@react-aria/utils";
import { useTreeState, type TreeState } from "@react-stately/tree";
import { Item } from "@react-stately/collections";
import type { Node } from "@react-types/shared";
import { motion } from "motion/react";
import { Download, ExternalLink, FolderOpen, Info, Star } from "lucide-react";
import SpotlightCard from "./react-bits/SpotlightCard";
import { useReducedMotion } from "./react-bits/useReducedMotion";
import "./asset-context-menu.css";

export type AssetContextAction = "details" | "favorite" | "store" | "reveal" | "download";
type MenuAction = { id: AssetContextAction; label: string; icon: ReactNode; disabled?: boolean; hint?: string };

function ActionRow({ item, state, instant }: { item: Node<MenuAction>; state: TreeState<MenuAction>; instant: boolean }) {
  const ref = useRef<HTMLButtonElement>(null);
  const { menuItemProps, isFocused, isDisabled } = useMenuItem({ key: item.key }, state, ref);
  const action = item.value!;
  return <li role="none">
    <button {...menuItemProps} ref={ref} type="button" className="asset-context-menu__item" data-focused={isFocused || undefined} data-action={action.id}>
      {isFocused && !isDisabled && <motion.span className="asset-context-menu__glide" layoutId="asset-context-glide" initial={false} transition={{ duration: instant ? 0 : 0.16, ease: [0.23, 1, 0.32, 1] }} aria-hidden="true" />}
      <span className="asset-context-menu__icon" aria-hidden="true">{action.icon}</span>
      <span className="asset-context-menu__label">{action.label}</span>
      {action.hint && <span className="asset-context-menu__hint" aria-hidden="true">{action.hint}</span>}
    </button>
  </li>;
}

export default function AssetContextMenu({ asset, favorited, busy, favoritePending, downloading, x, y, keyboard, onClose, onAction }: {
  asset: Asset; favorited: boolean; busy: boolean; favoritePending: boolean; downloading: boolean;
  x: number; y: number; keyboard: boolean; onClose: () => void; onAction: (action: AssetContextAction) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const reducedMotion = useReducedMotion();
  const [position, setPosition] = useState({ left: x, top: y });
  const versions = asset.versions || [];
  const cloudCount = versions.filter(version => version.file && version.availability === "cloud_only").length;
  const actions: MenuAction[] = [
    { id: "details", label: "View details", icon: <Info />, hint: "↵" },
    { id: "favorite", label: favorited ? "Remove from favorites" : "Add to favorites", icon: <Star fill={favorited ? "currentColor" : "none"} />, disabled: busy || favoritePending },
    { id: "store", label: "Open in Asset Store", icon: <ExternalLink />, disabled: !asset.store },
    { id: "reveal", label: "Reveal in Finder", icon: <FolderOpen />, disabled: !versions.some(version => version.file && version.availability !== "missing") },
    ...(cloudCount ? [{ id: "download" as const, label: downloading ? "Downloading…" : "Download package" + (cloudCount > 1 ? "s" : ""), icon: <Download />, disabled: busy, hint: cloudCount > 1 ? String(cloudCount) : undefined }] : []),
  ];
  const state = useTreeState<MenuAction>({ items: actions, disabledKeys: actions.filter(action => action.disabled).map(action => action.id), children: action => <Item key={action.id} textValue={action.label}>{action.label}</Item> });
  const { menuProps } = useMenu({ "aria-label": `Actions for ${asset.name}`, autoFocus: "first", onAction: key => onAction(key as AssetContextAction), onClose }, state, menuRef);
  const { overlayProps } = useOverlay({ isOpen: true, onClose, isDismissable: true }, ref);
  useLayoutEffect(() => {
    const place = () => {
      const bounds = ref.current!.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(x, window.innerWidth - bounds.width - 8)), top: Math.max(8, Math.min(y, window.innerHeight - bounds.height - 8)) });
    };
    place();
    window.addEventListener("resize", place);
    const closeOnScroll = (event: Event) => { if (!(event.target instanceof Element) || !ref.current?.contains(event.target)) onClose(); };
    document.addEventListener("scroll", closeOnScroll, true);
    return () => { window.removeEventListener("resize", place); document.removeEventListener("scroll", closeOnScroll, true); };
  }, [x, y, onClose]);
  return createPortal(<div {...overlayProps} ref={ref} className="asset-context-menu" data-instant={keyboard || reducedMotion || undefined} style={position} onKeyDownCapture={event => { if (event.key === "Tab") { event.preventDefault(); onClose(); } }}>
    <SpotlightCard className="asset-context-menu__surface" enabled={!reducedMotion}>
      <ul {...mergeProps(menuProps, { onContextMenu: (event: React.MouseEvent) => event.preventDefault() })} ref={menuRef}>
        {[...state.collection].map(item => <ActionRow key={item.key} item={item} state={state} instant={keyboard || reducedMotion} />)}
      </ul>
    </SpotlightCard>
  </div>, document.body);
}
