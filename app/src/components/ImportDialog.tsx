import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { AlertDialog, AlertDialogBackdrop, AlertDialogBody, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, Button, Icon } from "./ui";
import { ArrowDown, ArrowUp, ArrowDownUp, Check, CircleAlert, Cloud, Folder, Package, ShieldCheck, X } from "lucide-react";
import SpotlightCard from "./react-bits/SpotlightCard";
import GlideSelect from "./react-bits/GlideSelect";
import LatticeLoader from "./react-bits/LatticeLoader";
import SquishSwitch from "./react-bits/SquishSwitch";

const api = window.ual;
const filename = (file: string) => file.split(/[\\/]/).pop() || file;

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes;
  let unit = -1;
  do { value /= 1024; unit += 1; } while (value >= 1024 && unit < units.length - 1);
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function unityPackages(asset: Asset) {
  return (asset.versions || []).filter((version) => (version.file || "").toLowerCase().endsWith(".unitypackage"));
}
const AVAILABILITY_LABELS: Record<NonNullable<AssetVersion["availability"]>, string> = {
  cloud_only: "Cloud only", local: "Local", unknown: "Availability unknown", missing: "Missing",
};
export function availabilityLabel(availability?: AssetVersion["availability"]) {
  return availability ? AVAILABILITY_LABELS[availability] : null;
}
export function availabilitySummary(versions: AssetVersion[]) {
  const statuses = (versions || []).map((version) => version.availability);
  if (!statuses.length || statuses.every((status) => status == null)) return null;
  if (statuses.every((status) => status === "local")) return { label: "Available locally", short: "ON DISK", kind: "local" };
  if (statuses.every((status) => status === "cloud_only")) return { label: "Cloud only", short: "CLOUD", kind: "cloud_only" };
  if (statuses.every((status) => status === "unknown")) return { label: "Availability unknown", short: "UNKNOWN", kind: "unknown" };
  if (statuses.every((status) => status === "missing")) return { label: "Missing", short: "MISSING", kind: "missing" };
  return { label: "Mixed availability", short: "MIXED", kind: "mixed" };
}

