import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import manifest from "./manifest";

const appDirectory = path.dirname(fileURLToPath(import.meta.url));
const publicDirectory = path.resolve(appDirectory, "../../public");

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const PNG_COLOUR_TYPE_RGBA = 6;

interface IcoEntry {
  readonly label: string;
  readonly payload: Buffer;
}

/** Split an .ico container into its sub-images, newest Pillow writes PNG payloads. */
function readIcoEntries(file: Buffer): IcoEntry[] {
  const count = file.readUInt16LE(4);
  return Array.from({ length: count }, (_unused, index) => {
    const directory = 6 + index * 16;
    const width = file.readUInt8(directory) || 256;
    const height = file.readUInt8(directory + 1) || 256;
    const size = file.readUInt32LE(directory + 8);
    const offset = file.readUInt32LE(directory + 12);
    return {
      label: `${width}x${height}`,
      payload: file.subarray(offset, offset + size),
    };
  });
}

function readPngDimensions(png: Buffer): { width: number; height: number } {
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
}

describe("brand assets", () => {
  it("ships favicon sub-images Turbopack can decode", () => {
    // Turbopack rejects the whole route tree with "The PNG is not in RGBA format!"
    // when an .ico embeds RGB PNGs, so the colour type has to stay at 6.
    const entries = readIcoEntries(
      readFileSync(path.join(appDirectory, "favicon.ico")),
    );

    expect(entries.map((entry) => entry.label)).toEqual(["16x16", "32x32", "48x48"]);
    for (const entry of entries) {
      if (!entry.payload.subarray(0, 8).equals(PNG_SIGNATURE)) {
        continue; // BMP sub-images carry their own alpha mask and always decode.
      }
      expect(entry.payload.readUInt8(25), `${entry.label} colour type`).toBe(
        PNG_COLOUR_TYPE_RGBA,
      );
    }
  });

  it("ships every manifest icon at the size it declares", () => {
    const icons = manifest().icons ?? [];

    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      const file = readFileSync(path.join(publicDirectory, icon.src));
      expect(file.subarray(0, 8).equals(PNG_SIGNATURE), `${icon.src} is a PNG`).toBe(
        true,
      );

      const { width, height } = readPngDimensions(file);
      expect(`${width}x${height}`, `${icon.src} dimensions`).toBe(icon.sizes);
    }
  });
});
