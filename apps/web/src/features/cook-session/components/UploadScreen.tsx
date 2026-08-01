"use client";

import { useRef, type ChangeEvent, type DragEvent } from "react";
import { ArrowRight, Camera, Image as ImageIcon } from "lucide-react";

import { ScreenHeader } from "./ScreenHeader";

interface UploadScreenProps {
  onUpload: (file: File) => void;
  onManualEntry: () => void;
  onWeakDetection: () => void;
  showWeakDetection?: boolean;
}

export function UploadScreen({
  onUpload,
  onManualEntry,
  onWeakDetection,
  showWeakDetection = false,
}: UploadScreenProps) {
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);

  function submitFirstFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) onUpload(file);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (file) onUpload(file);
  }

  return (
    <section aria-labelledby="upload-title">
      <ScreenHeader
        kicker="Step 1 — Ingredients"
        title={<span id="upload-title">What&apos;s in your kitchen?</span>}
        titleClassName="screen-title-upload"
        intro="Take a photo of what you have. Cook Mantra reads it, you check the list — nothing is assumed without your say-so."
      />

      <div
        className="upload-zone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <Camera aria-hidden="true" className="upload-zone-icon" />
        <div>
          <div className="upload-zone-title">Photograph your ingredients</div>
          <p className="upload-zone-copy">
            Lay them out on a counter — one photo is enough. Your photo is used only to
            identify your ingredients for this session.
          </p>
        </div>
        <div className="upload-zone-actions">
          <button
            className="btn btn-primary btn-lg"
            type="button"
            onClick={() => cameraInput.current?.click()}
          >
            <Camera aria-hidden="true" size={18} />
            Take a photo
          </button>
          <button
            className="btn btn-secondary btn-lg"
            type="button"
            onClick={() => galleryInput.current?.click()}
          >
            <ImageIcon aria-hidden="true" size={18} />
            Choose from gallery
          </button>
        </div>
        <p className="upload-zone-note">
          JPEG, PNG, or WebP · up to 10 MB · processed for this session
        </p>
        <input
          ref={cameraInput}
          hidden
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          tabIndex={-1}
          aria-hidden="true"
          onChange={submitFirstFile}
        />
        <input
          ref={galleryInput}
          hidden
          type="file"
          accept="image/jpeg,image/png,image/webp"
          tabIndex={-1}
          aria-hidden="true"
          onChange={submitFirstFile}
        />
      </div>

      <div className="screen-secondary-actions">
        <button className="btn btn-ghost" type="button" onClick={onManualEntry}>
          Type ingredients instead
          <ArrowRight aria-hidden="true" size={16} />
        </button>
        {showWeakDetection ? (
          <button
            className="btn btn-ghost text-muted"
            type="button"
            onClick={onWeakDetection}
          >
            Simulate weak detection
          </button>
        ) : null}
      </div>
    </section>
  );
}
