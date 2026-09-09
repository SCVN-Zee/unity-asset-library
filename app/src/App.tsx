import appIcon from "../../assets/icons/icon.png";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import {
  Alert,
  AlertDialog,
  AlertDialogBackdrop,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  Button,
  Icon,
  Input,
  InputField,
  Popover,
  PopoverContent,
  Select,
  SelectContent,
  SelectIcon,
  SelectItem,
  SelectPortal,
  SelectInput,
  SelectTrigger,
  Spinner,
} from "./components/ui";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  ExternalLink,
  Folder,
  FolderOpen,
  LayoutGrid,
  List,
  PanelRight,
  Menu,
  Sun,
  Moon,
  Monitor,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Star,
  X,
} from "lucide-react";
import { ImportDialog, unityPackages } from "./components/ImportDialog";

type QuickFilter = "all" | "favorites" | "pending" | "flagged" | "non-store";
type ViewMode = "grid" | "list";
type SortMode = "name" | "author" | "size";
type DialogState = {
  mode: "resync" | "cleanup" | "organize";
  title: string;
  result: ActionResult;
} | null;

const api = window.ual;

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function assetSize(asset: Asset) {
  return (asset.versions || []).reduce(
    (sum, version) => sum + Number(version.size_bytes || 0),
    0,
  );
}

function isPending(asset: Asset) {
  return !asset.non_store && asset.resolution?.id_verified !== true;
}


function isValidUserTags(value: unknown): value is UserTags {
  if (!value || typeof value !== "object") return false;
  const candidate = value as UserTags;
  const validTags = (tags: unknown): tags is string[] => Array.isArray(tags) && tags.every((tag) => typeof tag === "string" && tag.length > 0 && tag === tag.trim().toLowerCase());
  return validTags(candidate.tags) && Boolean(candidate.assignments) && typeof candidate.assignments === "object" && !Array.isArray(candidate.assignments) && Object.values(candidate.assignments).every((tags) => validTags(tags) && tags.every((tag) => candidate.tags.includes(tag)));
}

type FilterState = {
  query: string;
  category: string;
  quick: QuickFilter;
  author: string;
  selectedTags: string[];
  tagMode: "all" | "any";
  untagged: boolean;
};

const DEFAULT_FILTERS: FilterState = {
  query: "",
  category: "All assets",
  quick: "all",
  author: "",
  selectedTags: [],
  tagMode: "all",
  untagged: false,
};

function loadFilters(): FilterState {
  try {
    const saved = JSON.parse(localStorage.getItem("ual:filters") || "null") as Partial<FilterState> | null;
    if (!saved || typeof saved !== "object") return DEFAULT_FILTERS;
    return {
      query: typeof saved.query === "string" ? saved.query : "",
      category: typeof saved.category === "string" && saved.category ? saved.category : "All assets",
      quick: (["all", "favorites", "pending", "flagged", "non-store"] as const).includes(saved.quick as QuickFilter) ? (saved.quick as QuickFilter) : "all",
      author: typeof saved.author === "string" ? saved.author : "",
      selectedTags: Array.isArray(saved.selectedTags) ? [...new Set(saved.selectedTags.filter((tag): tag is string => typeof tag === "string" && !!tag.trim()).map((tag) => tag.trim().toLowerCase()))] : [],
      tagMode: saved.tagMode === "any" ? "any" : "all",
      untagged: saved.untagged === true,
    };
  } catch {
    return DEFAULT_FILTERS;
  }
}

// Search tier: 0 = exact name, 1 = every word in name, 2 = every word in name/local_name,
// 3 = every word across name/local_name/author/tags. null = excluded. Existing sort breaks ties.
function searchTier(asset: Asset, words: string[], exact: string): number | null {
  if (!words.length) return 0;
  const name = asset.name.toLowerCase();
  const local = (asset.local_name || "").toLowerCase();
  const author = (asset.author || "").toLowerCase();
  const tags = (asset.tags || []).join(" ").toLowerCase();
  if (name === exact) return 0;
  if (words.every((word) => name.includes(word))) return 1;
  if (words.every((word) => name.includes(word) || local.includes(word))) return 2;
  if (words.every((word) => name.includes(word) || local.includes(word) || author.includes(word) || tags.includes(word))) return 3;
  return null;
}

function initials(asset: Asset) {
  return (asset.name || asset.local_name || "Asset")
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

type CategoryNode = {
  name: string;
  path: string;
  count: number;
  children: Map<string, CategoryNode>;
};

function CategoryBranch({
  node,
  selected,
  onSelect,
}: {
  node: CategoryNode;
  selected: string;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = node.children.size > 0;
  return (
    <li>
      <div className={`category-row ${selected === node.path ? "active" : ""}`}>
        {hasChildren ? (
          <Button
            type="button"
            className="category-toggle"
            aria-label={`${expanded ? "Collapse" : "Expand"} ${node.path}`}
            aria-expanded={expanded}
            onPress={() => setExpanded(!expanded)}
          >
            <Icon
              as={expanded ? ChevronDown : ChevronRight}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
          </Button>
        ) : (
          <span className="category-toggle-space" />
        )}
        <Button
          type="button"
          className={`nav-row ${selected === node.path ? "active" : ""}`}
          title={node.path}
          aria-current={selected === node.path ? "true" : undefined}
          onPress={() => {
            onSelect(node.path);
            if (hasChildren) setExpanded(true);
          }}
        >
          <span className="category-name">
            <Icon
              as={Folder}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
            <span>{node.name}</span>
          </span>
          <span className="nav-count">{node.count}</span>
        </Button>
      </div>
      {hasChildren && (
        <ul hidden={!expanded}>
          {[...node.children.values()]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((child) => (
              <CategoryBranch
                key={child.path}
                node={child}
                selected={selected}
                onSelect={onSelect}
              />
            ))}
        </ul>
      )}
    </li>
  );
}
type ProgressProps = {
  job: ProgressSnapshot & { id: string; status: string; kind?: string; phase?: string; error?: string | null; error_code?: string | null; result?: ActionResult | null };
  onDismiss?: (id: string) => void;
  announce?: boolean;
};

const PROGRESS_STAGE_LABELS: Record<string, string> = {
  queued: "Waiting to start", waiting: "Waiting to start", starting: "Starting",
  "waiting-for-lock": "Waiting for another operation",
  scan: "Scanning files", build: "Building index", compare: "Comparing changes",
  prepare: "Preparing changes", validate: "Validating files",
  stage: "Staging files for cleanup", move: "Moving files", remove: "Removing files",
  rollback: "Rolling back changes", refresh: "Refreshing index",
  resolve: "Looking up store details", enrich: "Merging store details", merge: "Merging store details",
  "refreshing index": "Refreshing index", "index refreshed": "Index refreshed",
  "index refresh failed": "Index refresh failed",
  import: "Importing packages", install: "Installing import bridge", stopping: "Stopping after current package",
  unknown: "Outcome unknown", "outcome unknown": "Outcome unknown",
};

const PROGRESS_COUNT_LABELS: Record<string, string> = { moved: "files moved", skipped: "files skipped", staged: "files staged", removed: "files deleted", rolled_back: "files rolled back", rollback_failed: "rollback failures", resolved: "resolved", failed: "failed", blocked: "blocked", imported: "packages imported", cancelled: "packages cancelled", index_added: "index entries added", index_removed: "index entries removed", index_resized: "index entries resized", inventory_examined: "inventory examined", inventory_found: "inventory found" };

function ActionProgress({ job, onDismiss, announce = true }: ProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!["queued", "running"].includes(job.status) || !job.started_at) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job.status, job.started_at]);
  const elapsed = Math.max(0, (job.finished_at ?? now / 1000) - (job.started_at ?? now / 1000));
  const total = typeof job.total === "number" && job.total >= 0 ? job.total : null;
  const completed = Math.max(0, job.completed ?? 0);
  const percent = total === null ? undefined : total === 0 ? 0 : Math.min(100, Math.round((completed / total) * 100));
  const terminal = ["completed", "failed", "cancelled"].includes(job.status);
  const counts = job.counts || {};
  const actionLabel = job.kind === "import" ? "Import" : job.kind === "resync" ? "Resync" : job.kind === "cleanup" ? "Cleanup" : job.kind === "organize" ? "Organize" : "Enrich";
  const phaseLabel = job.kind === "enrich" || job.kind === "import" ? "" : " · " + (job.phase === "apply" ? "Apply" : "Preview");
  const title = actionLabel + phaseLabel;
  const rawStage = (job.stage || (job.status === "queued" ? "waiting" : "working")).toLowerCase();
  const outcome = job.status === "completed" ? "Completed" : job.status === "cancelled" ? "Cancelled" : job.status === "failed" ? "Failed" : job.status === "queued" ? "Queued" : "In progress";
  const stage = PROGRESS_STAGE_LABELS[rawStage] ?? job.stage ?? "Working";
  const unknownProgress = rawStage.includes("scan") ? completed + " files found" : completed + " processed · total unknown";
  const result = job.result as ActionResult | null | undefined;
  const refreshFailed = result?.applied === true && result?.state_refreshed === false;
  const rollbackIncomplete = result?.rollback_incomplete === true;
  const displayCounts = Object.entries(counts).filter(([key]) => Boolean(PROGRESS_COUNT_LABELS[key]) || key.startsWith("index_") || key.startsWith("inventory_"));
  const guidance = rollbackIncomplete ? "Residual files may need manual attention." : refreshFailed ? "Verify the library before trying again." : "";
  return (
    <section className={["progress-panel", terminal ? "terminal" : "", job.status === "failed" ? "failed" : ""].join(" ")} data-progress-id={job.id} aria-label={title + " progress"}>
      <div className="progress-heading">
        <div><strong>{title}</strong><span className="progress-status">{outcome}</span></div>
        {terminal && onDismiss && <Button type="button" className="icon-button" onPress={() => onDismiss(job.id)} aria-label={"Dismiss " + title + " result"}><Icon as={X} className="icon" aria-hidden="true" focusable={false} /></Button>}
      </div>
      <div className="progress-stage"><span aria-live={announce ? "polite" : undefined}>{stage}</span><time>{Math.floor(elapsed / 60)}:{String(Math.floor(elapsed % 60)).padStart(2, "0")}</time></div>
      {!(terminal && total === null) && <div className="progress-track"><progress value={percent} max="100" aria-label={title + " stage progress"} />{percent === undefined && !terminal && <span className="progress-indeterminate" aria-hidden="true" />}</div>}
      {(total !== null || !terminal || job.current_item) && <div className="progress-meta">{total !== null && <span>{total === 0 ? "No items in this stage" : completed + " of " + total + " in this stage"}{total !== 0 && percent !== undefined ? " · " + percent + "%" : ""}</span>}{total === null && !terminal && <span>{unknownProgress}</span>}{job.current_item && <span className="progress-current" title={job.current_item}>Current: {job.current_item}</span>}</div>}
      <div className="progress-counts">{displayCounts.filter(([, value]) => typeof value === "number").map(([key, value]) => <span key={key}><strong>{value}</strong> {key === "skipped" && job.kind === "enrich" ? "assets skipped" : PROGRESS_COUNT_LABELS[key] || key.replaceAll("_", " ")}</span>)}</div>
      {(job.error || job.error_code || guidance) && <p className="progress-error">{rollbackIncomplete && <strong>Rollback incomplete. </strong>}{refreshFailed && <strong>Changes applied, but index refresh failed. </strong>}{job.error && <span>{job.error} </span>}{guidance && <span>{guidance}</span>}{!job.error && !guidance && job.error_code}</p>}
    </section>
  );
}
type StorageView = "loading" | "onboarding" | "settings" | "library";

