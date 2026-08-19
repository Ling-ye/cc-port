import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const [mode, contextPath, reportPath, expectedOperationId = ""] = process.argv.slice(2);
if (!mode || !contextPath || !reportPath || !["enable", "approve", "uninstall"].includes(mode)) {
  throw new Error("Usage: node ui_driver.mjs <enable|approve|uninstall> <context.json> <report.json> [operation-id]");
}
if (mode === "approve" && !expectedOperationId) {
  throw new Error("approve mode requires the expected operation id");
}
if (mode === "approve" && !/^[0-9a-f]{32}$/i.test(expectedOperationId)) {
  throw new Error("approve mode requires one exact generated operation id");
}

const context = JSON.parse(fs.readFileSync(contextPath, "utf8"));
if (context.schemaVersion !== 1) {
  throw new Error("Session context schema is missing or unsupported.");
}
const evidenceDirectory = path.win32.resolve(context.evidenceDirectory || "");
if (
  path.win32.dirname(path.win32.resolve(contextPath)) !== evidenceDirectory ||
  path.win32.dirname(path.win32.resolve(reportPath)) !== evidenceDirectory
) {
  throw new Error("Context and report must share the session evidence directory.");
}
if (fs.existsSync(reportPath)) {
  throw new Error("Refusing to overwrite an existing UI report.");
}
if (!Number.isInteger(context.debugPort) || context.debugPort < 49152 || context.debugPort > 60000) {
  throw new Error("Session context has an invalid WebView debug port.");
}
const startedAtUtc = new Date().toISOString();
const steps = [];
let socket;
let nextId = 1;
const pending = new Map();

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(description, probe, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await probe();
      if (last) return last;
    } catch (error) {
      last = String(error);
    }
    await sleep(350);
  }
  throw new Error(`Timed out waiting for ${description}; last=${JSON.stringify(last)}`);
}

function send(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.text || "WebView evaluation failed");
  }
  return response.result?.value;
}

async function bodyText() {
  return evaluate("document.body?.innerText || ''");
}

async function clickButton(labels, description, timeoutMs = 60_000) {
  const result = await waitFor(description, () => evaluate(`(() => {
    const labels = ${JSON.stringify(labels)};
    const buttons = Array.from(document.querySelectorAll("button"));
    const button = buttons.find((candidate) => {
      const text = (candidate.textContent || "").replace(/\\s+/g, " ").trim();
      return labels.some((label) => text === label || text.includes(label));
    });
    if (!button || button.disabled) return null;
    const text = (button.textContent || "").replace(/\\s+/g, " ").trim();
    button.scrollIntoView({ block: "center", inline: "center" });
    button.click();
    return { text };
  })()`), timeoutMs);
  steps.push({ name: description, ok: true, detail: result });
  return result;
}

async function navigateSettings({ reload = false } = {}) {
  const text = await bodyText();
  if (reload && (text.includes("AI 自动化") || text.includes("AI automation"))) {
    await clickButton(["资源", "Resources"], "leave-settings-for-refresh");
  }
  await clickButton(["设置", "Settings"], "navigate-settings");
  await waitFor("AI automation settings", async () => {
    const current = await bodyText();
    return current.includes("AI 自动化") || current.includes("AI automation");
  });
}

async function waitForFile(path, shouldExist, description, timeoutMs = 90_000) {
  await waitFor(description, () => fs.existsSync(path) === shouldExist, timeoutMs);
  steps.push({ name: description, ok: true, detail: { pathState: shouldExist ? "present" : "absent" } });
}

async function enableIntegration() {
  await navigateSettings();
  await clickButton(["审阅启用计划", "Review enable plan"], "open-enable-plan");
  await clickButton(["批准并启用", "Approve and enable"], "approve-and-enable");

  const installedSkill = `${context.skillRoot}\\cc-port\\SKILL.md`;
  await waitForFile(installedSkill, true, "managed-automation-skill-installed");
  await waitForFile(context.mcpPath, true, "managed-mcp-config-written");
  const mcp = JSON.parse(fs.readFileSync(context.mcpPath, "utf8"));
  const server = mcp.mcpServers?.["cc-port"];
  if (!server || server.command !== context.agentExe || JSON.stringify(server.args) !== JSON.stringify(["mcp", "--stdio"])) {
    throw new Error("Approved integration wrote an unexpected MCP entry.");
  }
  const visible = await bodyText();
  if (visible.includes("Approval requires a trusted CC Port desktop interaction")) {
    throw new Error("The installed desktop reported the trusted interaction error.");
  }

  const verify = spawnSync(context.sidecarExe, ["ai_integration_verify"], {
    encoding: "utf8",
    timeout: 90_000,
    env: {
      ...process.env,
      CC_PORT_CONFIG: context.configPath,
      CC_PORT_STATE_HOME: context.stateDir,
      CC_PORT_DESKTOP_API_PAYLOAD: JSON.stringify({ profile_id: "package-test", verify_transport: true }),
    },
  });
  if (verify.status !== 0) {
    throw new Error(`Installed integration verification failed: ${verify.stderr || verify.stdout}`);
  }
  const verified = JSON.parse(verify.stdout);
  if (!verified.ok || !verified.data?.configured || verified.data.transport_status !== "verified") {
    throw new Error(`Installed integration transport was not verified: ${verify.stdout}`);
  }
  steps.push({
    name: "verify-enabled-real-integration",
    ok: true,
    detail: {
      configured: true,
      transportStatus: verified.data.transport_status,
      mcpCommandMatchesInstalledAgent: true,
      trustedInteractionErrorObserved: false,
    },
  });
}

