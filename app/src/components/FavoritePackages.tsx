import { useEffect, useRef, useState, type FormEvent } from "react";
import { FolderOpen, GripVertical, Package, Pencil, Plus, Trash2, X } from "lucide-react";
import { AlertDialog, AlertDialogBackdrop, AlertDialogBody, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, Button, Icon, Input, InputField } from "./ui";
import "./FavoritePackages.css";
import SpotlightCard from "./react-bits/SpotlightCard";
import GlideSelect from "./react-bits/GlideSelect";
import SpringCheck from "./react-bits/SpringCheck";

type Props = {
  disabled: boolean;
  storageEpoch: number;
  hidden?: boolean;
  job: PackageInstallJob | null;
  jobError: string;
  onInstall: (request: { project: string; ids: string[] }) => Promise<PackageInstallJob>;
};

const api = window.ual;
const message = (cause: unknown, fallback: string) => cause instanceof Error ? cause.message : fallback;
const resultLabels: Record<PackageInstallResult["status"], string> = {
  pending: "Waiting", installing: "Installing", installed: "Installed", skipped: "Already in project", failed: "Failed",
};
function detectPackageKind(source: string): FavoritePackage["kind"] | null {
  const value = source.trim();
  if (/^[a-z0-9][a-z0-9._-]*$/.test(value) && value.includes(".")) return "registry";
  if (/\s/.test(value)) return null;
  if (/^[^/@\s]+@[^/:\s]+:.+/.test(value)) return "git";
  if (/^(?:git\+)?(?:https?|ssh|git|file):\/\//.test(value)) {
    try {
      const url = new URL(value.replace(/^git\+/, ""));
      if ((url.hostname || url.protocol === "file:") && value.slice(value.indexOf("://") + 3).includes("/")) return "git";
    } catch { return null; }
  }
  return null;
}

