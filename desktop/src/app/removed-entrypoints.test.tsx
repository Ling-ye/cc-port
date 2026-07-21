import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createTranslator } from "@/app/i18n";
import { navItems } from "@/app/navigation";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { AddResourceView } from "@/features/add/AddResourceView";

afterEach(cleanup);

describe("removed environment and discovery entry points", () => {
  it("does not expose an environment navigation item", () => {
    expect(navItems.map((item) => item.id)).not.toContain("environment");
  });

  it("keeps local discovery out of the add-resource page", () => {
    render(
      <TaskCenterProvider>
        <AddResourceView
          t={createTranslator("en")}
          onChanged={vi.fn()}
          onError={vi.fn()}
        />
      </TaskCenterProvider>,
    );

    expect(screen.getByRole("button", { name: "Collect" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Upload" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Discover" })).not.toBeInTheDocument();
  });
});
