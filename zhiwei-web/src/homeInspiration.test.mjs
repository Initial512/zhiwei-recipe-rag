import assert from "node:assert/strict";
import test from "node:test";

import { HOME_INSPIRATION_POOL, pickHomeInspirations } from "./homeInspiration.js";

test("home inspirations are unique and avoid the previous set when possible", () => {
  const previous = HOME_INSPIRATION_POOL.slice(0, 3);
  const inspirations = pickHomeInspirations(previous, 3, () => 0.5);
  assert.equal(inspirations.length, 3);
  assert.equal(new Set(inspirations).size, 3);
  assert.ok(inspirations.every((item) => !previous.includes(item)));
});
