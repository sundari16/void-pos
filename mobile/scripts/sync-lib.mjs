// Keep the mobile till's shared logic identical to the web till's.
//
// The order, menu, payment and API rules live in web/src/lib; the mobile till uses the same
// files, copied verbatim, and the web tests that cover them (vitest -> jest). Mobile-only
// modules (config, money, till, storage, and the offline store / outbox / sync) are not
// touched.
//
//   node scripts/sync-lib.mjs           copy web -> mobile
//   node scripts/sync-lib.mjs --check   fail if the copies have drifted (CI runs this)

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const WEB = join(here, "..", "..", "web", "src", "lib");
const MOBILE = join(here, "..", "src", "lib");

const SHARED = ["types", "ulid", "menu", "order", "placed", "payments", "commands", "api"];
// The web tests for the shared modules; api.test.ts also covers the web-only config.
const TESTED = ["ulid", "menu", "order", "placed", "payments", "commands"];

/** A vitest file as jest runs it: jest's globals instead of vitest's imports, and jest's
 *  names for the few matchers that differ. */
function toJest(source) {
  return source
    .replace(/^import \{[^}]*\} from "vitest";\r?\n/m, "")
    .replace(/\bvi\.(fn|spyOn|useFakeTimers|useRealTimers|advanceTimersByTime)\b/g, "jest.$1")
    .replace(/\.toHaveBeenCalledOnce\(\)/g, ".toHaveBeenCalledTimes(1)");
}

// The checkout's own line endings (CRLF on a Windows clone), so --check passes on any OS.
const header = (from, source) =>
  `// Generated from web/src/lib/${from} by mobile/scripts/sync-lib.mjs — edit the web copy.` +
  (source.includes("\r\n") ? "\r\n" : "\n");

function planned() {
  const files = SHARED.map((name) => ({
    target: join(MOBILE, `${name}.ts`),
    content: readFileSync(join(WEB, `${name}.ts`), "utf8"),
  }));
  for (const name of TESTED) {
    const source = readFileSync(join(WEB, `${name}.test.ts`), "utf8");
    files.push({
      target: join(MOBILE, `${name}.test.ts`),
      content: header(`${name}.test.ts`, source) + toJest(source),
    });
  }
  return files;
}

const check = process.argv.includes("--check");
const drifted = [];
for (const { target, content } of planned()) {
  const current = existsSync(target) ? readFileSync(target, "utf8") : null;
  if (current === content) continue;
  if (check) drifted.push(target);
  else writeFileSync(target, content);
}
if (check && drifted.length) {
  console.error("mobile/src/lib has drifted from web/src/lib; run `npm run sync-lib`:");
  for (const file of drifted) console.error(`  ${file}`);
  process.exit(1);
}
console.log(check ? "mobile/src/lib matches web/src/lib" : "mobile/src/lib synced from web/src/lib");
