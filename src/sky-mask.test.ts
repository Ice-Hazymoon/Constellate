import { existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, test } from "bun:test";
import { PYTHON_BIN_CANDIDATES, ROOT_DIR } from "./app-config";

function resolvePythonBinary() {
  for (const candidate of PYTHON_BIN_CANDIDATES) {
    if (candidate.includes(path.sep)) {
      if (existsSync(candidate)) {
        return candidate;
      }
      continue;
    }
    return candidate;
  }
  return "python3";
}

type MaskStats = {
  applied: boolean;
  areaRatio: number;
  topCoverage: number;
  bottomCoverage: number;
};

function computeMaskStats(filename: string): MaskStats {
  const script = `
import json
import sys
from pathlib import Path

from PIL import Image

root = Path.cwd()
sys.path.insert(0, str(root / "python"))

from annotate_sky_mask import compute_sky_mask

image_path = root / "samples" / ${JSON.stringify(filename)}
with Image.open(image_path) as image:
    mask = compute_sky_mask(image)

if mask is None:
    print(json.dumps({"applied": False}))
    raise SystemExit(0)

band = max(1, mask.shape[0] // 10)
print(json.dumps({
    "applied": True,
    "areaRatio": float(mask.mean()),
    "topCoverage": float(mask[:band, :].mean()),
    "bottomCoverage": float(mask[-band:, :].mean()),
}))
`.trim();

  const proc = Bun.spawnSync({
    cmd: [resolvePythonBinary(), "-c", script],
    cwd: ROOT_DIR,
    stdout: "pipe",
    stderr: "pipe",
  });

  if (proc.exitCode !== 0) {
    throw new Error(new TextDecoder().decode(proc.stderr));
  }

  const stdout = new TextDecoder().decode(proc.stdout).trim();
  return JSON.parse(stdout) as MaskStats;
}

describe("sky mask", () => {
  test("keeps a nearly full pure-sky frame", () => {
    const stats = computeMaskStats("apod4.jpg");
    expect(stats.applied).toBe(true);
    expect(stats.areaRatio).toBeGreaterThan(0.95);
    expect(stats.bottomCoverage).toBeGreaterThan(0.95);
  });

  test("cuts away the treeline in the Orion sample", () => {
    const stats = computeMaskStats("orion-over-pines.jpg");
    expect(stats.applied).toBe(true);
    expect(stats.areaRatio).toBeGreaterThan(0.30);
    expect(stats.areaRatio).toBeLessThan(0.75);
    expect(stats.bottomCoverage).toBeLessThan(0.05);
  });

  test("finds the skyline on the local input sample", () => {
    const stats = computeMaskStats("input.jpeg");
    expect(stats.applied).toBe(true);
    expect(stats.areaRatio).toBeGreaterThan(0.60);
    expect(stats.areaRatio).toBeLessThan(0.85);
    expect(stats.bottomCoverage).toBeLessThan(0.20);
  });
});
