import { useState, useCallback } from "react";
import { Upload, FileText, Loader2 } from "lucide-react";
import type { Session } from "./types";
import { uploadPdf } from "./services/api";
import PdfViewer from "./components/PdfViewer";
import PageThumbnails from "./components/PageThumbnails";

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleUpload = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Please select a PDF file");
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const result = await uploadPdf(file);
      setSession(result);
      setCurrentPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
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

  if (!session) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={`
            flex flex-col items-center gap-6 p-16 rounded-2xl border-2 border-dashed
            transition-colors cursor-pointer
            ${dragOver
              ? "border-blue-500 bg-blue-50"
              : "border-gray-300 bg-white hover:border-gray-400"
            }
          `}
          onClick={() => document.getElementById("file-input")?.click()}
        >
          {uploading ? (
            <Loader2 className="w-16 h-16 text-blue-500 animate-spin" />
          ) : (
            <Upload className="w-16 h-16 text-gray-400" />
          )}

          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-2">
              PDF Editor
            </h1>
            <p className="text-gray-500">
              {uploading
                ? "Uploading and rendering pages..."
                : "Drop a PDF here or click to browse"}
            </p>
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-4 py-2 rounded-lg">
              {error}
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

  return (
    <div className="h-screen flex flex-col">
      {/* Header */}
      <header className="flex items-center gap-3 px-4 py-2 bg-white border-b shrink-0">
        <FileText className="w-5 h-5 text-blue-600" />
        <span className="font-semibold text-gray-900 truncate">
          {session.filename}
        </span>
        <span className="text-sm text-gray-400">
          {session.page_count} {session.page_count === 1 ? "page" : "pages"}
        </span>
        <button
          onClick={() => {
            setSession(null);
            setCurrentPage(1);
            setError(null);
          }}
          className="ml-auto text-sm text-gray-500 hover:text-gray-700 px-3 py-1 rounded hover:bg-gray-100 transition-colors"
        >
          Upload new
        </button>
      </header>

      {/* Main content */}
      <div className="flex flex-1 min-h-0">
        {/* Thumbnails sidebar — 15% */}
        <div className="w-[15%] min-w-[140px] max-w-[200px]">
          <PageThumbnails
            session={session}
            currentPage={currentPage}
            onSelectPage={setCurrentPage}
          />
        </div>

        {/* PDF Viewer — center, takes remaining space */}
        <div className="flex-1 min-w-0">
          <PdfViewer session={session} currentPage={currentPage} />
        </div>

        {/* Chat panel placeholder — 35% */}
        <div className="w-[35%] min-w-[280px] max-w-[480px] bg-gray-50 border-l flex flex-col">
          <div className="p-4 border-b">
            <h2 className="font-semibold text-gray-900">Edit Chat</h2>
          </div>
          <div className="flex-1 flex items-center justify-center p-6">
            <p className="text-sm text-gray-400 text-center">
              Chat panel coming next — you'll send edit instructions here
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
