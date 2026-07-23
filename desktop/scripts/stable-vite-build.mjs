import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP_DIRECTORY = path.resolve(SCRIPT_DIRECTORY, "..");

async function pathExists(candidate) {
  try {
    await fs.access(candidate);
    return true;
  } catch {
    return false;
  }
}

async function listFiles(root) {
  if (!(await pathExists(root))) {
    return null;
  }
  const files = [];
  async function visit(directory, relativeDirectory) {
    const entries = await fs.readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const relativePath = path.join(relativeDirectory, entry.name);
      const absolutePath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(absolutePath, relativePath);
      } else if (entry.isFile()) {
        const stat = await fs.stat(absolutePath);
        files.push({ relativePath, absolutePath, size: stat.size });
      } else {
        throw new Error(`Unsupported Vite output entry: ${absolutePath}`);
      }
    }
  }
  await visit(root, "");
  return files;
}

export async function outputTreesEqual(leftRoot, rightRoot) {
  const [leftFiles, rightFiles] = await Promise.all([
    listFiles(leftRoot),
    listFiles(rightRoot),
  ]);
  if (leftFiles === null || rightFiles === null) {
    return false;
  }
  if (leftFiles.length !== rightFiles.length) {
    return false;
  }
  for (let index = 0; index < leftFiles.length; index += 1) {
    const left = leftFiles[index];
    const right = rightFiles[index];
    if (left.relativePath !== right.relativePath || left.size !== right.size) {
      return false;
    }
    const [leftContent, rightContent] = await Promise.all([
      fs.readFile(left.absolutePath),
      fs.readFile(right.absolutePath),
    ]);
    if (!leftContent.equals(rightContent)) {
      return false;
    }
  }
  return true;
}

export async function publishStableOutput(
  stagingDirectory,
  outputDirectory,
  fileSystem = fs,
) {
  if (await outputTreesEqual(stagingDirectory, outputDirectory)) {
    await fileSystem.rm(stagingDirectory, { recursive: true, force: true });
    return "reused";
  }

  const parent = path.dirname(outputDirectory);
  const backupDirectory = path.join(
    parent,
    `.vite-dist-backup-${process.pid}-${Date.now()}`,
  );
  await fileSystem.mkdir(parent, { recursive: true });
  const hadOutput = await pathExists(outputDirectory);
  if (hadOutput) {
    await fileSystem.rename(outputDirectory, backupDirectory);
  }
  try {
    await fileSystem.rename(stagingDirectory, outputDirectory);
  } catch (error) {
    if (
      hadOutput &&
      (await pathExists(backupDirectory)) &&
      !(await pathExists(outputDirectory))
    ) {
      try {
        await fileSystem.rename(backupDirectory, outputDirectory);
      } catch (rollbackError) {
        throw new Error(
          `Stable Vite output switch failed (${error.message}); rollback also failed (${rollbackError.message}). Preserved backup: ${backupDirectory}`,
          { cause: error },
        );
      }
    }
    throw error;
  }
  if (await pathExists(backupDirectory)) {
    try {
      await fileSystem.rm(backupDirectory, { recursive: true, force: true });
    } catch (error) {
      console.warn(
        `[stable-vite-build] output updated, but stale backup cleanup failed: ${backupDirectory} (${error.message})`,
      );
    }
  }
  return "updated";
}

async function main() {
  const { build: viteBuild } = await import("vite");
  const cacheDirectory = path.join(DESKTOP_DIRECTORY, ".cache");
  const stagingDirectory = path.join(
    cacheDirectory,
    `vite-dist-${process.pid}-${Date.now()}`,
  );
  const outputDirectory = path.join(DESKTOP_DIRECTORY, "dist");
  await fs.mkdir(cacheDirectory, { recursive: true });
  try {
    await viteBuild({
      root: DESKTOP_DIRECTORY,
      build: {
        outDir: stagingDirectory,
        emptyOutDir: true,
      },
    });
    const result = await publishStableOutput(stagingDirectory, outputDirectory);
    console.log(
      result === "reused"
        ? "[stable-vite-build] output unchanged; preserved desktop/dist timestamps"
        : "[stable-vite-build] output changed; replaced desktop/dist",
    );
  } finally {
    await fs.rm(stagingDirectory, { recursive: true, force: true });
  }
}

const isEntryPoint =
  process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (isEntryPoint) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
