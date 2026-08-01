import { afterEach, describe, expect, it, vi } from "vitest";

import { captureDishPhotoThumbnail } from "./dish-photo";

const imageUrl = "http://127.0.0.1:8000/artifacts/dish-photo";
const encodedThumbnail = "data:image/jpeg;base64,dGh1bWJuYWls";

type PipelineOptions = {
  width?: number;
  height?: number;
  context?: CanvasRenderingContext2D | null;
  encode?: () => string;
};

type FakeImageOptions = {
  height?: number;
  load?: boolean | null;
  width?: number;
};

function installImageFallback({
  height = 512,
  load = true,
  width = 768,
}: FakeImageOptions = {}) {
  const createObjectURL = vi.fn().mockReturnValue("blob:dish-photo");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });

  class FakeImage {
    naturalHeight = height;
    naturalWidth = width;
    onerror: OnErrorEventHandler | null = null;
    onload: ((this: GlobalEventHandlers, ev: Event) => unknown) | null = null;

    set src(value: string) {
      if (!value) {
        return;
      }

      if (load === null) {
        return;
      }

      queueMicrotask(() => {
        if (load) {
          this.onload?.call(this as unknown as GlobalEventHandlers, new Event("load"));
        } else {
          this.onerror?.call(
            this as unknown as GlobalEventHandlers,
            new Event("error"),
            "decode failed",
            0,
            0,
            new Error("decode failed"),
          );
        }
      });
    }
  }

  vi.stubGlobal("Image", FakeImage);

  return { createObjectURL, revokeObjectURL };
}

