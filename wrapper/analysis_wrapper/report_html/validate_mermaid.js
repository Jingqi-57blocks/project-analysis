"use strict";

/* Parse authored diagrams with the exact vendored Mermaid runtime used by the
 * generated HTML report. This performs no rendering, target-code execution,
 * filesystem discovery, or network access. */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const runtime = path.join(__dirname, "vendor", "mermaid.min.js");
let runtimeSource = fs.readFileSync(runtime, "utf8");
// The browser bundle creates DOMPurify from `window`; Node intentionally has no
// DOM. Parsing does not render or persist HTML, so replace only that browser
// adapter with inert hooks while retaining the exact vendored Mermaid grammar.
const domPurifyInit = "ah=iG()";
if (!runtimeSource.includes(domPurifyInit)) {
  throw new Error("vendored Mermaid runtime shape changed; update validator");
}
runtimeSource = runtimeSource.replace(
  domPurifyInit,
  "ah={addHook:()=>{},sanitize:(value)=>value}",
);
vm.runInThisContext(runtimeSource, { filename: runtime });

async function main() {
  const blocks = JSON.parse(fs.readFileSync(0, "utf8"));
  if (!Array.isArray(blocks) || blocks.some((value) => typeof value !== "string")) {
    throw new Error("input must be a JSON array of Mermaid source strings");
  }
  globalThis.mermaid.initialize({ startOnLoad: false, logLevel: "fatal" });
  const results = [];
  for (const source of blocks) {
    try {
      await globalThis.mermaid.parse(source);
      results.push({ valid: true });
    } catch (error) {
      results.push({ valid: false, error: String(error) });
    }
  }
  process.stdout.write(JSON.stringify(results));
}

main().catch((error) => {
  process.stderr.write(String(error));
  process.exitCode = 1;
});
