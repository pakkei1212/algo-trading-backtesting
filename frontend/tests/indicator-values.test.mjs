import assert from "node:assert/strict";
import test from "node:test";
import { availableIndicators } from "../.test-build/indicator-values.mjs";

test("indicators become available at their recorded close, never earlier", () => {
  const observations = [
    { time: 100, ema_20: 10 },
    { time: 200, ema_20: 12 },
  ];
  assert.equal(availableIndicators(observations, 99), undefined);
  assert.equal(availableIndicators(observations, 100)?.ema_20, 10);
  assert.equal(availableIndicators(observations, 199)?.ema_20, 10);
  assert.equal(availableIndicators(observations, 200)?.ema_20, 12);
});

test("weekend cursors use the latest recorded values without creating observations", () => {
  const observations = [
    { time: 100, sma_20: 10 },
    { time: 1000, sma_20: 11 },
  ];
  assert.equal(availableIndicators(observations, 900)?.sma_20, 10);
  assert.equal(availableIndicators(observations, 1100)?.sma_20, 11);
  assert.equal(observations.length, 2);
  assert.equal(availableIndicators([], 100), undefined);
});
