/** The latest recorded close-known values available at a cursor time, including gaps. */
export function availableIndicators<T extends { time: number }>(
  observations: T[],
  cursorTime: number,
): T | undefined {
  let low = 0;
  let high = observations.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (observations[middle].time <= cursorTime) low = middle + 1;
    else high = middle;
  }
  return low ? observations[low - 1] : undefined;
}
