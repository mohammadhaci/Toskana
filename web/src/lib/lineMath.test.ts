import { describe, expect, it } from "vitest";

import {
  clamp01,
  dragEndpoint,
  flipDirection,
  hitEndpoint,
  normToPx,
  outArrow,
  pxToNorm,
  type NormLine,
} from "./lineMath";

const W = 640;
const H = 360;

describe("px <-> normalized conversion", () => {
  it("round-trips a point", () => {
    const norm = { x: 0.25, y: 0.75 };
    const px = normToPx(norm, W, H);
    expect(px).toEqual({ x: 160, y: 270 });
    expect(pxToNorm(px, W, H)).toEqual(norm);
  });

  it("clamps out-of-frame pixels into 0..1", () => {
    expect(pxToNorm({ x: -50, y: 400 }, W, H)).toEqual({ x: 0, y: 1 });
    expect(pxToNorm({ x: 9999, y: -1 }, W, H)).toEqual({ x: 1, y: 0 });
  });

  it("clamp01 handles NaN and bounds", () => {
    expect(clamp01(Number.NaN)).toBe(0);
    expect(clamp01(-0.1)).toBe(0);
    expect(clamp01(1.5)).toBe(1);
    expect(clamp01(0.42)).toBe(0.42);
  });

  it("degenerate canvas size yields origin instead of NaN", () => {
    expect(pxToNorm({ x: 10, y: 10 }, 0, 0)).toEqual({ x: 0, y: 0 });
  });
});

describe("endpoint drag", () => {
  const line: NormLine = { x1: 0.5, y1: 0.1, x2: 0.5, y2: 0.9 };

  it("updates only the dragged endpoint", () => {
    const moved = dragEndpoint(line, "a", { x: 64, y: 36 }, W, H);
    expect(moved.x1).toBeCloseTo(0.1);
    expect(moved.y1).toBeCloseTo(0.1);
    expect(moved.x2).toBe(line.x2);
    expect(moved.y2).toBe(line.y2);

    const movedB = dragEndpoint(line, "b", { x: 320, y: 180 }, W, H);
    expect(movedB.x1).toBe(line.x1);
    expect(movedB.x2).toBeCloseTo(0.5);
    expect(movedB.y2).toBeCloseTo(0.5);
  });

  it("clamps dragged endpoints to 0..1", () => {
    const moved = dragEndpoint(line, "b", { x: -100, y: 99999 }, W, H);
    expect(moved.x2).toBe(0);
    expect(moved.y2).toBe(1);
  });
});

describe("direction flip", () => {
  it("swaps the endpoints", () => {
    const line: NormLine = { x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 };
    expect(flipDirection(line)).toEqual({ x1: 0.8, y1: 0.9, x2: 0.1, y2: 0.2 });
    expect(flipDirection(flipDirection(line))).toEqual(line);
  });

  it("flips the out-arrow to the opposite side", () => {
    const line: NormLine = { x1: 0.5, y1: 0.1, x2: 0.5, y2: 0.9 }; // vertical, top->bottom
    const arrow = outArrow(line, W, H)!;
    const flipped = outArrow(flipDirection(line), W, H)!;
    // vertical top->bottom: "out" (+1 side) is image-right (matches backend geometry.py)
    expect(arrow.to.x).toBeGreaterThan(arrow.from.x);
    expect(flipped.to.x).toBeLessThan(flipped.from.x);
    // both arrows share the line midpoint
    expect((arrow.from.x + arrow.to.x) / 2).toBeCloseTo(320);
    expect((flipped.from.x + flipped.to.x) / 2).toBeCloseTo(320);
  });

  it("returns null for a degenerate line", () => {
    expect(outArrow({ x1: 0.5, y1: 0.5, x2: 0.5, y2: 0.5 }, W, H)).toBeNull();
  });
});

describe("hit testing", () => {
  const line: NormLine = { x1: 0.1, y1: 0.1, x2: 0.9, y2: 0.9 };

  it("hits the nearest endpoint within the radius", () => {
    expect(hitEndpoint(line, { x: 64, y: 36 }, W, H)).toBe("a");
    expect(hitEndpoint(line, { x: 576, y: 324 }, W, H)).toBe("b");
  });

  it("misses when outside the radius", () => {
    expect(hitEndpoint(line, { x: 320, y: 180 }, W, H)).toBeNull();
  });
});
