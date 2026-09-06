import { useEffect, useMemo, useRef, useState } from "react";

type QuickFilter = "all" | "pending" | "flagged" | "non-store";
type ViewMode = "grid" | "list";
type SortMode = "name" | "author" | "size";
type DialogState = { mode: "resync" | "cleanup" | "organize"; title: string; result: ActionResult } | null;

const api = window.uai;

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function assetSize(asset: Asset) {
  return (asset.versions || []).reduce((sum, version) => sum + Number(version.size_bytes || 0), 0);
}

function isPending(asset: Asset) {
  return !asset.non_store && asset.resolution?.id_verified !== true;
}

function initials(asset: Asset) {
  return (asset.name || asset.local_name || "Asset").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function Icon({ name }: { name: "search" | "refresh" | "folder" | "spark" | "grid" | "list" | "external" }) {
  const paths: Record<string, string> = {
    search: "M11 19a8 8 0 1 1 5.66-2.34L22 22M16.66 16.66 22 22",
    refresh: "M20 11a8.1 8.1 0 0 0-14.7-3L3 11m0 0V5m0 6h6M4 13a8.1 8.1 0 0 0 14.7 3L21 13m0 0v6m0-6h-6",
    folder: "M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z",
    spark: "m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4z",
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    list: "M5 6h14M5 12h14M5 18h14",
    external: "M14 4h6v6M20 4l-9 9M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5",
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d={paths[name]} /></svg>;
}

type CategoryNode = { name: string; path: string; count: number; children: Map<string, CategoryNode> };

function CategoryBranch({ node, selected, onSelect }: { node: CategoryNode; selected: string; onSelect: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = node.children.size > 0;
  return <li>
    <div className={`category-row ${selected === node.path ? "active" : ""}`}>
      {hasChildren ? <button className="category-toggle" aria-label={`${expanded ? "Collapse" : "Expand"} ${node.path}`} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><svg aria-hidden="true" viewBox="0 0 24 24" className="icon"><path d={expanded ? "m6 9 6 6 6-6" : "m9 6 6 6-6 6"} /></svg></button> : <span className="category-toggle-space" />}
      <button className={`nav-row ${selected === node.path ? "active" : ""}`} title={node.path} aria-current={selected === node.path ? "true" : undefined} onClick={() => { onSelect(node.path); if (hasChildren) setExpanded(true); }}><span className="category-name"><Icon name="folder" /><span>{node.name}</span></span><span className="nav-count">{node.count}</span></button>
    </div>
    {hasChildren && <ul hidden={!expanded}>{[...node.children.values()].sort((a, b) => a.name.localeCompare(b.name)).map((child) => <CategoryBranch key={child.path} node={child} selected={selected} onSelect={onSelect} />)}</ul>}
  </li>;
}

function App() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [state, setState] = useState<State | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All assets");
  const [quick, setQuick] = useState<QuickFilter>("all");
  const [sort, setSort] = useState<SortMode>(() => (localStorage.getItem("uai:sort") as SortMode) || "name");
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem("uai:view") as ViewMode) || "grid");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);

  async function load() {
    try {
      setError("");
      const [nextState, nextIndex] = await Promise.all([api.getState(), api.getAssets()]);
      setState(nextState);
      setAssets(nextIndex.assets || []);
      setSelectedKey((current) => current && nextIndex.assets.some((asset) => asset.asset_key === current) ? current : nextIndex.assets[0]?.asset_key || null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The backend could not be reached.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => { localStorage.setItem("uai:sort", sort); }, [sort]);
  useEffect(() => { localStorage.setItem("uai:view", view); }, [view]);

  useEffect(() => {
    const job = state?.job;
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.getState();
        setState(next);
        if (next.job && ["completed", "failed", "cancelled"].includes(next.job.status)) {
          window.clearInterval(timer);
          if (next.job.status === "completed") await load();
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Could not read job progress.");
      }
    }, 900);
    return () => window.clearInterval(timer);
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
    return assets.filter((asset) => {
      const haystack = [asset.name, asset.local_name, asset.author, asset.category?.path, ...(asset.tags || []), ...(asset.flags || [])].filter(Boolean).join(" ").toLowerCase();
      if (needle && !haystack.includes(needle)) return false;
      const assetCategory = asset.category?.path || "Uncategorized";
      if (category !== "All assets" && assetCategory !== category && !assetCategory.startsWith(`${category}/`)) return false;
      if (quick === "pending" && !isPending(asset)) return false;
      if (quick === "flagged" && !(asset.flags || []).length) return false;
      if (quick === "non-store" && !asset.non_store) return false;
      return true;
    }).sort((a, b) => {
      if (sort === "author") return (a.author || "").localeCompare(b.author || "") || a.name.localeCompare(b.name);
      if (sort === "size") return assetSize(b) - assetSize(a) || a.name.localeCompare(b.name);
      return a.name.localeCompare(b.name);
    });
  }, [assets, category, query, quick, sort]);

  const selected = assets.find((asset) => asset.asset_key === selectedKey) || filtered[0] || null;
  const flaggedCount = assets.filter((asset) => (asset.flags || []).length > 0).length;
  const pendingCount = state?.pending_enrichment ?? assets.filter(isPending).length;
  const storeCount = assets.filter((asset) => !asset.non_store).length;

  async function runAction(name: "resync" | "organize") {
    try {
      setBusy(name);
      setError("");
      const result = name === "resync" ? await api.resync() : await api.organizePlan();
      setDialog({ mode: name, title: name === "resync" ? "Review resync" : "Review organization", result });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The action failed.");
    } finally {
      setBusy(null);
    }
  }

  async function startEnrich() {
    try {
      setBusy("enrich");
      setError("");
      await api.enrichStart();
      setState(await api.getState());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Enrichment could not start.");
    } finally {
      setBusy(null);
    }
  }

  async function cancelEnrich() {
    try { await api.enrichCancel(); setState(await api.getState()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Enrichment could not be cancelled."); }
  }

  async function confirmDialog() {
    if (!dialog?.result.plan_hash || busy) return;
    try {
      setBusy(dialog.mode);
      setError("");
      if (dialog.mode === "resync") {
        const result = await api.resyncApply(dialog.result.plan_hash);
        setDialog(result.preview ? { mode: "cleanup", title: "Review disk cleanup", result } : null);
        if (result.cleanup_error) setError("Resync completed, but cleanup could not be previewed: " + result.cleanup_error);
      } else {
        if (dialog.mode === "cleanup") await api.cleanupApply(dialog.result.plan_hash);
        else await api.organizeApply(dialog.result.plan_hash);
        setDialog(null);
      }
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The plan changed before it could be applied.");
      setDialog(null);
    } finally { setBusy(null); }
  }

  const jobFailure = state?.job?.status === "failed" ? state.job.error || "Enrichment failed." : "";
  const jobRunning = state?.job && ["queued", "running"].includes(state.job.status);
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark">UI</div><div><div className="eyebrow">UNITY ASSET INDEX</div><h1>Asset library</h1></div></div>
      <div className="top-actions">
        <button className="button primary" popoverTarget="library-actions">Library actions <span aria-hidden="true">▾</span></button>
        <div id="library-actions" className="library-actions" popover="auto" aria-label="Library actions">
          <div className="summary-strip"><div><span className="summary-label">INDEXED</span><strong>{assets.length}</strong></div><div><span className="summary-label">PENDING</span><strong className={pendingCount ? "accent-text" : ""}>{pendingCount}</strong></div><div><span className="summary-label">FLAGGED</span><strong>{flaggedCount}</strong></div></div>
          <div className={`actions-intro connection ${error ? "offline" : ""}`}><span className="connection-dot" />{error ? "Needs attention" : loading ? "Starting Python" : "Connected"}</div>
          <p className="actions-intro">Choose an action for your library.</p>
          <button className="library-action" popoverTarget="library-actions" popoverTargetAction="hide" onClick={() => void runAction("resync")} disabled={Boolean(busy) || loading}>
            <Icon name="refresh" /><span><strong>{busy === "resync" ? "Syncing…" : "Resync"}</strong><span>Scan local files and review index changes before applying. Old-version cleanup is a separate confirmation.</span></span>
          </button>
          <button className="library-action" popoverTarget="library-actions" popoverTargetAction="hide" onClick={() => void startEnrich()} disabled={Boolean(busy) || loading || Boolean(jobRunning)}>
            <Icon name="spark" /><span><strong>{jobRunning ? "Enrich · running" : "Enrich"}</strong><span>Look up pending assets online to add Store details, categories, thumbnails, and ratings. Does not change package files.</span></span>
          </button>
          <button className="library-action" popoverTarget="library-actions" popoverTargetAction="hide" onClick={() => void runAction("organize")} disabled={Boolean(busy) || loading}>
            <Icon name="folder" /><span><strong>{busy === "organize" ? "Planning…" : "Organize"}</strong><span>Preview moving archives into Store-category folders. Moves files only after confirmation; never renames or unpacks them.</span></span>
          </button>
          {jobRunning && <button className="button quiet danger-text" popoverTarget="library-actions" popoverTargetAction="hide" onClick={() => void cancelEnrich()}>Cancel enrich</button>}
          <p className="actions-hint">Suggested order: Resync → Enrich → Organize</p>
        </div>
      </div>
    </header>

    {(error || jobFailure) && <div className="error-banner"><strong>{jobFailure ? "Enrichment failed" : "Backend notice"}</strong><span>{error || jobFailure}</span><button onClick={() => { if (jobFailure) setState((current) => current ? { ...current, job: null } : current); setError(""); if (!jobFailure) void load(); }}>{jobFailure ? "Dismiss" : "Retry"}</button></div>}

    <main className="workspace">
      <aside className="sidebar">
        <div className="search-wrap"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search library" aria-label="Search library" /></div>
        <section className="side-section"><div className="section-label">Library</div>
          {([ ["all", "All assets", assets.length], ["pending", "Needs enrichment", pendingCount], ["flagged", "Flagged", flaggedCount], ["non-store", "Local only", assets.filter((asset) => asset.non_store).length] ] as const).map(([key, label, count]) => <button key={key} className={`nav-row ${quick === key ? "active" : ""}`} onClick={() => setQuick(key)}><span>{label}</span><span className="nav-count">{count}</span></button>)}
        </section>
        <section className="side-section categories"><div className="section-label">Categories <span>{categoryTree.size}</span></div>
          <button className={`nav-row ${category === "All assets" ? "active" : ""}`} onClick={() => setCategory("All assets")}><span>All categories</span><span className="nav-count">{assets.length}</span></button>
          <ul className="category-tree">{[...categoryTree.values()].sort((a, b) => a.name.localeCompare(b.name)).map((node) => <CategoryBranch key={node.path} node={node} selected={category} onSelect={(path) => { setCategory(path); setQuick("all"); }} />)}</ul>
        </section>
        <div className="sidebar-foot"><div className="mini-status"><span className="connection-dot" /><span>{storeCount} store records</span></div><span className="muted">Python engine online</span></div>
      </aside>

      <section className="content-column">
        <div className="content-head"><div><h2>{category}</h2><p>{filtered.length} of {assets.length} assets visible</p></div><div className="view-controls"><button className={`icon-button ${view === "grid" ? "selected" : ""}`} onClick={() => setView("grid")} aria-label="Grid view"><Icon name="grid" /></button><button className={`icon-button ${view === "list" ? "selected" : ""}`} onClick={() => setView("list")} aria-label="List view"><Icon name="list" /></button><select value={sort} onChange={(event) => setSort(event.target.value as SortMode)} aria-label="Sort assets"><option value="name">Name</option><option value="author">Author</option><option value="size">Size</option></select></div></div>
        {state?.job && state.job.status !== "completed" && state.job.status !== "cancelled" && <div className={`job-line ${state.job.status === "failed" ? "failed" : ""}`}><span className={state.job.status === "failed" ? "status-mark failed" : "spinner"} />{state.job.status === "failed" ? "Enrichment failed" : state?.job?.stage ? `Enrichment · ${state.job.stage}` : "Starting enrichment"}<span className="job-error">{state?.job?.error || ""}</span></div>}
        {loading ? <div className="loading-state"><span className="spinner" />Loading your library</div> : filtered.length ? <div className={view === "grid" ? "asset-grid" : "asset-list"}>{filtered.map((asset) => <AssetCard key={asset.asset_key} asset={asset} selected={asset.asset_key === selected?.asset_key} view={view} onClick={() => setSelectedKey(asset.asset_key)} />)}</div> : <div className="empty-state"><div className="empty-mark">∅</div><h3>No assets match</h3><p>Try clearing a filter or searching for a broader term.</p><button className="button quiet" onClick={() => { setQuery(""); setCategory("All assets"); setQuick("all"); }}>Clear filters</button></div>}
      </section>

      <aside className="inspector">{selected ? <Inspector asset={selected} onOpen={(url) => void api.openExternal(url)} /> : <div className="inspector-empty"><div className="empty-mark">＋</div><h3>Select an asset</h3><p>Details, versions, and links appear here.</p></div>}</aside>
    </main>
    {dialog && <ActionDialog dialog={dialog} busy={Boolean(busy)} onClose={() => setDialog(null)} onConfirm={() => void confirmDialog()} />}
  </div>;
}

function AssetCard({ asset, selected, view, onClick }: { asset: Asset; selected: boolean; view: ViewMode; onClick: () => void }) {
  const flagged = (asset.flags || []).length > 0;
  if (view === "list") return <button className={`list-row ${selected ? "selected" : ""}`} onClick={onClick}><span className="list-name"><span className="list-avatar">{initials(asset)}</span><strong>{asset.name}</strong></span><span>{asset.author || "Unknown author"}</span><span>{asset.category?.path || "Uncategorized"}</span><span>{asset.upstream_version || "—"}</span><span>{flagged ? <span className="flag-dot" /> : ""}</span></button>;
  return <button className={`asset-card ${selected ? "selected" : ""}`} onClick={onClick}><div className="thumb">{asset.thumbnail?.remote ? <img src={asset.thumbnail.remote} loading="lazy" alt="" /> : <span>{initials(asset)}</span>}<span className="source-badge">{asset.non_store ? "LOCAL" : "STORE"}</span></div><div className="card-copy"><div className="card-title">{asset.name}</div><div className="card-meta">{asset.author || "Unknown author"}</div><div className="card-bottom"><span>{asset.category?.levels?.[0] || "Other"}</span>{flagged && <span className="flag-dot" aria-label="Flagged" />}</div></div></button>;
}

function Inspector({ asset, onOpen }: { asset: Asset; onOpen: (url: string) => void }) {
  return <div className="inspector-inner"><div className="inspector-kicker">ASSET DETAILS</div><div className="inspector-hero"><div className="hero-avatar">{initials(asset)}</div><h2>{asset.name}</h2><p>{asset.author || "Unknown author"}</p></div><div className="inspector-actions">{asset.store && <button className="button primary wide" onClick={() => onOpen(asset.store!)}><Icon name="external" />Open Unity Asset Store</button>}</div><div className="detail-block"><div className="section-label">At a glance</div><dl><div><dt>Category</dt><dd>{asset.category?.path || "Uncategorized"}</dd></div><div><dt>Local name</dt><dd>{asset.local_name || "—"}</dd></div><div><dt>Store ID</dt><dd>{asset.store_id || "—"}</dd></div><div><dt>Latest version</dt><dd>{asset.upstream_version || "—"}</dd></div></dl></div>{(asset.tags || []).length > 0 && <div className="detail-block"><div className="section-label">Tags</div><div className="tag-list">{asset.tags!.map((tag) => <span key={tag} className="tag">{tag}</span>)}</div></div>}<div className="detail-block"><div className="section-label">On disk <span>{asset.versions?.length || 0}</span></div><div className="version-list">{(asset.versions || []).slice(0, 5).map((version, index) => <div key={`${version.file}-${index}`}><span>{version.file || "Unnamed file"}</span><small>{version.duplicate ? "Duplicate" : "Archive"}</small></div>)}</div></div></div>;
}

function ActionDialog({ dialog, busy, onClose, onConfirm }: { dialog: NonNullable<DialogState>; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const modal = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = modal.current!;
    element.showModal();
    return () => element.close();
  }, []);
  const preview = dialog.result.preview || {};
  const resync = dialog.mode === "resync";
  const rows = dialog.mode === "cleanup" ? preview.removals || [] : preview.moves || [];
  const totals = preview.totals || {};
  const groups = [
    { label: "Add to index", rows: preview.additions || [], tone: "add", hint: "Files found in the vault that are not indexed yet." },
    { label: "Remove from index", rows: preview.removals || [], tone: "remove", hint: "Files no longer found in the vault. Does not delete files from disk." },
    { label: "Update size in index", rows: preview.resized || [], tone: "resize", hint: "Existing paths whose file size changed." },
  ];
  return <dialog ref={modal} className="action-dialog" aria-labelledby="action-title" aria-describedby="action-description" onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-top"><div><div className="eyebrow">CONFIRMATION REQUIRED</div><h2 id="action-title">{dialog.title}</h2></div><button className="icon-button" onClick={onClose} disabled={busy} aria-label="Close dialog">×</button></div>
    <p id="action-description" className="dialog-lead">{resync ? "Review the vault-relative paths below before updating the index. No files will be deleted from disk." : dialog.mode === "cleanup" ? "The index is synced. This separate cleanup will delete the listed files from disk only if you confirm." : "Review these moves before anything changes on disk."}</p>
    {resync ? <>
      <div className="dialog-stats">{groups.map((group) => <div key={group.tone}><strong className={group.tone === "add" ? "added-text" : group.tone === "remove" ? "danger-text" : ""}>{group.rows.length}</strong><span>{group.label}</span></div>)}</div>
      <div className="resync-groups">{groups.filter((group) => group.tone !== "resize" || group.rows.length > 0).map((group) => <section className="change-group" key={group.tone} aria-label={group.label}>
        <h3>{group.label} <span>{group.rows.length}</span></h3><p>{group.hint}</p>
        <ul className="change-list">{group.rows.map((row) => <li key={row.path}><span>{row.path}</span><small>{group.tone === "resize" ? formatBytes(row.previous_size_bytes ?? 0) + " → " : ""}{formatBytes(row.size_bytes ?? 0)}</small></li>)}</ul>
        {!group.rows.length && <p className="change-empty">No files to {group.tone === "add" ? "add" : "remove"}.</p>}
      </section>)}</div>
    </> : <>
      <div className="dialog-stats"><div><strong>{totals.files ?? totals.moves ?? rows.length}</strong><span>{dialog.mode === "cleanup" ? "files to delete" : "moves planned"}</span></div><div><strong>{formatBytes(totals.bytes ?? totals.move_bytes ?? 0)}</strong><span>storage</span></div></div>
      <div className="plan-list">{rows.map((row, index) => <div key={row.path || row.src || index}><span>{row.path || row.src}</span><span className="plan-arrow">→</span><span>{row.dst || formatBytes(row.size_bytes ?? 0)}</span></div>)}{!rows.length && <div className="plan-more">No file changes are planned.</div>}</div>
    </>}
    <div className="dialog-foot"><button className="button quiet" onClick={onClose} disabled={busy} autoFocus>Cancel</button><button className="button primary" onClick={onConfirm} disabled={busy || !dialog.result.plan_hash || (!resync && !rows.length)}>{busy ? "Applying…" : resync ? "Apply resync" : dialog.mode === "cleanup" ? "Delete files from disk" : "Confirm changes"}</button></div>
  </dialog>;
}

export default App;
