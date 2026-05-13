export type ResourceKind = "skill" | "mcp" | "rule" | "prompt" | "plugin";

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
  private?: boolean | null;
  reachable?: boolean | null;
  last_checked?: string | null;
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

export interface PlatformProfile {
  name: string;
  enabled: boolean;
  skills_dir: string;
  mcp_json: string;
  rules_dir: string;
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

export interface Summary {
  version: string;
  registry_path: string;
  resource_repo: ResourceRepoInfo;
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

export interface DoctorCheck {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
  enabled?: boolean;
  profile?: PlatformProfile;
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
