/// <reference types="vite/client" />

declare global {
  type AssetVersion = { file?: string; size_bytes?: number; duplicate?: boolean | null; editor_version?: string | null; availability?: "cloud_only" | "local" | "unknown" | "missing" };
  type Asset = {
    asset_key: string;
    name: string;
    local_name?: string;
    author?: string;
    category?: { path?: string; levels?: string[]; source?: string };
    tags?: string[];
    flags?: string[];
    non_store?: boolean;
    store?: string;
    store_id?: string;
    price?: string;
    thumbnail?: { remote?: string };
    resolution?: { id_verified?: boolean; method?: string; score?: number };
    upstream_version?: string;
    versions?: AssetVersion[];
  };
  type AssetIndex = { assets: Asset[]; asset_count?: number; file_count?: number; generated?: string };
  type ProgressSnapshot = { stage?: string | null; completed?: number; total?: number | null; current_item?: string | null; counts?: Record<string, number>; started_at?: number; finished_at?: number | null };
  type PortChoice = { action: "use"; port: number } | { action: "stop" | "confirm-stop" | "cancel" };
  type PortRecovery = { id: string; port: number; suggestion: number; owner: { pid: number; identity: string; name: string } | null; detail: string; confirm: boolean; awaiting: boolean };
  type StorageState = { path: string | null; ready: boolean; needsSetup: boolean; error: string | null; canChange: boolean; busy: boolean; progress: ProgressSnapshot | null; portRecovery: PortRecovery | null };
  type Job = ProgressSnapshot & { id: string; kind?: string; phase?: string; status: string; remaining?: number | null; error?: string | null; error_code?: string | null } | null;
  type ActionJob = ProgressSnapshot & { id: string; kind: "resync" | "cleanup" | "organize"; phase: "plan" | "apply"; status: string; result?: ActionResult | null; error?: string | null; error_code?: string | null };
  type State = { service: string; api_version?: number; ready: boolean; csrf: string; pending_enrichment: number; job: Job; action: ActionJob | null; vault_root: string; repo: string; instance_id: string };
  type PlanRow = { path?: string; src?: string; dst?: string; size_bytes?: number; previous_size_bytes?: number };
  type CleanupFamily = { asset_key: string; reason: string; survivor: PlanRow | null; removals: PlanRow[] };
  type ActionPreview = { families?: CleanupFamily[]; additions?: PlanRow[]; removals?: PlanRow[]; resized?: PlanRow[]; moves?: PlanRow[]; totals?: Record<string, number> };
  type ActionResult = { preview?: ActionPreview; plan_hash?: string; [key: string]: unknown };
  type TagChange = { action: "create" | "rename" | "delete" | "assign" | "remove"; tag: string; new_tag?: string; asset_key?: string };
  type UserTags = { tags: string[]; assignments: Record<string, string[]> };
  type UnityProject = { path: string; title: string; version: string };
  type ImportProject = UnityProject;
  type ImportRequest = { project: string; overwrite?: boolean; packages: { asset_key: string; file: string }[] };
  type ImportResultRow = { file: string; status: "pending" | "preparing" | "downloading" | "ready" | "installing" | "installed" | "failed" | "cancelled"; error?: string; backup_path?: string; bytes_completed?: number | null; bytes_total?: number | null };
  type ActionIdentity = { id: string; kind: "resync" | "cleanup" | "organize"; phase: "plan" | "apply" };
  type Preferences = { theme?: string | null; sort?: string | null; view?: string | null; filters?: Record<string, unknown> | null; action?: ActionIdentity | null };
  type ImportJob = ProgressSnapshot & { id: string; kind: "import"; status: "queued" | "running" | "completed" | "failed" | "cancelled"; project: string; overwrite?: boolean; results: ImportResultRow[]; error?: string | null; stop_requested?: boolean };
  type UalBridge = {
    getStorage: () => Promise<StorageState>;
    answerPortChoice: (id: string, choice: PortChoice) => Promise<void>;
    retryBackend: () => Promise<void>;
    chooseStorageFolder: (currentPath?: string) => Promise<string | null>;
    saveStorage: (path: string) => Promise<StorageState>;
    getState: () => Promise<State>;
    getAssets: () => Promise<AssetIndex>;
    resync: () => Promise<{ job_id: string }>;
    resyncApply: (planHash: string) => Promise<{ job_id: string }>;
    cleanupPlan: () => Promise<{ job_id: string }>;
    cleanupApply: (planHash: string) => Promise<{ job_id: string }>;
    organizePlan: () => Promise<{ job_id: string }>;
    organizeApply: (planHash: string) => Promise<{ job_id: string }>;
    enrichStart: () => Promise<{ job_id: string }>;
    enrichCancel: () => Promise<ActionResult>;
    getJob: (jobId: string) => Promise<{ job: Job }>;
    getAction: (actionId: string) => Promise<{ action: ActionJob }>;
    getPreferences: () => Promise<Preferences>;
    setPreferences: (prefs: Preferences, libraryRoot: string, onlyIfMissing?: boolean) => Promise<Preferences>;
    getLegacyLibraryRoot: () => Promise<string | null>;
    openExternal: (url: string) => Promise<void>;
    favorites: () => Promise<{ favorites: string[] }>;
    setFavorite: (assetKey: string, favorite: boolean) => Promise<{ favorites: string[] }>;
    revealItem: (filePath: string) => Promise<void>;
    getTags: () => Promise<UserTags>;
    mutateTags: (change: TagChange) => Promise<UserTags>;
    importProjects: () => Promise<UnityProject[]>;
    chooseImportProject: () => Promise<string | null>;
    inspectImportProject: (path: string) => Promise<ImportProject>;
    startImport: (request: ImportRequest) => Promise<ImportJob>;
    getImport: () => Promise<ImportJob | null>;
    stopImport: (id: string) => Promise<ImportJob>;
  };
  interface Window { ual: UalBridge }
}

export {};