export default function FavoritePackages({ disabled, storageEpoch, hidden = false, job, jobError, onInstall }: Props) {
  const [packages, setPackages] = useState<FavoritePackage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [draft, setDraft] = useState<Omit<FavoritePackage, "kind"> | null>(null);
  const [projects, setProjects] = useState<UnityProject[]>([]);
  const [project, setProject] = useState<ImportProject | null>(null);
  const [projectError, setProjectError] = useState("");
  const [inspecting, setInspecting] = useState(false);
  const [working, setWorking] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const inspectEpoch = useRef(0);
  const mounted = useRef(true);
  const reviewRef = useRef<HTMLButtonElement>(null);
  const active = Boolean(job && ["queued", "running"].includes(job.status));
  const blocked = disabled || working || active || loading || Boolean(loadError);

  async function loadFavorites() {
    setLoading(true);
    setLoadError("");
    try {
      const saved = await api.getPackageFavorites();
      if (mounted.current) setPackages(saved.packages);
    } catch (cause) {
      if (mounted.current) setLoadError(message(cause, "Could not load saved packages."));
    } finally { if (mounted.current) setLoading(false); }
  }

  async function loadProjects() {
    try {
      const saved = await api.importProjects();
      if (mounted.current) { setProjects(saved); setProjectError(""); }
    } catch (cause) {
      if (mounted.current) setProjectError(message(cause, "Could not load Unity Hub projects. Browse to choose a project."));
    }
  }

  useEffect(() => {
    mounted.current = true;
    void loadFavorites();
    return () => { mounted.current = false; inspectEpoch.current++; };
  }, [storageEpoch]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || blocked) return;
    const source = draft.source.trim();
    const kind = detectPackageKind(source);
    if (!kind) { setError("Enter a registry package name or a supported Git URL."); return; }
    const item = { ...draft, kind, id: draft.id || crypto.randomUUID(), source, label: draft.label.trim() || source, version: draft.version.trim() };
    setWorking(true);
    setError("");
    try {
      const saved = await api.savePackageFavorite(item);
      if (mounted.current) { setPackages(saved.packages); setDraft(null); }
    } catch (cause) {
      if (mounted.current) setError(message(cause, "Could not save this package."));
    } finally { if (mounted.current) setWorking(false); }
  }

  async function remove(id: string) {
    if (blocked) return;
    setWorking(true);
    setError("");
    try {
      const saved = await api.removePackageFavorite(id);
      if (mounted.current) { setPackages(saved.packages); if (draft?.id === id) setDraft(null); }
    } catch (cause) {
      if (mounted.current) setError(message(cause, "Could not remove this package."));
    } finally { if (mounted.current) setWorking(false); }
  }
  async function reorder(id: string, targetId: string) {
    if (blocked || id === targetId) return;
    const from = packages.findIndex(item => item.id === id);
    const to = packages.findIndex(item => item.id === targetId);
    if (from < 0 || to < 0) return;
    const ordered = [...packages];
    ordered.splice(to, 0, ordered.splice(from, 1)[0]);
    setWorking(true);
    setError("");
    try {
      const saved = await api.reorderPackageFavorites(ordered.map(item => item.id));
      if (mounted.current) setPackages(saved.packages);
    } catch (cause) {
      if (mounted.current) setError(message(cause, "Could not reorder saved packages."));
    } finally { if (mounted.current) setWorking(false); }
  }

  async function selectProject(path: string) {
    const epoch = ++inspectEpoch.current;
    setProject(null);
    setProjectError("");
    if (!path) { setInspecting(false); return; }
    setInspecting(true);
    try {
      const inspected = await api.inspectImportProject(path);
      if (epoch === inspectEpoch.current && mounted.current) setProject(inspected);
    } catch (cause) {
      if (epoch === inspectEpoch.current && mounted.current) setProjectError(message(cause, "Could not inspect the Unity project."));
    } finally {
      if (epoch === inspectEpoch.current && mounted.current) setInspecting(false);
    }
  }

  async function browse() {
    const epoch = inspectEpoch.current;
    try {
      const path = await api.chooseImportProject();
      if (path && mounted.current && epoch === inspectEpoch.current) await selectProject(path);
    } catch (cause) {
      if (mounted.current) setProjectError(message(cause, "Could not choose a Unity project."));
    }
  }

  function closeReview() {
    if (working) return;
    inspectEpoch.current++;
    setInspecting(false);
    setProject(null);
    setReviewOpen(false);
  }

  function openReview(ids: string[]) {
    inspectEpoch.current++;
    setInspecting(false);
    setSelected(ids);
    setError("");
    setProject(null);
    setProjectError("");
    setReviewOpen(true);
    void loadProjects();
  }

  async function install() {
    if (blocked || inspecting || !project || !selected.length) return;
    setWorking(true);
    setError("");
    try {
      await onInstall({ project: project.path, ids: selected });
      if (mounted.current) setReviewOpen(false);
    } catch (cause) {
      if (mounted.current) setError(message(cause, "Could not start package installation."));
    } finally { if (mounted.current) setWorking(false); }
  }

  const draftKind = draft ? detectPackageKind(draft.source) : null;
  const packageInfo = (item: FavoritePackage) => <span className="favorite-package-info"><strong>{item.label}</strong><code>{item.source}{item.version ? `${item.kind === "git" ? "#" : "@"}${item.version}` : ""}</code><span>{item.kind === "git" ? "Git package" : "Registry package"}</span></span>;
  return <SpotlightCard className="favorite-packages" role="region" aria-label="Saved packages" hidden={hidden}>
    <details className="favorite-package-disclosure">
      <summary><Icon as={Package} className="icon" />Saved packages <span>{loading ? "Loading…" : packages.length}</span></summary>
      <div className="favorite-packages-heading">
        <div className="favorite-package-actions">
          <Button type="button" className="button quiet" aria-label="Add package" onPress={() => { setDraft({ id: "", label: "", source: "", version: "" }); setError(""); }} isDisabled={blocked}><Icon as={Plus} className="icon" />Add</Button>
          <Button ref={reviewRef} type="button" className="button primary" onPress={() => openReview(packages.map(item => item.id))} isDisabled={blocked || !packages.length}>Import</Button>
        </div>
      </div>
      {loadError && <div className="favorite-packages-error" role="alert"><p>{loadError}</p><Button type="button" className="button quiet" onPress={() => void loadFavorites()}>Retry saved packages</Button></div>}
      {error && !reviewOpen && <p className="favorite-packages-error" role="alert">{error}</p>}
      {draft && <form className="favorite-package-form" onSubmit={event => void save(event)}>
        <h3>{draft.id ? "Edit saved package" : "Save a package"}</h3>
        <label className="favorite-package-source" htmlFor="favorite-source">Git URL or package name<Input><InputField id="favorite-source" aria-label="Git URL or package name" aria-describedby="favorite-source-detection" value={draft.source} onChangeText={source => setDraft(current => current ? { ...current, source } : null)} disabled={blocked} required placeholder="com.unity.mathematics or https://github.com/org/package" /></Input></label>
        <p id="favorite-source-detection" className="favorite-package-detection" aria-live="polite">{draftKind === "git" ? "Detected: Git package" : draftKind === "registry" ? "Detected: Registry package" : draft.source.trim() ? "Enter a registry package name (com.example.package) or a Git URL." : "Package type is detected automatically."}</p>
        <label htmlFor="favorite-label">Display name (optional)<Input><InputField id="favorite-label" aria-label="Display name (optional)" value={draft.label} onChangeText={label => setDraft(current => current ? { ...current, label } : null)} disabled={blocked} placeholder="My favorite package" /></Input></label>
        <label htmlFor="favorite-version">{draftKind === "git" ? "Git ref (optional)" : "Version (optional)"}<Input><InputField id="favorite-version" aria-label={draftKind === "git" ? "Git ref (optional)" : "Version (optional)"} value={draft.version} onChangeText={version => setDraft(current => current ? { ...current, version } : null)} disabled={blocked} /></Input></label>
        <div className="favorite-package-actions"><Button type="submit" className="button primary" isDisabled={blocked || !draftKind}>Save</Button><Button type="button" className="button quiet" isDisabled={working} onPress={() => { setDraft(null); setError(""); }}>Cancel</Button></div>
      </form>}
      {loading ? <p className="favorite-packages-empty" role="status">Loading saved packages…</p> : !loadError && (!packages.length ? <p className="favorite-packages-empty">No saved packages yet. Add a Git URL or package name.</p> : <ol className="favorite-packages-list">{packages.map((item, index) => <li className="favorite-package-row" key={item.id} data-package-id={item.id} data-dragging={draggedId === item.id} data-drop-target={dropTarget === item.id}
        onDragOver={event => { if (!blocked && draggedId && draggedId !== item.id) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; setDropTarget(item.id); } }}
        onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null); }}
        onDrop={event => { event.preventDefault(); if (draggedId) void reorder(draggedId, item.id); setDraggedId(null); setDropTarget(null); }}>
        <button type="button" className="icon-button favorite-package-drag" draggable={!blocked && packages.length > 1} disabled={blocked || packages.length < 2} aria-label={`Reorder ${item.label}`} title="Drag to reorder; use Arrow Up or Arrow Down with keyboard"
          onDragStart={event => {
            const row = event.currentTarget.closest<HTMLElement>(".favorite-package-row")!;
            const bounds = row.getBoundingClientRect();
            event.dataTransfer.setDragImage(row, event.clientX - bounds.left, event.clientY - bounds.top);
            setDraggedId(item.id);
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", item.id);
          }}
          onDragEnd={() => { setDraggedId(null); setDropTarget(null); }}
          onKeyDown={event => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); const target = packages[index + (event.key === "ArrowUp" ? -1 : 1)]; if (target) void reorder(item.id, target.id); } }}><GripVertical className="icon" aria-hidden="true" /></button>
        <span className="favorite-package-number" aria-hidden="true">{index + 1}.</span>
        {packageInfo(item)}
        <div className="favorite-package-actions"><Button type="button" className="icon-button" aria-label={`Edit ${item.label}`} title="Edit" isDisabled={blocked} onPress={() => { setDraft({ ...item }); setError(""); }}><Icon as={Pencil} className="icon" aria-hidden="true" /></Button><Button type="button" className="icon-button" aria-label={`Remove ${item.label}`} title="Remove" isDisabled={blocked} onPress={() => void remove(item.id)}><Icon as={Trash2} className="icon" aria-hidden="true" /></Button></div>
      </li>)}</ol>)}
    </details>
    {jobError && <p className="favorite-packages-error" role="alert">{jobError}</p>}
    {job && <div className="favorite-package-progress" aria-live="polite">
      <div role="status"><strong>{active ? "Installing packages…" : job.status === "completed" ? "Package installation complete" : "Package installation finished with issues"}</strong><p>{job.completed} / {job.total} processed{job.current_item ? ` · ${job.current_item}` : ""}</p><code>{job.project}</code></div>
      {job.error && <p className="favorite-packages-error">{job.error}</p>}
      <ul>{job.results.map(row => <li key={row.id} data-status={row.status}><span>{row.label}</span><strong>{resultLabels[row.status]}</strong>{row.error && <p className="favorite-packages-error">{row.error}</p>}</li>)}</ul>
    </div>}
    {reviewOpen && <AlertDialog isOpen onClose={closeReview} finalFocusRef={reviewRef} isKeyboardDismissable={!working} closeOnOverlayClick={false} className="dialog-overlay">
      <AlertDialogBackdrop className="dialog-backdrop" />
      <AlertDialogContent className="action-dialog favorite-package-dialog" aria-labelledby="favorite-review-title">
        <AlertDialogHeader className="dialog-top"><h2 id="favorite-review-title">Review package import</h2><Button className="icon-button" aria-label="Close package review" isDisabled={working} onPress={closeReview}><Icon as={X} className="icon" /></Button></AlertDialogHeader>
        <AlertDialogBody className="favorite-package-body">
          <SpotlightCard className="favorite-package-project">
            <h3>Target Unity project</h3>
            <div className="favorite-package-project-controls">
              <GlideSelect className="favorite-package-project-select" value={project?.path || ""} onChange={path => void selectProject(path)} disabled={blocked || inspecting} ariaLabel="Target Unity project" placeholder="Choose a project…" menuWidth={480} options={[
                ...(project && !projects.some(entry => entry.path === project.path) ? [{ value: project.path, label: project.title, description: project.path, tag: "Unity " + project.version }] : []),
                ...projects.map(entry => ({ value: entry.path, label: entry.title, description: entry.path, tag: "Unity " + entry.version })),
              ]} />
              <Button className="button quiet" onPress={() => void browse()} isDisabled={blocked || inspecting}><Icon as={FolderOpen} className="icon" />Browse…</Button>
            </div>
            {project && <p>{project.title} · Unity {project.version}</p>}
            <p role={inspecting ? "status" : undefined}>{inspecting ? "Checking project…" : "Close its Unity Editor before installing. Matching packages are skipped; conflicting versions are kept."}</p>
            {projectError && <p className="favorite-packages-error" role="alert">{projectError}</p>}
          </SpotlightCard>
          <h3>Packages · {selected.length} selected</h3>
          <ul className="favorite-packages-list">{packages.map(item => <li className="favorite-package-row" key={item.id}><SpringCheck className="favorite-package-choice" label={packageInfo(item)} ariaLabel={`Include ${item.label}`} checked={selected.includes(item.id)} disabled={blocked} strike="none" doneOpacity={1} boxSize={22} onChange={checked => setSelected(current => checked ? [...current, item.id] : current.filter(id => id !== item.id))} /></li>)}</ul>
          {error && <p className="favorite-packages-error" role="alert">{error}</p>}
        </AlertDialogBody>
        <AlertDialogFooter className="dialog-footer"><Button className="button quiet" isDisabled={working} onPress={closeReview}>Cancel</Button><Button className="button primary" isDisabled={blocked || inspecting || !project || !selected.length} onPress={() => void install()}>{working ? "Starting…" : `Install ${selected.length} ${selected.length === 1 ? "package" : "packages"}`}</Button></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>}
  </SpotlightCard>;
}
