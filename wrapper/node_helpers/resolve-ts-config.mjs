// Doctor-owned TS alias resolver. Reads a repo's tsconfig (baseUrl / paths /
// project references) with the OFFICIAL TypeScript compiler API, and statically
// extracts vite `resolve.alias` mappings from the config AST. It NEVER executes
// target config — vite aliases are read from the parsed syntax tree only, and
// only literal mappings (string keys; string or path.resolve/join-of-literals
// values) are accepted. Anything dynamic (variables, env, spreads, regex finds)
// is reported as `unresolved`, never guessed.
//
// Usage:  node resolve-ts-config.mjs --repo <abs> --tsconfig <rel> [--vite <rel>]
// Env:    DOCTOR_TS_LIB = absolute path to the doctor-owned typescript package.
// Output: JSON on stdout — { aliases, baseUrl, paths, references, unresolved,
//         sources, typescriptVersion }. A fatal error prints { error } + exit 1.

import { createRequire } from 'module';
import { readFileSync } from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (argv[i].startsWith('--')) out[argv[i].slice(2)] = argv[i + 1];
  }
  return out;
}

function die(message) {
  process.stdout.write(JSON.stringify({ error: message }) + '\n');
  process.exit(1);
}

const args = parseArgs(process.argv.slice(2));
if (!args.repo || !args.tsconfig) die('--repo and --tsconfig are required');

let ts;
try {
  ts = require(process.env.DOCTOR_TS_LIB || 'typescript');
} catch (e) {
  die('cannot load doctor typescript lib: ' + e.message);
}

const repo = path.resolve(args.repo);
const aliases = {};          // enhanced-resolve form: prefix key -> absolute dir
const unresolved = [];
const sources = [];
const paths = {};
const references = [];
let baseUrl = repo;

function rel(p) {
  return path.relative(repo, p) || '.';
}

// Register an alias. tsconfig wildcard keys ("src/*") become prefix aliases;
// exact keys become "$"-suffixed exact aliases (enhanced-resolve convention).
function addTsAlias(key, absTarget) {
  if (key.endsWith('/*')) {
    aliases[key.slice(0, -2)] = absTarget.replace(/[\\/]\*$/, '').replace(/\*$/, '');
  } else if (key.endsWith('*')) {
    aliases[key.slice(0, -1)] = absTarget.replace(/\*$/, '');
  } else {
    aliases[key + '$'] = absTarget;
  }
}

function loadTsconfig(tsconfigRel, seen) {
  const abs = path.resolve(repo, tsconfigRel);
  if (seen.has(abs)) return;
  seen.add(abs);
  const read = ts.readConfigFile(abs, ts.sys.readFile);
  if (read.error) {
    unresolved.push('tsconfig ' + rel(abs) + ': ' +
      ts.flattenDiagnosticMessageText(read.error.messageText, ' '));
    return;
  }
  sources.push(rel(abs));
  const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, path.dirname(abs));
  const options = parsed.options || {};
  if (options.baseUrl) baseUrl = options.baseUrl;
  const configPaths = options.paths || {};
  const base = options.baseUrl || path.dirname(abs);
  for (const [key, targets] of Object.entries(configPaths)) {
    paths[key] = targets;
    if (!Array.isArray(targets) || targets.length === 0 || typeof targets[0] !== 'string') {
      unresolved.push('tsconfig path ' + key + ': no static target');
      continue;
    }
    addTsAlias(key, path.resolve(base, targets[0]));
  }
  for (const ref of parsed.projectReferences || []) {
    if (ref && typeof ref.path === 'string') {
      references.push(rel(ref.path));
      loadTsconfig(ref.path, seen);
    }
  }
}

// ---- vite resolve.alias: STATIC AST extraction only (never executed) ----

function calleeName(expr) {
  if (ts.isPropertyAccessExpression(expr)) {
    return calleeName(expr.expression) + '.' + expr.name.text;
  }
  if (ts.isIdentifier(expr)) return expr.text;
  return '';
}

