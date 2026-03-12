import { useCallback, useState } from "react";
import { Upload, FileText, Loader2, Download } from "lucide-react";
import { usePdfSession } from "./hooks/usePdfSession";
import { getPageImageUrl, exportPdf } from "./services/api";
import PdfViewer from "./components/PdfViewer";
import PageThumbnails from "./components/PageThumbnails";
import ChatPanel from "./components/ChatPanel";

function App() {
  const {
    session,
    currentPage,
    pageVersions,
    currentPageVersion,
    currentMessages,
    editProgress,
    isEditing,
    uploading,
    uploadError,

    uploadPdf,
    selectPage,
    sendEdit,
    getImageUrl,
    setSession,
    setUploadError,
  } = usePdfSession();

  const [exporting, setExporting] = useState(false);

  const hasEdits = Object.values(pageVersions).some((v) => v > 0);

  const handleExport = useCallback(async () => {
    if (!session || exporting) return;
    setExporting(true);
    try {
      await exportPdf(session.session_id, session.filename);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Export failed");
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
      <div className="h-screen flex items-center justify-center bg-gray-50">
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
            <p className="text-sm text-red-600 bg-red-50 px-4 py-2 rounded-lg">
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
    <div className="h-screen flex flex-col bg-gray-100">
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
        <span className="text-xs text-gray-400">
          {session.page_count} {session.page_count === 1 ? "page" : "pages"}
        </span>
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
        {/* Thumbnails */}
        <div className="w-[15%] min-w-[140px] max-w-[200px] shrink-0">
          <PageThumbnails
            session={session}
            currentPage={currentPage}
            onSelectPage={selectPage}
            pageVersions={pageVersions}
          />
        </div>

        {/* Viewer */}
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

        {/* Chat */}
        <div className="w-[35%] min-w-[280px] max-w-[480px] bg-gray-50 border-l shrink-0">
          <ChatPanel
            messages={currentMessages}
            currentPage={currentPage}
            isEditing={isEditing}
            onSendEdit={sendEdit}
          />
        </div>
      </div>
    </div>
  );
}

export default App;
