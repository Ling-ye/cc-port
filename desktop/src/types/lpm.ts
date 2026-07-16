export type ResourceKind = "skill" | "mcp" | "rule" | "prompt" | "plugin";
export type DiscoveryScope = "global" | "directory";
export type ResourceLifecycle = "active" | "removed";
export type RemovedEffect = "index_only" | "local_files_deleted" | "remote_repo_deleted" | "";

export interface RegistryItem {
  name: string;
  kind: ResourceKind;
  source: "owned" | "external" | "local";
  repo: string;
  path: string;
  subdir: string;
  ref: string;
  install_dir: string;
  description: string;
  tags: string[];
  category: string;
  platforms?: string[];
  private?: boolean | null;
  reachable?: boolean | null;
  last_checked?: string | null;
  lifecycle?: ResourceLifecycle;
  removed_at?: string | null;
  removed_reason?: string;
  removed_effect?: RemovedEffect;
  status?: ItemStatus | null;
}

export interface ItemStatus {
  name: string;
  install_path: string;
  installed: boolean;
  local_commit?: string | null;
  remote_commit?: string | null;
  has_update: boolean;
}

export type SyncPlannedAction = "clone" | "pull" | "copy" | "inject_mcp";

export interface SyncPreviewItem {
  name: string;
  kind: ResourceKind;
  source: RegistryItem["source"];
  planned_action: SyncPlannedAction;
  install_path: string;
  target_platforms: string[];
  target_paths: string[];
  installed: boolean;
  has_update: boolean;
  blocked: boolean;
  warnings: string[];
}

export interface SyncPreviewResult {
  registry_path?: string | null;
  items: SyncPreviewItem[];
}

export interface SyncResultItem {
  name: string;
  install_path: string;
  action: string;
  detail?: string;
  platforms_installed: string[];
}

export interface ResourceRepoInfo {
  local_path: string;
  registry_path: string;
  repo_name: string;
  repo_url: string;
  branch: string;
  exists: boolean;
  is_git_repo: boolean;
  dirty: boolean;
  current_branch: string;
  remote_url: string;
}

export interface ResourceRemoteState {
  repo: string;
  ref: string;
  subdir: string;
  reachable?: boolean | null;
  last_checked?: string | null;
  can_delete_remote: boolean;
  delete_remote_reason: string;
}

export interface ResourceLocalState {
  source_path?: string | null;
  source_exists: boolean;
  install_path: string;
  installed: boolean;
  open_path?: string | null;
  target_paths: string[];
  targets: ResourceTargetState[];
}

export interface ResourceTargetState {
  platform: string;
  path: string;
  supported: boolean;
  exists: boolean;
  installed: boolean;
}

export interface ResourceActionState {
  can_install: boolean;
  can_uninstall: boolean;
  can_preview: boolean;
  can_open: boolean;
  can_delete_resource: boolean;
  can_delete_remote: boolean;
  install_reason: string;
  delete_reason: string;
}

export interface ResourceInventoryItem {
  entry: RegistryItem;
  status?: ItemStatus | null;
  sync_preview?: SyncPreviewItem | null;
  remote_state: ResourceRemoteState;
  local_state: ResourceLocalState;
  actions: ResourceActionState;
}

export interface ResourceInventoryResult {
  registry_path: string;
  items: ResourceInventoryItem[];
}

export interface ResourcePreviewResult {
  name: string;
  path: string;
  text: string;
  truncated: boolean;
  warning: string;
}

export interface ResourceDeleteResult {
  name: string;
  effect: RemovedEffect;
  entry: RegistryItem;
  deleted_path?: string | null;
  deleted_local_files: boolean;
  remote_repo_deleted: boolean;
}

export interface PlatformProfile {
  name: string;
  enabled: boolean;
  skills_dir: string;
  mcp_json: string;
  rules_dir: string;
  plugins_dir: string;
}

export type TokenSource = "env" | "config" | "none";

export interface EditableConfig {
  github: {
    owner: string;
    repo_prefix: string;
    default_private: boolean;
  };
  install: {
    target: string;
  };
  resources: {
    repo_name: string;
    repo_url: string;
    local_path: string;
    branch: string;
  };
  platforms: PlatformProfile[];
}

export interface ConfigSettings {
  path: string;
  exists: boolean;
  token_source: TokenSource;
  token_preview: string;
  config_token_preview: string;
  env_token_active: boolean;
  config: EditableConfig;
}

export interface ConfigCheckItem {
  id: string;
  label: string;
  detail: string;
}

