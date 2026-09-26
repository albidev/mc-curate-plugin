import assert from "node:assert/strict";
import test from "node:test";

import { paginateItems } from "./ui/pagination.ts";

test("paginates candidates into stable, non-overlapping ranges", () => {
  const candidates = Array.from({ length: 27 }, (_, index) => `candidate-${index + 1}`);

  const firstPage = paginateItems(candidates, 1, 25);
  const secondPage = paginateItems(candidates, 2, 25);

  assert.deepEqual(firstPage.items, candidates.slice(0, 25));
  assert.equal(firstPage.page, 1);
  assert.equal(firstPage.pageCount, 2);
  assert.equal(firstPage.start, 1);
  assert.equal(firstPage.end, 25);
  assert.deepEqual(secondPage.items, ["candidate-26", "candidate-27"]);
  assert.equal(secondPage.start, 26);
  assert.equal(secondPage.end, 27);
});

test("clamps an out-of-range page to the last available page", () => {
  const page = paginateItems(["a", "b", "c"], 99, 2);

  assert.equal(page.page, 2);
  assert.equal(page.pageCount, 2);
  assert.deepEqual(page.items, ["c"]);
});

test("returns a stable empty page and normalizes invalid inputs", () => {
  const empty = paginateItems([], 4, 25);
  const invalid = paginateItems(["a", "b"], Number.NaN, 0);

  assert.deepEqual(empty, {
    items: [],
    page: 1,
    pageCount: 1,
    start: 0,
    end: 0,
    total: 0,
  });
  assert.equal(invalid.page, 1);
  assert.equal(invalid.pageCount, 2);
  assert.deepEqual(invalid.items, ["a"]);
});
