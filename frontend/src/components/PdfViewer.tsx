import { useState, useEffect } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Session, EditProgress, PageEditType } from "@/types";
import BeforeAfterToggle from "./BeforeAfterToggle";

interface Props {
  session: Session;
  currentPage: number;
  imageUrl: string;
  originalImageUrl: string;
  pageVersion?: number;
  isEditing: boolean;
  editProgress: EditProgress | null;
  editType?: PageEditType;
}

export default function PdfViewer({
  session,
  currentPage,
  imageUrl,
  originalImageUrl,
  pageVersion,
  isEditing,
  editProgress,
  editType,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [imgError, setImgError] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const hasEdits = pageVersion !== undefined && pageVersion > 0;
  const displayUrl = hasEdits && showOriginal ? originalImageUrl : imageUrl;

  useEffect(() => {
    setLoading(true);
    setShowOriginal(false);
    setImgError(false);
  }, [currentPage]);

  useEffect(() => {
    setLoading(true);
    setImgError(false);
  }, [displayUrl, retryKey]);

  return (
    <div className="flex h-full flex-col bg-canvas">
      {/* Toolbar strip */}
      <div className="flex h-9 items-center gap-3 border-b bg-panel-header px-4 shrink-0">
        <span className="text-[12px] font-medium text-muted-foreground select-none">
          Page {currentPage}
          <span className="text-muted-foreground/50"> / {session.page_count}</span>
        </span>

        {hasEdits && (
          <>
            <div className="h-3.5 w-px bg-border" />
            <BeforeAfterToggle
              showOriginal={showOriginal}
              onToggle={setShowOriginal}
              editType={editType}
            />
          </>
        )}
      </div>

      {/* Canvas — dark background so the PDF page "floats" */}
      <div className="flex-1 overflow-auto flex items-start justify-center p-6">
        <div className="relative w-full max-w-2xl">
          {/* Loading overlay */}
          {loading && !imgError && (
            <div className="absolute inset-0 z-10 flex items-center justify-center rounded bg-card/80">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Edit progress overlay */}
          {isEditing && editProgress && (
            <div className="absolute inset-0 z-20 flex flex-col items-center justify-center rounded-sm bg-black/50 backdrop-blur-sm">
              <Loader2 className="mb-2 h-8 w-8 animate-spin text-white" />
              <p className="text-[13px] font-medium text-white">
                {editProgress.message}
              </p>
              <p className="mt-0.5 text-[11px] capitalize text-white/50 font-mono">
                {editProgress.stage}
              </p>
            </div>
          )}

          {imgError ? (
            <div className="flex flex-col items-center justify-center rounded bg-card py-20">
              <p className="mb-2 text-sm text-muted-foreground">
                Failed to load page
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setRetryKey((k) => k + 1)}
                className="h-7 gap-1 text-[12px]"
              >
                <RefreshCw className="h-3 w-3" />
                Retry
              </Button>
            </div>
          ) : (
            <img
              key={`${displayUrl}-${retryKey}`}
              src={displayUrl}
              alt={`Page ${currentPage}`}
              className={cn(
                "w-full h-auto bg-white rounded-sm transition-opacity duration-150",
                "shadow-[0_2px_12px_rgba(0,0,0,0.25)]",
                loading && "opacity-0",
              )}
              onLoad={() => setLoading(false)}
              onError={() => {
                setLoading(false);
                setImgError(true);
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
