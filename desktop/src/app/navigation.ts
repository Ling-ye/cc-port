import {
  Activity,
  Database,
  FolderSync,
  HeartPulse,
  PackagePlus,
  Settings,
} from "lucide-react";

export type View = "dashboard" | "resources" | "add" | "sync" | "health" | "platforms";

export const navItems = [
  { id: "dashboard", label: "Overview", icon: Activity },
  { id: "resources", label: "Resources", icon: Database },
  { id: "add", label: "Add Resource", icon: PackagePlus },
  { id: "sync", label: "Sync", icon: FolderSync },
  { id: "health", label: "Health", icon: HeartPulse },
  { id: "platforms", label: "Platforms", icon: Settings },
] satisfies Array<{ id: View; label: string; icon: typeof Activity }>;

