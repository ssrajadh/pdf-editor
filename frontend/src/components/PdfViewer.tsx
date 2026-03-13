import { useState, useEffect } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import type { Session, EditProgress, PageEditType } from "../types";
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
    <div className="flex-1 flex flex-col items-center bg-gray-100 overflow-auto h-full">
      <div className="flex items-center gap-4 py-3 px-4 w-full max-w-3xl">
        <span className="text-sm text-gray-500 font-medium">
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

      <div className="relative w-full max-w-3xl px-4 pb-6">
        {loading && !imgError && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 rounded-lg z-10 mx-4">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        )}

        {isEditing && editProgress && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 rounded-lg z-20 mx-4 backdrop-blur-[2px]">
            <Loader2 className="w-10 h-10 text-white animate-spin mb-3" />
            <p className="text-white text-sm font-medium">
              {editProgress.message}
            </p>
            <p className="text-white/60 text-xs mt-1 capitalize">
              {editProgress.stage}
            </p>
          </div>
        )}

        {imgError ? (
          <div className="flex flex-col items-center justify-center py-24 bg-white rounded-lg shadow-md">
            <p className="text-gray-500 text-sm mb-3">Failed to load page image</p>
            <button
              onClick={() => setRetryKey((k) => k + 1)}
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 px-3 py-1.5 rounded-lg hover:bg-blue-50 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              Retry
            </button>
          </div>
        ) : (
          <img
            key={`${displayUrl}-${retryKey}`}
            src={displayUrl}
            alt={`Page ${currentPage}`}
            className="w-full h-auto rounded-lg shadow-md bg-white transition-opacity duration-200"
            onLoad={() => setLoading(false)}
            onError={() => { setLoading(false); setImgError(true); }}
          />
        )}
      </div>
    </div>
  );
}