function StorageProgress({ progress }: { progress: ProgressSnapshot | null }) {
  if (!progress) return null;
  const total = typeof progress.total === "number" && progress.total > 0 ? progress.total : null;
  const completed = Math.max(0, progress.completed ?? 0);
  const percent = total === null ? undefined : Math.min(100, Math.round((completed / total) * 100));
  return (
    <section className="storage-progress" aria-live="polite" aria-label="Storage setup progress">
      <div className="progress-heading"><strong>{progress.stage || "Preparing library"}</strong><span>{percent === undefined ? "Working…" : `${percent}%`}</span></div>
      <div className="progress-track"><progress value={percent} max="100" aria-label="Storage setup progress" />{percent === undefined && <span className="progress-indeterminate" aria-hidden="true" />}</div>
      <div className="progress-meta"><span>{total === null ? `${completed} items processed` : `${completed} of ${total} items`}</span>{progress.current_item && <span className="progress-current" title={progress.current_item}>{progress.current_item}</span>}</div>
    </section>
  );
}

function StorageScreen({
  mode,
  storage,
  path,
  error,
  busy,
  blocked,
  onPathChange,
  onBrowse,
  onSave,
  onCancel,
  onRetry,
}: {
  mode: "loading" | "onboarding" | "settings";
  storage: StorageState | null;
  path: string;
  error: string;
  busy: boolean;
  blocked: boolean;
  onPathChange: (path: string) => void;
  onBrowse: () => void;
  onSave: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const [pathTouched, setPathTouched] = useState(false);
  if (mode === "loading") {
    return <div className="storage-shell"><div className="storage-loading"><div><Spinner className="spinner" aria-label="Loading storage configuration" /><span>Checking your library…</span>{storage?.busy && <StorageProgress progress={storage.progress} />}</div></div></div>;
  }
  const onboarding = mode === "onboarding";
  const pathError = pathTouched && !path.trim() ? "Choose a library folder to continue." : "";
  const displayedError = pathError || error || storage?.error || "";
  const saveLabel = onboarding ? "Save & scan library" : "Save & reload library";
  const blockedByExternalBackend = storage?.canChange === false && !busy;
  const handlePathChange = (nextPath: string) => { setPathTouched(true); onPathChange(nextPath); };
  const handleSave = () => { setPathTouched(true); onSave(); };
  return (
    <div className="storage-shell">
      <header className="storage-topbar">
        <div className="brand"><img className="brand-mark" src={appIcon} alt="" draggable={false} /><div><div className="eyebrow">UNITY ASSET LIBRARY</div><h1>{onboarding ? "Set up your library" : "Settings"}</h1></div></div>
        {storage?.ready && <Button type="button" className="button quiet" onPress={onCancel} isDisabled={busy}><Icon as={ArrowLeft} className="icon" aria-hidden="true" focusable={false} />Back to library</Button>}
      </header>
      <main className="storage-main">
        <section className="storage-card" aria-labelledby="storage-heading">
          <div className="storage-kicker">{onboarding ? "WELCOME" : "LIBRARY SETTINGS"}</div>
          <h2 id="storage-heading">{onboarding ? "Where should Asset Library look?" : "Library location"}</h2>
          <p className="storage-lead">{onboarding ? "Choose a folder containing your Unity asset packages. We’ll scan packages and subfolders, then keep the index up to date." : "Change the folder used for your asset index. The current library stays untouched until the new folder is ready."}</p>
          <label className="storage-label" htmlFor="storage-path">Library folder</label>
          <div className={`storage-input-row ${displayedError ? "invalid" : ""}`}>
            <Input className="storage-input"><Icon as={Folder} className="icon" aria-hidden="true" focusable={false} /><InputField id="storage-path" value={path} onChangeText={handlePathChange} placeholder="/Users/you/Assets" aria-label="Library folder" aria-invalid={Boolean(displayedError)} aria-describedby={displayedError ? "storage-help storage-error" : "storage-help"} disabled={busy || blocked} /></Input>
            <Button type="button" className="button" onPress={onBrowse} isDisabled={busy || blocked} aria-label="Browse for library folder"><Icon as={FolderOpen} className="icon" aria-hidden="true" focusable={false} />Browse</Button>
          </div>
          <p id="storage-help" className="storage-help">The full path is stored locally. Existing files are never moved or deleted.</p>
          {displayedError && <p id="storage-error" className="storage-error" role="alert">{displayedError}</p>}
          {blocked && !busy && <p className="storage-notice" role="status">{blockedByExternalBackend ? "Another Unity Asset Library backend is using this library. Stop it before changing folders." : "Library activity is in progress. You can change this folder when it finishes."}</p>}
          {busy && <StorageProgress progress={storage?.progress ?? null} />}
          <div className="storage-actions">
            {!onboarding && <Button type="button" className="button quiet" onPress={onCancel} isDisabled={busy}>Cancel</Button>}
            {displayedError && !busy && <Button type="button" className="button quiet" onPress={storage?.ready ? handleSave : onRetry} isDisabled={blocked}>Retry</Button>}
            <Button type="button" className="button primary" onPress={handleSave} isDisabled={busy || blocked || !path.trim()}>{busy ? "Saving…" : saveLabel}<Icon as={ArrowRight} className="icon" aria-hidden="true" focusable={false} /></Button>
          </div>
        </section>
        <p className="storage-footnote">{onboarding ? "You can change this later from Settings." : storage?.path ? <>Current folder: <code title={storage.path}>{storage.path}</code></> : "No library folder is configured."}</p>
      </main>
    </div>
  );
}

function PortRecoveryScreen({ request, onStorage }: { request: PortRecovery; onStorage: (state: StorageState) => void }) {
  const [port, setPort] = useState(String(request.suggestion));
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const busy = sending || !request.awaiting;
  const processName = request.owner?.name || "this process";
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  async function answer(choice: PortChoice) {
    if (busy) return;
    setSending(true);
    setError("");
    try {
      await api.answerPortChoice(request.id, choice);
      onStorage(await api.getStorage());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not send your choice. Try again.");
      setSending(false);
    }
  }
  return <div className="storage-shell port-recovery">
    <header className="storage-topbar"><div className="brand"><img className="brand-mark" src={appIcon} alt="" draggable={false} /><div><div className="eyebrow">UNITY ASSET LIBRARY</div><h1>Open your library</h1></div></div></header>
    <main className="storage-main">
      <section className="storage-card" aria-labelledby="port-heading" aria-busy={busy}>
        <h2 id="port-heading" ref={heading} tabIndex={-1}>{request.confirm ? `Stop ${processName}?` : `Port ${request.port} is busy`}</h2>
        <p className="storage-lead">{request.confirm ? <>Stopping <strong>{processName}</strong> will free this port for Asset Library. It may interrupt another app or lose unsaved work.</> : <>Another app is using this port. Use a different one to open your library without interrupting it.</>}</p>
        {request.owner ? <dl className="port-process"><div><dt>Process</dt><dd>{processName}</dd></div><div><dt>Process ID</dt><dd>{request.owner.pid}</dd></div><div><dt>Port</dt><dd>{request.port}</dd></div></dl> : <p className="storage-help">We couldn’t identify the app, so stopping it here isn’t available.</p>}
        {request.confirm && <p className="storage-help">We won’t force it to stop. If it stays open, you can use another port.</p>}
        {!request.confirm && <form onSubmit={(event) => { event.preventDefault(); void answer({ action: "use", port: Number(port) }); }}>
          <label className="storage-label" htmlFor="server-port">Use a different port</label>
          <div className="storage-input-row"><div className="storage-input"><input id="server-port" type="number" min="1" max="65535" step="1" required value={port} onChange={(event) => setPort(event.target.value)} disabled={busy} aria-describedby="port-help port-error" /></div><Button type="submit" className="button primary" isDisabled={busy}>Use this port</Button></div>
          <p className="storage-help" id="port-help">We’ll use this port until you close Asset Library.</p>
        </form>}
        <p id="port-error" className="storage-error" role="alert">{error || (!request.confirm ? request.detail : "")}</p>
        {busy && <p className="storage-help" role="status">{request.confirm ? `Waiting for ${processName} to stop…` : "Checking the port…"}</p>}
        <div className="storage-actions">
          <Button type="button" className="button quiet" onPress={() => void answer({ action: "cancel" })} isDisabled={busy}>{request.confirm ? "Keep it running" : "Cancel"}</Button>
          {request.owner && <Button type="button" className="button" onPress={() => void answer({ action: request.confirm ? "confirm-stop" : "stop" })} isDisabled={busy}>{request.confirm ? "Stop and continue" : "Review stop option"}</Button>}
        </div>
      </section>
    </main>
  </div>;
}

function App() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [state, setState] = useState<State | null>(null);
  const [storage, setStorage] = useState<StorageState | null>(null);
  const [storageView, setStorageView] = useState<StorageView>("loading");
  const [storagePath, setStoragePath] = useState("");
  const [storageError, setStorageError] = useState("");
  const [storageBusy, setStorageBusy] = useState(false);
  const storageEpochRef = useRef(0);
  const [storageEpoch, setStorageEpoch] = useState(0);
  const [actionStatus, setActionStatus] = useState<ActionJob | null>(null);
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const savedFilters = useMemo(loadFilters, []);
  const [query, setQuery] = useState(savedFilters.query);
  const [category, setCategory] = useState(savedFilters.category);
  const [quick, setQuick] = useState<QuickFilter>(savedFilters.quick);
  const [author, setAuthor] = useState(savedFilters.author);
  const [selectedTags, setSelectedTags] = useState<string[]>(savedFilters.selectedTags);
  const [tagMode, setTagMode] = useState<"all" | "any">(savedFilters.tagMode);
  const [untagged, setUntagged] = useState(savedFilters.untagged);
  const [sort, setSort] = useState<SortMode>(() => (localStorage.getItem("ual:sort") as SortMode) || "name");
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem("ual:view") as ViewMode) || "grid");
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth >= 1000);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    const saved = localStorage.getItem("ual:theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  });
  const searchRef = useRef<HTMLInputElement>(null);
  const detailsToggleRef = useRef<HTMLButtonElement>(null);
  const detailsCloseRef = useRef<HTMLButtonElement>(null);
  const inspectorWasOpen = useRef(inspectorOpen);
  useEffect(() => {
    if (inspectorOpen && window.innerWidth < 768) detailsCloseRef.current?.focus();
    else if (!inspectorOpen && inspectorWasOpen.current) detailsToggleRef.current?.focus();
    inspectorWasOpen.current = inspectorOpen;
  }, [inspectorOpen]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("ual:theme", theme);
  }, [theme]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [enrichCancelling, setEnrichCancelling] = useState(false);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [importSelection, setImportSelection] = useState<Set<string>>(() => new Set());
  const [importOpen, setImportOpen] = useState(false);
  const [importJob, setImportJob] = useState<ImportJob | null>(null);
  const importTriggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (storageView !== "library" || dialog || actionsOpen) return;
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, [storageView, dialog, actionsOpen]);
  const actionTriggerRef = useRef<HTMLButtonElement>(null);
  const actionRequestRef = useRef(false);
  const uiRefreshRef = useRef(false);
  const actionGenerationRef = useRef(0);
  const dismissedRef = useRef(dismissedIds);
  dismissedRef.current = dismissedIds;
  const importActive = Boolean(importJob && ["queued", "running"].includes(importJob.status));
  useEffect(() => {
    setImportSelection((current) => {
      const next = new Set([...current].filter((key) => assets.some((asset) => asset.asset_key === key && unityPackages(asset).length > 0)));
      return next.size === current.size ? current : next;
    });
  }, [assets]);
  useEffect(() => {
    setImportSelection(new Set());
    setImportOpen(false);
  }, [storageEpoch]);
  useEffect(() => {
    if (storageView !== "library") return;
    let cancelled = false;
    api.getImport().then((job) => { if (!cancelled && job) setImportJob(job); }).catch(() => {});
    return () => { cancelled = true; };
  }, [storageView, storageEpoch]);
  useEffect(() => {
    if (!importJob || !["queued", "running"].includes(importJob.status)) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      api.getImport().then((next) => { if (!cancelled) setImportJob(next); }).catch(() => {});
    }, 600);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [importJob?.id, importJob?.status]);
  const [favorites, setFavorites] = useState<string[]>([]);
  const [favPending, setFavPending] = useState<Set<string>>(() => new Set());
  const favEpochRef = useRef(new Map<string, number>());
  const favRevRef = useRef(0);
  const [userTags, setUserTags] = useState<UserTags | null>(null);
  const [tagError, setTagError] = useState("");
  const [tagBusy, setTagBusy] = useState(false);
  const tagBusyRef = useRef(false);
  const tagRevRef = useRef(0);
  useEffect(() => {
    if (!actionsOpen) return;
    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest(".top-actions, #library-actions")
      ) {
        return;
      }
      setActionsOpen(false);
    }
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
    };
  }, [actionsOpen]);
  async function load() {
    const epoch = storageEpochRef.current;
    let loaded = false;
    let storageReady = false;
    try {
      setError("");
      let nextStorage = await api.getStorage();
      while (nextStorage.busy) {
        if (epoch !== storageEpochRef.current) return false;
        setStorage(nextStorage);
        setStoragePath((current) => current || nextStorage.path || "");
        await new Promise<void>((resolve) => window.setTimeout(resolve, 450));
        nextStorage = await api.getStorage();
      }
      if (epoch !== storageEpochRef.current) return false;
      setStorage(nextStorage);
      setStoragePath((current) => current || nextStorage.path || "");
      if (!nextStorage.ready) {
        setAssets([]);
        setSelectedKey(null);
        setState(null);
        setStorageView(nextStorage.needsSetup ? "onboarding" : "settings");
        return false;
      }
      setStorageView("library");
      storageReady = true;
      const favRev = favRevRef.current;
      const tagRev = tagRevRef.current;
      const [nextState, nextIndex, nextFavorites, nextTags] = await Promise.all([api.getState(), api.getAssets(), api.favorites(), api.getTags()]);
      if (!isValidUserTags(nextTags)) throw new Error("The tag store returned an unreadable response.");
      if (epoch !== storageEpochRef.current) return false;
      setState(nextState);
      if (favRev === favRevRef.current) setFavorites(nextFavorites.favorites || []);
      if (tagRev === tagRevRef.current) { setUserTags(nextTags); setTagError(""); }
      const saved = sessionStorage.getItem("ual:action");
      let savedIdentity: { id: string; kind: "resync" | "cleanup" | "organize"; phase: "plan" | "apply" } | null = null;
      try { if (saved) savedIdentity = JSON.parse(saved); } catch { sessionStorage.removeItem("ual:action"); }
      if (!uiRefreshRef.current && nextState.action && !dismissedRef.current.has(nextState.action.id)) setActionStatus(nextState.action);
      else if (!uiRefreshRef.current && savedIdentity && (!nextState.action || nextState.action.id !== savedIdentity.id)) setActionStatus({ id: savedIdentity.id, kind: savedIdentity.kind, phase: savedIdentity.phase, status: "running", stage: "Recovering operation", completed: 0, total: null, current_item: null, counts: {} });
      setAssets(nextIndex.assets || []);
      setSelectedKey((current) => current && nextIndex.assets.some((asset) => asset.asset_key === current) ? current : nextIndex.assets[0]?.asset_key || null);
      loaded = true;
    } catch (cause) {
      if (epoch === storageEpochRef.current) {
        if (!storageReady) setStorageView("settings");
        setTagError("Library refresh failed. Retry before editing tags.");
        setError(cause instanceof Error ? cause.message : "The backend could not be reached.");
      }
    } finally {
      if (epoch === storageEpochRef.current) setLoading(false);
    }
    return loaded;
  }

  const libraryActivity = Boolean(busy) || importActive || Boolean(state?.job && ["queued", "running"].includes(state.job.status)) || Boolean(actionStatus && ["queued", "running"].includes(actionStatus.status));
  const storageChangeBlocked = storageBusy || storage?.canChange === false || libraryActivity || tagBusy;

  async function chooseStorageFolder() {
    if (storageChangeBlocked) return;
    try {
      const selected = await api.chooseStorageFolder(storagePath || undefined);
      if (selected) {
        setStoragePath(selected);
        setStorageError("");
      }
    } catch (cause) {
      setStorageError(cause instanceof Error ? cause.message : "The folder picker could not be opened.");
    }
  }
  function selectVisibleImport() {
    setImportSelection((current) => {
      const next = new Set(current);
      for (const asset of filtered) if (unityPackages(asset).length > 0) next.add(asset.asset_key);
      return next;
    });
  }
  function toggleImportSelection(key: string) {
    setImportSelection((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  async function startImport(request: ImportRequest) {
    const job = await api.startImport(request);
    setImportJob(job);
  }
  async function stopImport() {
    if (!importJob) return;
    try {
      setImportJob(await api.stopImport(importJob.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not stop the import.");
    }
  }
  function dismissImportJob(id: string) {
    setDismissedIds((current) => new Set(current).add(id));
  }

  async function saveStorageRoot() {
    const nextPath = storagePath.trim();
    if (!nextPath || storageChangeBlocked) {
      setStorageError("Choose a library folder to continue.");
      return;
    }
    const epoch = storageEpochRef.current;
    setStorageBusy(true);
    setStorageError("");
    let stopped = false;
    const poll = async () => {
      if (stopped || epoch !== storageEpochRef.current) return;
      try {
        const next = await api.getStorage();
        if (!stopped && epoch === storageEpochRef.current) setStorage(next);
      } catch (cause) {
        if (!stopped && epoch === storageEpochRef.current) setStorageError(cause instanceof Error ? cause.message : "Could not read library setup progress.");
      }
    };
    const timer = window.setInterval(() => void poll(), 450);
    try {
      const nextStorage = await api.saveStorage(nextPath);
      stopped = true;
      window.clearInterval(timer);
      if (epoch !== storageEpochRef.current) return;
      if (!nextStorage.ready) throw new Error(nextStorage.error || "The new library folder is not ready.");
      storageEpochRef.current += 1;
      setStorageEpoch((current) => current + 1);
      setStorage(nextStorage);
      setStoragePath(nextStorage.path || nextPath);
      setStorageView("library");
      setStorageError("");
      setAssets([]);
      setSelectedKey(null);
      setQuery("");
      setCategory("All assets");
      setQuick("all");
      setAuthor("");
      setSelectedTags([]);
      setTagMode("all");
      setUntagged(false);
      setUserTags(null);
      setTagError("");
      setState(null);
      setActionStatus(null);
      setDialog(null);
      setDismissedIds(new Set());
      sessionStorage.removeItem("ual:action");
      actionGenerationRef.current += 1;
      setLoading(true);
      await load();
    } catch (cause) {
      stopped = true;
      window.clearInterval(timer);
      const message = cause instanceof Error ? cause.message : "The library could not be saved.";
      setStorageError(message);
      try {
        const authoritative = await api.getStorage();
        if (epoch === storageEpochRef.current) setStorage(authoritative);
      } catch (refreshCause) {
        const refreshMessage = refreshCause instanceof Error ? refreshCause.message : "Could not refresh the previous library state.";
        setStorage((current) => current ? { ...current, busy: false, progress: null, error: message } : current);
        setStorageError(`${message} ${refreshMessage}`);
      }
    } finally {
      stopped = true;
      window.clearInterval(timer);
      setStorageBusy(false);
    }
  }

  function cancelStorageChanges() {
    setStorageError("");
    setStoragePath(storage?.path || "");
    setStorageView(storage?.ready ? "library" : "settings");
  }


  useEffect(() => { void load(); }, []);
  useEffect(() => { localStorage.setItem("ual:sort", sort); }, [sort]);
  useEffect(() => { localStorage.setItem("ual:view", view); }, [view]);
  useEffect(() => {
    localStorage.setItem("ual:filters", JSON.stringify({ query, category, quick, author, selectedTags, tagMode, untagged }));
  }, [query, category, quick, author, selectedTags, tagMode, untagged]);
  useEffect(() => {
    if (!userTags) return;
    const known = new Set(userTags.tags);
    setSelectedTags((current) => {
      const next = current.filter((tag) => known.has(tag));
      return next.length === current.length ? current : next;
    });
  }, [userTags]);
  useEffect(() => {
    const job = actionStatus;
    if (!job || dismissedRef.current.has(job.id)) return;
    const generation = ++actionGenerationRef.current;
    let stopped = false;
    const poll = async () => {
      if (stopped || actionRequestRef.current) return;
      actionRequestRef.current = true;
      try {
        const next = (await api.getAction(job.id)).action;
        if (stopped || generation !== actionGenerationRef.current || dismissedRef.current.has(job.id)) return;
        setActionStatus(next);
        setState((current) => current ? { ...current, action: next } : current);
        if (["completed", "failed", "cancelled"].includes(next.status)) { stopped = true; window.clearInterval(timer); }
      } catch (cause) {
        const details = cause as { status?: number; code?: string };
        const missing = details.status === 404 || details.code === "unknown_action" || (cause instanceof Error && /unknown[_ ]action/i.test(cause.message));
        if (missing) {
          stopped = true;
          window.clearInterval(timer);
          setActionStatus((current) => current && current.id === job.id ? { ...current, status: "failed", stage: "Outcome unknown", error: "This operation is no longer known to the backend. Its outcome is unknown; no automatic retry was attempted.", error_code: "unknown_action" } : current);
        } else if (!stopped && generation === actionGenerationRef.current) {
          setActionStatus((current) => current && current.id === job.id ? { ...current, error: "Connection interrupted while checking this operation; retrying…", error_code: "connection_lost" } : current);
          setError(cause instanceof Error ? cause.message : "Connection interrupted while checking action progress; retrying.");
        }
      } finally {
        actionRequestRef.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 900);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [actionStatus?.id]);

  useEffect(() => {
    const job = state?.job;
    if (!job || !["queued", "running"].includes(job.status)) return;
    let stopped = false;
    let inFlight = false;
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const next = await api.getState();
        if (!stopped) setState(next);
        if (!stopped && next.job && !["queued", "running"].includes(next.job.status)) setEnrichCancelling(false);
        if (!stopped && next.job?.status === "completed") await load();
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : "Could not read enrichment progress.");
      } finally { inFlight = false; }
    };
    const timer = window.setInterval(() => void poll(), 900);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [state?.job?.id, state?.job?.status]);

  const categoryTree = useMemo(() => {
    const roots = new Map<string, CategoryNode>();
    for (const asset of assets) {
      let children = roots;
      let path = "";
      for (const name of (asset.category?.path || "Uncategorized").split("/")) {
        path = path ? `${path}/${name}` : name;
        let node = children.get(name);
        if (!node) {
          node = { name, path, count: 0, children: new Map() };
          children.set(name, node);
        }
        node.count += 1;
        children = node.children;
      }
    }
    return roots;
  }, [assets]);
  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);
  const importAssets = useMemo<Asset[]>(
    () => [...importSelection].map((key) => assets.find((asset) => asset.asset_key === key)).filter((asset): asset is Asset => Boolean(asset)),
    [importSelection, assets],
  );
  const displayAssets = useMemo<Asset[]>(() => {
    if (!userTags) return [];
    return assets.map((asset) => ({ ...asset, tags: userTags.assignments[asset.asset_key] || [] }));
  }, [assets, userTags]);
  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const asset of displayAssets)
      for (const tag of asset.tags || []) counts.set(tag, (counts.get(tag) || 0) + 1);
    return counts;
  }, [displayAssets]);
  const untaggedCount = useMemo(
    () => displayAssets.filter((asset) => !(asset.tags || []).length).length,
    [displayAssets],
  );
  const authors = useMemo(
    () => [...new Set(displayAssets.map((asset) => asset.author || "").filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [displayAssets],
  );

  const filtered = useMemo(() => {
    const exact = query.trim().toLowerCase();
    const words = exact.split(/\s+/).filter(Boolean);
    return displayAssets
      .map((asset) => ({ asset, tier: searchTier(asset, words, exact) }))
      .filter((entry): entry is { asset: Asset; tier: number } => {
        const { asset, tier } = entry;
        if (tier === null) return false;
        const assetCategory = asset.category?.path || "Uncategorized";
        if (
          category !== "All assets" &&
          assetCategory !== category &&
          !assetCategory.startsWith(`${category}/`)
        )
          return false;
        if (author && (asset.author || "") !== author) return false;
        if (quick === "favorites" && !favoriteSet.has(asset.asset_key)) return false;
        if (quick === "pending" && !isPending(asset)) return false;
        if (quick === "flagged" && !(asset.flags || []).length) return false;
        if (quick === "non-store" && !asset.non_store) return false;
        const assetTags = asset.tags || [];
        if (untagged && assetTags.length) return false;
        if (selectedTags.length) {
          const matches = (tag: string) => assetTags.includes(tag);
          if (!(tagMode === "all" ? selectedTags.every(matches) : selectedTags.some(matches))) return false;
        }
        return true;
      })
      .sort((a, b) => {
        if (a.tier !== b.tier) return a.tier - b.tier;
        const { asset: a2 } = a;
        const { asset: b2 } = b;
        if (sort === "author")
          return (
            (a2.author || "").localeCompare(b2.author || "") ||
            a2.name.localeCompare(b2.name)
          );
        if (sort === "size")
          return assetSize(b2) - assetSize(a2) || a2.name.localeCompare(b2.name);
        return a2.name.localeCompare(b2.name);
      })
      .map((entry) => entry.asset);
  }, [displayAssets, category, author, query, quick, sort, favoriteSet, untagged, selectedTags, tagMode]);
  function clearFilters() {
    setQuery("");
    setCategory("All assets");
    setQuick("all");
    setAuthor("");
    setSelectedTags([]);
    setTagMode("all");
    setUntagged(false);
  }
  const selected =
    filtered.find((asset) => asset.asset_key === selectedKey) ||
    filtered[0] ||
    null;
  const flaggedCount = assets.filter(
    (asset) => (asset.flags || []).length > 0,
  ).length;
  const pendingCount =
    state?.pending_enrichment ?? assets.filter(isPending).length;
  const storeCount = assets.filter((asset) => !asset.non_store).length;
  const favoriteCount = assets.filter((asset) => favoriteSet.has(asset.asset_key)).length;
  const selectedFavorited = Boolean(selected && favoriteSet.has(selected.asset_key));
  async function toggleFavorite(assetKey: string, favorite: boolean) {
    const epoch = (favEpochRef.current.get(assetKey) || 0) + 1;
    favEpochRef.current.set(assetKey, epoch);
    favRevRef.current += 1;
    setFavPending((current) => new Set(current).add(assetKey));
    try {
      await api.setFavorite(assetKey, favorite);
      if (favEpochRef.current.get(assetKey) !== epoch) return;
      favRevRef.current += 1;
      setFavorites((current) => {
        const next = new Set(current);
        if (favorite) next.add(assetKey);
        else next.delete(assetKey);
        return [...next];
      });
    } catch (cause) {
      if (favEpochRef.current.get(assetKey) !== epoch) return;
      setError(cause instanceof Error ? cause.message : "Favorites could not be saved.");
    } finally {
      if (favEpochRef.current.get(assetKey) === epoch) {
        setFavPending((current) => {
          const next = new Set(current);
          next.delete(assetKey);
          return next;
        });
      }
    }
  }

  async function mutateTag(change: TagChange) {
    if (tagBusyRef.current || !userTags || storageBusy) throw new Error("Tag editing is unavailable.");
    const epoch = storageEpochRef.current;
    tagBusyRef.current = true;
    tagRevRef.current += 1;
    setTagBusy(true);
    setTagError("");
    try {
      const next = await api.mutateTags(change);
      if (epoch !== storageEpochRef.current) throw new Error("The library changed during the tag operation.");
      if (!isValidUserTags(next)) throw new Error("The tag store returned an unreadable response.");
      tagRevRef.current += 1;
      setUserTags(next);
      if (change.action === "rename") setSelectedTags((tags) => [...new Set(tags.map((tag) => tag === change.tag ? change.new_tag!.trim().toLowerCase() : tag))]);
    } catch (cause) {
      if (epoch === storageEpochRef.current) setTagError(cause instanceof Error ? cause.message : "The tag change could not be saved.");
      throw cause;
    } finally {
      tagBusyRef.current = false;
      setTagBusy(false);
    }
  }

  async function assignTag(assetKey: string, tag: string) {
    await mutateTag({ action: "assign", tag, asset_key: assetKey });
  }

  async function removeTag(assetKey: string, tag: string) {
    await mutateTag({ action: "remove", tag, asset_key: assetKey });
  }

  const tagAssignmentCounts = useMemo(() => {
    const counts = new Map<string, number>();
    if (!userTags) return counts;
    for (const assigned of Object.values(userTags.assignments))
      for (const tag of assigned) counts.set(tag, (counts.get(tag) || 0) + 1);
    return counts;
  }, [userTags]);

  async function runAction(name: "resync" | "cleanup" | "organize") {
    try {
      setBusy(name);
      setError("");
      const response = name === "resync" ? await api.resync() : name === "cleanup" ? await api.cleanupPlan() : await api.organizePlan();
      sessionStorage.setItem("ual:action", JSON.stringify({ id: response.job_id, kind: name, phase: "plan" }));
      setActionStatus({ id: response.job_id, kind: name, phase: "plan", status: "queued", stage: "Starting", completed: 0, total: null, current_item: null, counts: {} });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The action could not start.");
      setBusy(null);
    }
  }

  async function startEnrich() {
    try {
      setBusy("enrich");
      setError("");
      await api.enrichStart();
      const next = await api.getState();
      setState(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Enrichment could not start.");
    } finally { setBusy(null); }
  }

  async function cancelEnrich() {
    setEnrichCancelling(true);
    try {
      await api.enrichCancel();
      const next = await api.getState();
      setState(next);
      if (!next.job || !["queued", "running"].includes(next.job.status)) setEnrichCancelling(false);
    } catch (cause) {
      setEnrichCancelling(false);
      setError(cause instanceof Error ? cause.message : "Enrichment could not be cancelled.");
    }
  }

  async function confirmDialog() {
    if (!dialog?.result.plan_hash || busy) return;
    try {
      setBusy(dialog.mode);
      setError("");
      const response = dialog.mode === "resync" ? await api.resyncApply(dialog.result.plan_hash) : dialog.mode === "cleanup" ? await api.cleanupApply(dialog.result.plan_hash) : await api.organizeApply(dialog.result.plan_hash);
      sessionStorage.setItem("ual:action", JSON.stringify({ id: response.job_id, kind: dialog.mode, phase: "apply" }));
      setActionStatus({ id: response.job_id, kind: dialog.mode, phase: "apply", status: "queued", stage: "Starting", completed: 0, total: null, current_item: null, counts: {} });
    } catch (cause) {
      setBusy(null);
      setError(cause instanceof Error ? cause.message : "The plan changed before it could be applied.");
    }
  }

  const actionTerminalRef = useRef("");
  useEffect(() => {
    const job = actionStatus;
    if (!job || !["completed", "failed", "cancelled"].includes(job.status)) return;
    const marker = job.id + ":" + job.status;
    if (actionTerminalRef.current === marker) return;
    actionTerminalRef.current = marker;
    sessionStorage.removeItem("ual:action");
    if (job.phase === "plan") {
      setBusy(null);
      if (job.status === "completed" && job.result) {
        setDialog({ mode: job.kind as "resync" | "cleanup" | "organize", title: job.kind === "resync" ? "Review resync" : job.kind === "cleanup" ? "Clean up old versions" : "Review organization", result: job.result });
      } else if (job.status === "failed") {
        setError(job.error || job.error_code || "The preview failed.");
      } else if (job.status === "cancelled") {
        setError("The preview was cancelled.");
      }
      return;
    }
    if (job.phase !== "apply") return;
    if (job.status === "completed") {
      uiRefreshRef.current = true;
      setActionStatus((current) => current && current.id === job.id ? { ...current, status: "running", stage: "Refreshing index", completed: 0, total: null, current_item: null } : current);
      void (async () => {
        const refreshed = await load();
        uiRefreshRef.current = false;
        setActionStatus((current) => current && current.id === job.id ? { ...current, status: refreshed ? "completed" : "failed", stage: refreshed ? "Index refreshed" : "Index refresh failed", error: refreshed ? current.error : "Changes completed, but refreshing the index failed. Verify the library before continuing." } : current);
        if (refreshed) setDialog(null);
        setBusy(null);
      })();
    } else {
      setBusy(null);
      if (job.error_code === "plan_changed" && job.result?.preview && job.result.plan_hash) {
        setDialog((current) => current ? { ...current, result: job.result as ActionResult, title: "Review updated plan" } : current);
        setError("The library changed. Review the fresh plan and confirm again.");
      } else if (job.status === "failed") {
        setError(job.error || job.error_code || "The action failed. No changes were applied unless the result says otherwise.");
        setDialog((current) => current ? { ...current, result: { ...current.result, plan_hash: undefined } } : current);
      }
    }
  }, [actionStatus?.id, actionStatus?.status, actionStatus?.phase]);

  function dismissAction(id: string) {
    setDismissedIds((current) => new Set(current).add(id));
    setActionStatus((current) => current?.id === id ? null : current);
  }
  function dismissEnrich(id: string) {
    setDismissedIds((current) => new Set(current).add(id));
    setState((current) => current ? { ...current, job: null } : current);
  }

  const jobFailure =
    state?.job?.status === "failed"
      ? state.job.error || "Enrichment failed."
      : "";
  const jobRunning =
    state?.job && ["queued", "running"].includes(state.job.status);
  if (storage?.portRecovery) return <PortRecoveryScreen key={storage.portRecovery.id} request={storage.portRecovery} onStorage={setStorage} />;
  if (storageView !== "library") {
    return <StorageScreen mode={storageView} storage={storage} path={storagePath} error={storageError || (storage?.path === storagePath ? storage?.error || "" : "") || error} busy={storageBusy} blocked={storageChangeBlocked} onPathChange={(nextPath) => { setStoragePath(nextPath); setStorageError(""); }} onBrowse={() => void chooseStorageFolder()} onSave={() => void saveStorageRoot()} onCancel={cancelStorageChanges} onRetry={() => { setStorageError(""); setStorageView("loading"); void api.retryBackend().then(() => load()).catch((cause) => { setStorageView("settings"); setStorageError(String(cause)); }); }} />;
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <img className="brand-mark" src={appIcon} alt="" draggable={false} />
          <h1>Unity Asset Library</h1>
        </div>
        <div className="top-actions">
          <Button type="button" className="icon-button theme-toggle" title={`Theme: ${theme}. Switch to ${theme === "system" ? "light" : theme === "light" ? "dark" : "system"}`} aria-label={`Theme: ${theme}. Switch to ${theme === "system" ? "light" : theme === "light" ? "dark" : "system"}`} onPress={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}>
            <Icon as={theme === "system" ? Monitor : theme === "light" ? Sun : Moon} className="icon" aria-hidden="true" />
          </Button>
          <Popover
            isOpen={actionsOpen}
            onClose={() => setActionsOpen(false)}
            finalFocusRef={actionTriggerRef}
            placement="bottom right"
            offset={10}
            trigger={(triggerProps) => (
              <Button
                type="button"
                {...triggerProps}
                ref={(node) => {
                  actionTriggerRef.current = node;
                  const ref = triggerProps.ref;
                  if (typeof ref === "function") ref(node);
                  else if (ref) ref.current = node;
                }}
                className="button primary"
                onPress={() => setActionsOpen((open) => !open)}
              >
                Library actions{" "}
                <Icon
                  as={ChevronDown}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
              </Button>
            )}
          >
            <PopoverContent
              id="library-actions"
              className="library-actions"
              aria-label="Library actions"
            >
              <div className="summary-strip">
                <div>
                  <span className="summary-label">INDEXED</span>
                  <strong>{assets.length}</strong>
                </div>
                <div>
                  <span className="summary-label">PENDING</span>
                  <strong className={pendingCount ? "accent-text" : ""}>
                    {pendingCount}
                  </strong>
                </div>
                <div>
                  <span className="summary-label">FLAGGED</span>
                  <strong>{flaggedCount}</strong>
                </div>
              </div>
              <div
                className={`actions-intro connection ${error ? "offline" : ""}`}
              >
                <span className="connection-dot" />
                {error
                  ? "Needs attention"
                  : loading
                    ? "Starting Python"
                    : "Connected"}
              </div>
              <p className="actions-intro">
                Choose an action for your library.
              </p>
              <Button
                type="button"
                className="library-action"
                onPress={() => {
                  setActionsOpen(false);
                  void runAction("resync");
                }}
                isDisabled={Boolean(busy) || loading || importActive}
              >
                <Icon
                  as={RefreshCw}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
                <span>
                  <strong>
                    {busy === "resync" ? "Syncing…" : "Resync index"}
                  </strong>
                  <span>
                    Scan local files and review index changes before applying.
                    Never deletes files from disk.
                  </span>
                </span>
              </Button>
              <Button
                type="button"
                className="library-action"
                onPress={() => {
                  setActionsOpen(false);
                  void startEnrich();
                }}
                isDisabled={Boolean(busy) || loading || importActive || Boolean(jobRunning)}
              >
                <Icon
                  as={Sparkles}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
                <span>
                  <strong>{jobRunning ? "Enrich · running" : "Enrich"}</strong>
                  <span>
                    Look up pending assets online to add Store details,
                    categories, thumbnails, and ratings. Does not change package
                    files.
                  </span>
                </span>
              </Button>
              <Button
                type="button"
                className="library-action"
                onPress={() => {
                  setActionsOpen(false);
                  void runAction("organize");
                }}
                isDisabled={Boolean(busy) || loading || importActive}
              >
                <Icon
                  as={Folder}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
                <span>
                  <strong>
                    {busy === "organize" ? "Planning…" : "Organize"}
                  </strong>
                  <span>
                    Preview moving archives into Store-category folders. Moves
                    files only after confirmation; never renames or unpacks
                    them.
                  </span>
                </span>
              </Button>
              <Button
                type="button"
                className="library-action"
                onPress={() => {
                  setActionsOpen(false);
                  void runAction("cleanup");
                }}
                isDisabled={Boolean(busy) || loading || importActive}
              >
                <Icon
                  as={Folder}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
                <span>
                  <strong>
                    {busy === "cleanup" ? "Planning…" : "Clean up old versions"}
                  </strong>
                  <span>
                    Review old archives to delete and the versions to keep.
                    Deletes files only after confirmation.
                  </span>
                </span>
              </Button>
              {jobRunning && (
                <Button
                  type="button"
                  className="button quiet danger-text"
                  onPress={() => {
                    setActionsOpen(false);
                    void cancelEnrich();
                  }}
                  isDisabled={enrichCancelling}
                >
                  {enrichCancelling ? "Cancelling…" : "Cancel enrich"}
                </Button>
              )}
              <p className="actions-hint">
                Suggested order: Resync index → Enrich → Organize → Clean up old
                versions (optional)
              </p>
            </PopoverContent>
          </Popover>
        </div>
      </header>
      {(error || jobFailure) && (
        <Alert className="error-banner">
          <strong>{jobFailure ? "Enrichment failed" : "Backend notice"}</strong>
          <span>{error || jobFailure}</span>
          <Button
            type="button"
            onPress={() => {
              if (jobFailure)
                setState((current) =>
                  current ? { ...current, job: null } : current,
                );
              setError("");
              if (!jobFailure) void load();
            }}
          >
            {jobFailure ? "Dismiss" : "Retry"}
          </Button>
        </Alert>
      )}

      <main className={`workspace ${inspectorOpen ? "with-inspector" : ""} ${navigationOpen ? "navigation-open" : ""}`} key={storageEpoch}>
        <aside className="sidebar" id="library-navigation" aria-label="Library navigation">
          <section className="side-section">
            <div className="section-label">Library</div>
            {(
              [
                ["all", "All assets", assets.length],
                ["favorites", "Favorites", favoriteCount],
                ["pending", "Needs enrichment", pendingCount],
                ["flagged", "Flagged", flaggedCount],
                [
                  "non-store",
                  "Local only",
                  assets.filter((asset) => asset.non_store).length,
                ],
              ] as const
            ).map(([key, label, count]) => (
              <Button
                type="button"
                key={key}
                className={`nav-row ${quick === key ? "active" : ""}`}
                aria-pressed={quick === key}
                onPress={() => setQuick(key)}
              >
                <span>{label}</span>
                <span className="nav-count">{count}</span>
              </Button>
            ))}
          </section>
          <section className="side-section categories">
            <div className="section-label">
              Categories <span>{categoryTree.size}</span>
            </div>
            <Button
              type="button"
              className={`nav-row ${category === "All assets" ? "active" : ""}`}
              onPress={() => setCategory("All assets")}
            >
              <span>All categories</span>
              <span className="nav-count">{assets.length}</span>
            </Button>
            <ul className="category-tree">
              {[...categoryTree.values()]
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((node) => (
                  <CategoryBranch
                    key={node.path}
                    node={node}
                    selected={category}
                    onSelect={(path) => { setCategory(path); setNavigationOpen(false); }}
                  />
                ))}
            </ul>
          </section>
          <section className="side-section tags">
            <div className="section-label">
              Tags <span>{userTags ? userTags.tags.length : 0}</span>
            </div>
            {tagError && <p className="tag-note danger-text" role="alert">{tagError}</p>}
            {!userTags && !tagError && <p className="tag-note">Tags are unavailable right now.</p>}
            {userTags && (
              <>
                <Button
                  type="button"
                  className={`nav-row ${untagged ? "active" : ""}`}
                  aria-pressed={untagged}
                  onPress={() => setUntagged(!untagged)}
                >
                  <span>Untagged</span>
                  <span className="nav-count">{untaggedCount}</span>
                </Button>
                {[...tagCounts.entries()]
                  .sort((a, b) => a[0].localeCompare(b[0]))
                  .map(([tag, count]) => (
                    <Button
                      type="button"
                      key={tag}
                      className={`nav-row ${selectedTags.includes(tag) ? "active" : ""}`}
                      aria-pressed={selectedTags.includes(tag)}
                      onPress={() =>
                        setSelectedTags((current) =>
                          current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
                        )
                      }
                    >
                      <span>{tag}</span>
                      <span className="nav-count">{count}</span>
                    </Button>
                  ))}
                {selectedTags.length > 1 && (
                  <div className="tag-mode-toggle" role="group" aria-label="Match selected tags">
                    <Button type="button" className={tagMode === "all" ? "selected" : ""} aria-pressed={tagMode === "all"} onPress={() => setTagMode("all")}>Match all</Button>
                    <Button type="button" className={tagMode === "any" ? "selected" : ""} aria-pressed={tagMode === "any"} onPress={() => setTagMode("any")}>Match any</Button>
                  </div>
                )}
                <TagManager
                  tags={userTags.tags}
                  counts={tagAssignmentCounts}
                  busy={tagBusy}
                  error={tagError}
                  onMutate={mutateTag}
                />
              </>
            )}
          </section>
          <div className="sidebar-foot">
            <div className="mini-status">
              <span className="connection-dot" />
              <span>{storeCount} store records</span>
              <span className="muted">{error ? "Connection needs attention" : "Library connected"}</span>
            </div>
            <Button type="button" className="settings-link" onPress={() => { setStoragePath(storage?.path || ""); setStorageError(""); setStorageView("settings"); }} isDisabled={storageBusy} aria-label="Open Settings">
              <Icon as={Settings} className="icon" aria-hidden="true" focusable={false} />Settings
            </Button>
          </div>

        </aside>
        <section className="content-column" aria-label="Asset browser">
          <div className="library-toolbar">
            <Button type="button" className="icon-button navigation-toggle" aria-label="Toggle library navigation" aria-expanded={navigationOpen} aria-controls="library-navigation" onPress={() => setNavigationOpen(!navigationOpen)}><Icon as={Menu} className="icon" aria-hidden="true" /></Button>
          <Input className="search-wrap" role="search">
            <Icon
              as={Search}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
            <InputField
              ref={searchRef}
              value={query}
              onChangeText={setQuery}
              placeholder="Search assets, authors, tags…"
              aria-label="Search library"
            />
            {!query && <kbd>⌘ K</kbd>}
            {query && <Button className="search-clear" aria-label="Clear search" onPress={() => setQuery("")}><Icon as={X} className="icon" aria-hidden="true" /></Button>}
          </Input>
          <div className="import-controls">
            <Button type="button" className="button quiet" onPress={selectVisibleImport} isDisabled={loading || importActive} aria-label="Select visible packages for import">Select visible</Button>
            {importSelection.size > 0 && (
              <>
                <Button type="button" className="button quiet" onPress={() => setImportSelection(new Set())} isDisabled={importActive} aria-label="Clear import selection">Clear selection</Button>
                <Button ref={importTriggerRef} type="button" className="button primary" onPress={() => setImportOpen(true)} isDisabled={importActive} aria-label={`Import ${importSelection.size} selected package${importSelection.size === 1 ? "" : "s"}`}>Import {importSelection.size}</Button>
              </>
            )}
          </div>

            <Button ref={detailsToggleRef} type="button" className={`icon-button ${inspectorOpen ? "selected" : ""}`} aria-label={inspectorOpen ? "Hide asset details" : "Show asset details"} title={inspectorOpen ? "Hide asset details" : "Show asset details"} aria-expanded={inspectorOpen} aria-controls="asset-inspector" onPress={() => setInspectorOpen(!inspectorOpen)}><Icon as={PanelRight} className="icon" aria-hidden="true" /></Button>
          </div>
          <div className="content-head">
            <div>
              <h2>{quick === "all" ? category : { favorites: "Favorites", pending: "Needs enrichment", flagged: "Flagged", "non-store": "Local only" }[quick]}</h2>
              <p>
                {filtered.length.toLocaleString()} assets{filtered.length !== assets.length && ` · ${assets.length.toLocaleString()} in library`}
              </p>
            </div>
            <div className="view-controls">
              <Button
                type="button"
                className={`icon-button ${view === "grid" ? "selected" : ""}`}
                onPress={() => setView("grid")}
                aria-label="Grid view"
                aria-pressed={view === "grid"}
                title="Grid view"
              >
                <Icon
                  as={LayoutGrid}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
              </Button>
              <Button
                type="button"
                className={`icon-button ${view === "list" ? "selected" : ""}`}
                onPress={() => setView("list")}
                aria-label="List view"
                aria-pressed={view === "list"}
                title="List view"
              >
                <Icon
                  as={List}
                  className="icon"
                  aria-hidden="true"
                  focusable={false}
                />
              </Button>
              <Select
                className="sort-control"
                selectedValue={author ? `author:${author}` : "all"}
                initialLabel={author || "All authors"}
                onValueChange={(value: string) => setAuthor(value === "all" ? "" : value.slice(7))}
              >
                <SelectTrigger className="sort-trigger">
                  <SelectInput className="sort-input" placeholder="Filter by author" aria-label="Filter by author" />
                  <SelectIcon>
                    <Icon as={ChevronDown} className="icon" aria-hidden="true" focusable={false} />
                  </SelectIcon>
                </SelectTrigger>
                <SelectPortal>
                  <SelectContent className="sort-menu">
                    <SelectItem value="all" label="All authors" className="sort-option" />
                    {authors.map((name) => (
                      <SelectItem key={name} value={`author:${name}`} label={name} className="sort-option" />
                    ))}
                  </SelectContent>
                </SelectPortal>
              </Select>
              <Select
                className="sort-control"
                selectedValue={sort}
                initialLabel={
                  sort === "author"
                    ? "Author"
                    : sort === "size"
                      ? "Size"
                      : "Name"
                }
                onValueChange={(value: string) => setSort(value as SortMode)}
              >
                <SelectTrigger className="sort-trigger">
                  <SelectInput
                    className="sort-input"
                    placeholder="Sort assets"
                  />
                  <SelectIcon>
                    <Icon
                      as={ChevronDown}
                      className="icon"
                      aria-hidden="true"
                      focusable={false}
                    />
                  </SelectIcon>
                </SelectTrigger>
                <SelectPortal>
                  <SelectContent className="sort-menu">
                    <SelectItem
                      value="name"
                      label="Name"
                      className="sort-option"
                    />
                    <SelectItem
                      value="author"
                      label="Author"
                      className="sort-option"
                    />
                    <SelectItem
                      value="size"
                      label="Size"
                      className="sort-option"
                    />
                  </SelectContent>
                </SelectPortal>
              </Select>
            </div>
          </div>
          {(query || category !== "All assets" || quick !== "all" || author || untagged || selectedTags.length > 0) && <div className="filter-context" aria-label="Active filters">
            {quick !== "all" && <Button className="filter-chip" aria-label="Remove library filter" onPress={() => setQuick("all")}>{ { favorites: "Favorites", pending: "Needs enrichment", flagged: "Flagged", "non-store": "Local only" }[quick]}<Icon as={X} className="icon" aria-hidden="true" /></Button>}
            {category !== "All assets" && <Button className="filter-chip" aria-label="Remove category filter" onPress={() => setCategory("All assets")}>{category}<Icon as={X} className="icon" aria-hidden="true" /></Button>}
            {author && <Button className="filter-chip" aria-label="Remove author filter" onPress={() => setAuthor("")}>Author: {author}<Icon as={X} className="icon" aria-hidden="true" /></Button>}
            {untagged && <Button className="filter-chip" aria-label="Remove untagged filter" onPress={() => setUntagged(false)}>Untagged<Icon as={X} className="icon" aria-hidden="true" /></Button>}
            {selectedTags.map((tag) => (
              <Button key={tag} className="filter-chip" aria-label={`Remove tag filter ${tag}`} onPress={() => setSelectedTags((current) => current.filter((item) => item !== tag))}>Tag: {tag}<Icon as={X} className="icon" aria-hidden="true" /></Button>
            ))}
            {selectedTags.length > 1 && (
              <Button className="filter-chip" aria-label={`Switch to matching ${tagMode === "all" ? "any" : "all"} selected tags`} onPress={() => setTagMode(tagMode === "all" ? "any" : "all")}>
                {tagMode === "all" ? "Match all tags" : "Match any tags"}
              </Button>
            )}
            {query && <Button className="filter-chip" aria-label="Remove search filter" onPress={() => setQuery("")}>Search: {query}<Icon as={X} className="icon" aria-hidden="true" /></Button>}
            <Button className="filter-reset" onPress={clearFilters}>Clear filters</Button>
          </div>}
          {actionStatus && !dismissedIds.has(actionStatus.id) && <ActionProgress job={actionStatus} onDismiss={dismissAction} announce={!(dialog && actionStatus.phase === "apply")} />}
          {state?.job && !dismissedIds.has(state.job.id) && <ActionProgress job={state.job} onDismiss={dismissEnrich} />}
          {importJob && !dismissedIds.has(importJob.id) && (
            <div className="import-progress">
              <ActionProgress job={importJob} onDismiss={dismissImportJob} />
              {importActive && <Button type="button" className="button quiet" onPress={() => setImportOpen(true)} aria-label="Open import progress">Review import</Button>}
            </div>
          )}
          {loading ? (
            <div className="library-loading" role="status" aria-label="Loading your library"><span>Loading your library…</span><div className="asset-grid" aria-hidden="true">{Array.from({ length: 8 }, (_, index) => <div className="asset-skeleton" key={index}><div /><span /><span /></div>)}</div></div>
          ) : assets.length === 0 ? (
            <div className="empty-state">
              <Icon as={Folder} className="icon empty-mark" aria-hidden="true" focusable={false} />
              <h3>Your library is empty</h3>
              <p>No Unity packages or archives have been indexed in this folder yet.</p>
              <Button type="button" className="button quiet" onPress={() => void runAction("resync")} isDisabled={storageBusy || libraryActivity}>Scan library</Button>
            </div>
          ) : filtered.length ? (
            <div className={view === "grid" ? "asset-grid" : "asset-list"}>
              {view === "list" && <div className="list-heading" aria-hidden="true"><span>Asset</span><span>Author</span><span>Category</span><span>Version</span><span /></div>}
              {filtered.map((asset) => (
                <div className="asset-cell" key={asset.asset_key}>
                  {unityPackages(asset).length > 0 && (
                    <label className="import-check">
                      <input type="checkbox" checked={importSelection.has(asset.asset_key)} onChange={() => toggleImportSelection(asset.asset_key)} disabled={importActive} aria-label={`Select ${asset.name} for import`} />
                    </label>
                  )}
                  <AssetCard
                    asset={asset}
                    selected={asset.asset_key === selected?.asset_key}
                    view={view}
                    onClick={() => { setSelectedKey(asset.asset_key); if (window.innerWidth < 768) setInspectorOpen(true); }}
                  />
                  <StarButton
                    assetKey={asset.asset_key}
                    name={asset.name}
                    favorited={favoriteSet.has(asset.asset_key)}
                    pending={favPending.has(asset.asset_key)}
                    onToggle={toggleFavorite}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <Icon as={quick === "favorites" ? Star : CircleSlash} className="icon empty-mark" aria-hidden="true" focusable={false} />
              <h3>{quick === "favorites" && favoriteCount === 0 ? "No favorites yet" : "No assets match"}</h3>
              <p>{quick === "favorites" && favoriteCount === 0 ? "Star assets to pin them here." : "Try clearing a filter or searching for a broader term."}</p>
              <Button type="button" className="button quiet" onPress={clearFilters}>Clear filters</Button>
            </div>
          )}
        </section>

        <aside className="inspector" id="asset-inspector" aria-label="Asset details" hidden={!inspectorOpen}>
          <div className="inspector-heading">
            <span>Asset details</span>
            <Button ref={detailsCloseRef} type="button" className="icon-button" aria-label="Close asset details" onPress={() => setInspectorOpen(false)}>
              <Icon as={X} className="icon" aria-hidden="true" />
            </Button>
          </div>
          {selected ? (
            <Inspector
              asset={selected}
              favorited={selectedFavorited}
              favoritePending={selected ? favPending.has(selected.asset_key) : false}
              onToggleFavorite={toggleFavorite}
              onOpen={(url) => void api.openExternal(url)}
              allTags={userTags?.tags || []}
              tagBusy={tagBusy || !userTags || storageBusy || loading}
              tagError={tagError}
              onAssign={assignTag}
              onRemove={removeTag}
              onReveal={(filePath) => {
                void api.revealItem(filePath).catch((cause) => {
                  setError(cause instanceof Error ? cause.message : "The file could not be revealed.");
                });
              }}
            />
          ) : (
            <div className="inspector-empty">
              <Icon
                as={Plus}
                className="icon empty-mark"
                aria-hidden="true"
                focusable={false}
              />
              <h3>Select an asset</h3>
              <p>Details, versions, and links appear here.</p>
            </div>
          )}
        </aside>
      </main>
      {dialog && (
        <ActionDialog
          dialog={dialog}
          busy={Boolean(busy)}
          progress={actionStatus?.phase === "apply" ? actionStatus : null}
          finalFocusRef={actionTriggerRef}
          onClose={() => setDialog(null)}
          onConfirm={() => void confirmDialog()}
        />
      )}
      {importOpen && (
        <ImportDialog
          assets={importAssets}
          job={importJob}
          finalFocusRef={importTriggerRef}
          onClose={() => setImportOpen(false)}
          onStart={startImport}
          onStop={stopImport}
        />
      )}
    </div>
  );
}

function AssetCard({
  asset,
  selected,
  view,
  onClick,
}: {
  asset: Asset;
  selected: boolean;
  view: ViewMode;
  onClick: () => void;
}) {
  const flagged = (asset.flags || []).length > 0;
  if (view === "list")
    return (
      <Button
        type="button"
        className={`list-row ${selected ? "selected" : ""}`}
        aria-pressed={selected}
        title={asset.name}
        onPress={onClick}
      >
        <span className="list-name">
          <span className="list-avatar">{asset.thumbnail?.remote ? <img src={asset.thumbnail.remote} loading="lazy" alt="" /> : initials(asset)}</span>
          <strong>{asset.name}</strong>
        </span>
        <span>{asset.author || "Unknown author"}</span>
        <span>{asset.category?.path || "Uncategorized"}</span>
        <span>{asset.upstream_version || "Not available"}</span>
        <span>{flagged ? <span className="flag-dot" /> : ""}</span>
      </Button>
    );
  return (
    <Button
      type="button"
      className={`asset-card ${selected ? "selected" : ""}`}
      aria-pressed={selected}
      title={asset.name}
      onPress={onClick}
    >
      <div className="thumb">
        {asset.thumbnail?.remote ? (
          <img src={asset.thumbnail.remote} loading="lazy" alt="" />
        ) : (
          <span>{initials(asset)}</span>
        )}
        {asset.non_store && <span className="source-badge">LOCAL</span>}
      </div>
      <div className="card-copy">
        <div className="card-title">{asset.name}</div>
        <div className="card-meta">{asset.author || "Unknown author"}</div>
        <div className="card-bottom">
          <span>{asset.category?.levels?.[0] || "Other"}</span>
          {flagged && <span className="flag-dot" aria-label="Flagged" />}
        </div>
      </div>
    </Button>
  );
}

function Inspector({
  asset,
  favorited,
  favoritePending,
  onToggleFavorite,
  onOpen,
  onReveal,
  allTags,
  tagBusy,
  tagError,
  onAssign,
  onRemove,
}: {
  asset: Asset;
  favorited: boolean;
  favoritePending: boolean;
  onToggleFavorite: (assetKey: string, favorite: boolean) => void;
  onOpen: (url: string) => void;
  onReveal: (filePath: string) => void;
  allTags: string[];
  tagBusy: boolean;
  tagError: string;
  onAssign: (assetKey: string, tag: string) => Promise<void>;
  onRemove: (assetKey: string, tag: string) => Promise<void>;
}) {
  const onDisk = (asset.versions || []).find((version) => version.file);
  return (
    <div className="inspector-inner">
      <div
        className="inspector-hero"
        style={
          asset.thumbnail?.remote
            ? { backgroundImage: `url(${asset.thumbnail.remote})` }
            : undefined
        }
      >
        {!asset.thumbnail?.remote && <span className="inspector-placeholder">{initials(asset)}</span>}
        <StarButton
          assetKey={asset.asset_key}
          name={asset.name}
          favorited={favorited}
          pending={favoritePending}
          onToggle={onToggleFavorite}
          hero
        />
      </div>
      <div className="inspector-title"><h2>{asset.name}</h2><p>{asset.author || "Unknown author"}</p></div>
      <div className="inspector-actions">
        {asset.store && (
          <Button
            type="button"
            className="button primary wide"
            onPress={() => onOpen(asset.store!)}
          >
            <Icon
              as={ExternalLink}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
            Open in Asset Store
          </Button>
        )}
        {onDisk && (
          <Button
            type="button"
            className="button wide"
            onPress={() => onReveal(onDisk.file!)}
          >
            <Icon
              as={FolderOpen}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
            Reveal in Finder
          </Button>
        )}
      </div>
      <div className="detail-block">
        <div className="section-label">
          On disk <span>{asset.versions?.length || 0}</span>
        </div>
        <div className="version-list">
          {!asset.versions?.length && <p className="muted">No local archives indexed for this asset.</p>}
          {(asset.versions || []).map((version, index) => (
            <div key={(version.file || "Unnamed file") + "-" + index}>
              <span className="version-filename" title={version.file}>{version.file?.split(/[\\/]/).pop() || "Unnamed file"}</span>
              {version.file && <details className="version-path"><summary>File path</summary><code>{version.file}</code></details>}
              <small>{version.duplicate ? "Duplicate" : "Archive"}{version.size_bytes != null ? ` · ${formatBytes(version.size_bytes)}` : ""}</small>
              {version.file && <Button className="file-reveal" onPress={() => onReveal(version.file!)} aria-label={`Reveal ${version.file} in Finder`}><Icon as={FolderOpen} className="icon" aria-hidden="true" />Reveal in Finder</Button>}
            </div>
          ))}
        </div>
      </div>
      <div className="detail-block">
        <div className="section-label">Store metadata</div>
        <dl>
          <div>
            <dt>Category</dt>
            <dd>{asset.category?.path || "Uncategorized"}</dd>
          </div>
          <div>
            <dt>Local name</dt>
            <dd>{asset.local_name || "Not available"}</dd>
          </div>
          <div>
            <dt>Store ID</dt>
            <dd>{asset.store_id || "Not available"}</dd>
          </div>
          <div>
            <dt>Store version</dt>
            <dd>{asset.upstream_version || "Not available"}</dd>
          </div>
        </dl>
      </div>
      <div className="detail-block">
        <div className="section-label">Tags</div>
        {tagError && <p className="tag-note danger-text" role="alert">{tagError}</p>}
        <div className="tag-list">
          {(asset.tags || []).map((tag) => (
            <span key={tag} className="tag">
              {tag}
              <button
                type="button"
                className="tag-remove"
                aria-label={`Remove tag ${tag}`}
                disabled={tagBusy}
                onClick={() => void onRemove(asset.asset_key, tag).catch(() => {})}
              >
                ×
              </button>
            </span>
          ))}
          {!(asset.tags || []).length && <span className="tag-note">No tags yet.</span>}
        </div>
        <TagEditor key={asset.asset_key}
          assetKey={asset.asset_key}
          allTags={allTags}
          assigned={asset.tags || []}
          busy={tagBusy}
          onAssign={onAssign}
        />
      </div>

    </div>
  );
}

function StarButton({
  assetKey,
  name,
  favorited,
  pending,
  onToggle,
  hero,
}: {
  assetKey: string;
  name: string;
  favorited: boolean;
  pending: boolean;
  onToggle: (assetKey: string, favorite: boolean) => void;
  hero?: boolean;
}) {
  const label = `${favorited ? "Remove" : "Add"} ${name} ${favorited ? "from" : "to"} favorites`;
  return (
    <Button
      type="button"
      className={`star-button ${favorited ? "favorited" : ""} ${hero ? "hero" : ""}`}
      onPress={() => onToggle(assetKey, !favorited)}
      isDisabled={pending}
      aria-pressed={favorited}
      aria-label={label}
      title={favorited ? "Remove from favorites" : "Add to favorites"}
    >
      <Icon as={Star} className="icon" aria-hidden="true" focusable={false} />
    </Button>
  );
}

function ActionDialog({
  dialog,
  busy,
  progress,
  finalFocusRef,
  onClose,
  onConfirm,
}: {
  dialog: NonNullable<DialogState>;
  busy: boolean;
  progress: ActionJob | null;
  finalFocusRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const setContentRef = useCallback(
    (node: HTMLDivElement | null) => {
      contentRef.current = node;
      // OverlayProvider removes portals after the owner unmounts. Restore once
      // the content actually detaches, after its focus scope has been removed.
      if (!node)
        queueMicrotask(() => {
          if (!contentRef.current) finalFocusRef.current?.focus();
        });
    },
    [finalFocusRef],
  );
  const preview = dialog.result.preview || {};
  const resync = dialog.mode === "resync";
  const rows =
    dialog.mode === "cleanup" ? preview.removals || [] : preview.moves || [];
  const totals = preview.totals || {};
  const groups = [
    {
      label: "Add to index",
      rows: preview.additions || [],
      tone: "add",
      hint: "Files found in the vault that are not indexed yet.",
    },
    {
      label: "Remove from index",
      rows: preview.removals || [],
      tone: "remove",
      hint: "Files no longer found in the vault. Does not delete files from disk.",
    },
    {
      label: "Update size in index",
      rows: preview.resized || [],
      tone: "resize",
      hint: "Existing paths whose file size changed.",
    },
  ];
  const applyComplete = progress?.status === "completed";
  const applyDrift = progress?.status === "failed" && progress.error_code === "plan_changed";
  const applyApplied = applyComplete || progress?.result?.applied === true;
  const applying = Boolean(progress && ["queued", "running"].includes(progress.status));
  return (
    <AlertDialog
      isOpen
      onClose={() => {
        if (!busy) onClose();
      }}
      finalFocusRef={finalFocusRef}
      initialFocusRef={cancelRef}
      isKeyboardDismissable={!busy}
      closeOnOverlayClick={false}
      className="dialog-overlay"
    >
      <AlertDialogBackdrop className="dialog-backdrop" />
      <AlertDialogContent
        ref={setContentRef}
        className="action-dialog"
        aria-labelledby="action-title"
        aria-describedby="action-description"
      >
        <AlertDialogHeader className="dialog-top">
          <div>
            <div className="eyebrow">CONFIRMATION REQUIRED</div>
            <h2 id="action-title">{dialog.title}</h2>
          </div>
          <Button
            type="button"
            className="icon-button"
            onPress={onClose}
            isDisabled={busy}
            aria-label="Close dialog"
          >
            <Icon
              as={X}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
          </Button>
        </AlertDialogHeader>
        <AlertDialogBody>
          {progress && <ActionProgress job={progress} announce />}
          <p id="action-description" className="dialog-lead">
            {applying
              ? "Applying the approved changes now. This dialog will stay open until the operation and index refresh finish."
              : resync
                ? "Review the vault-relative paths below before updating the index. No files will be deleted from disk."
                : dialog.mode === "cleanup"
                  ? "Compare the archives to delete with the versions kept on disk. Only files marked Delete will be removed, and only after you confirm."
                  : "Review these moves before anything changes on disk."}
          </p>
          {resync ? (
            <>
              <div className="dialog-stats">
                {groups.map((group) => (
                  <div key={group.tone}>
                    <strong
                      className={
                        group.tone === "add"
                          ? "added-text"
                          : group.tone === "remove"
                            ? "danger-text"
                            : ""
                      }
                    >
                      {group.rows.length}
                    </strong>
                    <span>{group.label}</span>
                  </div>
                ))}
              </div>
              <div className="resync-groups">
                {groups
                  .filter(
                    (group) => group.tone !== "resize" || group.rows.length > 0,
                  )
                  .map((group) => (
                    <section
                      className="change-group"
                      key={group.tone}
                      aria-label={group.label}
                    >
                      <h3>
                        {group.label} <span>{group.rows.length}</span>
                      </h3>
                      <p>{group.hint}</p>
                      <ul className="change-list">
                        {group.rows.map((row) => (
                          <li key={row.path}>
                            <span>{row.path}</span>
                            <small>
                              {group.tone === "resize"
                                ? formatBytes(row.previous_size_bytes ?? 0) +
                                  " → "
                                : ""}
                              {formatBytes(row.size_bytes ?? 0)}
                            </small>
                          </li>
                        ))}
                      </ul>
                      {!group.rows.length && (
                        <p className="change-empty">
                          No files to {group.tone === "add" ? "add" : "remove"}.
                        </p>
                      )}
                    </section>
                  ))}
              </div>
            </>
          ) : (
            <>
              <div className="dialog-stats">
                <div>
                  <strong>{totals.files ?? totals.moves ?? rows.length}</strong>
                  <span>
                    {dialog.mode === "cleanup"
                      ? "files to delete"
                      : "moves planned"}
                  </span>
                </div>
                <div>
                  <strong>
                    {formatBytes(totals.bytes ?? totals.move_bytes ?? 0)}
                  </strong>
                  <span>storage</span>
                </div>
              </div>
              {dialog.mode === "cleanup" ? (
                <div className="resync-groups">
                  {(preview.families || [])
                    .filter((family) => family.removals.length > 0)
                    .map((family) => (
                      <section className="change-group" key={family.asset_key}>
                        <h3>{family.asset_key}</h3>
                        <p>
                          {family.reason === "superseded"
                            ? "Older version replaced by the retained archive."
                            : family.reason}
                        </p>
                        <ul className="change-list">
                          {family.survivor && (
                            <li key={`keep-${family.asset_key}`}>
                              <span>
                                <strong className="added-text">Keep</strong> ·{" "}
                                {family.survivor.path}
                              </span>
                              <small>
                                {formatBytes(family.survivor.size_bytes ?? 0)}
                              </small>
                            </li>
                          )}
                          {family.removals.map((row) => (
                            <li key={row.path}>
                              <span>
                                <strong className="danger-text">Delete</strong>{" "}
                                · {row.path}
                              </span>
                              <small>{formatBytes(row.size_bytes ?? 0)}</small>
                            </li>
                          ))}
                        </ul>
                      </section>
                    ))}
                  {!rows.length && (
                    <p className="change-empty">No old versions to delete.</p>
                  )}
                </div>
              ) : (
                <div className="plan-list">
                  {rows.map((row, index) => (
                    <div key={row.src || index}>
                      <span>{row.src}</span>
                      <span className="plan-arrow">
                        <Icon
                          as={ArrowRight}
                          className="icon"
                          aria-hidden="true"
                          focusable={false}
                        />
                      </span>
                      <span>{row.dst}</span>
                    </div>
                  ))}
                  {!rows.length && (
                    <div className="plan-more">
                      No file changes are planned.
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </AlertDialogBody>
        <AlertDialogFooter className="dialog-foot">
          <Button
            type="button"
            ref={cancelRef}
            className="button quiet"
            onPress={onClose}
            isDisabled={busy}
            autoFocus
          >
            {applyApplied ? "Done" : "Cancel"}
          </Button>
          <Button
            type="button"
            className="button primary"
            onPress={onConfirm}
            isDisabled={busy || applyApplied || (!dialog.result.plan_hash && !applyDrift) || (!resync && !rows.length)}
          >
            {busy
              ? "Applying…"
              : applyApplied
                ? "Applied"
                : applyDrift
                  ? "Confirm updated plan"
                  : resync
                    ? "Apply resync"
                    : dialog.mode === "cleanup"
                      ? "Delete files from disk"
                      : "Confirm changes"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function TagEditor({
  assetKey,
  allTags,
  assigned,
  busy,
  onAssign,
}: {
  assetKey: string;
  allTags: string[];
  assigned: string[];
  busy: boolean;
  onAssign: (assetKey: string, tag: string) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const assignedSet = new Set(assigned);
  const draft = value.trim().toLowerCase();
  const suggestions = draft
    ? allTags.filter((tag) => !assignedSet.has(tag) && tag.includes(draft)).slice(0, 6)
    : [];
  const canAdd = draft.length > 0 && !assignedSet.has(draft);
  async function submit(tag: string) {
    if (!tag || assignedSet.has(tag) || busy) return;
    try {
      await onAssign(assetKey, tag);
      setValue("");
      setShowSuggestions(false);
    } catch {
      // Error is surfaced by the parent's tagError message.
    }
  }
  return (
    <div className="tag-editor">
      <Input className="tag-add">
        <InputField
          value={value}
          onChangeText={(next: string) => { setValue(next); setShowSuggestions(true); }}
          placeholder="Add a tag…"
          aria-label={`Add tag to ${assetKey}`}
        />
      </Input>
      <Button type="button" className="button quiet" isDisabled={!canAdd || busy} onPress={() => void submit(draft)}>
        Add
      </Button>
      {showSuggestions && suggestions.length > 0 && (
        <ul className="tag-suggestions" aria-label="Existing tags">
          {suggestions.map((tag) => (
            <li key={tag}>
              <Button type="button" className="tag-suggestion" isDisabled={busy} onPress={() => void submit(tag)}>
                {tag}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TagManager({
  tags,
  counts,
  busy,
  error,
  onMutate,
}: {
  tags: string[];
  counts: Map<string, number>;
  busy: boolean;
  error: string;
  onMutate: (change: TagChange) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [newTag, setNewTag] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  async function run(change: TagChange) {
    try {
      await onMutate(change);
      return true;
    } catch {
      return false;
    }
  }
  async function create() {
    const tag = newTag.trim().toLowerCase();
    if (!tag) return;
    if (await run({ action: "create", tag })) setNewTag("");
  }
  async function submitRename() {
    if (!renaming) return;
    const next = renameValue.trim().toLowerCase();
    if (!next) return;
    if (await run({ action: "rename", tag: renaming, new_tag: next })) {
      setRenaming(null);
      setRenameValue("");
    }
  }
  async function submitDelete() {
    if (!deleteTarget) return;
    if (await run({ action: "delete", tag: deleteTarget })) setDeleteTarget(null);
  }
  const deleteCount = deleteTarget ? counts.get(deleteTarget) || 0 : 0;
  return (
    <div className="tag-manage">
      <Button type="button" className="nav-row" aria-expanded={open} onPress={() => setOpen(!open)}>
        <span>Manage tags</span>
      </Button>
      {open && (
        <div className="tag-manage-body">
          <div className="tag-editor">
            <Input className="tag-add">
              <InputField value={newTag} onChangeText={setNewTag} placeholder="New tag" aria-label="New tag name" />
            </Input>
            <Button type="button" className="button quiet" isDisabled={busy || !newTag.trim()} onPress={() => void create()}>
              Create
            </Button>
          </div>
          <ul className="tag-manage-list">
            {[...tags].sort((a, b) => a.localeCompare(b)).map((tag) => (
              <li key={tag} className="tag-manage-row">
                {renaming === tag ? (
                  <>
                    <Input className="tag-add">
                      <InputField value={renameValue} onChangeText={setRenameValue} aria-label={`Rename tag ${tag}`} />
                    </Input>
                    <Button type="button" className="tag-manage-action" isDisabled={busy || !renameValue.trim()} onPress={() => void submitRename()}>Save</Button>
                    <Button type="button" className="tag-manage-action" isDisabled={busy} onPress={() => setRenaming(null)}>Cancel</Button>
                  </>
                ) : (
                  <>
                    <span className="tag-manage-name">{tag}</span>
                    <span className="nav-count" aria-label={`${counts.get(tag) || 0} packages tagged ${tag}`}>{counts.get(tag) || 0}</span>
                    <Button type="button" className="tag-manage-action" isDisabled={busy} onPress={() => { setRenaming(tag); setRenameValue(tag); }}>Rename</Button>
                    <Button type="button" className="tag-manage-action danger-text" isDisabled={busy} onPress={() => setDeleteTarget(tag)}>Delete</Button>
                  </>
                )}
              </li>
            ))}
            {!tags.length && <li className="tag-note">No tags yet. Create one above.</li>}
          </ul>
        </div>
      )}
      {deleteTarget !== null && (
        <AlertDialog
          isOpen
          onClose={() => { if (!busy) setDeleteTarget(null); }}
          isKeyboardDismissable={!busy}
          closeOnOverlayClick={false}
          className="dialog-overlay"
        >
          <AlertDialogBackdrop className="dialog-backdrop" />
          <AlertDialogContent className="action-dialog" aria-labelledby="tag-delete-title" aria-describedby="tag-delete-description">
            <AlertDialogHeader className="dialog-top">
              <div>
                <div className="eyebrow">CONFIRMATION REQUIRED</div>
                <h2 id="tag-delete-title">Delete tag</h2>
              </div>
              <Button type="button" className="icon-button" isDisabled={busy} aria-label="Close dialog" onPress={() => setDeleteTarget(null)}>
                <Icon as={X} className="icon" aria-hidden="true" focusable={false} />
              </Button>
            </AlertDialogHeader>
            <AlertDialogBody>
              <p id="tag-delete-description" className="dialog-lead">
                Delete the tag “{deleteTarget}” from {deleteCount} {deleteCount === 1 ? "package" : "packages"}, including packages temporarily absent from this library? Package files will not be changed.
              </p>
              {error && <p className="tag-note danger-text" role="alert">{error}</p>}
            </AlertDialogBody>
            <AlertDialogFooter className="dialog-foot">
              <Button type="button" className="button quiet" autoFocus isDisabled={busy} onPress={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button type="button" className="button primary" isDisabled={busy} onPress={() => void submitDelete()}>
                {busy ? "Deleting…" : "Delete tag"}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </div>
  );
}

export default App;
