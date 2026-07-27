// @vitest-environment node

import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { outputTreesEqual, publishStableOutput } from "./stable-vite-build.mjs";

const temporaryRoots = [];

async function createFixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cc-port-stable-vite-"));
  temporaryRoots.push(root);
  const output = path.join(root, "dist");
  const staging = path.join(root, "staging");
  await fs.mkdir(path.join(output, "assets"), { recursive: true });
  await fs.mkdir(path.join(staging, "assets"), { recursive: true });
  return { output, staging };
}

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      fs.rm(root, { recursive: true, force: true }),
    ),
  );
});

describe("stable Vite output publishing", () => {
  it("preserves the existing output and timestamp when content is unchanged", async () => {
    const { output, staging } = await createFixture();
    const outputFile = path.join(output, "assets", "app.js");
    const stagingFile = path.join(staging, "assets", "app.js");
    await fs.writeFile(outputFile, "same-content");
    await fs.writeFile(stagingFile, "same-content");
    const fixedTime = new Date("2020-01-02T03:04:05.000Z");
    await fs.utimes(outputFile, fixedTime, fixedTime);
    await fs.utimes(path.join(output, "assets"), fixedTime, fixedTime);
    await fs.utimes(output, fixedTime, fixedTime);

    expect(await outputTreesEqual(output, staging)).toBe(true);
    await expect(publishStableOutput(staging, output)).resolves.toBe("reused");
    expect(await fs.readFile(outputFile, "utf8")).toBe("same-content");
    expect((await fs.stat(outputFile)).mtimeMs).toBe(fixedTime.getTime());
    expect((await fs.stat(path.join(output, "assets"))).mtimeMs).toBe(
      fixedTime.getTime(),
    );
    expect((await fs.stat(output)).mtimeMs).toBe(fixedTime.getTime());
    await expect(fs.access(staging)).rejects.toThrow();
  });

  it("replaces the output when any generated file changes", async () => {
    const { output, staging } = await createFixture();
    await fs.writeFile(path.join(output, "assets", "app.js"), "old-content");
    await fs.writeFile(path.join(staging, "assets", "app.js"), "new-content");

    expect(await outputTreesEqual(output, staging)).toBe(false);
    await expect(publishStableOutput(staging, output)).resolves.toBe("updated");
    expect(await fs.readFile(path.join(output, "assets", "app.js"), "utf8")).toBe(
      "new-content",
    );
    await expect(fs.access(staging)).rejects.toThrow();
  });

  it("restores the previous output when the verified switch fails", async () => {
    const { output, staging } = await createFixture();
    await fs.writeFile(path.join(output, "assets", "app.js"), "old-content");
    await fs.writeFile(path.join(staging, "assets", "app.js"), "new-content");
    let renameCalls = 0;
    const injectedFileSystem = {
      ...fs,
      async rename(source, destination) {
        renameCalls += 1;
        if (renameCalls === 2) {
          throw new Error("injected output switch failure");
        }
        return fs.rename(source, destination);
      },
    };

    await expect(
      publishStableOutput(staging, output, injectedFileSystem),
    ).rejects.toThrow("injected output switch failure");
    expect(renameCalls).toBe(3);
    expect(await fs.readFile(path.join(output, "assets", "app.js"), "utf8")).toBe(
      "old-content",
    );
    expect(await fs.readFile(path.join(staging, "assets", "app.js"), "utf8")).toBe(
      "new-content",
    );
  });

  it("reports and preserves the backup when output switch and rollback both fail", async () => {
    const { output, staging } = await createFixture();
    const root = path.dirname(output);
    await fs.writeFile(path.join(output, "assets", "app.js"), "old-content");
    await fs.writeFile(path.join(staging, "assets", "app.js"), "new-content");
    let renameCalls = 0;
    const injectedFileSystem = {
      ...fs,
      async rename(source, destination) {
        renameCalls += 1;
        if (renameCalls >= 2) {
          throw new Error(
            renameCalls === 2
              ? "injected output switch failure"
              : "injected rollback failure",
          );
        }
        return fs.rename(source, destination);
      },
    };

    let failure;
    try {
      await publishStableOutput(staging, output, injectedFileSystem);
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(Error);
    expect(failure.message).toContain("injected output switch failure");
    expect(failure.message).toContain("injected rollback failure");
    expect(failure.message).toContain("Preserved backup:");
    const backups = (await fs.readdir(root)).filter((name) =>
      name.startsWith(".vite-dist-backup-"),
    );
    expect(backups).toHaveLength(1);
    expect(failure.message).toContain(path.join(root, backups[0]));
    expect(
      await fs.readFile(path.join(root, backups[0], "assets", "app.js"), "utf8"),
    ).toBe("old-content");
  });
});
