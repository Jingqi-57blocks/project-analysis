#!/usr/bin/env node
// Analyzer-owned JS/TS call extractor (57B-30 production lane, READ-ONLY).
//
// Loads a TypeScript Program via the ANALYZER-OWNED pinned typescript
// (resolved from ANALYSIS_TS_LIB — never a path resolved from a target), walks
// every CallExpression / NewExpression, asks the type checker for the resolved
// signature, and classifies each call site:
//   resolved-internal : concrete callee declaration lives in an analyzed
//                       PRODUCTION file  -> EMIT edge (caller -> callee)
//   external          : declaration in node_modules / lib.*.d.ts / a loaded but
//                       non-production file -> never emitted
//   ambiguous         : bare signature/interface/union (no concrete impl) -> never emitted
//   unresolved        : no signature / no declaration (dynamic) -> never emitted
//
// The PRODUCTION boundary is decided in Python (analysis_wrapper.callgraph.sources)
// and handed in as an explicit file list (--files), so the boundary lives in ONE
// place. This helper only loads those files (with the repo's resolution
// semantics) and never re-derives the boundary.
//
// Usage:
//   node extract-calls.mjs --repo <abs> --mode tsconfig --tsconfig <rel> --files <listpath>
//   node extract-calls.mjs --repo <abs> --mode inferred [--module commonjs|esnext] --files <listpath>
// Output: a single JSON object on stdout; a fatal error prints {error} + exit 2.

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve, sep } from 'node:path';

const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const eq = a.indexOf('=');
    if (eq >= 0) out[a.slice(2, eq)] = a.slice(eq + 1);
    else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) out[a.slice(2)] = argv[i += 1];
    else out[a.slice(2)] = true;
  }
  return out;
}
const args = parseArgs(process.argv.slice(2));

function die(message) {
  process.stdout.write(JSON.stringify({ error: message }) + '\n');
  process.exit(2);
}

let ts;
try {
  ts = require(process.env.ANALYSIS_TS_LIB || 'typescript');
} catch (e) {
  die('cannot load analyzer typescript lib: ' + e.message);
}

const MODE = args.mode;
const REPO = args.repo && resolve(args.repo);
if (!REPO || (MODE !== 'tsconfig' && MODE !== 'inferred')) {
  die('need --repo and --mode=tsconfig|inferred');
}
if (!args.files) die('need --files <path to newline-delimited production file list>');

let rootFiles;
try {
  rootFiles = readFileSync(args.files, 'utf8').split('\n').map((s) => s.trim()).filter(Boolean);
} catch (e) {
  die('cannot read --files list: ' + e.message);
}

// ---- build the Program ------------------------------------------------------
let program;
if (MODE === 'tsconfig') {
  const configPath = resolve(REPO, args.tsconfig);
  const read = ts.readConfigFile(configPath, ts.sys.readFile);
  if (read.error) die('tsconfig read error: ' + ts.flattenDiagnosticMessageText(read.error.messageText, ' '));
  const configDir = configPath.slice(0, configPath.lastIndexOf(sep));
  const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, configDir, undefined, configPath);
  const options = { ...parsed.options, noEmit: true };
  // Keep the repo's declared resolution semantics (paths/baseUrl/module), but
  // restrict the analyzed set to the Python-provided production files.
  program = ts.createProgram({ rootNames: rootFiles, options });
} else {
  const options = {
    allowJs: true, checkJs: false, noEmit: true, allowNonTsExtensions: true,
    module: args.module === 'esnext' ? ts.ModuleKind.ESNext : ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020, moduleResolution: ts.ModuleResolutionKind.Node10,
    esModuleInterop: true, skipLibCheck: true, resolveJsonModule: true,
  };
  program = ts.createProgram({ rootNames: rootFiles, options });
}

const checker = program.getTypeChecker();
const prodSet = new Set(rootFiles);
const loaded = new Set(program.getSourceFiles().map((sf) => sf.fileName));
// Production files we asked for but the compiler did not load = parse/load failures.
const failed = rootFiles.filter((f) => !loaded.has(f));
// Analyzed = production files actually loaded (walk call sites only in these).
const analyzed = rootFiles.filter((f) => loaded.has(f));

// ---- helpers ----------------------------------------------------------------
const isNodeModules = (fn) => fn.includes('/node_modules/');
const isDefaultLib = (sf) => sf.isDeclarationFile && /(^|\/)lib\.[^/]*\.d\.ts$/.test(sf.fileName);

const posOf = (sf, node) => {
  const lc = sf.getLineAndCharacterOfPosition(node.getStart(sf));
  return `${sf.fileName}:${lc.line + 1}:${lc.character + 1}`;
};
const declPos = (decl) => {
  const sf = decl.getSourceFile();
  const lc = sf.getLineAndCharacterOfPosition(decl.getStart(sf));
  return `${sf.fileName}:${lc.line + 1}:${lc.character + 1}`;
};