async function approvePending() {
  await navigateSettings({ reload: true });
  await waitFor("expected pending approval", async () => {
    const current = await bodyText();
    return current.includes("待处理 AI 审批") || current.includes("Pending AI approvals") ? current : null;
  });
  await clickButton(["审阅审批", "Review approval"], "open-pending-approval", 90_000);
  const dialog = await waitFor("approval review dialog", () => evaluate(`(() => {
    const element = document.querySelector('[role="dialog"]');
    return element ? (element.innerText || "") : null;
  })()`));
  if (!dialog.includes(expectedOperationId)) {
    throw new Error(`Approval dialog did not match expected operation ${expectedOperationId}.`);
  }
  steps.push({
    name: "verify-exact-operation-in-dialog",
    ok: true,
    detail: { expectedOperationId, matched: true },
  });
  const checkbox = await evaluate(`(() => {
    const dialog = document.querySelector('[role="dialog"]');
    const input = dialog?.querySelector('input[type="checkbox"]');
    if (!input) return null;
    input.click();
    return { checked: input.checked };
  })()`);
  if (!checkbox?.checked) {
    throw new Error("The exact-scope confirmation checkbox could not be selected.");
  }
  steps.push({ name: "confirm-exact-scope", ok: true, detail: { checked: true } });
  await clickButton(["单次批准", "Approve once"], "approve-once");
  await waitFor("approval dialog to close", () => evaluate("!document.querySelector('[role=dialog]')"));
}

async function uninstallIntegration() {
  await navigateSettings({ reload: true });
  await clickButton(["审阅卸载计划", "Review uninstall plan"], "open-uninstall-plan", 90_000);
  await clickButton(["批准并卸载", "Approve and uninstall"], "approve-and-uninstall");
  await waitForFile(`${context.skillRoot}\\cc-port`, false, "managed-automation-skill-removed", 90_000);
  await waitFor("managed MCP entry removal", () => {
    if (!fs.existsSync(context.mcpPath)) return true;
    const remaining = JSON.parse(fs.readFileSync(context.mcpPath, "utf8"));
    return !remaining.mcpServers?.["cc-port"];
  }, 90_000);
  if (!fs.existsSync(`${context.fixtureDir}\\SKILL.md`)) {
    throw new Error("Uninstall removed the E2E fixture Skill instead of only the managed cc-port integration.");
  }
  const visible = await bodyText();
  if (visible.includes("Approval requires a trusted CC Port desktop interaction")) {
    throw new Error("The installed desktop reported the trusted interaction error during uninstall.");
  }
  steps.push({
    name: "verify-scoped-integration-uninstall",
    ok: true,
    detail: { fixturePreserved: true, managedMcpEntryRemoved: true, trustedInteractionErrorObserved: false },
  });
}

let success = false;
let failure = null;
try {
  const targets = await waitFor("WebView2 DevTools target", async () => {
    const response = await fetch(`http://127.0.0.1:${context.debugPort}/json/list`);
    if (!response.ok) return null;
    const list = await response.json();
    return list.length ? list : null;
  }, 45_000);
  const target = targets.find((item) => item.type === "page") || targets[0];
  if (!target?.webSocketDebuggerUrl) throw new Error("WebView2 target did not expose a debugger WebSocket.");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (!message.id || !pending.has(message.id)) return;
    const operation = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) operation.reject(new Error(message.error.message));
    else operation.resolve(message.result || {});
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  await send("Runtime.enable");
  steps.push({ name: "connect-real-installed-webview", ok: true, detail: { title: target.title, url: target.url } });

  if (mode === "enable") await enableIntegration();
  if (mode === "approve") await approvePending();
  if (mode === "uninstall") await uninstallIntegration();
  success = true;
} catch (error) {
  failure = error instanceof Error ? error.stack || error.message : String(error);
} finally {
  if (socket) socket.close();
  const report = {
    mode,
    testId: context.testId,
    expectedOperationId,
    startedAtUtc,
    finishedAtUtc: new Date().toISOString(),
    success,
    failure,
    steps,
  };
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

process.exitCode = success ? 0 : 1;
