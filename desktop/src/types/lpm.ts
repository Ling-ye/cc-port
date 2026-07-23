export type ResourceKind = "skill" | "mcp" | "rule" | "prompt" | "plugin";
export type DiscoveryScope = "global" | "directory";
export type ResourceLifecycle = "active" | "removed";
export type RemovedEffect = "index_only" | "local_files_deleted" | "remote_repo_deleted" | "";

export type McpTransport = "stdio" | "http";

export interface PortableMcpStdioConfig {
  command: string;
  args?: string[];
  env?: Record<string, string>;
}

export interface PortableMcpHttpConfig {
  type: "http";
  url: string;
  env?: Record<string, string>;
}

export type PortableMcpConfig = PortableMcpStdioConfig | PortableMcpHttpConfig;

export interface CollectResourcePayload extends Record<string, unknown> {
  github_url: string;
  kind?: ResourceKind;
  name: string;
  push: boolean;
  mcp_config?: PortableMcpConfig;
}

export interface RegistryItem {
  name: string;
  kind: ResourceKind;
  source: "owned" | "external" | "local";
  repo: string;
  path: string;
  subdir: string;
  ref: string;
  install_dir: string;
  platform_install_dirs?: Record<string, string>;
  description: string;
  version?: string;
  author?: string;
  license?: string;
  mcp_config?: Record<string, unknown> | null;
  plugin?: PluginSpec | null;
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
  operation_id?: string;
  operation_status?: string;
  backup_root?: string | null;
  rolled_back?: boolean;
}

export type PluginPlatform = "codex" | "claude-code" | "opencode";
export type PluginTrack = "content" | "reference";
export type PluginScope = "user" | "project" | "local" | "managed";
export type PluginOriginType = "marketplace" | "npm" | "git" | "local";

export interface PluginSpec {
  track: PluginTrack;
  platform: PluginPlatform;
  plugin_id: string;
  origin: {
    type: PluginOriginType;
    marketplace: string;
    source: string;
    package: string;
    repo: string;
    selector: string;
  };
  observed_version: string;
  installations: Array<{
    scope: PluginScope;
    enabled: boolean;
    project?: { repo: string; subdir: string } | null;
  }>;
  dependencies: Record<string, string>;
}

export interface PluginProject {
  id: string;
  path: string;
  repo: string;
  subdir: string;
  portable: boolean;
  exists: boolean;
}

export interface PluginReferenceResult {
  status: string;
  resource_key: string;
  entry: RegistryItem;
  remote_commit: string;
  pushed: boolean;
}

export interface PluginDeleteInstancePlan {
  id: string;
  platform: PluginPlatform;
  scope: PluginScope;
  project_id: string;
  enabled?: boolean | null;
  writable: boolean;
  selectable: boolean;
  method: string;
  detail: string;
  local_path?: string | null;
  state_path?: string | null;
}

export interface PluginDeletePlan {
  resource_key: string;
  remote_commit: string;
  selected_instance_ids: string[];
  instances: PluginDeleteInstancePlan[];
  plan_hash: string;
  blocked: boolean;
  blockers: string[];
}

export interface PluginDeleteResult {
  status: string;
  resource_key: string;
  plan_hash: string;
  results: AssetActionResult[];
  remote_deleted: boolean;
  remote_commit: string;
  stale_plan?: PluginDeletePlan | null;
}

