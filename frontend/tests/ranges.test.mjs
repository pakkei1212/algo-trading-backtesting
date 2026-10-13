import test from "node:test";
import assert from "node:assert/strict";
import { paddedRange, initialRange } from "../.test-build/ranges.mjs";

test("trade focus pads observed bars over a weekend without generating bars", () => {
  const times = [10, 20, 30, 40, 500, 510, 520, 530];
  assert.deepEqual(paddedRange(times, 500, 510, 2), { from: 30, to: 530 });
});
test("focus clamps at dataset boundaries", () => {
  assert.deepEqual(paddedRange([10, 20, 30], 0, 100), { from: 10, to: 30 });
});
test("empty-trade default uses the last 200 observations", () => {
  const times = Array.from({ length: 300 }, (_, i) => i * 3600);
  assert.deepEqual(initialRange(times, []), { from: 360000, to: 1076400 });
});
test("trade activity includes context on both ends", () => {
  const times = Array.from({ length: 100 }, (_, i) => i);
  assert.deepEqual(initialRange(times, [20, 70]), { from: 10, to: 80 });
});
test("empty data rejected explicitly", () => {
  assert.throws(() => initialRange([], []), /observed bar/);
});
