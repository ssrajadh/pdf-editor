import { useCallback, useRef, useState } from "react";
import { FileText, FileUp, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const MAX_SIZE_MB = 50;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

interface UploadAreaProps {
  uploading: boolean;
  uploadError: string | null;
  onUpload: (file: File) => void;
  onClearError: () => void;
}

export default function UploadArea({
  uploading,
  uploadError,
  onUpload,
  onClearError,
}: UploadAreaProps) {
  const [dragging, setDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        onClearError();
        return;
      }
      if (file.size > MAX_SIZE_BYTES) {
        onClearError();
        return;
      }
      setSelectedFile(file.name);
      onUpload(file);
    },
    [onUpload, onClearError],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  return (
    <div className="flex h-screen flex-col items-center justify-center bg-canvas">
      {/* Branding */}
      <div className="mb-10 flex items-center gap-2.5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600">
          <FileText className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Nano PDF Studio</h1>
          <p className="text-[12px] text-muted-foreground -mt-0.5">AI-powered PDF editing</p>
        </div>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={(e) => { e.preventDefault(); setDragging(false); }}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "group relative w-full max-w-md cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-all",
          dragging
            ? "border-blue-500 bg-blue-500/5 scale-[1.01]"
            : "border-border hover:border-blue-500/50 hover:bg-muted/50",
        )}
      >
        {uploading ? (
          <div className="flex flex-col items-center gap-4">
            <div className="relative">
              <Loader2 className="h-12 w-12 text-blue-500 animate-spin" />
            </div>
            <div>
              <p className="text-sm font-medium">Processing PDF...</p>
              {selectedFile && (
                <p className="mt-1 text-xs text-muted-foreground font-mono truncate max-w-[280px]">
                  {selectedFile}
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4">
            <FileUp
              className={cn(
                "h-10 w-10 transition-all",
                dragging
                  ? "text-blue-500 -translate-y-1"
                  : "text-muted-foreground/40 group-hover:text-muted-foreground/60",
              )}
            />
            <div>
              <p className="text-sm font-medium">
                Drop a PDF here or click to browse
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Max {MAX_SIZE_MB}MB &middot; Files stay on this server
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {uploadError && (
        <Badge variant="destructive" className="mt-4 animate-fade-in">
          {uploadError}
        </Badge>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
      />

      <Dialog
        open={!!uploadError && uploadError.length > 60}
        onOpenChange={() => onClearError()}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload Error</DialogTitle>
            <DialogDescription>{uploadError}</DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    </div>
  );
}