function installSuccessfulPipeline({
  width = 768,
  height = 512,
  context,
  encode = () => encodedThumbnail,
}: PipelineOptions = {}) {
  const blob = new Blob(["image bytes"], { type: "image/png" });
  const responseBlob = vi.fn().mockResolvedValue(blob);
  const fetchImage = vi.fn().mockResolvedValue({
    ok: true,
    blob: responseBlob,
  });
  vi.stubGlobal("fetch", fetchImage);

  const close = vi.fn();
  const bitmap = { width, height, close } as unknown as ImageBitmap;
  const decodeImage = vi.fn().mockResolvedValue(bitmap);
  vi.stubGlobal("createImageBitmap", decodeImage);

  const drawImage = vi.fn();
  const drawingContext =
    context === undefined
      ? ({ drawImage } as unknown as CanvasRenderingContext2D)
      : context;
  const toDataURL = vi.fn(encode);
  const canvas = {
    width: 0,
    height: 0,
    getContext: vi.fn().mockReturnValue(drawingContext),
    toDataURL,
  } as unknown as HTMLCanvasElement;
  vi.spyOn(document, "createElement").mockReturnValue(canvas);

  return {
    bitmap,
    blob,
    canvas,
    close,
    decodeImage,
    drawImage,
    fetchImage,
    responseBlob,
    toDataURL,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("captureDishPhotoThumbnail", () => {
  it("downscales the longest edge and returns the JPEG encoder output", async () => {
    const pipeline = installSuccessfulPipeline();

    const result = await captureDishPhotoThumbnail(imageUrl);

    expect(result).toBe(encodedThumbnail);
    expect(pipeline.canvas.width).toBe(384);
    expect(pipeline.canvas.height).toBe(256);
    expect(pipeline.close).toHaveBeenCalledOnce();
  });

  it("does not upscale an image that already fits the thumbnail bounds", async () => {
    const pipeline = installSuccessfulPipeline({ width: 120, height: 180 });

    await captureDishPhotoThumbnail(imageUrl);

    expect(pipeline.canvas.width).toBe(120);
    expect(pipeline.canvas.height).toBe(180);
    expect(pipeline.drawImage).toHaveBeenCalledWith(pipeline.bitmap, 0, 0, 120, 180);
  });

  it("returns null when the image request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();
  });

  it("returns null when the image response is unsuccessful", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();
  });

  it("returns null when image decoding fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: vi.fn().mockResolvedValue(new Blob(["bad image"])),
      }),
    );
    vi.stubGlobal(
      "createImageBitmap",
      vi.fn().mockRejectedValue(new Error("decode failed")),
    );
    const objectUrl = installImageFallback({ load: false });

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();
    expect(objectUrl.revokeObjectURL).toHaveBeenCalledWith("blob:dish-photo");
  });

  it("falls back to an object URL decoder and always revokes the URL", async () => {
    const pipeline = installSuccessfulPipeline();
    vi.stubGlobal("createImageBitmap", undefined);
    const objectUrl = installImageFallback();

    const result = await captureDishPhotoThumbnail(imageUrl);

    expect(result).toBe(encodedThumbnail);
    expect(objectUrl.createObjectURL).toHaveBeenCalledWith(pipeline.blob);
    expect(objectUrl.revokeObjectURL).toHaveBeenCalledWith("blob:dish-photo");
  });

  it("revokes the object URL when fallback decoding fails", async () => {
    installSuccessfulPipeline();
    vi.stubGlobal("createImageBitmap", undefined);
    const objectUrl = installImageFallback({ load: false });

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();

    expect(objectUrl.revokeObjectURL).toHaveBeenCalledWith("blob:dish-photo");
  });

  it("returns null when a canvas context is unavailable", async () => {
    const pipeline = installSuccessfulPipeline({
      context: null,
    });

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();
    expect(pipeline.close).toHaveBeenCalledOnce();
  });

  it("returns null when JPEG encoding fails", async () => {
    const pipeline = installSuccessfulPipeline({
      encode: () => {
        throw new Error("canvas is tainted");
      },
    });

    await expect(captureDishPhotoThumbnail(imageUrl)).resolves.toBeNull();
    expect(pipeline.close).toHaveBeenCalledOnce();
  });

  it("returns null without starting work when the external signal is already aborted", async () => {
    const fetchImage = vi.fn();
    vi.stubGlobal("fetch", fetchImage);
    const controller = new AbortController();
    controller.abort();

    await expect(
      captureDishPhotoThumbnail(imageUrl, controller.signal),
    ).resolves.toBeNull();

    expect(fetchImage).not.toHaveBeenCalled();
  });

  it("aborts a pending request after the capture deadline", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) => {
        requestSignal = init?.signal ?? undefined;

        return new Promise<Response>((_resolve, reject) => {
          requestSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        });
      }),
    );

    const pendingCapture = captureDishPhotoThumbnail(imageUrl);
    await vi.advanceTimersByTimeAsync(8_000);

    expect(requestSignal?.aborted).toBe(true);
    await expect(pendingCapture).resolves.toBeNull();
    vi.useRealTimers();
  });

  it("closes a bitmap that resolves after an external abort", async () => {
    const pipeline = installSuccessfulPipeline();
    let resolveBitmap: ((bitmap: ImageBitmap) => void) | undefined;
    pipeline.decodeImage.mockImplementation(
      () =>
        new Promise<ImageBitmap>((resolve) => {
          resolveBitmap = resolve;
        }),
    );
    const controller = new AbortController();

    const pendingCapture = captureDishPhotoThumbnail(imageUrl, controller.signal);
    await vi.waitFor(() => expect(resolveBitmap).toBeTypeOf("function"));
    controller.abort();
    resolveBitmap?.(pipeline.bitmap);

    await expect(pendingCapture).resolves.toBeNull();
    await vi.waitFor(() => expect(pipeline.close).toHaveBeenCalledOnce());
  });

  it("revokes a fallback object URL after an external abort", async () => {
    installSuccessfulPipeline();
    vi.stubGlobal("createImageBitmap", undefined);
    const objectUrl = installImageFallback({ load: null });
    const controller = new AbortController();

    const pendingCapture = captureDishPhotoThumbnail(imageUrl, controller.signal);
    await vi.waitFor(() => expect(objectUrl.createObjectURL).toHaveBeenCalledOnce());
    controller.abort();

    await expect(pendingCapture).resolves.toBeNull();
    expect(objectUrl.revokeObjectURL).toHaveBeenCalledWith("blob:dish-photo");
  });
});
