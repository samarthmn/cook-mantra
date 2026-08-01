const THUMBNAIL_MAX_EDGE = 384;
const JPEG_QUALITY = 0.72;
const CAPTURE_DEADLINE_MS = 8_000;

type DecodedImage = {
  height: number;
  release: () => void;
  source: CanvasImageSource;
  width: number;
};

function thumbnailDimensions(width: number, height: number) {
  const scale = Math.min(1, THUMBNAIL_MAX_EDGE / Math.max(width, height));

  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function abortError(signal: AbortSignal) {
  return signal.reason ?? new DOMException("Photo capture aborted", "AbortError");
}

function throwIfAborted(signal: AbortSignal) {
  if (signal.aborted) {
    throw abortError(signal);
  }
}

function waitWithSignal<T>(promise: Promise<T>, signal: AbortSignal) {
  if (signal.aborted) {
    return Promise.reject<T>(abortError(signal));
  }

  return new Promise<T>((resolve, reject) => {
    const handleAbort = () => {
      signal.removeEventListener("abort", handleAbort);
      reject(abortError(signal));
    };

    signal.addEventListener("abort", handleAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", handleAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", handleAbort);
        reject(error);
      },
    );
  });
}

function closeBitmap(bitmap: ImageBitmap) {
  try {
    bitmap.close?.();
  } catch {
    // Cleanup failures must not make photo capture fail noisily.
  }
}

async function decodeWithBitmap(
  blob: Blob,
  signal: AbortSignal,
): Promise<DecodedImage> {
  throwIfAborted(signal);

  const bitmapPromise = createImageBitmap(blob).then((bitmap) => {
    if (signal.aborted) {
      closeBitmap(bitmap);
      throw abortError(signal);
    }

    return bitmap;
  });
  const bitmap = await waitWithSignal(bitmapPromise, signal);

  if (signal.aborted) {
    closeBitmap(bitmap);
    throw abortError(signal);
  }

  let released = false;

  return {
    source: bitmap,
    width: bitmap.width,
    height: bitmap.height,
    release: () => {
      if (!released) {
        released = true;
        closeBitmap(bitmap);
      }
    },
  };
}

async function decodeWithImage(blob: Blob, signal: AbortSignal): Promise<DecodedImage> {
  throwIfAborted(signal);

  if (
    typeof Image === "undefined" ||
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function" ||
    typeof URL.revokeObjectURL !== "function"
  ) {
    throw new Error("No browser image decoder is available");
  }

  const objectUrl = URL.createObjectURL(blob);
  let image: HTMLImageElement | null = null;
  let released = false;

  const release = () => {
    if (released) {
      return;
    }

    released = true;
    if (image) {
      try {
        image.onload = null;
        image.onerror = null;
        if (typeof image.removeAttribute === "function") {
          image.removeAttribute("src");
        } else {
          image.src = "";
        }
      } catch {
        // Keep cleanup best-effort so capture remains fail-safe.
      }
    }
    try {
      URL.revokeObjectURL(objectUrl);
    } catch {
      // Revocation failures must not make photo capture reject.
    }
  };

  try {
    image = new Image();
    await new Promise<void>((resolve, reject) => {
      const handleAbort = () => {
        cleanup();
        reject(abortError(signal));
      };
      const handleError = () => {
        cleanup();
        reject(new Error("Image decoding failed"));
      };
      const handleLoad = () => {
        cleanup();
        resolve();
      };
      const cleanup = () => {
        signal.removeEventListener("abort", handleAbort);
        if (image) {
          image.onload = null;
          image.onerror = null;
        }
      };

      signal.addEventListener("abort", handleAbort, { once: true });
      image!.onerror = handleError;
      image!.onload = handleLoad;
      image!.src = objectUrl;

      if (signal.aborted) {
        handleAbort();
      }
    });
    throwIfAborted(signal);

    return {
      source: image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      release,
    };
  } catch (error) {
    release();
    throw error;
  }
}

async function decodeImage(blob: Blob, signal: AbortSignal) {
  if (typeof createImageBitmap === "function") {
    try {
      return await decodeWithBitmap(blob, signal);
    } catch {
      throwIfAborted(signal);

      // Some browsers expose createImageBitmap but cannot decode every format.
      // Let the regular image decoder try before giving up.
    }
  }

  return decodeWithImage(blob, signal);
}

function captureSignal(externalSignal?: AbortSignal) {
  const controller = new AbortController();
  const handleExternalAbort = () => {
    controller.abort(externalSignal?.reason);
  };

  if (externalSignal?.aborted) {
    handleExternalAbort();
  } else {
    externalSignal?.addEventListener("abort", handleExternalAbort, {
      once: true,
    });
  }

  const deadline = setTimeout(() => {
    controller.abort(new DOMException("Photo capture timed out", "TimeoutError"));
  }, CAPTURE_DEADLINE_MS);

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(deadline);
      externalSignal?.removeEventListener("abort", handleExternalAbort);
    },
  };
}

export async function captureDishPhotoThumbnail(
  imageUrl: string,
  externalSignal?: AbortSignal,
): Promise<string | null> {
  const capture = captureSignal(externalSignal);
  let decodedImage: DecodedImage | null = null;

  try {
    throwIfAborted(capture.signal);
    const response = await fetch(imageUrl, {
      mode: "cors",
      signal: capture.signal,
    });
    if (!response.ok) {
      return null;
    }

    const blob = await waitWithSignal(response.blob(), capture.signal);
    decodedImage = await decodeImage(blob, capture.signal);
    if (
      !Number.isFinite(decodedImage.width) ||
      !Number.isFinite(decodedImage.height) ||
      decodedImage.width <= 0 ||
      decodedImage.height <= 0
    ) {
      return null;
    }

    const dimensions = thumbnailDimensions(decodedImage.width, decodedImage.height);
    const canvas = document.createElement("canvas");
    canvas.width = dimensions.width;
    canvas.height = dimensions.height;

    const context = canvas.getContext("2d");
    if (!context) {
      return null;
    }

    context.drawImage(decodedImage.source, 0, 0, dimensions.width, dimensions.height);

    return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  } catch {
    return null;
  } finally {
    try {
      decodedImage?.release();
    } catch {
      // Decoder cleanup must never escape this fail-safe boundary.
    }
    capture.cleanup();
  }
}
