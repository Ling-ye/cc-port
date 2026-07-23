import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(desktopRoot, "..");
const sourceRoot = path.join(desktopRoot, "src");
const catalogPath = path.join(sourceRoot, "app", "i18n", "catalog.ts");

const allowedVisibleLiterals = new Map([
  ["Git", "product name"],
  ["GitHub", "product name"],
  ["LPM", "product name"],
  ["MCP", "protocol name"],
  ["HTTP", "protocol name"],
  ["L", "product monogram"],
  ["npm", "package ecosystem name"],
  ["marketplace", "technical origin value"],
  ["stdio", "transport value"],
  ["package=^1.0.0", "technical example"],
  ["EN", "language selector marker"],
  ["中", "language selector marker"],
]);

function walkFiles(root, extension, result = []) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) {
      walkFiles(absolute, extension, result);
    } else if (entry.name.endsWith(extension) && !entry.name.includes(".test.")) {
      result.push(absolute);
    }
  }
  return result;
}

function unwrapExpression(expression) {
  let current = expression;
  while (
    ts.isAsExpression(current)
    || ts.isSatisfiesExpression(current)
    || ts.isParenthesizedExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

function readCatalogObject(sourceFile, variableName) {
  let object;
  sourceFile.forEachChild((node) => {
    if (!ts.isVariableStatement(node)) return;
    for (const declaration of node.declarationList.declarations) {
      if (!ts.isIdentifier(declaration.name) || declaration.name.text !== variableName) continue;
      const initializer = declaration.initializer && unwrapExpression(declaration.initializer);
      if (initializer && ts.isObjectLiteralExpression(initializer)) object = initializer;
    }
  });
  if (!object) throw new Error(`Could not find the ${variableName} catalog.`);

  const entries = new Map();
  for (const property of object.properties) {
    if (!ts.isPropertyAssignment(property)) continue;
    const name = property.name;
    const value = unwrapExpression(property.initializer);
    if (
      !(ts.isStringLiteral(name) || ts.isIdentifier(name))
      || !(ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value))
    ) continue;
    entries.set(name.text, value.text);
  }
  return entries;
}

function placeholders(value) {
  return [...value.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)]
    .map((match) => match[1])
    .sort();
}

function auditCatalog(errors) {
  const source = fs.readFileSync(catalogPath, "utf8");
  const sourceFile = ts.createSourceFile(
    catalogPath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const en = readCatalogObject(sourceFile, "en");
  const zh = readCatalogObject(sourceFile, "zh");

  for (const key of en.keys()) {
    if (!zh.has(key)) errors.push(`catalog: missing Chinese key ${key}`);
  }
  for (const key of zh.keys()) {
    if (!en.has(key)) errors.push(`catalog: unexpected Chinese key ${key}`);
  }
  for (const [key, value] of en) {
    const zhValue = zh.get(key);
    if (zhValue === undefined) continue;
    const enPlaceholders = JSON.stringify(placeholders(value));
    const zhPlaceholders = JSON.stringify(placeholders(zhValue));
    if (enPlaceholders !== zhPlaceholders) {
      errors.push(
        `catalog: placeholder mismatch for ${key}: en=${enPlaceholders}, zh=${zhPlaceholders}`,
      );
    }
  }
  return { en, zh };
}

function collectBackendMessageCodes() {
  const files = [
    ...walkFiles(path.join(repositoryRoot, "src", "lpm"), ".py"),
    path.join(desktopRoot, "src-tauri", "src", "lib.rs"),
  ];
  const codes = new Set();
  const codePattern =
    /["']((?:(?:api|asset|bridge|doctor)\.[a-z0-9_.]+|plugin\.delete\.[a-z0-9_.]+))["']/g;
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(codePattern)) codes.add(match[1]);
  }
  return codes;
}

function auditMessageContract(en, errors) {
  const backendCodes = collectBackendMessageCodes();
  const catalogCodes = new Set(
    [...en.keys()]
      .filter((key) => key.startsWith("message."))
      .map((key) => key.slice("message.".length)),
  );
  for (const code of backendCodes) {
    if (!catalogCodes.has(code)) errors.push(`message contract: missing catalog code ${code}`);
  }
  for (const code of catalogCodes) {
    if (!backendCodes.has(code)) errors.push(`message contract: unused catalog code ${code}`);
  }
}

function visibleText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function shouldAuditLiteral(value) {
  return /[A-Za-z\u3400-\u9fff]/u.test(value);
}

function auditLiteral(file, node, rawValue, errors) {
  const value = visibleText(rawValue);
  if (!value || !shouldAuditLiteral(value) || allowedVisibleLiterals.has(value)) return;
  const sourceFile = node.getSourceFile();
  const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  const relative = path.relative(desktopRoot, file).replaceAll("\\", "/");
  errors.push(`${relative}:${position.line + 1}: unlocalized visible literal "${value}"`);
}

function auditExpressionLiterals(file, expression, errors) {
  const current = unwrapExpression(expression);
  if (ts.isStringLiteral(current) || ts.isNoSubstitutionTemplateLiteral(current)) {
    auditLiteral(file, current, current.text, errors);
    return;
  }
  if (ts.isConditionalExpression(current)) {
    auditExpressionLiterals(file, current.whenTrue, errors);
    auditExpressionLiterals(file, current.whenFalse, errors);
  }
}

function auditTsx(errors) {
  const visibleAttributes = new Set(["alt", "aria-label", "placeholder", "title"]);
  for (const file of walkFiles(sourceRoot, ".tsx")) {
    const source = fs.readFileSync(file, "utf8");
    const sourceFile = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const visit = (node) => {
      if (ts.isJsxText(node)) {
        auditLiteral(file, node, node.text, errors);
      } else if (ts.isJsxAttribute(node) && visibleAttributes.has(node.name.text)) {
        if (node.initializer && ts.isStringLiteral(node.initializer)) {
          auditLiteral(file, node.initializer, node.initializer.text, errors);
        }
      } else if (
        ts.isJsxExpression(node)
        && node.parent
        && !ts.isJsxAttribute(node.parent)
        && node.expression
      ) {
        auditExpressionLiterals(file, node.expression, errors);
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);
  }
}

const errors = [];
const { en } = auditCatalog(errors);
auditMessageContract(en, errors);
auditTsx(errors);

if (errors.length) {
  console.error(["Desktop i18n gate failed:", ...errors.map((error) => `- ${error}`)].join("\n"));
  process.exitCode = 1;
} else {
  console.log("Desktop i18n gate passed.");
}
