import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  AlertDialog,
  AlertDialogBackdrop,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  Button,
  Icon,
  Spinner,
} from "./ui";
import { ArrowDown, ArrowUp, X } from "lucide-react";

const api = window.ual;

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
  cloud_only: "Cloud only",
  local: "Local",
  unknown: "Availability unknown",
  missing: "Missing",
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


type ImportDialogProps = {
  assets: Asset[];
  job: ImportJob | null;
  finalFocusRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onStart: (request: ImportRequest) => Promise<void>;
  onStop: () => void;
  onRefreshAssets: () => Promise<void>;
};

export function ImportDialog({ assets, job, finalFocusRef, onClose, onStart, onStop, onRefreshAssets }: ImportDialogProps) {
  const running = Boolean(job && ["queued", "running"].includes(job.status));
  const [order, setOrder] = useState<Asset[]>(assets);
  const [chosen, setChosen] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const asset of assets) {
      const packages = unityPackages(asset);
      if (packages.length === 1 && packages[0].file) initial[asset.asset_key] = packages[0].file;
    }
    return initial;
  });
  const [projects, setProjects] = useState<UnityProject[] | null>(null);
  const [projectsError, setProjectsError] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [projectInfo, setProjectInfo] = useState<ImportProject | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState("");
  const [mode, setMode] = useState<"closed" | "live">("closed");
  const inspectEpochRef = useRef(0);
  const [installing, setInstalling] = useState(false);
  const [startError, setStartError] = useState("");
  const [starting, setStarting] = useState(false);
  const firstButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { firstButtonRef.current?.focus(); }, []);

  async function loadProjects() {
    setProjects(null);
    setProjectsError("");
    try {
      setProjects(await api.importProjects());
    } catch (cause) {
      setProjects([]);
      setProjectsError(cause instanceof Error ? cause.message : "Could not load Unity Hub projects.");
    }
  }
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    setOrder((current) => {
      const byKey = new Map(assets.map((asset) => [asset.asset_key, asset]));
      const kept = current.map((asset) => byKey.get(asset.asset_key)).filter((asset): asset is Asset => Boolean(asset));
      const keptKeys = new Set(kept.map((asset) => asset.asset_key));
      return [...kept, ...assets.filter((asset) => !keptKeys.has(asset.asset_key))];
    });
    setChosen((current) => Object.fromEntries(assets.map((asset) => [
      asset.asset_key,
      unityPackages(asset).some((version) => version.file === current[asset.asset_key]) ? current[asset.asset_key] : "",
    ])));
  }, [assets]);
  async function refreshAssets() {
    setRefreshing(true);
    try {
      await onRefreshAssets();
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => { void refreshAssets(); }, []);
  useEffect(() => { void loadProjects(); }, []);

  async function inspect(path: string) {
    const epoch = ++inspectEpochRef.current;
    setInspecting(true);
    setInspectError("");
    try {
      const info = await api.inspectImportProject(path);
      if (epoch !== inspectEpochRef.current) return;
      setProjectInfo(info);
      setProjectPath(info.path);
    } catch (cause) {
      if (epoch !== inspectEpochRef.current) return;
      setProjectInfo(null);
      setInspectError(cause instanceof Error ? cause.message : "Could not inspect the project.");
    } finally {
      if (epoch === inspectEpochRef.current) setInspecting(false);
    }
  }

  async function handleProject(nextPath: string) {
    inspectEpochRef.current += 1;
    setProjectPath(nextPath);
    setProjectInfo(null);
    setInspecting(false);
    setInspectError("");
    setMode("closed");
    if (nextPath) await inspect(nextPath);
  }

  async function browseProject() {
    try {
      const path = await api.chooseImportProject();
      if (path) await handleProject(path);
    } catch (cause) {
      setInspectError(cause instanceof Error ? cause.message : "Could not open the folder picker.");
    }
  }

  async function installBridge() {
    if (!projectPath) return;
    const epoch = ++inspectEpochRef.current;
    setInstalling(true);
    setInspectError("");
    try {
      const info = await api.installImportBridge(projectPath);
      if (epoch !== inspectEpochRef.current) return;
      setProjectInfo(info);
    } catch (cause) {
      if (epoch !== inspectEpochRef.current) return;
      setInspectError(cause instanceof Error ? cause.message : "Could not install the bridge.");
    } finally {
      if (epoch === inspectEpochRef.current) setInstalling(false);
    }
  }

  const allChosen = order.length > 0 && order.every((asset) => unityPackages(asset).some((version) => version.file === chosen[asset.asset_key]));
  const closedModeBlocked = Boolean(projectInfo?.open);
  const liveModeBlocked = Boolean(projectInfo && !projectInfo.open);
  const liveStartBlocked = Boolean(projectInfo && !projectInfo.bridge_ready);
  const canStart = Boolean(projectPath && projectInfo && projectInfo.path === projectPath && allChosen && !inspecting && !installing && !running && !starting && (mode === "closed" ? !closedModeBlocked : !liveStartBlocked));

  async function start() {
    if (!canStart) return;
    setStarting(true);
    setStartError("");
    try {
      await onStart({ project: projectPath, mode, packages: order.map((asset) => ({ asset_key: asset.asset_key, file: chosen[asset.asset_key] })) });
    } catch (cause) {
      setStartError(cause instanceof Error ? cause.message : "Could not start the import.");
    } finally {
      setStarting(false);
    }
  }

  function move(index: number, delta: number) {
    setOrder((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  const installingBridge = installing;
  const showSetup = !job;
  const showRunning = Boolean(job && !["completed", "failed", "cancelled"].includes(job.status));
  const showFinished = Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
  const title = running || showFinished ? "Import packages" : "Import selected packages";

  return (
    <AlertDialog
      isOpen
      onClose={() => { if (!running && !starting && !installing) onClose(); }}
      finalFocusRef={finalFocusRef}
      initialFocusRef={firstButtonRef}
      isKeyboardDismissable={!running && !starting && !installing}
      closeOnOverlayClick={false}
      className="dialog-overlay"
    >
      <AlertDialogBackdrop className="dialog-backdrop" />
      <AlertDialogContent className="action-dialog import-dialog" aria-labelledby="import-title">
        <AlertDialogHeader className="dialog-top">
          <div>
            <div className="eyebrow">UNITY PROJECT</div>
            <h2 id="import-title">{title}</h2>
          </div>
          <Button type="button" className="icon-button" onPress={() => { if (!running && !starting && !installing) onClose(); }} isDisabled={running || starting || installing} aria-label="Close dialog">
            <Icon as={X} className="icon" aria-hidden="true" focusable={false} />
          </Button>
        </AlertDialogHeader>
        <AlertDialogBody>
          {showSetup && (
            <>
              <p className="dialog-lead">Pick an archive version and order for each package, then choose a Unity project. Packages are staged locally (up to 3 at a time), imported in order, and the batch stops at the first failure. Packages overwrite existing files — there is no rollback.</p>
              <section className="import-section" aria-label="Packages">
                {refreshing && <p className="muted" role="status">Refreshing availability…</p>}
                <h3>Packages <span>{order.length}</span></h3>
                <ol className="import-packages">
                  {order.map((asset, index) => {
                    const packages = unityPackages(asset);
                    const availability = availabilityLabel(packages[0]?.availability);
                    return (
                      <li key={asset.asset_key}>
                        <div className="import-package-order">
                          <Button type="button" className="icon-button" onPress={() => move(index, -1)} isDisabled={index === 0 || running} aria-label={`Move ${asset.name} up`}><Icon as={ArrowUp} className="icon" aria-hidden="true" focusable={false} /></Button>
                          <Button type="button" className="icon-button" onPress={() => move(index, +1)} isDisabled={index === order.length - 1 || running} aria-label={`Move ${asset.name} down`}><Icon as={ArrowDown} className="icon" aria-hidden="true" focusable={false} /></Button>
                        </div>
                        <div className="import-package-main">
                          <strong title={asset.name}>{asset.name}</strong>
                          {packages.length > 1 || !chosen[asset.asset_key] ? (
                            <select
                              className="native-select"
                              value={chosen[asset.asset_key] || ""}
                              onChange={(event) => { setChosen((current) => ({ ...current, [asset.asset_key]: event.target.value })); void refreshAssets(); }}
                              aria-label={`Archive version for ${asset.name}`}
                            >
                              <option value="" disabled>Choose archive version…</option>
                              {packages.map((version) => {
                                const label = availabilityLabel(version.availability);
                                return (
                                  <option key={version.file} value={version.file || ""}>
                                    {(version.file || "").split(/[\\/]/).pop()}{version.size_bytes != null ? ` · ${formatBytes(version.size_bytes)}` : ""}{version.editor_version ? ` · ${version.editor_version}` : ""}{label ? ` · ${label}` : ""}
                                  </option>
                                );
                              })}
                            </select>
                          ) : (
                            <span className="import-package-file" title={packages[0]?.file}>{(packages[0]?.file || "").split(/[\\/]/).pop()}{availability && <span className="availability-tag" data-availability={packages[0]?.availability}>{availability}</span>}</span>
                          )}
                          {!chosen[asset.asset_key] && <span className="import-warning-text" role="alert">Choose the exact archive version to import.</span>}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
              <section className="import-section" aria-label="Target project">
                <h3>Target project</h3>
                <div className="import-project-row">
                  <select
                    className="native-select"
                    value={projectPath}
                    onChange={(event) => void handleProject(event.target.value)}
                    aria-label="Unity Hub project"
                    disabled={installingBridge}
                  >
                    <option value="">{projects === null ? "Loading Hub projects…" : "Choose a Hub project…"}</option>
                    {projectPath && !(projects || []).some((project) => project.path === projectPath) && <option value={projectPath}>{projectInfo?.title || projectPath}</option>}
                    {(projects || []).map((project) => (
                      <option key={project.path} value={project.path}>{project.title} · Unity {project.version}</option>
                    ))}
                  </select>
                  <Button type="button" className="button quiet" onPress={() => void browseProject()} isDisabled={installingBridge}>Browse…</Button>
                </div>
                  {projectsError && <p className="import-warning-text" role="alert">{projectsError} <Button type="button" className="button quiet" onPress={() => void loadProjects()}>Retry</Button></p>}
                {inspecting && <p className="muted" role="status">Checking project…</p>}
                {inspectError && <p className="import-warning-text" role="alert">{inspectError} <Button type="button" className="button quiet" onPress={() => void inspect(projectPath)} isDisabled={installing}>Retry</Button></p>}
                {projectInfo && (
                  <dl className="import-project-info">
                    <div><dt>Project</dt><dd title={projectInfo.path}>{projectInfo.title} — <code>{projectInfo.path}</code></dd></div>
                    <div><dt>Unity</dt><dd>{projectInfo.version}</dd></div>
                    <div><dt>State</dt><dd>{projectInfo.open ? "Open in Unity" : "Closed"} <Button type="button" className="button quiet" aria-label="Refresh project status" onPress={() => void inspect(projectPath)} isDisabled={inspecting || installing}>Refresh</Button></dd></div>
                    <div><dt>Bridge</dt><dd>{projectInfo.bridge_ready ? "Installed and ready" : projectInfo.bridge_installed ? "Installed, not ready yet" : "Not installed"}</dd></div>
                  </dl>
                )}
                {projectInfo && (
                  <fieldset className="import-mode">
                    <legend>Import mode</legend>
                    <label className="import-mode-option">
                      <input type="radio" name="import-mode" value="closed" checked={mode === "closed"} onChange={() => setMode("closed")} disabled={closedModeBlocked || installingBridge} />
                      Closed project — imports the batch in one headless Unity session, then quits. A temporary Editor runner is added and removed for this batch.
                      {closedModeBlocked && <span className="import-warning-text">The project is currently open in Unity. Close it or use live mode.</span>}
                    </label>
                    <label className="import-mode-option">
                      <input type="radio" name="import-mode" value="live" checked={mode === "live"} onChange={() => setMode("live")} disabled={liveModeBlocked || installingBridge} />
                      Live Editor — imports into the running Editor
                      {projectInfo && !projectInfo.open && <span className="import-warning-text">Open the project in Unity to use live mode.</span>}
                      {projectInfo.open && !projectInfo.bridge_ready && <span className="import-warning-text">The bridge is not ready in this Editor.</span>}
                    </label>
                  </fieldset>
                )}
                {projectInfo && mode === "live" && !projectInfo.bridge_ready && (
                  <div className="install-bridge">
                    {projectInfo.bridge_installed ? <p>Bridge installed. Let Unity finish compiling (use Assets → Refresh if auto-refresh is off), then refresh the project status above.</p> : <>
                      <p>Live import needs a small Editor-only bridge. Installing adds <code>Assets/UnityAssetLibraryImport/UnityAssetLibraryImport.cs</code> and <code>UnityAssetLibraryImport.asmdef</code> to <code title={projectPath}>{projectPath}</code>. Unity generates .meta files and compiles the bridge. A system dialog asks for confirmation before any files are written.</p>
                      <Button type="button" className="button" onPress={() => void installBridge()} isDisabled={installing || inspecting}>{installing ? "Installing…" : "Install bridge"}</Button>
                    </>}
                  </div>
                )}
                <p className="import-warning">Packages overwrite files already in the target project. There is no rollback — check the project before importing.</p>
              </section>
            </>
          )}
          {job && (
            <section className="import-section" aria-label="Import progress">
              <h3>Progress</h3>
              <p className="muted" role="status">
                {job.status === "queued" && "Waiting to start…"}
                {showRunning && <>Importing into <code title={job.project}>{job.project}</code>{job.mode === "live" ? " (live Editor)" : ""} — {job.completed ?? 0} of {job.total ?? order.length} packages.</>}
                {showFinished && job.status === "completed" && "Import completed."}
                {showFinished && job.status === "cancelled" && "Import cancelled."}
                {showFinished && job.status === "failed" && "Import failed."}
              </p>
              {job.stop_requested && showRunning && <p className="import-warning-text" role="status">Stop requested — the current package finishes first.</p>}
              {job.error && <p className="import-warning-text" role="alert">{job.error}</p>}
              {startError && <p className="import-warning-text" role="alert">{startError}</p>}
              <table className="import-results">
                <thead><tr><th scope="col">Package</th><th scope="col">Status</th></tr></thead>
                <tbody>
                  {(job.results || order.map((asset) => ({ file: chosen[asset.asset_key] || "", status: "pending" as const }))).map((row) => (
                    <tr key={row.file} data-status={row.status}>
                      <td title={row.file}>{row.file.split(/[\\/]/).pop() || "—"}</td>
                      <td>
                        {row.status === "pending" && "Pending"}
                        {(row.status === "preparing" || row.status === "downloading") && (
                          <>
                            <Spinner className="spinner" aria-label={row.status === "preparing" ? "Preparing" : "Downloading"} />
                            {" "}{row.status === "preparing" ? "Preparing staging copy…" : "Downloading…"}
                            {row.bytes_total ? <small className="prep-bytes">{formatBytes(row.bytes_completed ?? 0)} / {formatBytes(row.bytes_total)}</small> : null}
                            {row.bytes_total ? <span className="prep-progress" role="progressbar" aria-label="Download progress" aria-valuemin={0} aria-valuemax={row.bytes_total} aria-valuenow={row.bytes_completed ?? 0}><span className="prep-progress-fill" style={{ width: `${Math.min(100, Math.round(((row.bytes_completed ?? 0) / row.bytes_total) * 100))}%` }} /></span> : null}
                          </>
                        )}
                        {row.status === "ready" && "Staged locally"}
                        {row.status === "importing" && <><Spinner className="spinner" aria-label="Importing" /> Importing…</>}
                        {row.status === "imported" && "Imported"}
                        {row.status === "failed" && <span className="danger-text">Failed{row.error ? ` — ${row.error}` : ""}</span>}
                        {row.status === "cancelled" && "Cancelled"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </AlertDialogBody>
        <AlertDialogFooter className="dialog-foot">
          {showSetup && <Button ref={firstButtonRef} type="button" className="button quiet" onPress={onClose} isDisabled={running || starting || installing}>Cancel</Button>}
          {showRunning && <Button ref={firstButtonRef} type="button" className="button quiet" onPress={onClose} isDisabled={starting || installing}>Hide</Button>}
          {showRunning && <Button type="button" className="button danger" onPress={onStop} isDisabled={Boolean(job?.stop_requested)}>{job?.stop_requested ? "Stopping…" : "Stop after current package"}</Button>}
          {showFinished && <Button type="button" className="button primary" onPress={onClose} autoFocus>Done</Button>}
          {showSetup && <Button type="button" className="button primary" onPress={() => void start()} isDisabled={!canStart}>{starting ? "Starting…" : `Import ${order.length} package${order.length === 1 ? "" : "s"}`}</Button>}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
