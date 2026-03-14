import { useCallback, useState, useEffect, useRef } from "react";
import { FileText, FileUp, Loader2, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { toast } from "@/hooks/use-toast";
import { usePdfSession } from "@/hooks/usePdfSession";
import { getPageImageUrl, exportPdf } from "@/services/api";
import Header from "@/components/Header";
import PdfViewer from "@/components/PdfViewer";
import PageThumbnails from "@/components/PageThumbnails";
import ChatPanel from "@/components/ChatPanel";
import { cn } from "@/lib/utils";

const MAX_SIZE_MB = 50;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

function App() {
  const {
    session,
    currentPage,
    pageVersions,
    pageEditTypes,
    currentPageVersion,
    currentMessages,
    currentHistory,
    editProgress,
    isEditing,
    isPreviewing,
    isReverting,
    isRestoring,
    isReconnecting,
    uploading,
    uploadError,

    uploadPdf,
    selectPage,
    sendEdit,
    forceVisualEdit,
    previewPlan,
    executePlanEdit,
    retryLastEdit,
    revertToStep,
    getImageUrl,
    setUploadError,
  } = usePdfSession();

  const [exporting, setExporting] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const dragCounter = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
        if (v !== undefined && v > 0 && !isReverting) {
          revertToStep(currentPage, v - 1);
          toast({ description: `Undoing last edit on page ${currentPage}` });
        }
      }

      // Cmd/Ctrl+Shift+Z: redo (go forward one step)
      if (mod && e.key === "z" && e.shiftKey) {
        e.preventDefault();
        const history = currentHistory;
        if (history && !isReverting) {
          const maxStep = history.total_steps - 1;
          const v = pageVersions[currentPage] ?? 0;
          if (v < maxStep) {
            revertToStep(currentPage, v + 1);
            toast({ description: `Redoing to step ${v + 1} on page ${currentPage}` });
          }
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [session, currentPage, selectPage, pageVersions, isReverting, revertToStep, currentHistory]);

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
      if (file.size > MAX_SIZE_BYTES) {
        setUploadError(`File too large. Max ${MAX_SIZE_MB}MB`);
        return;
      }
      await uploadPdf(file);
    },
    [uploadPdf, setUploadError],
  );

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dragCounter.current = 0;
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) {
        void handleUpload(file);
      }
    },
    [handleUpload],
  );

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current += 1;
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current -= 1;
    if (dragCounter.current <= 0) {
      setIsDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const imageUrl = session ? getImageUrl(currentPage) : "";
  const originalImageUrl = session
    ? getPageImageUrl(session.session_id, currentPage, 0)
    : "";
  const hasEditWarnings = Boolean(
    currentHistory?.snapshots
      ?.find((s) => s.step === currentHistory.current_step)
      ?.operations_summary?.some((op) => !op.success || op.path === "blocked"),
  );

  return (
    <TooltipProvider>
      <div className="flex h-screen min-w-[1024px] flex-col bg-canvas">
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
          onUploadNew={openFilePicker}
        />

        {/* Three-panel layout */}
        <div className="grid flex-1 min-h-0 grid-cols-[100px_minmax(0,1fr)_380px]">
          {/* Thumbnails */}
          <div className="border-r bg-panel overflow-hidden w-[100px] shrink-0">
            {session ? (
              <PageThumbnails
                session={session}
                currentPage={currentPage}
                onSelectPage={selectPage}
                pageVersions={pageVersions}
                pageEditTypes={pageEditTypes}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
                <div className="rounded-md bg-muted/50 p-2">
                  <FileText className="h-4 w-4" />
                </div>
                <p className="text-xs">No pages</p>
              </div>
            )}
          </div>

          {/* Viewer */}
          <div
            className="relative min-w-0 overflow-hidden"
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          >
            {session ? (
              <PdfViewer
                session={session}
                currentPage={currentPage}
                imageUrl={imageUrl}
                originalImageUrl={originalImageUrl}
                pageVersion={currentPageVersion}
                isEditing={isEditing}
                editProgress={editProgress}
                hasEditWarnings={hasEditWarnings}
              />
            ) : isRestoring ? (
              <div className="flex h-full items-center justify-center p-6">
                <div className="flex flex-col items-center gap-2 text-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  <p className="text-[13px] text-muted-foreground">
                    Restoring session...
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-6">
                <div className="flex w-full max-w-3xl flex-col items-center gap-4 text-center">
                  <FileUp className="h-8 w-8 text-muted-foreground/60" />
                  <p className="text-[14px] font-medium">
                    Upload a PDF to start editing
                  </p>
                  <div
                    onClick={openFilePicker}
                    className={cn(
                      "w-full max-w-[60%] min-w-[240px] cursor-pointer rounded-lg border-2 border-dashed px-6 py-6 text-center transition-all",
                      isDragOver
                        ? "border-primary bg-primary/5"
                        : "border-border hover:border-primary/60 hover:bg-muted/40",
                    )}
                  >
                    {uploading ? (
                      <div className="flex flex-col items-center gap-2">
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                        <p className="text-xs text-muted-foreground">
                          Processing PDF...
                        </p>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-2">
                        <p className="text-[12px] text-muted-foreground">
                          Drag and drop a PDF here
                        </p>
                        <Button
                          variant="link"
                          size="sm"
                          className="h-5 px-0 text-[12px]"
                        >
                          or click to browse
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">Max {MAX_SIZE_MB}MB</p>
                  {uploadError && (
                    <Badge variant="destructive" className="mt-1">
                      {uploadError}
                    </Badge>
                  )}
                </div>
              </div>
            )}

            {isDragOver && session && (
              <div className="pointer-events-none absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-background/70 backdrop-blur-sm">
                <p className="text-sm font-medium">Drop to replace current PDF</p>
                <p className="text-xs text-muted-foreground">
                  This will start a new session
                </p>
              </div>
            )}
          </div>

          {/* Chat */}
          <div className="border-l bg-panel min-w-0 overflow-hidden w-[380px] shrink-0">
            <ChatPanel
              messages={session ? currentMessages : []}
              currentPage={session ? currentPage : 1}
              isEditing={session ? isEditing : false}
              isPreviewing={session ? isPreviewing : false}
              history={session ? currentHistory : null}
              isReverting={session ? isReverting : false}
              hasSession={!!session}
              isRestoring={isRestoring}
              onSendEdit={sendEdit}
              onForceEdit={forceVisualEdit}
              onPreviewPlan={previewPlan}
              onExecutePlan={executePlanEdit}
              onRetry={retryLastEdit}
              onRevert={(step) => revertToStep(currentPage, step)}
            />
          </div>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleUpload(file);
        }}
      />

      <Toaster />
    </TooltipProvider>
  );
}

export default App;