export interface AddResourceResult {
  entry: RegistryItem;
  push?: unknown;
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
export type ResourceCredentialMode = "auto" | "native" | "token";

export interface EditableConfig {
  github: {
    owner: string;
    repo_prefix: string;
    default_private: boolean;
  };
  git: {
    executable: string;
  };
  install: {
    target: string;
  };
  resources: {
    repo_name: string;
    repo_url: string;
    local_path: string;
    branch: string;
    credential_mode: ResourceCredentialMode;
  };
  state: {
    lock_timeout_seconds: number;
    retention_days: number;
    keep_latest_operations: number;
    max_backup_mb: number;
  };
  platforms: PlatformProfile[];
}

export type AssetStatus =
  | "remote-only"
  | "local-only"
  | "same"
  | "content-different"
  | "metadata-only"
  | "read-only-reference"
  | "target-conflict"
  | "uncomparable";

export type AssetAction =
  | "download"
  | "upload"
  | "copy-to-local"
  | "copy-to-remote"
  | "set-platform-install-name"
  | "align-plugin-state"
  | "plugin-delete";

export interface AssetPlatformRow {
  resource_key: string;
  kind: ResourceKind;
  name: string;
  platform: string;
  local_instance_id: string;
  local_locator: string;
  install_name: string;
  configured: boolean;
  enabled: boolean;
  detected: boolean;
  supported: boolean;
  remote_exists: boolean;
  local_exists: boolean;
  remote_writable: boolean;
  read_only_reference: boolean;
  remote_path?: string | null;
  local_path?: string | null;
  target_path?: string | null;
  ownership: string;
  status: AssetStatus;
  remote_commit: string;
  reference_commit: string;
  remote_content_fingerprint: string;
  remote_asset_fingerprint: string;
  local_fingerprint: string;
  metadata_differences: string[];
  diff_summary: string[];
  blockers: string[];
  warnings: string[];
  available_actions: AssetAction[];
  entry?: RegistryItem | null;
}

export interface AssetInventory {
  branch: string;
  remote_commit: string;
  repo_url: string;
  remote_available: boolean;
  remote_warning: string;
  scanned_local: boolean;
  generated_at: string;
  legacy_write_blocker: string;
  resources: AssetResourceRow[];
}

export type AssetLocalStatus = "unknown" | "missing" | "single" | "identical-copies" | "variants";
export type AssetRemoteStatus = "present" | "missing" | "read-only" | "unavailable";

export interface AssetLocalInstance {
  id: string;
  platform: string;
  install_name: string;
  path?: string | null;
  ownership: string;
  fingerprint: string;
  description: string;
  status: AssetStatus;
  warnings: string[];
  blockers: string[];
  track?: PluginTrack | "";
  scope?: PluginScope | "";
  project_id?: string;
  source_kind?: PluginOriginType | "";
  source_id?: string;
  selector?: string;
  observed_version?: string;
  enabled?: boolean | null;
  writable?: boolean;
}

export interface AssetRemoteState {
  exists: boolean;
  status: AssetRemoteStatus;
  writable: boolean;
  read_only: boolean;
  commit: string;
  path?: string | null;
  description: string;
}

export interface AssetResourceRow {
  resource_key: string;
  kind: ResourceKind;
  name: string;
  description: string;
  description_source: "remote" | "local" | "none";
  local_status: AssetLocalStatus;
  remote_status: AssetRemoteStatus;
  status: AssetStatus;
  remote: AssetRemoteState;
  local_instances: AssetLocalInstance[];
  metadata_differences: string[];
  diff_summary: string[];
  warnings: string[];
  blockers: string[];
  available_actions: AssetAction[];
  plugin_track?: PluginTrack | "";
  plugin_platform?: PluginPlatform | "";
  plugin_id?: string;
  plugin_source_kind?: PluginOriginType | "";
  plugin_source_id?: string;
  plugin_selector?: string;
  plugin_observed_version?: string;
}

export interface AssetBatchChoice {
  resource_key: string;
  platform?: string;
  local_instance_id?: string;
  resolution?: "overwrite" | "rename";
  new_name?: string;
  overwrite_unmanaged?: boolean;
  plugin_track?: PluginTrack | "skip" | "";
  ownership_confirmed?: boolean;
  reference_origin?: Record<string, string>;
  plugin_dependencies?: Record<string, string>;
}

export interface AssetBatchPlanItem {
  id: string;
  resource_key: string;
  platform: string;
  local_instance_id: string;
  action: string;
  disposition: "create" | "update" | "rename" | "unchanged" | "skip" | "manual" | "blocked";
  target_resource_key: string;
  reason: string;
  warnings: string[];
  blockers: string[];
  plan?: AssetActionPlan | null;
}

export interface AssetBatchPlan {
  direction: "upload" | "download";
  resource_keys: string[];
  target_platforms: string[];
  remote_commit: string;
  plan_hash: string;
  items: AssetBatchPlanItem[];
  executable_count: number;
  blocked_count: number;
  skipped_count: number;
  status: string;
}

export interface AssetBatchResult {
  status: string;
  plan_hash: string;
  results: AssetActionResult[];
  stale_plan?: AssetBatchPlan | null;
}

export interface AssetActionPlan {
  operation_id: string;
  action: AssetAction;
  resource_key: string;
  target_resource_key: string;
  kind: ResourceKind;
  name: string;
  platform: string;
  local_instance_id: string;
  local_locator: string;
  remote_commit: string;
  remote_target_exists: boolean;
  remote_target_fingerprint: string;
  local_source_fingerprint: string;
  target_path?: string | null;
  target_exists: boolean;
  target_fingerprint: string;
  target_managed: boolean;
  overwrite_unmanaged: boolean;
  new_name: string;
  new_install_name: string;
  warnings: string[];
  blockers: string[];
  blocked: boolean;
  created_at: string;
  schema_version: number;
}

export interface AssetActionResult {
  operation_id: string;
  action: AssetAction;
  status: string;
  resource_key: string;
  target_resource_key: string;
  platform: string;
  message: string;
  remote_commit: string;
  local_path?: string | null;
  replayed_on_latest: boolean;
  push_retry_count: number;
  warnings: string[];
  operation_status: string;
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

export interface GithubAuthStatus {
  state: "connected" | "missing" | "invalid";
  source: TokenSource;
  login: string;
  scopes: string[];
  token_preview: string;
  config_token_preview: string;
  can_reveal: boolean;
  can_clear: boolean;
  env_override: boolean;
  oauth_configured: boolean;
  error: string;
}

export type GithubAuthPurpose = "standard" | "organization_owner" | "remote_delete";

export interface GithubAuthSession {
  session_id: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
  purpose: GithubAuthPurpose;
  scopes: string[];
}

export interface GithubWebAuthSession {
  session_id: string;
  authorization_url: string;
  expires_in: number;
  interval: number;
  purpose: GithubAuthPurpose;
  scopes: string[];
}

export interface GithubAuthPollResult {
  state: "pending" | "slow_down" | "authorized" | "denied" | "expired";
  retry_after?: number;
  login?: string;
  scopes?: string[];
  token_preview?: string;
}

export interface GithubOwnerSetResult {
  owner: string;
  owner_type: "user" | "organization";
  authorized_login: string;
  settings: ConfigSettings;
}

export interface ResourceRepoBinding {
  owner: string;
  repo_name: string;
  repo_url: string;
  branch: string;
  branches: string[];
  transport: "https" | "ssh";
  credential_mode: "native";
  read_verified: boolean;
  write_verified: boolean;
  remote_empty: boolean;
  local_path: string;
  replaced_repo_url: string;
}

export interface ConfigBindRepoResult {
  settings: ConfigSettings;
  binding: ResourceRepoBinding;
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
  operation_id: string;
  status: string;
  rolled_back: boolean;
}

export interface ResourceSyncConflict {
  id: string;
  path: string;
  resource: string;
  reason: string;
}

export interface ResourceCommitIssue {
  path: string;
  reason: string;
}

export interface ResourceSecretFinding extends ResourceCommitIssue {
  preview: string;
  commit: string;
}

export interface ResourceCommitChange {
  name: string;
  kind: string;
  action: string;
  paths: string[];
}

export interface ResourceCommitPlan {
  repo_path: string;
  changed_paths: string[];
  managed_paths: string[];
  resources: ResourceCommitChange[];
  blocked_paths: ResourceCommitIssue[];
  secret_findings: ResourceSecretFinding[];
  suggested_message: string;
  blocked: boolean;
}

export interface ResourceSyncPlan {
  operation_id: string;
  repo_path: string;
  branch: string;
  status:
    | "clean"
    | "ahead"
    | "behind"
    | "diverged"
    | "unborn"
    | "no-remote"
    | "wrong-branch"
    | "dirty"
    | "conflict"
    | "ready"
    | "applied"
    | "cancelled"
    | "abandoned"
    | string;
  local_commit?: string | null;
  remote_commit?: string | null;
  merge_base?: string | null;
  ahead: number;
  behind: number;
  worktree_path?: string | null;
  merge_commit?: string | null;
  conflicts: ResourceSyncConflict[];
  detail: string;
  created_at: string;
  updated_at: string;
}

export interface OperationTarget {
  path: string;
  action: "restore" | "remove" | string;
  change_action: string;
  backup_path: string;
  resource: string;
  platform: string;
  before_hash: string;
  after_hash: string;
  verified: boolean;
}

export interface OperationHistorySummary {
  operation_id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string;
  message: string;
  rolled_back: boolean;
  target_count: number;
  changed_target_count: number;
  restorable: boolean;
}

export interface OperationHistoryEntry extends OperationHistorySummary {
  metadata: Record<string, unknown>;
  targets: OperationTarget[];
}

export interface OperationHistoryResult {
  operations: OperationHistoryEntry[];
}

export interface OperationHistoryPage {
  operations: OperationHistorySummary[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface OperationRestoreResult {
  source_operation_id: string;
  operation: {
    operation_id: string;
    status: string;
  };
}

export interface StateRetentionCandidate {
  operation_id: string;
  kind: string;
  status: string;
  timestamp: string;
  age_days: number;
  record_bytes: number;
  backup_bytes: number;
  reclaimable_bytes: number;
  reasons: string[];
}

export interface StateRetentionPlan {
  generated_at: string;
  state_root: string;
  policy: {
    retention_days: number;
    keep_latest_operations: number;
    max_backup_mb: number;
    max_backup_bytes: number;
  };
  operation_count: number;
  running_operation_count: number;
  protected_operation_count: number;
  operation_record_bytes: number;
  backup_bytes: number;
  orphan_backup_count: number;
  orphan_backup_bytes: number;
  candidate_count: number;
  reclaimable_bytes: number;
  projected_backup_bytes: number;
  candidates: StateRetentionCandidate[];
}

export interface StatePruneResult {
  cleanup_id: string;
  deleted_operation_ids: string[];
  failed: Array<{ operation_id: string; error: string }>;
  reclaimed_bytes: number;
  audit_path: string;
}

export interface OrphanBackup {
  name: string;
  path: string;
  kind: string;
  size_bytes: number;
  modified_at: string;
}

export interface OrphanBackupResult {
  orphans: OrphanBackup[];
}

export interface OrphanBackupExport {
  name: string;
  output_path: string;
  size_bytes: number;
  exported_at: string;
}

export interface OrphanQuarantine {
  quarantine_id: string;
  created_at: string;
  names: string[];
  item_count: number;
  size_bytes: number;
  path: string;
}

export interface OrphanQuarantineList {
  quarantines: OrphanQuarantine[];
}

export interface OrphanQuarantineResult {
  quarantine: OrphanQuarantine;
  audit_path: string;
}

export interface OrphanDeleteResult {
  delete_id: string;
  quarantine_id: string;
  deleted: boolean;
  reclaimed_bytes: number;
  error: string;
  audit_path: string;
}

export interface MaintenanceAudit {
  audit_id: string;
  action: string;
  status: string;
  created_at: string;
  item_count: number;
  reclaimed_bytes: number;
  path: string;
}

export interface MaintenanceAuditList {
  audits: MaintenanceAudit[];
}

export interface MaintenanceAuditDetail {
  audit: Record<string, unknown>;
}

export interface StaleResourceSyncPlan {
  operation_id: string;
  status: string;
  repo_path: string;
  worktree_path: string;
  updated_at: string;
  age_hours: number;
  reason: string;
}

export interface StaleResourceSyncResult {
  plans: StaleResourceSyncPlan[];
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
  deprecated?: boolean;
  warnings?: string[];
  error?: {
    code: string;
    message: string;
  };
}
