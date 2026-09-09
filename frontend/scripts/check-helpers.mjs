/** The two pure helpers behind the plan form, checked without a test runner:
 *   node scripts/check-helpers.mjs
 * Node strips the TypeScript, so these are the shipped functions, not copies.
 */

import assert from "node:assert/strict";
import { composeNotePre, plannedQty } from "../src/api/trades.ts";
import { DASH, ago } from "../src/fmt.ts";

// risk / |entry - stop|, whichever side the stop is on
assert.equal(plannedQty("100", "95", "25"), "5");
assert.equal(plannedQty("100", "105", "25"), "5");
assert.equal(plannedQty("100", "99.5", "25"), "50");
assert.equal(plannedQty("2.5", "2", "1"), "2");
assert.equal(plannedQty("100", "90", "1"), "0.1");
assert.equal(plannedQty("100", "95", "0"), "0");
// No answer beats a made-up one
assert.equal(plannedQty("100", "100", "25"), "");
assert.equal(plannedQty("", "95", "25"), "");
assert.equal(plannedQty("100", "95", "abc"), "");

// Empty answers leave no empty heading behind
assert.equal(composeNotePre("", "", ""), null);
assert.equal(composeNotePre("", "   ", ""), null);
assert.equal(
  composeNotePre("breakout", "", " below 95 "),
  "## Why\n\nbreakout\n\n## What would stop me\n\nbelow 95",
);
assert.equal(
  composeNotePre("a", "b", "c"),
  "## Why\n\na\n\n## Where I am wrong\n\nb\n\n## What would stop me\n\nc",
);

// Heartbeat age — the fleet page's "is this bot alive" line
const now = Date.parse("2026-09-09T12:00:00Z");
assert.equal(ago("2026-09-09T11:59:30Z", now), "30 s ago");
assert.equal(ago("2026-09-09T11:48:00Z", now), "12 min ago");
assert.equal(ago("2026-09-09T09:00:00Z", now), "3 h ago");
assert.equal(ago("2026-09-07T12:00:00Z", now), "2 d ago");
assert.equal(ago("2026-09-09T15:00:00Z", now), "in 3 h");
assert.equal(ago(null, now), DASH);
assert.equal(ago("not a date", now), DASH);

console.log("check-helpers: ok");