export interface ConfigCheckResult {
  missing: ConfigCheckItem[];
  warnings: ConfigCheckItem[];
  can_prepare: boolean;
  local: {
    path: string;
    exists: boolean;
    is_git_repo: boolean;
  };
  remote: {
    checked: boolean;
    exists: boolean;
    repo: string;
  };
}

export interface ConfigBranchOptions {
  branches: string[];
  default_branch: string;
  selected_branch: string;
  warning: string;
}

export interface Summary {
  version: string;
  registry_path: string;
  resource_repo: ResourceRepoInfo;
  resource_repo_display_name: string;
  counts: {
    total: number;
    by_kind: Record<string, number>;
    by_source: Record<string, number>;
  };
  updates: number;
  installed: number;
  config: {
    path: string;
    exists: boolean;
    github: {
      token_configured: boolean;
      owner: string;
      repo_prefix: string;
      default_private: boolean;
    };
  };
}

export type DoctorStatus = "ok" | "warning" | "error" | "skipped";

export interface DoctorCheck {
  id: string;
  label: string;
  ok: boolean;
  status: DoctorStatus;
  detail: string;
  enabled?: boolean;
  profile?: PlatformProfile;
}

export interface DiscoveredResource {
  id: string;
  tool: string;
  source: DiscoveryScope;
  kind: ResourceKind;
  name_hint: string;
  path: string;
  description: string;
  size: number;
  mtime: number;
  exists_in_registry: boolean;
  status: "ready" | "warning" | "conflict";
  warnings: string[];
}

export interface DiscoveryReadResult {
  id: string;
  path: string;
  text: string;
  truncated: boolean;
  warning: string;
}

export interface DiscoveryUploadItemResult {
  id: string;
  name: string;
  kind: ResourceKind;
  path: string;
  ok: boolean;
  error?: string;
  entry?: RegistryItem;
  source_path?: string;
  stored_path?: string;
}

export interface DiscoveryUploadResult {
  results: DiscoveryUploadItemResult[];
  imported: number;
  failed: number;
  push?: unknown;
}


export interface DiscoveredTool {
  id: string;
  name: string;
  root_path: string;
  detected: boolean;
  confidence: string;
  config_paths: string[];
  resource_paths: string[];
  mcp_config_paths: string[];
  supports_kinds: ResourceKind[];
}

export interface DiscoveredMcpServer {
  id: string;
  tool: string;
  name: string;
  config_path: string;
  config: Record<string, unknown>;
  secret_keys: string[];
}

export interface EnvDiscoveryResult {
  tools: DiscoveredTool[];
  resources: DiscoveredResource[];
  mcp_servers: DiscoveredMcpServer[];
}

export interface CapturedResource {
  name: string;
  kind: ResourceKind;
  source: string;
  path: string;
  target_tools: string[];
  secret_placeholders: string[];
  warnings: string[];
}

export interface SecretPlaceholder {
  name: string;
  tool: string;
  resource: string;
  purpose: string;
}

export interface CaptureResult {
  root: string;
  registry_path: string;
  profile_path: string;
  secrets_path: string;
  captured: CapturedResource[];
  skipped: CapturedResource[];
  secrets: SecretPlaceholder[];
}

export interface DeployPlanItem {
  name: string;
  kind: ResourceKind;
  platform: string;
  target_path: string;
  action: "create" | "update" | "skip" | "conflict" | string;
  reason: string;
  backup_path?: string | null;
}

export interface DeployPlan {
  root: string;
  registry_path: string;
  dry_run: boolean;
  backup_root?: string | null;
  items: DeployPlanItem[];
  missing_secrets: SecretPlaceholder[];
  selected_names: string[];
}

export type EnvDiffStatus = "added" | "modified" | "deleted" | "same" | "conflict" | string;
export type EnvVersionChoice = "local" | "incoming";

export interface EnvSecretFinding {
  path: string;
  reason: string;
  preview: string;
}

export interface EnvDiffItem {
  id: string;
  group: string;
  name: string;
  kind: string;
  status: EnvDiffStatus;
  local_path?: string | null;
  incoming_path?: string | null;
  default_choice: EnvVersionChoice;
  selected_choice?: EnvVersionChoice | "";
  preview: string;
  reason: string;
}

export interface EnvDiffPlan {
  operation: "push" | "pull" | "import" | string;
  source: "remote" | "snapshot" | string;
  local_root: string;
  incoming_root: string;
  items: EnvDiffItem[];
  default_choices: Record<string, EnvVersionChoice>;
  blocked: boolean;
  secret_findings: EnvSecretFinding[];
}

export interface LpmResponse<T> {
  ok: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
  };
  raw: string;
}
