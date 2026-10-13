export type TimeRange = { from: number; to: number };

/** Pad with observed bars, never artificial calendar hours across market closures. */
export function paddedRange(
  times: number[],
  start: number,
  end: number,
  padding = 10,
): TimeRange {
  if (!times.length) throw new Error("A chart needs at least one observed bar");
  let first = times.findIndex((t) => t >= start);
  let last = times.findIndex((t) => t >= end);
  if (first < 0) first = times.length - 1;
  if (last < 0) last = times.length - 1;
  return {
    from: times[Math.max(0, first - padding)],
    to: times[Math.min(times.length - 1, last + padding)],
  };
}

export function initialRange(
  times: number[],
  executionTimes: number[],
): TimeRange {
  if (executionTimes.length)
    return paddedRange(
      times,
      Math.min(...executionTimes),
      Math.max(...executionTimes),
    );
  if (!times.length) throw new Error("A chart needs at least one observed bar");
  return {
    from: times[Math.max(0, times.length - 200)],
    to: times[times.length - 1],
  };
}
