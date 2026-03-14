import { useState, useEffect } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { Session, EditProgress } from "@/types";
import BeforeAfterToggle from "./BeforeAfterToggle";

interface Props {
  session: Session;
  currentPage: number;
  imageUrl: string;
  originalImageUrl: string;
  pageVersion?: number;
  isEditing: boolean;
  editProgress: EditProgress | null;
}

export default function PdfViewer({
  session,
  currentPage,
  imageUrl,
  originalImageUrl,
  pageVersion,
  isEditing,
  editProgress,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [imgError, setImgError] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const hasEdits = pageVersion !== undefined && pageVersion > 0;
  const displayUrl = hasEdits && showOriginal ? originalImageUrl : imageUrl;

  // Reset on page change
  useEffect(() => {
    setLoading(true);
    setShowOriginal(false);
    setImgError(false);
  }, [currentPage]);

  // Reset on URL change
  useEffect(() => {
    setLoading(true);
    setImgError(false);
  }, [displayUrl, retryKey]);

  return (
    <div className="flex h-full flex-col bg-canvas">
      {/* ---- Canvas area ---- */}
      <div className="flex-1 overflow-auto flex items-start justify-center p-6">
        <div className="relative w-full max-w-2xl">
          {/* Skeleton / loading pulse */}
          {loading && !imgError && (
            <div className="absolute inset-0 z-10 flex items-center justify-center rounded-sm">
              <div className="absolute inset-0 animate-pulse rounded-sm bg-muted" />
              <Loader2 className="relative z-10 h-6 w-6 animate-spin text-muted-foreground" />
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
            <div className="flex flex-col items-center justify-center rounded-sm bg-card py-20">
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
                "w-full h-auto object-contain bg-white rounded-sm",
                "shadow-[0_2px_12px_rgba(0,0,0,0.25)]",
                "transition-opacity duration-150",
                loading ? "opacity-0" : "opacity-100",
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

      {/* ---- Bottom info bar ---- */}
      <div className="flex h-9 items-center border-t bg-panel px-4 shrink-0 select-none">
        {/* Left: page indicator */}
        <span className="text-[12px] font-medium text-muted-foreground tabular-nums">
          Page {currentPage}
          <span className="text-muted-foreground/40"> of {session.page_count}</span>
        </span>

        {/* Center: before/after toggle */}
        {hasEdits && (
          <>
            <Separator orientation="vertical" className="mx-3 h-4" />
            <BeforeAfterToggle
              showOriginal={showOriginal}
              onToggle={setShowOriginal}
              pageVersion={pageVersion}
            />
          </>
        )}

        {/* Right: reserved for zoom or empty */}
        <div className="ml-auto" />
      </div>
    </div>
  );
}