const FN_KINDS = new Set([
  ts.SyntaxKind.FunctionDeclaration, ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.ArrowFunction, ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.Constructor, ts.SyntaxKind.GetAccessor, ts.SyntaxKind.SetAccessor,
]);
const METHOD_KINDS = new Set([
  ts.SyntaxKind.MethodDeclaration, ts.SyntaxKind.GetAccessor, ts.SyntaxKind.SetAccessor,
]);
const SIG_ONLY_KINDS = new Set([
  ts.SyntaxKind.MethodSignature, ts.SyntaxKind.CallSignature,
  ts.SyntaxKind.ConstructSignature, ts.SyntaxKind.FunctionType,
  ts.SyntaxKind.IndexSignature, ts.SyntaxKind.PropertySignature,
]);
const CONCRETE_KINDS = new Set([...FN_KINDS,
  ts.SyntaxKind.ClassDeclaration, ts.SyntaxKind.ClassExpression]);

function describeFn(node) {
  if (!node) return '<module>';
  if (node.name && ts.isIdentifier(node.name)) {
    const cls = node.parent && ts.isClassLike(node.parent) && node.parent.name ? node.parent.name.text + '.' : '';
    return cls + node.name.text;
  }
  const p = node.parent;
  if (p && ts.isVariableDeclaration(p) && p.name && ts.isIdentifier(p.name)) return p.name.text;
  if (p && ts.isPropertyAssignment(p) && p.name) return p.name.getText();
  if (p && ts.isPropertyDeclaration(p) && p.name) {
    const cls = p.parent && ts.isClassLike(p.parent) && p.parent.name ? p.parent.name.text + '.' : '';
    return cls + p.name.getText();
  }
  if (node.kind === ts.SyntaxKind.Constructor) {
    const cls = node.parent && node.parent.name ? node.parent.name.text : 'class';
    return cls + '.constructor';
  }
  return '<anonymous>';
}
function enclosingFn(node) {
  let n = node.parent;
  while (n) { if (FN_KINDS.has(n.kind)) return n; n = n.parent; }
  return null;
}
function calleeNameFromDecl(decl) {
  if (decl.name) return decl.name.getText();
  if (decl.kind === ts.SyntaxKind.Constructor && decl.parent && decl.parent.name) {
    return decl.parent.name.text + '.constructor';
  }
  return describeFn(decl);
}
// The class a `new X()` targets, for the common case of an implicit constructor
// (TypeScript resolves no signature declaration then). Follows import aliases.
function classDeclOf(node) {
  let sym;
  try { sym = checker.getSymbolAtLocation(node.expression); } catch { return null; }
  if (sym && (sym.flags & ts.SymbolFlags.Alias)) {
    try { sym = checker.getAliasedSymbol(sym); } catch { /* keep original */ }
  }
  const decl = sym && (sym.valueDeclaration || (sym.declarations && sym.declarations[0]));
  if (decl && (ts.isClassDeclaration(decl) || ts.isClassExpression(decl))) return decl;
  return null;
}

// ---- walk -------------------------------------------------------------------
const counts = { resolved: 0, ambiguous: 0, external: 0, unresolved: 0 };
const edges = [];

function classify(node, sf) {
  const isNew = ts.isNewExpression(node);
  let decl;
  try { const sig = checker.getResolvedSignature(node); decl = sig && sig.declaration; }
  catch { decl = undefined; }
  if (isNew && (!decl || !CONCRETE_KINDS.has(decl.kind))) {
    const cd = classDeclOf(node);
    if (cd) decl = cd;
  }
  if (!decl) { counts.unresolved++; return; }

  const declSf = decl.getSourceFile();
  if (isNodeModules(declSf.fileName) || isDefaultLib(declSf)) { counts.external++; return; }

  const inProd = prodSet.has(declSf.fileName);
  if (inProd && CONCRETE_KINDS.has(decl.kind)) {
    counts.resolved++;
    const caller = enclosingFn(node);
    const isMethod = METHOD_KINDS.has(decl.kind)
      || (!isNew && node.expression
          && (ts.isPropertyAccessExpression(node.expression) || ts.isElementAccessExpression(node.expression)));
    edges.push({
      lang: /\.[cm]?tsx?$/i.test(sf.fileName) ? 'ts' : 'js',
      kind: isNew ? 'constructor' : (isMethod ? 'method-dispatch' : 'static-call'),
      callerSymbol: describeFn(caller),
      callerDecl: caller ? declPos(caller) : `${sf.fileName}:1:1`,
      callsite: posOf(sf, node),
      calleeSymbol: calleeNameFromDecl(decl),
      calleeDecl: declPos(decl),
    });
    return;
  }
  if (SIG_ONLY_KINDS.has(decl.kind)) { counts.ambiguous++; return; }
  counts.external++;          // resolved to a non-production, non-node_modules decl
}

for (const sf of program.getSourceFiles()) {
  if (!prodSet.has(sf.fileName) || sf.isDeclarationFile) continue;
  const visit = (node) => {
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) classify(node, sf);
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

process.stdout.write(JSON.stringify({
  tsVersion: ts.version,
  mode: MODE,
  analyzed,
  failed,
  counts,
  edges,
}) + '\n');
