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
  type Job = { id: string; status: string; stage?: string | null; counts?: Record<string, number>; remaining?: number | null; error?: string | null } | null;
  type State = { service: string; ready: boolean; csrf: string; pending_enrichment: number; job: Job };
  type PlanRow = { path?: string; src?: string; dst?: string; size_bytes?: number; previous_size_bytes?: number };
  type CleanupFamily = { asset_key: string; reason: string; survivor: PlanRow | null; removals: PlanRow[] };
  type ActionPreview = { families?: CleanupFamily[]; additions?: PlanRow[]; removals?: PlanRow[]; resized?: PlanRow[]; moves?: PlanRow[]; totals?: Record<string, number> };
  type ActionResult = { preview?: ActionPreview; plan_hash?: string; [key: string]: unknown };
  type UaiBridge = {
    getState: () => Promise<State>;
    getAssets: () => Promise<AssetIndex>;
    resync: () => Promise<ActionResult>;
    resyncApply: (planHash: string) => Promise<ActionResult>;
    cleanupPlan: () => Promise<ActionResult>;
    cleanupApply: (planHash: string) => Promise<ActionResult>;
    organizePlan: () => Promise<ActionResult>;
    organizeApply: (planHash: string) => Promise<ActionResult>;
    enrichStart: () => Promise<{ job_id: string }>;
    enrichCancel: () => Promise<ActionResult>;
    getJob: (jobId: string) => Promise<{ job: Job }>;
    openExternal: (url: string) => Promise<void>;
  };
  interface Window { uai: UaiBridge }
}

export {};
