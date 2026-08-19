"use client";

import { FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { uploadOutlets } from "@/lib/api";
import { useToast } from "./ToastProvider";

const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".xls"];

interface FileUploadZoneProps {
  onUploaded?: (distributorCodes: string[]) => void;
}

function isValidFile(file: File): boolean {
  const lowerName = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
}

export default function FileUploadZone({ onUploaded }: FileUploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { showToast } = useToast();

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;

      if (!isValidFile(file)) {
        showToast(
          "error",
          "Unsupported file type. Please upload a .csv, .xlsx, or .xls file."
        );
        return;
      }

      setSelectedFile(file);
      setIsUploading(true);
      try {
        const result = await uploadOutlets(file);
        const duplicatesNote =
          result.duplicates_removed > 0
            ? ` (${result.duplicates_removed} duplicate rows skipped)`
            : "";
        showToast(
          "success",
          `Uploaded ${result.total_outlets} active outlets (${result.inserted} new, ` +
            `${result.updated} updated) for distributor(s): ${result.distributor_codes.join(", ")}.` +
            duplicatesNote
        );
        onUploaded?.(result.distributor_codes);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Upload failed.";
        showToast("error", message);
      } finally {
        setIsUploading(false);
      }
    },
    [onUploaded, showToast]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setIsDragging(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
        isDragging
          ? "border-amul-blue bg-amul-blue/5"
          : "border-slate-300 bg-slate-50 hover:border-amul-blue/50 hover:bg-amul-blue/5"
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />

      {isUploading ? (
        <>
          <Loader2 className="h-8 w-8 animate-spin text-amul-blue" />
          <p className="text-sm font-medium text-slate-600">
            Uploading and validating outlets…
          </p>
        </>
      ) : selectedFile ? (
        <>
          <FileSpreadsheet className="h-8 w-8 text-amul-blue" />
          <p className="text-sm font-medium text-slate-700">{selectedFile.name}</p>
          <p className="text-xs text-slate-400">Click or drop a new file to replace it</p>
        </>
      ) : (
        <>
          <UploadCloud className="h-8 w-8 text-slate-400" />
          <p className="text-sm font-medium text-slate-600">
            Drag & drop your retail master sheet here
          </p>
          <p className="text-xs text-slate-400">or click to browse (.csv, .xlsx, .xls)</p>
        </>
      )}
    </div>
  );
}
