import {
  BookOpenText,
  Database,
  History,
  SlidersHorizontal,
} from "lucide-react";
import type { I18nKey } from "@/app/i18n";

export type View =
  | "resources"
  | "operations"
  | "settings"
  | "guide";

export const navItems = [
  { id: "resources", labelKey: "nav.resources", icon: Database },
  { id: "operations", labelKey: "nav.operations", icon: History },
  { id: "settings", labelKey: "nav.settings", icon: SlidersHorizontal },
  { id: "guide", labelKey: "nav.guide", icon: BookOpenText },
] satisfies Array<{ id: View; labelKey: I18nKey; icon: typeof Database }>;