// Resolve a value expression to an absolute string, or null if not static.
function staticString(node, viteDir) {
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isCallExpression(node)) {
    const name = calleeName(node.expression);
    if (name === 'path.resolve' || name === 'path.join' || name === 'resolve' || name === 'join') {
      const parts = [];
      for (const arg of node.arguments) {
        if (ts.isIdentifier(arg) && arg.text === '__dirname') parts.push(viteDir);
        else if (ts.isStringLiteralLike(arg)) parts.push(arg.text);
        else return null;
      }
      return name.endsWith('join') ? path.join(...parts) : path.resolve(...parts);
    }
  }
  return null;
}

function propertyKey(prop) {
  const name = prop.name;
  if (!name) return null;
  if (ts.isIdentifier(name) || ts.isStringLiteralLike(name)) return name.text;
  return null;
}

function addViteAlias(key, node, viteDir) {
  const value = staticString(node, viteDir);
  if (value === null) {
    unresolved.push("vite alias '" + key + "' -> non-literal expression (dynamic; not resolved)");
    return;
  }
  aliases[key] = value; // vite aliases are prefix matches
}

function extractViteAliasObject(objNode, viteDir) {
  for (const prop of objNode.properties) {
    if (ts.isPropertyAssignment(prop)) {
      const key = propertyKey(prop);
      if (key === null) {
        unresolved.push('vite alias with a computed/dynamic key (not resolved)');
        continue;
      }
      addViteAlias(key, prop.initializer, viteDir);
    } else {
      unresolved.push('vite alias entry is a spread/shorthand (not resolved)');
    }
  }
}

function extractViteAliasArray(arrNode, viteDir) {
  for (const el of arrNode.elements) {
    if (!ts.isObjectLiteralExpression(el)) {
      unresolved.push('vite alias array entry is not a literal {find,replacement}');
      continue;
    }
    let find = null, replacement = null, dynamicFind = false;
    for (const prop of el.properties) {
      if (!ts.isPropertyAssignment(prop)) continue;
      const key = propertyKey(prop);
      if (key === 'find') {
        if (ts.isStringLiteralLike(prop.initializer)) find = prop.initializer.text;
        else dynamicFind = true; // regex or expression
      } else if (key === 'replacement') {
        replacement = prop.initializer;
      }
    }
    if (dynamicFind || find === null || replacement === null) {
      unresolved.push('vite alias array entry is dynamic (regex find or non-literal; not resolved)');
      continue;
    }
    addViteAlias(find, replacement, viteDir);
  }
}

function loadVite(viteRel) {
  const abs = path.resolve(repo, viteRel);
  let text;
  try {
    text = readFileSync(abs, 'utf8');
  } catch (e) {
    unresolved.push('vite config unreadable ' + rel(abs) + ': ' + e.message);
    return;
  }
  sources.push(rel(abs));
  const viteDir = path.dirname(abs);
  const sf = ts.createSourceFile(abs, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const visit = (node) => {
    // A property named `alias` whose enclosing object is a `resolve: {...}`.
    if (ts.isPropertyAssignment(node) && propertyKey(node) === 'alias') {
      const enclosingObj = node.parent;                 // ObjectLiteralExpression
      const enclosingProp = enclosingObj && enclosingObj.parent; // PropertyAssignment resolve
      if (enclosingProp && ts.isPropertyAssignment(enclosingProp) &&
          propertyKey(enclosingProp) === 'resolve') {
        if (ts.isObjectLiteralExpression(node.initializer)) {
          extractViteAliasObject(node.initializer, viteDir);
        } else if (ts.isArrayLiteralExpression(node.initializer)) {
          extractViteAliasArray(node.initializer, viteDir);
        } else {
          unresolved.push('vite resolve.alias is a non-literal expression (not resolved)');
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

const seen = new Set();
loadTsconfig(args.tsconfig, seen);
if (args.vite) loadVite(args.vite);

process.stdout.write(JSON.stringify({
  aliases,
  baseUrl,
  paths,
  references,
  unresolved,
  sources,
  typescriptVersion: ts.version,
}) + '\n');
