import {
  Activity,
  BookOpenText,
  Database,
  History,
  PackagePlus,
  SlidersHorizontal,
} from "lucide-react";
import type { I18nKey } from "@/app/i18n";

export type View =
  | "dashboard"
  | "resources"
  | "operations"
  | "add"
  | "settings"
  | "guide";

export const navItems = [
  { id: "dashboard", labelKey: "nav.dashboard", icon: Activity },
  { id: "resources", labelKey: "nav.resources", icon: Database },
  { id: "operations", labelKey: "nav.operations", icon: History },
  { id: "add", labelKey: "nav.add", icon: PackagePlus },
  { id: "settings", labelKey: "nav.settings", icon: SlidersHorizontal },
  { id: "guide", labelKey: "nav.guide", icon: BookOpenText },
] satisfies Array<{ id: View; labelKey: I18nKey; icon: typeof Activity }>;
