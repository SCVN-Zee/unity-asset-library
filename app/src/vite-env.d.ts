/// <reference types="vite/client" />

declare global {
  type AssetVersion = { file?: string; size_bytes?: number; duplicate?: boolean | null; editor_version?: string | null };
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
  type StorageState = { path: string | null; ready: boolean; needsSetup: boolean; error: string | null; canChange: boolean; busy: boolean; progress: ProgressSnapshot | null };
  type Job = ProgressSnapshot & { id: string; kind?: string; phase?: string; status: string; remaining?: number | null; error?: string | null; error_code?: string | null } | null;
  type ActionJob = ProgressSnapshot & { id: string; kind: "resync" | "cleanup" | "organize"; phase: "plan" | "apply"; status: string; result?: ActionResult | null; error?: string | null; error_code?: string | null };
  type State = { service: string; api_version?: number; ready: boolean; csrf: string; pending_enrichment: number; job: Job; action: ActionJob | null; vault_root: string; repo: string; instance_id: string };
  type PlanRow = { path?: string; src?: string; dst?: string; size_bytes?: number; previous_size_bytes?: number };
  type CleanupFamily = { asset_key: string; reason: string; survivor: PlanRow | null; removals: PlanRow[] };
  type ActionPreview = { families?: CleanupFamily[]; additions?: PlanRow[]; removals?: PlanRow[]; resized?: PlanRow[]; moves?: PlanRow[]; totals?: Record<string, number> };
  type ActionResult = { preview?: ActionPreview; plan_hash?: string; [key: string]: unknown };
  type UaiBridge = {
    getStorage: () => Promise<StorageState>;
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
    openExternal: (url: string) => Promise<void>;
    revealItem: (filePath: string) => Promise<void>;
  };
  interface Window { uai: UaiBridge }
}

export {};
