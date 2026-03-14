import { useCallback, useRef, useState } from "react";
import { FileUp, Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

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

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  return (
    <div className="flex h-screen items-center justify-center p-8">
      <Card
        className={`w-full max-w-lg cursor-pointer border-2 border-dashed transition-colors ${
          dragging
            ? "border-blue-500 bg-blue-500/5"
            : "border-border hover:border-muted-foreground/50"
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
      >
        <CardContent className="flex flex-col items-center gap-6 py-16">
          {uploading ? (
            <>
              <Loader2 className="h-16 w-16 text-blue-500 animate-spin" />
              <div className="text-center">
                <p className="text-lg font-semibold">Uploading...</p>
                {selectedFile && (
                  <p className="text-sm text-muted-foreground mt-1 font-mono">
                    {selectedFile}
                  </p>
                )}
              </div>
            </>
          ) : (
            <>
              <FileUp
                className={`h-16 w-16 transition-colors ${
                  dragging ? "text-blue-500" : "text-muted-foreground/50"
                }`}
              />
              <div className="text-center">
                <h1 className="text-2xl font-semibold">
                  Drop a PDF here or click to browse
                </h1>
                <p className="text-sm text-muted-foreground mt-2">
                  Max {MAX_SIZE_MB}MB &middot; Your files stay on this server
                </p>
              </div>
            </>
          )}

          {uploadError && (
            <Badge variant="destructive" className="animate-fade-in text-sm px-3 py-1">
              {uploadError}
            </Badge>
          )}

          <input
            ref={inputRef}
            type="file"
            accept=".pdf"
            className="hidden"
            onChange={handleInputChange}
          />
        </CardContent>
      </Card>

      {/* Error details dialog */}
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
