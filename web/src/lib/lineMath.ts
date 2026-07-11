/** Geometry for the LineEditor.
 *
 * Backend convention (`src/toskana/vision/geometry.py`): the counting line
 * is a directed segment a->b in image coordinates (origin top-left, y down).
 * side(P) = sign of (P - a) x (b - a); "out" = positive crossing = side -1
 * -> +1. The unit normal pointing to the "out" side is therefore
 * `(dy, -dx) / |d|` with `d = b - a`.
 */

export interface NormPoint {
  x: number;
  y: number;
}

export interface NormLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Normalized (0..1) -> canvas pixels. */
export function normToPx(point: NormPoint, width: number, height: number): { x: number; y: number } {
  return { x: point.x * width, y: point.y * height };
}

/** Canvas pixels -> normalized (0..1), clamped into the frame. */
export function pxToNorm(point: { x: number; y: number }, width: number, height: number): NormPoint {
  if (width <= 0 || height <= 0) return { x: 0, y: 0 };
  return { x: clamp01(point.x / width), y: clamp01(point.y / height) };
}

export type Endpoint = "a" | "b";

/** Move one endpoint to a new pixel position (result stays normalized + clamped). */
export function dragEndpoint(
  line: NormLine,
  endpoint: Endpoint,
  px: { x: number; y: number },
  width: number,
  height: number,
): NormLine {
  const p = pxToNorm(px, width, height);
  return endpoint === "a" ? { ...line, x1: p.x, y1: p.y } : { ...line, x2: p.x, y2: p.y };
}

/** Flip the counting direction by swapping the endpoints (a<->b). */
export function flipDirection(line: NormLine): NormLine {
  return { x1: line.x2, y1: line.y2, x2: line.x1, y2: line.y1 };
}

/** Squared pixel distance point<->endpoint, for hit-testing drag handles. */
export function hitEndpoint(
  line: NormLine,
  px: { x: number; y: number },
  width: number,
  height: number,
  radius = 14,
): Endpoint | null {
  const a = normToPx({ x: line.x1, y: line.y1 }, width, height);
  const b = normToPx({ x: line.x2, y: line.y2 }, width, height);
  const d2 = (p: { x: number; y: number }) => (p.x - px.x) ** 2 + (p.y - px.y) ** 2;
  const da = d2(a);
  const db = d2(b);
  const r2 = radius * radius;
  if (da <= r2 && da <= db) return "a";
  if (db <= r2) return "b";
  return null;
}

/** Arrow describing the positive ("out") crossing direction, in canvas px.
 *
 * Returns null for a degenerate (zero-length) line. `from`/`to` span an
 * arrow of `length` px centered on the line midpoint, pointing to side +1.
 */
export function outArrow(
  line: NormLine,
  width: number,
  height: number,
  length = 46,
): { from: { x: number; y: number }; to: { x: number; y: number } } | null {
  const a = normToPx({ x: line.x1, y: line.y1 }, width, height);
  const b = normToPx({ x: line.x2, y: line.y2 }, width, height);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  // Unit normal pointing to side +1 ("out") in image coordinates.
  const nx = dy / len;
  const ny = -dx / len;
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const half = length / 2;
  return {
    from: { x: mid.x - nx * half, y: mid.y - ny * half },
    to: { x: mid.x + nx * half, y: mid.y + ny * half },
  };
}
