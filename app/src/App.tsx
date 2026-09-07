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
  ArrowRight,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  ExternalLink,
  Folder,
  LayoutGrid,
  List,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";

type QuickFilter = "all" | "pending" | "flagged" | "non-store";
type ViewMode = "grid" | "list";
type SortMode = "name" | "author" | "size";
type DialogState = {
  mode: "resync" | "cleanup" | "organize";
  title: string;
  result: ActionResult;
} | null;

const api = window.uai;

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
  write: "Saving index", emit: "Writing library outputs", complete: "Finished",
  "refreshing index": "Refreshing index", "index refreshed": "Index refreshed",
  "index refresh failed": "Index refresh failed",
  unknown: "Outcome unknown", "outcome unknown": "Outcome unknown",
};

const PROGRESS_COUNT_LABELS: Record<string, string> = { moved: "files moved", skipped: "files skipped", staged: "files staged", removed: "files deleted", rolled_back: "files rolled back", rollback_failed: "rollback failures", resolved: "resolved", failed: "failed", blocked: "blocked", index_added: "index entries added", index_removed: "index entries removed", index_resized: "index entries resized", inventory_examined: "inventory examined", inventory_found: "inventory found" };

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
  const actionLabel = job.kind === "resync" ? "Resync" : job.kind === "cleanup" ? "Cleanup" : job.kind === "organize" ? "Organize" : "Enrich";
  const phaseLabel = job.kind === "enrich" ? "" : " · " + (job.phase === "apply" ? "Apply" : "Preview");
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
function App() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [state, setState] = useState<State | null>(null);
  const [actionStatus, setActionStatus] = useState<ActionJob | null>(null);
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All assets");
  const [quick, setQuick] = useState<QuickFilter>("all");
  const [sort, setSort] = useState<SortMode>(() => (localStorage.getItem("uai:sort") as SortMode) || "name");
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem("uai:view") as ViewMode) || "grid");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [enrichCancelling, setEnrichCancelling] = useState(false);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionTriggerRef = useRef<HTMLButtonElement>(null);
  const actionRequestRef = useRef(false);
  const uiRefreshRef = useRef(false);
  const actionGenerationRef = useRef(0);
  const dismissedRef = useRef(dismissedIds);
  dismissedRef.current = dismissedIds;
  async function load() {
    let loaded = false;
    try {
      setError("");
      const [nextState, nextIndex] = await Promise.all([api.getState(), api.getAssets()]);
      setState(nextState);
      const saved = sessionStorage.getItem("uai:action");
      let savedIdentity: { id: string; kind: "resync" | "cleanup" | "organize"; phase: "plan" | "apply" } | null = null;
      try { if (saved) savedIdentity = JSON.parse(saved); } catch { sessionStorage.removeItem("uai:action"); }
      if (!uiRefreshRef.current && nextState.action && !dismissedRef.current.has(nextState.action.id)) setActionStatus(nextState.action);
      else if (!uiRefreshRef.current && savedIdentity && (!nextState.action || nextState.action.id !== savedIdentity.id)) setActionStatus({ id: savedIdentity.id, kind: savedIdentity.kind, phase: savedIdentity.phase, status: "running", stage: "Recovering operation", completed: 0, total: null, current_item: null, counts: {} });
      setAssets(nextIndex.assets || []);
      setSelectedKey((current) => current && nextIndex.assets.some((asset) => asset.asset_key === current) ? current : nextIndex.assets[0]?.asset_key || null);
      loaded = true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The backend could not be reached.");
    } finally {
      setLoading(false);
    }
    return loaded;
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => { localStorage.setItem("uai:sort", sort); }, [sort]);
  useEffect(() => { localStorage.setItem("uai:view", view); }, [view]);
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

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return assets
      .filter((asset) => {
        const haystack = [
          asset.name,
          asset.local_name,
          asset.author,
          asset.category?.path,
          ...(asset.tags || []),
          ...(asset.flags || []),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (needle && !haystack.includes(needle)) return false;
        const assetCategory = asset.category?.path || "Uncategorized";
        if (
          category !== "All assets" &&
          assetCategory !== category &&
          !assetCategory.startsWith(`${category}/`)
        )
          return false;
        if (quick === "pending" && !isPending(asset)) return false;
        if (quick === "flagged" && !(asset.flags || []).length) return false;
        if (quick === "non-store" && !asset.non_store) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "author")
          return (
            (a.author || "").localeCompare(b.author || "") ||
            a.name.localeCompare(b.name)
          );
        if (sort === "size")
          return assetSize(b) - assetSize(a) || a.name.localeCompare(b.name);
        return a.name.localeCompare(b.name);
      });
  }, [assets, category, query, quick, sort]);

  const selected =
    assets.find((asset) => asset.asset_key === selectedKey) ||
    filtered[0] ||
    null;
  const flaggedCount = assets.filter(
    (asset) => (asset.flags || []).length > 0,
  ).length;
  const pendingCount =
    state?.pending_enrichment ?? assets.filter(isPending).length;
  const storeCount = assets.filter((asset) => !asset.non_store).length;

  async function runAction(name: "resync" | "cleanup" | "organize") {
    try {
      setBusy(name);
      setError("");
      const response = name === "resync" ? await api.resync() : name === "cleanup" ? await api.cleanupPlan() : await api.organizePlan();
      sessionStorage.setItem("uai:action", JSON.stringify({ id: response.job_id, kind: name, phase: "plan" }));
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
      sessionStorage.setItem("uai:action", JSON.stringify({ id: response.job_id, kind: dialog.mode, phase: "apply" }));
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
    sessionStorage.removeItem("uai:action");
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
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">UI</div>
          <div>
            <div className="eyebrow">UNITY ASSET INDEX</div>
            <h1>Asset library</h1>
          </div>
        </div>
        <div className="top-actions">
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
                isDisabled={Boolean(busy) || loading}
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
                isDisabled={Boolean(busy) || loading || Boolean(jobRunning)}
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
                isDisabled={Boolean(busy) || loading}
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
                isDisabled={Boolean(busy) || loading}
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

      <main className="workspace">
        <aside className="sidebar">
          <Input className="search-wrap">
            <Icon
              as={Search}
              className="icon"
              aria-hidden="true"
              focusable={false}
            />
            <InputField
              value={query}
              onChangeText={setQuery}
              placeholder="Search library"
              aria-label="Search library"
            />
          </Input>
          <section className="side-section">
            <div className="section-label">Library</div>
            {(
              [
                ["all", "All assets", assets.length],
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
                    onSelect={(path) => {
                      setCategory(path);
                      setQuick("all");
                    }}
                  />
                ))}
            </ul>
          </section>
          <div className="sidebar-foot">
            <div className="mini-status">
              <span className="connection-dot" />
              <span>{storeCount} store records</span>
            </div>
            <span className="muted">Python engine online</span>
          </div>
        </aside>

        <section className="content-column">
          <div className="content-head">
            <div>
              <h2>{category}</h2>
              <p>
                {filtered.length} of {assets.length} assets visible
              </p>
            </div>
            <div className="view-controls">
              <Button
                type="button"
                className={`icon-button ${view === "grid" ? "selected" : ""}`}
                onPress={() => setView("grid")}
                aria-label="Grid view"
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
          {actionStatus && !dismissedIds.has(actionStatus.id) && <ActionProgress job={actionStatus} onDismiss={dismissAction} announce={!(dialog && actionStatus.phase === "apply")} />}
          {state?.job && !dismissedIds.has(state.job.id) && <ActionProgress job={state.job} onDismiss={dismissEnrich} />}
          {loading ? (
            <div className="loading-state">
              <Spinner className="spinner" aria-label="loading" />
              Loading your library
            </div>
          ) : filtered.length ? (
            <div className={view === "grid" ? "asset-grid" : "asset-list"}>
              {filtered.map((asset) => (
                <AssetCard
                  key={asset.asset_key}
                  asset={asset}
                  selected={asset.asset_key === selected?.asset_key}
                  view={view}
                  onClick={() => setSelectedKey(asset.asset_key)}
                />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <Icon
                as={CircleSlash}
                className="icon empty-mark"
                aria-hidden="true"
                focusable={false}
              />
              <h3>No assets match</h3>
              <p>Try clearing a filter or searching for a broader term.</p>
              <Button
                type="button"
                className="button quiet"
                onPress={() => {
                  setQuery("");
                  setCategory("All assets");
                  setQuick("all");
                }}
              >
                Clear filters
              </Button>
            </div>
          )}
        </section>

        <aside className="inspector">
          {selected ? (
            <Inspector
              asset={selected}
              onOpen={(url) => void api.openExternal(url)}
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
        onPress={onClick}
      >
        <span className="list-name">
          <span className="list-avatar">{initials(asset)}</span>
          <strong>{asset.name}</strong>
        </span>
        <span>{asset.author || "Unknown author"}</span>
        <span>{asset.category?.path || "Uncategorized"}</span>
        <span>{asset.upstream_version || "—"}</span>
        <span>{flagged ? <span className="flag-dot" /> : ""}</span>
      </Button>
    );
  return (
    <Button
      type="button"
      className={`asset-card ${selected ? "selected" : ""}`}
      onPress={onClick}
    >
      <div className="thumb">
        {asset.thumbnail?.remote ? (
          <img src={asset.thumbnail.remote} loading="lazy" alt="" />
        ) : (
          <span>{initials(asset)}</span>
        )}
        <span className="source-badge">
          {asset.non_store ? "LOCAL" : "STORE"}
        </span>
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
  onOpen,
}: {
  asset: Asset;
  onOpen: (url: string) => void;
}) {
  return (
    <div className="inspector-inner">
      <div className="inspector-kicker">ASSET DETAILS</div>
      <div className="inspector-hero">
        <div className="hero-avatar">{initials(asset)}</div>
        <h2>{asset.name}</h2>
        <p>{asset.author || "Unknown author"}</p>
      </div>
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
            Open Unity Asset Store
          </Button>
        )}
      </div>
      <div className="detail-block">
        <div className="section-label">At a glance</div>
        <dl>
          <div>
            <dt>Category</dt>
            <dd>{asset.category?.path || "Uncategorized"}</dd>
          </div>
          <div>
            <dt>Local name</dt>
            <dd>{asset.local_name || "—"}</dd>
          </div>
          <div>
            <dt>Store ID</dt>
            <dd>{asset.store_id || "—"}</dd>
          </div>
          <div>
            <dt>Latest version</dt>
            <dd>{asset.upstream_version || "—"}</dd>
          </div>
        </dl>
      </div>
      {(asset.tags || []).length > 0 && (
        <div className="detail-block">
          <div className="section-label">Tags</div>
          <div className="tag-list">
            {asset.tags!.map((tag) => (
              <span key={tag} className="tag">
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="detail-block">
        <div className="section-label">
          On disk <span>{asset.versions?.length || 0}</span>
        </div>
        <div className="version-list">
          {(asset.versions || []).slice(0, 5).map((version, index) => (
            <div key={(version.file || "Unnamed file") + "-" + index}>
              <span>{version.file || "Unnamed file"}</span>
              <small>{version.duplicate ? "Duplicate" : "Archive"}</small>
            </div>
          ))}
        </div>
      </div>
    </div>
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
            Cancel
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

export default App;
