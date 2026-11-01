export interface Cell {
  row: number;
  col: number;
}

export interface GridSize {
  rows: number;
  cols: number;
}

const clamp = (n: number, max: number) => Math.max(0, Math.min(max, n));

/**
 * Next focused cell for an arrow key, clamped at the edges, or null for other keys.
 * Arrow keys stand in for the Siri Remote, so this is the start of the spatial-nav layer.
 */
export function moveFocus(from: Cell, key: string, size: GridSize): Cell | null {
  const step: Record<string, [number, number]> = {
    ArrowUp: [-1, 0],
    ArrowDown: [1, 0],
    ArrowLeft: [0, -1],
    ArrowRight: [0, 1],
  };
  const delta = step[key];
  if (!delta) return null;
  return {
    row: clamp(from.row + delta[0], size.rows - 1),
    col: clamp(from.col + delta[1], size.cols - 1),
  };
}
