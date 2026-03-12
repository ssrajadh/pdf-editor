import { useCallback, useState, useEffect } from "react";
import { Upload, FileText, Loader2, Download, WifiOff, Pencil, Clock } from "lucide-react";
import { usePdfSession } from "./hooks/usePdfSession";
import { getPageImageUrl, exportPdf } from "./services/api";
import PdfViewer from "./components/PdfViewer";
import PageThumbnails from "./components/PageThumbnails";
import ChatPanel from "./components/ChatPanel";
import Toast from "./components/Toast";

function App() {
  const {
    session,
    currentPage,
    pageVersions,
    currentPageVersion,
    currentMessages,
    editProgress,
    isEditing,
    isReconnecting,
    uploading,
    uploadError,
    editCount,
    sessionDuration,

    uploadPdf,
    selectPage,
    sendEdit,
    retryLastEdit,
    getImageUrl,
    setSession,
    setUploadError,
  } = usePdfSession();

  const [exporting, setExporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [, setTick] = useState(0);

  // Re-render every minute to update session duration display
  useEffect(() => {
    if (!session) return;
    const id = setInterval(() => setTick((t) => t + 1), 60000);
    return () => clearInterval(id);
  }, [session]);

  const hasEdits = Object.values(pageVersions).some((v) => v > 0);

  // Keyboard shortcuts
  useEffect(() => {
    if (!session) return;

    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        selectPage(Math.max(1, currentPage - 1));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        selectPage(Math.min(session.page_count, currentPage + 1));
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, currentPage, selectPage]);

  const handleExport = useCallback(async () => {
    if (!session || exporting) return;
    setExporting(true);
    try {
      await exportPdf(session.session_id, session.filename);
      setToast("PDF exported successfully");
    } catch (err) {
      setToast(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }, [session, exporting]);

  const handleUpload = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setUploadError("Please select a PDF file");
        return;
      }
      await uploadPdf(file);
    },
    [uploadPdf, setUploadError],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (file) handleUpload(file);
    },
    [handleUpload],
  );

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleUpload(file);
    },
    [handleUpload],
  );

  // ---- Upload screen ----
  if (!session) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50 min-w-[1024px]">
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="flex flex-col items-center gap-6 p-16 rounded-2xl border-2 border-dashed
                     border-gray-300 bg-white hover:border-gray-400 transition-colors cursor-pointer"
          onClick={() => document.getElementById("file-input")?.click()}
        >
          {uploading ? (
            <Loader2 className="w-16 h-16 text-blue-500 animate-spin" />
          ) : (
            <Upload className="w-16 h-16 text-gray-400" />
          )}

          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-1">
              Nano PDF Studio
            </h1>
            <p className="text-gray-500 text-sm">
              {uploading
                ? "Uploading and rendering pages..."
                : "Drop a PDF here or click to browse"}
            </p>
          </div>

          {uploadError && (
            <p className="text-sm text-red-600 bg-red-50 px-4 py-2 rounded-lg animate-fade-in">
              {uploadError}
            </p>
          )}

          <input
            id="file-input"
            type="file"
            accept=".pdf"
            className="hidden"
            onChange={handleFileInput}
          />
        </div>
      </div>
    );
  }

  // ---- Editor screen ----
  const imageUrl = getImageUrl(currentPage);
  const originalImageUrl = getPageImageUrl(session.session_id, currentPage, 0);

  return (
    <div className="h-screen flex flex-col bg-gray-100 min-w-[1024px]">
      {/* Reconnecting banner */}
      {isReconnecting && (
        <div className="bg-amber-500 text-white text-xs text-center py-1.5 px-4 flex items-center justify-center gap-2 animate-slide-down shrink-0">
          <WifiOff className="w-3.5 h-3.5" />
          Connection lost. Reconnecting...
        </div>
      )}

      {/* Header */}
      <header className="flex items-center gap-3 px-4 py-2 bg-white border-b shrink-0">
        <FileText className="w-5 h-5 text-blue-600" />
        <span className="text-sm font-bold text-blue-600 tracking-tight">
          Nano PDF Studio
        </span>
        <span className="text-gray-300">|</span>
        <span className="font-medium text-gray-900 truncate text-sm">
          {session.filename}
        </span>

        <div className="flex items-center gap-3 text-xs text-gray-400">
          <span>{session.page_count} {session.page_count === 1 ? "page" : "pages"}</span>
          {editCount > 0 && (
            <span className="flex items-center gap-1">
              <Pencil className="w-3 h-3" />
              {editCount} {editCount === 1 ? "edit" : "edits"}
            </span>
          )}
          {sessionDuration > 0 && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {sessionDuration}m
            </span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          {hasEdits && (
            <button
              onClick={handleExport}
              disabled={exporting}
              className="flex items-center gap-1.5 text-xs font-medium text-white
                         bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400
                         px-3 py-1.5 rounded-lg transition-colors"
            >
              {exporting ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Download className="w-3.5 h-3.5" />
              )}
              {exporting ? "Exporting..." : "Download PDF"}
            </button>
          )}
          <button
            onClick={() => {
              setSession(null);
              setUploadError(null);
            }}
            className="text-xs text-gray-500 hover:text-gray-700 px-3 py-1.5 rounded
                       hover:bg-gray-100 transition-colors"
          >
            Upload new
          </button>
        </div>
      </header>

      {/* Main layout */}
      <div className="flex flex-1 min-h-0">
        <div className="w-[15%] min-w-[140px] max-w-[200px] shrink-0">
          <PageThumbnails
            session={session}
            currentPage={currentPage}
            onSelectPage={selectPage}
            pageVersions={pageVersions}
          />
        </div>

        <div className="flex-1 min-w-0">
          <PdfViewer
            session={session}
            currentPage={currentPage}
            imageUrl={imageUrl}
            originalImageUrl={originalImageUrl}
            pageVersion={currentPageVersion}
            isEditing={isEditing}
            editProgress={editProgress}
          />
        </div>

        <div className="w-[35%] min-w-[280px] max-w-[480px] bg-gray-50 border-l shrink-0">
          <ChatPanel
            messages={currentMessages}
            currentPage={currentPage}
            isEditing={isEditing}
            onSendEdit={sendEdit}
            onRetry={retryLastEdit}
          />
        </div>
      </div>

      {/* Toast */}
      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}

export default App;
