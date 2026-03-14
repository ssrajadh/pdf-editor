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
    <div className="flex h-full flex-col items-center overflow-auto bg-muted/30">
      {/* Top bar with page info and toggle */}
      <div className="flex w-full max-w-3xl items-center gap-4 px-4 py-3">
        <span className="text-sm font-medium text-muted-foreground">
          Page {currentPage} of {session.page_count}
        </span>

        {hasEdits && (
          <BeforeAfterToggle
            showOriginal={showOriginal}
            onToggle={setShowOriginal}
            editType={editType}
          />
        )}
      </div>

      {/* Page image */}
      <div className="relative w-full max-w-3xl px-4 pb-6">
        {loading && !imgError && (
          <div className="absolute inset-0 z-10 mx-4 flex items-center justify-center rounded-lg bg-background/80">
            <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
          </div>
        )}

        {isEditing && editProgress && (
          <div className="absolute inset-0 z-20 mx-4 flex flex-col items-center justify-center rounded-lg bg-black/40 backdrop-blur-[2px]">
            <Loader2 className="mb-3 h-10 w-10 animate-spin text-white" />
            <p className="text-sm font-medium text-white">
              {editProgress.message}
            </p>
            <p className="mt-1 text-xs capitalize text-white/60">
              {editProgress.stage}
            </p>
          </div>
        )}

        {imgError ? (
          <div className="flex flex-col items-center justify-center rounded-lg bg-card py-24 shadow-sm">
            <p className="mb-3 text-sm text-muted-foreground">
              Failed to load page image
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRetryKey((k) => k + 1)}
            >
              <RefreshCw className="mr-1.5 h-4 w-4" />
              Retry
            </Button>
          </div>
        ) : (
          <img
            key={`${displayUrl}-${retryKey}`}
            src={displayUrl}
            alt={`Page ${currentPage}`}
            className={cn(
              "w-full h-auto rounded-lg shadow-sm bg-white transition-opacity duration-200",
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
  );
}