// Archive labels come from the selected filename, never the store's latest version.
function archiveLabel(file: string) {
  return filename(file).match(/(?:^|[\s_-])(v?\d+(?:\.\d+)+(?:[a-z]\d*)?)(?=[\s_.(-]|$)/i)?.[1] || "Archive details";
}

type ReviewRow = { id: string; assetKey: string | null; name: string; file: string };
type ImportDialogProps = {
  assets: Asset[];
  catalog: Asset[];
  job: ImportJob | null;
  finalFocusRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onStart: (request: ImportRequest) => Promise<void>;
  onStop: () => Promise<void>;
  onReview: (keys: string[]) => void;
  onRemove: (key: string) => void;
  onRefreshAssets: () => Promise<void>;
};

function ArchiveDetails({ file, version }: { file: string; version?: AssetVersion }) {
  return <details className="import-archive-details">
    <summary>{archiveLabel(file)}</summary>
    <div><code>{file}</code>{version?.size_bytes != null && <span>{formatBytes(version.size_bytes)}</span>}{version?.editor_version && <span>Archive editor: {version.editor_version}</span>}</div>
  </details>;
}

function ResultRow({ row, name, finished, onReveal }: { row: ImportResultRow; name: string; finished: boolean; onReveal: (path: string) => void }) {
  const active = ["preparing", "downloading", "installing"].includes(row.status);
  const labels: Record<ImportResultRow["status"], string> = {
    pending: finished ? "Not imported" : "Waiting", preparing: "Preparing", downloading: "Downloading", ready: finished ? "Not imported" : "Ready", installing: "Installing", installed: "Installed", failed: "Failed", cancelled: "Not imported",
  };
  return <li className="import-result-row" data-status={row.status}>
    <span className="import-row-symbol" aria-hidden="true">{active ? <LatticeLoader label="" decorative cellSize={3} gap={1} /> : <Icon as={row.status === "installed" ? Check : row.status === "failed" ? CircleAlert : Package} className="icon" />}</span>
    <div className="import-package-main">
      <strong>{name}</strong>
      <ArchiveDetails file={row.file} />
      {row.status === "failed" && <details className="import-error-details"><summary><span>{row.error || "The package could not be imported."}</span><small>Details</small></summary><p>{row.error || "The package could not be imported."}</p></details>}
      {row.backup_path && <Button type="button" className="import-backup import-text-button" onPress={() => onReveal(row.backup_path!)}>Show backups</Button>}
      {(row.status === "preparing" || row.status === "downloading") && Boolean(row.bytes_total && row.bytes_total > 0) && <div className="import-download">
        <span>{formatBytes(row.bytes_completed ?? 0)} / {formatBytes(row.bytes_total!)}</span>
        <span className="prep-progress" role="progressbar" aria-label={`Download progress for ${name}`} aria-valuemin={0} aria-valuemax={row.bytes_total!} aria-valuenow={Math.min(row.bytes_total!, row.bytes_completed ?? 0)}><span style={{ width: `${Math.min(100, Math.max(0, (row.bytes_completed ?? 0) / row.bytes_total! * 100))}%` }} /></span>
      </div>}
    </div>
    <span className="import-status" data-kind={row.status}>{labels[row.status]}</span>
  </li>;
}

export function ImportDialog({ assets, catalog, job, finalFocusRef, onClose, onStart, onStop, onReview, onRemove, onRefreshAssets }: ImportDialogProps) {
  const running = Boolean(job && ["queued", "running"].includes(job.status));
  const finished = Boolean(job && !running);
  const [order, setOrder] = useState<ReviewRow[]>(() => assets.map((asset) => {
    const versions = unityPackages(asset);
    return { id: asset.asset_key, assetKey: asset.asset_key, name: asset.name, file: versions.length === 1 ? versions[0].file || "" : "" };
  }));
  const byKey = useMemo(() => new Map(catalog.map((asset) => [asset.asset_key, asset])), [catalog]);
  const byFile = useMemo(() => new Map(catalog.flatMap((asset) => unityPackages(asset).map((version) => [version.file, asset] as const))), [catalog]);
  const [projects, setProjects] = useState<UnityProject[] | null>(null);
  const [projectsError, setProjectsError] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [projectInfo, setProjectInfo] = useState<ImportProject | null>(null);
  const [editingProject, setEditingProject] = useState(true);
  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState("");
  // Replacement is intentionally enabled; the warning stays visible before consent.
  const [overwrite, setOverwrite] = useState(true);
  const [reordering, setReordering] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const inspectEpochRef = useRef(0);
  const firstButtonRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  async function loadProjects() {
    setProjects(null);
    setProjectsError("");
    try { setProjects(await api.importProjects()); }
    catch (cause) { setProjects([]); setProjectsError(cause instanceof Error ? cause.message : "Could not load Unity Hub projects."); }
  }
  async function refreshAssets() {
    setRefreshing(true);
    setRefreshError("");
    try { await onRefreshAssets(); }
    catch (cause) { setRefreshError(cause instanceof Error ? cause.message : "Could not refresh package availability."); }
    finally { setRefreshing(false); }
  }
  useEffect(() => { void loadProjects(); void refreshAssets(); }, []);
  useEffect(() => {
    bodyRef.current?.scrollTo(0, 0);
    firstButtonRef.current?.focus();
  }, [job?.id, finished]);

  async function inspect(path: string) {
    const epoch = ++inspectEpochRef.current;
    setInspecting(true);
    setInspectError("");
    try {
      const info = await api.inspectImportProject(path);
      if (epoch !== inspectEpochRef.current) return;
      setProjectInfo(info);
      setProjectPath(info.path);
      setEditingProject(false);
    } catch (cause) {
      if (epoch !== inspectEpochRef.current) return;
      setProjectInfo(null);
      setInspectError(cause instanceof Error ? cause.message : "Could not inspect the project.");
    } finally { if (epoch === inspectEpochRef.current) setInspecting(false); }
  }
  async function handleProject(path: string) {
    inspectEpochRef.current += 1;
    setProjectPath(path);
    setProjectInfo(null);
    setInspecting(false);
    setInspectError("");
    // Changing projects must not silently undo the user's replacement choice.
    if (path) await inspect(path);
  }
  async function browseProject() {
    try { const path = await api.chooseImportProject(); if (path) await handleProject(path); }
    catch (cause) { setInspectError(cause instanceof Error ? cause.message : "Could not open the folder picker."); }
  }
  function versionsFor(row: ReviewRow) { return row.assetKey && byKey.has(row.assetKey) ? unityPackages(byKey.get(row.assetKey)!) : []; }
  const unresolved = order.filter((row) => !versionsFor(row).some((version) => version.file === row.file));
  const missing = order.filter((row) => versionsFor(row).find((version) => version.file === row.file)?.availability === "missing");
  const blocker = !order.length ? "Select at least one package" : refreshing ? "Checking package availability…" : refreshError ? "Refresh package availability to continue" : !projectPath ? "Choose a project" : inspecting ? "Checking project…" : !projectInfo || projectInfo.path !== projectPath ? "Choose a valid Unity project" : unresolved.length ? `Choose ${unresolved.length} archive version${unresolved.length === 1 ? "" : "s"}` : missing.length ? `${missing.length} archive${missing.length === 1 ? " is" : "s are"} missing` : "";
  const canStart = !blocker && !job && !starting;
  async function start() {
    if (!canStart) return;
    setStarting(true);
    setActionError("");
    try { await onStart({ project: projectPath, overwrite, packages: order.map((row) => ({ asset_key: row.assetKey!, file: row.file })) }); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "Could not start the import."); }
    finally { setStarting(false); }
  }
  async function stop() {
    setStopping(true);
    setActionError("");
    try { await onStop(); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "Could not stop the import. Try again."); }
    finally { setStopping(false); }
  }
  async function reveal(path: string) {
    try { await api.revealItem(path); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "Could not show backups."); }
  }
  function move(index: number, delta: number) {
    const next = [...order];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setOrder(next);
    setNotice(`${order[index].name} moved to position ${target + 1}`);
  }
  function remove(row: ReviewRow) {
    setOrder((current) => current.filter((item) => item.id !== row.id));
    if (row.assetKey) onRemove(row.assetKey);
    setNotice(`${row.name} removed`);
  }

  const results = job?.results || [];
  const installed = results.filter((row) => row.status === "installed");
  const failed = results.filter((row) => row.status === "failed");
  const remaining = results.filter((row) => row.status !== "installed");
  const notImported = remaining.length - failed.length;
  const preflight = job?.status === "failed" && !failed.length;
  const recoveryLabel = preflight ? "Review selection" : failed.length && !notImported ? "Review failed packages" : "Review remaining packages";
  function review() {
    if (!job || running) return;
    // Preflight errors leave rows pending. Preserve them, including archives no longer indexed.
    const rows = remaining.map((result): ReviewRow => {
      const asset = byFile.get(result.file);
      const previous = order.find((row) => row.file === result.file);
      return { id: result.file, assetKey: asset?.asset_key || previous?.assetKey || null, name: asset?.name || previous?.name || filename(result.file).replace(/\.unitypackage$/i, ""), file: result.file };
    });
    setOrder(rows);
    setOverwrite(job.overwrite ?? true);
    setReordering(false);
    setActionError("");
    onReview(rows.flatMap((row) => row.assetKey ? [row.assetKey] : []));
    void handleProject(job.project);
    void refreshAssets();
  }
  const title = !job ? `Import ${order.length} package${order.length === 1 ? "" : "s"}` : running ? "Importing packages" : preflight ? "Import couldn’t start" : job.status === "cancelled" ? "Import stopped" : failed.length ? "Import finished with issues" : "Import complete";
  const shownProjectPath = job?.project || projectPath;
  const shownProject = projectInfo?.path === shownProjectPath ? projectInfo : projects?.find((project) => project.path === shownProjectPath);
  const renderResults = (rows: ImportResultRow[]) => <ul className="import-results">{rows.map((row) => <ResultRow key={row.file} row={row} name={byFile.get(row.file)?.name || order.find((item) => item.file === row.file)?.name || filename(row.file).replace(/\.unitypackage$/i, "")} finished={finished} onReveal={(path) => void reveal(path)} />)}</ul>;

  return <AlertDialog isOpen onClose={() => { if (!starting) onClose(); }} finalFocusRef={finalFocusRef} initialFocusRef={firstButtonRef} isKeyboardDismissable={!starting} closeOnOverlayClick={false} className="dialog-overlay">
    <AlertDialogBackdrop className="dialog-backdrop" />
    <AlertDialogContent className="action-dialog import-dialog" aria-labelledby="import-title">
      <AlertDialogHeader className="dialog-top">
        <div className="import-heading"><span className="import-heading-icon"><Icon as={finished && !failed.length && !preflight && job?.status === "completed" ? Check : Package} className="icon" aria-hidden="true" /></span><div><div className="eyebrow">UNITY PACKAGES</div><h2 id="import-title">{title}</h2></div></div>
        <Button type="button" className="icon-button" onPress={onClose} isDisabled={starting} aria-label={running ? "Hide import progress" : "Close dialog"}><Icon as={X} className="icon" aria-hidden="true" /></Button>
      </AlertDialogHeader>
      <AlertDialogBody className="import-body" ref={bodyRef}>
        <SpotlightCard className="import-project-card" role="region" aria-label="Target project">
          <div className="import-section-heading"><h3><Icon as={Folder} className="icon" aria-hidden="true" /> Target project</h3>{!job && projectInfo && <Button type="button" className="import-text-button" onPress={() => setEditingProject(!editingProject)} isDisabled={starting}>{editingProject ? "Keep project" : "Change"}</Button>}</div>
          {!job && editingProject ? <div className="import-project-row">
            <GlideSelect className="import-project-select" value={projectPath} disabled={starting} ariaLabel="Unity Hub project"
              placeholder={projects === null ? "Loading projects…" : "Choose a Unity project…"}
              options={[
                ...(projectPath && !projects?.some(project => project.path === projectPath) ? [{ value: projectPath, label: projectInfo?.title || projectPath }] : []),
                ...(projects || []).map(project => ({ value: project.path, label: project.title + ' · Unity ' + project.version + ' · ' + project.path })),
              ]}
              onChange={value => void handleProject(value)} menuWidth={480}
            />
            <Button type="button" className="button quiet" onPress={() => void browseProject()} isDisabled={starting}>Browse…</Button>
          </div> : <div className="import-project-info"><strong>{shownProject?.title || filename(shownProjectPath)}</strong>{shownProject?.version && <span>Unity {shownProject.version}</span>}</div>}
          {shownProjectPath && <details className="import-project-path"><summary title={shownProjectPath}>{shownProjectPath}</summary><code>{shownProjectPath}</code></details>}
          {!job && <>
            {inspecting && <p className="import-note" role="status">Checking project…</p>}
            {projectsError && <p className="import-inline-error" role="alert">{projectsError} <Button className="import-text-button" onPress={() => void loadProjects()}>Retry</Button></p>}
            {inspectError && <p className="import-inline-error" role="alert">{inspectError} {projectPath && <Button className="import-text-button" onPress={() => void inspect(projectPath)}>Retry</Button>}</p>}
          </>}
        </SpotlightCard>
        {!job ? <>
          <section className="import-section" aria-label="Packages">
            <div className="import-section-heading"><h3>Packages <span className="import-count">{order.length}</span></h3><Button type="button" className="import-text-button" aria-pressed={reordering} onPress={() => setReordering(!reordering)} isDisabled={order.length < 2 || starting}><Icon as={ArrowDownUp} className="icon" aria-hidden="true" />{reordering ? "Done reordering" : "Reorder"}</Button></div>
            {reordering && <p className="import-note">Imported from top to bottom.</p>}
            {refreshing && <p className="import-note" role="status">Checking availability…</p>}
            {refreshError && <p className="import-inline-error" role="alert">{refreshError} <Button className="import-text-button" onPress={() => void refreshAssets()}>Retry</Button></p>}
            <ol className="import-packages">{order.map((row, index) => {
              const versions = versionsFor(row);
              const selected = versions.find((version) => version.file === row.file);
              const availability = selected?.availability;
              return <li key={row.id}>
                <span className="import-package-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                <div className="import-package-main"><strong>{row.name}</strong>
                  {versions.length > 1 || !selected ? <GlideSelect className="import-version-select" value={selected ? row.file : ""} disabled={starting || !versions.length} ariaLabel={'Archive version for ' + row.name}
                    placeholder={versions.length ? "Choose version…" : "Archive no longer in library"}
                    options={versions.map(version => ({ value: version.file || '', label: filename(version.file || '') + (version.size_bytes != null ? ' · ' + formatBytes(version.size_bytes) : '') + (version.editor_version ? ' · Unity ' + version.editor_version : '') + (version.availability ? ' · ' + availabilityLabel(version.availability) : '') }))}
                    onChange={file => setOrder(current => current.map(item => item.id === row.id ? { ...item, file } : item))} menuWidth={480}
                  /> : <ArchiveDetails file={row.file} version={selected} />}
                  {!selected && row.file && <span className="import-note">Previous archive: {filename(row.file)}</span>}
                </div>
                <div className="import-row-actions">
                  <span className="import-status" data-kind={!selected || availability === "missing" ? "failed" : availability === "local" ? "local" : "neutral"}>{!selected ? "Choose version" : availability === "missing" ? "Missing" : availability === "cloud_only" ? <><Icon as={Cloud} className="icon" aria-hidden="true" />Cloud</> : availability === "local" ? <><Icon as={Check} className="icon" aria-hidden="true" />Ready</> : "Unknown"}</span>
                  {reordering ? <div className="import-package-order"><Button type="button" className="icon-button" onPress={() => move(index, -1)} isDisabled={index === 0 || starting} aria-label={`Move ${row.name} up`}><Icon as={ArrowUp} className="icon" aria-hidden="true" /></Button><Button type="button" className="icon-button" onPress={() => move(index, 1)} isDisabled={index === order.length - 1 || starting} aria-label={`Move ${row.name} down`}><Icon as={ArrowDown} className="icon" aria-hidden="true" /></Button></div> : <Button type="button" className="icon-button import-remove" onPress={() => remove(row)} isDisabled={starting} aria-label={`Remove ${row.name} from import`}><Icon as={X} className="icon" aria-hidden="true" /></Button>}
                </div>
              </li>;
            })}</ol>
            {!order.length && <p className="import-empty">No packages selected. Close this dialog to choose packages.</p>}
          </section>
          <details className="import-policy-details"><summary>How existing assets are handled</summary><p>Identical files are skipped. {overwrite ? "Changed files are replaced with backups outside Assets." : "A differing existing asset fails that package; it is not replaced."} Conflicting asset identities are never overwritten. Packages install in order and continue after individual failures.</p></details>
        </> : <section className="import-section" aria-label="Import progress">
          <div className="import-outcome" role="status">
            <strong>{running ? `${installed.length} of ${job.total ?? results.length} installed` : `${installed.length} installed${failed.length ? ` · ${failed.length} failed` : ""}${notImported ? ` · ${notImported} not imported` : ""}`}</strong>
            {running && <span>{job.stop_requested ? "Stopping after the current package…" : job.status === "queued" ? "Waiting to start…" : "You can keep browsing while packages import."}</span>}
          </div>
          {job.error && (preflight || !failed.some((row) => row.error === job.error)) && <div className="import-inline-error" role="alert">{job.error}</div>}
          {finished && installed.length > 0 && <p className="import-note">Files installed. Unity processes them on refresh or next project open. Auto Refresh off? Use Assets → Refresh.</p>}
          {finished ? <>
            {renderResults([...failed, ...remaining.filter((row) => row.status !== "failed")])}
            {installed.length > 0 && <details className="import-successes" open={remaining.length === 0}><summary><Icon as={Check} className="icon" aria-hidden="true" />Installed packages <span>{installed.length}</span></summary>{renderResults(installed)}</details>}
          </> : renderResults(results)}
        </section>}
        <span className="sr-only" role="status">{notice}</span>
      </AlertDialogBody>
      <AlertDialogFooter className="dialog-foot">
        {actionError && <p className="import-inline-error import-action-error" role="alert">{actionError}</p>}
        {!job && <>
          <section className="import-policy" aria-label="Replacement policy">
            <label className="import-overwrite"><span><Icon as={ShieldCheck} className="icon" aria-hidden="true" /><strong>Replace changed assets</strong></span><SquishSwitch checked={overwrite} disabled={starting} onChange={setOverwrite} width={40} height={24} radius={12} ariaLabel="Replace changed assets" /><span className="import-toggle" aria-hidden="true">{overwrite ? "On" : "Off"}</span></label>
            <div id="import-safety" className={overwrite ? "import-warning" : "import-save-note"} role="note"><Icon as={CircleAlert} className="icon" aria-hidden="true" /><p>{overwrite && <><strong>Existing assets may be replaced.</strong> Changed files are backed up.<br /></>}Save your Unity changes before importing.</p></div>
          </section>
          <span id="import-blocker" className="import-footer-note" role="status">{blocker || "Ready to import"}</span><Button ref={firstButtonRef} type="button" className="button quiet" onPress={onClose} isDisabled={starting}>Cancel</Button><Button type="button" className="button primary" onPress={() => void start()} isDisabled={!canStart} aria-describedby="import-blocker import-safety">{starting ? "Starting…" : `Import ${order.length} package${order.length === 1 ? "" : "s"}`}</Button>
        </>}
        {running && <><Button ref={firstButtonRef} type="button" className="button quiet" onPress={onClose}>Hide</Button><Button type="button" className="button quiet danger-text" onPress={() => void stop()} isDisabled={stopping || Boolean(job?.stop_requested)}>{stopping || job?.stop_requested ? "Stopping…" : "Stop after current package"}</Button></>}
        {finished && <><Button ref={firstButtonRef} type="button" className={`button ${remaining.length ? "quiet" : "primary"}`} onPress={onClose}>Done</Button>{remaining.length > 0 && <Button type="button" className="button primary" onPress={review}>{recoveryLabel}</Button>}</>}
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
