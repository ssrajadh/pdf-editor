import { useCallback, useState, useEffect } from "react";
import { WifiOff } from "lucide-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { toast } from "@/hooks/use-toast";
import { usePdfSession } from "@/hooks/usePdfSession";
import { getPageImageUrl, exportPdf } from "@/services/api";
import Header from "@/components/Header";
import UploadArea from "@/components/UploadArea";
import PdfViewer from "@/components/PdfViewer";
import PageThumbnails from "@/components/PageThumbnails";
import ChatPanel from "@/components/ChatPanel";

function App() {
  const {
    session,
    currentPage,
    pageVersions,
    pageEditTypes,
    currentPageVersion,
    currentMessages,
    editProgress,
    isEditing,
    isPreviewing,
    isReconnecting,
    uploading,
    uploadError,

    uploadPdf,
    selectPage,
    sendEdit,
    previewPlan,
    executePlanEdit,
    retryLastEdit,
    getImageUrl,
    setSession,
    setUploadError,
  } = usePdfSession();

  const [exporting, setExporting] = useState(false);

  const hasEdits = Object.values(pageVersions).some((v) => v > 0);

  // Keyboard shortcuts
  useEffect(() => {
    if (!session) return;
    const handler = (e: KeyboardEvent) => {
      // Skip when focused on text inputs
      if (
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLInputElement
      )
        return;

      const mod = e.metaKey || e.ctrlKey;

      // Arrow keys: page navigation
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        selectPage(Math.max(1, currentPage - 1));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        selectPage(Math.min(session.page_count, currentPage + 1));
      }

      // Cmd/Ctrl+Z: undo (revert one step)
      if (mod && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        const v = pageVersions[currentPage];
        if (v !== undefined && v > 0) {
          // TODO: wire to revert API when history is exposed to frontend
          toast({ description: `Undo: revert page ${currentPage} to step ${v - 1}` });
        }
      }

      // Cmd/Ctrl+Shift+Z: redo (go forward one step)
      if (mod && e.key === "z" && e.shiftKey) {
        e.preventDefault();
        // TODO: wire to redo API when history is exposed to frontend
        toast({ description: `Redo not yet available` });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, currentPage, selectPage, pageVersions]);

  const handleExport = useCallback(async () => {
    if (!session || exporting) return;
    setExporting(true);
    try {
      await exportPdf(session.session_id, session.filename);
      toast({ description: "PDF exported successfully" });
    } catch (err) {
      toast({
        variant: "destructive",
        description: err instanceof Error ? err.message : "Export failed",
      });
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

  // ---- Upload screen ----
  if (!session) {
    return (
      <TooltipProvider>
        <UploadArea
          uploading={uploading}
          uploadError={uploadError}
          onUpload={handleUpload}
          onClearError={() => setUploadError(null)}
        />
        <Toaster />
      </TooltipProvider>
    );
  }

  // ---- Editor screen ----
  const imageUrl = getImageUrl(currentPage);
  const originalImageUrl = getPageImageUrl(session.session_id, currentPage, 0);

  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col bg-canvas">
        {/* Reconnecting banner */}
        {isReconnecting && (
          <div className="flex items-center justify-center gap-2 bg-amber-600 px-4 py-1 text-[11px] font-medium text-white animate-slide-down shrink-0">
            <WifiOff className="h-3 w-3" />
            Reconnecting...
          </div>
        )}

        <Header
          session={session}
          currentPage={currentPage}
          hasEdits={hasEdits}
          exporting={exporting}
          onExport={handleExport}
          onUploadNew={() => {
            setSession(null);
            setUploadError(null);
          }}
        />

        {/* Three-panel layout */}
        <div className="grid flex-1 min-h-0 grid-cols-[60px_1fr_320px]">
          {/* Thumbnails */}
          <div className="border-r bg-panel overflow-hidden">
            <PageThumbnails
              session={session}
              currentPage={currentPage}
              onSelectPage={selectPage}
              pageVersions={pageVersions}
              pageEditTypes={pageEditTypes}
            />
          </div>

          {/* Viewer */}
          <div className="min-w-0 overflow-hidden">
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
          <div className="border-l bg-panel overflow-hidden">
            <ChatPanel
              messages={currentMessages}
              currentPage={currentPage}
              isEditing={isEditing}
              isPreviewing={isPreviewing}
              onSendEdit={sendEdit}
              onPreviewPlan={previewPlan}
              onExecutePlan={executePlanEdit}
              onRetry={retryLastEdit}
            />
          </div>
        </div>
      </div>

      <Toaster />
    </TooltipProvider>
  );
}

export default App;
