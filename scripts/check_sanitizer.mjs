// Checks the clean() sanitizer in src/docs_mcp/static/index.html, which is the
// only thing standing between LLM-authored markdown and innerHTML.
//
//   node scripts/check_sanitizer.mjs
//
// Needs jsdom, which is deliberately NOT a project dependency (this repo has no
// JS toolchain). Point JSDOM_PATH at any node_modules that has it, or install
// one anywhere: npm i jsdom && JSDOM_PATH=$PWD/node_modules node scripts/...
import { createRequire } from "module";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import path from "path";

const candidates = [
  process.env.JSDOM_PATH,
  path.join(process.cwd(), "node_modules"),
  path.join(process.env.HOME || "", ".cache/opencode/packages/oh-my-opencode-slim/node_modules"),
].filter(Boolean);

let JSDOM;
for (const base of candidates) {
  try {
    JSDOM = createRequire(base.endsWith("/") ? base : base + "/")("jsdom").JSDOM;
    break;
  } catch {}
}
if (!JSDOM) {
  console.error("jsdom not found. Tried:\n  " + candidates.join("\n  "));
  console.error("Install it anywhere and set JSDOM_PATH to that node_modules dir.");
  process.exit(2);
}

const here = path.dirname(fileURLToPath(import.meta.url));
const html = readFileSync(path.join(here, "../src/docs_mcp/static/index.html"), "utf8");
const js = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join("\n");
const src = js.slice(js.indexOf("const OK_TAGS"), js.indexOf("function setHTML"));
if (!src) { console.error("could not locate clean() in index.html"); process.exit(2); }

global.document = new JSDOM("<!doctype html><body>").window.document;
const clean = new Function(src + "; return clean;")();

// Each payload paired with a pattern that must NOT appear in the output.
const attacks = [
  ["<script>alert(1)</script>", /alert|script/i],
  ["<style>body{x:y}</style>", /body\{|style/i],
  ['<img src=x onerror="alert(1)">', /onerror|<img/i],
  ['<a href="javascript:alert(1)">x</a>', /javascript:/i],
  ['<a href="vbscript:msgbox(1)">x</a>', /vbscript/i],
  ['<iframe src="http://evil"></iframe>', /iframe/i],
  ['<object data="evil"></object>', /object/i],
  ['<div onclick="alert(1)">x</div>', /onclick/i],
  ['<svg onload="alert(1)">', /onload|svg/i],
  ['<p style="background:url(javascript:alert(1))">x</p>', /style/i],
  ['<form action="/x"><input name="y"></form>', /<form|<input/i],
  ['<svg></p><style><a id="</style><img src=1 onerror=alert(1)>">', /onerror|<img|<svg/i],
  ['<noscript><p title="</noscript><img src=x onerror=alert(1)>">', /onerror|<img/i],
];

// Markdown that marked legitimately emits and must survive untouched.
const keep = [
  ["<p>hello <strong>world</strong></p>", "strong"],
  ["<pre><code>x = 1</code></pre>", "code"],
  ['<a href="https://x.com">link</a>', "href"],
  ['<span class="thinking">thinking…</span>', "thinking"],
  ["<ol><li>a</li></ol>", "<li>"],
  ["<table><tr><td>c</td></tr></table>", "<td>"],
  ["<blockquote><p>q</p></blockquote>", "blockquote"],
];

let fails = 0;
for (const [payload, forbidden] of attacks) {
  const out = clean(payload);
  if (forbidden.test(out)) { fails++; console.error(`FAIL  ${payload}\n   -> ${out}`); }
}
for (const [payload, must] of keep) {
  const out = clean(payload);
  if (!out.includes(must)) { fails++; console.error(`FAIL  dropped ${must} from ${payload}\n   -> ${out}`); }
}
if (!clean('<div onclick="x()">text</div>').includes("text")) {
  fails++; console.error("FAIL  stripping a tag must keep its text");
}
if (!clean('<a href="https://x.com">l</a>').includes('rel="noopener noreferrer"')) {
  fails++; console.error("FAIL  external links must get rel=noopener");
}

console.log(fails ? `${fails} failure(s)` : `sanitizer ok — ${attacks.length} payloads blocked, ${keep.length + 2} shapes preserved`);
process.exit(fails ? 1 : 0);
