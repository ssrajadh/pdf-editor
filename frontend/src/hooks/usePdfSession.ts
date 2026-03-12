import { useState, useCallback, useMemo } from "react";
import type {
  Session,
  ChatMessage,
  EditProgress,
  EditResult,
} from "../types";
import { uploadPdf as apiUploadPdf, getPageImageUrl } from "../services/api";
import { useWebSocket } from "./useWebSocket";

let msgCounter = 0;
function nextId() {
  return `msg-${++msgCounter}-${Date.now()}`;
}

export function usePdfSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageVersions, setPageVersions] = useState<Record<number, number>>({});
  const [chatMessages, setChatMessages] = useState<
    Record<number, ChatMessage[]>
  >({});
  const [editProgress, setEditProgress] = useState<EditProgress | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // ---- chat helpers ----

  const appendMsg = useCallback(
    (page: number, msg: ChatMessage) => {
      setChatMessages((prev) => ({
        ...prev,
        [page]: [...(prev[page] ?? []), msg],
      }));
    },
    [],
  );

  const replaceProgressMsg = useCallback(
    (page: number, msg: ChatMessage) => {
      setChatMessages((prev) => {
        const list = prev[page] ?? [];
        const idx = list.findLastIndex((m: ChatMessage) => m.role === "progress");
        if (idx === -1) return { ...prev, [page]: [...list, msg] };
        const updated = [...list];
        updated[idx] = msg;
        return { ...prev, [page]: updated };
      });
    },
    [],
  );

  const removeProgressMsgs = useCallback((page: number) => {
    setChatMessages((prev) => ({
      ...prev,
      [page]: (prev[page] ?? []).filter((m) => m.role !== "progress"),
    }));
  }, []);

  // We need a ref-stable way to know current page inside WS callbacks.
  // Store it in a ref indirectly via the pending-edit page.
  const [editingPage, setEditingPage] = useState<number | null>(null);

  // ---- WebSocket handlers ----

  const handleProgress = useCallback(
    (progress: EditProgress) => {
      setEditProgress(progress);
      const page = editingPage ?? currentPage;
      replaceProgressMsg(page, {
        id: "progress-live",
        role: "progress",
        content: progress.message,
        timestamp: progress.timestamp,
        stage: progress.stage,
      });
    },
    [editingPage, currentPage, replaceProgressMsg],
  );

  const handleComplete = useCallback(
    (result: EditResult) => {
      setEditProgress(null);
      const page = result.page_num;
      setEditingPage(null);

      setPageVersions((prev) => ({ ...prev, [page]: result.version }));

      removeProgressMsgs(page);
      appendMsg(page, {
        id: nextId(),
        role: "assistant",
        content: `Edit applied in ${(result.processing_time_ms / 1000).toFixed(1)}s`,
        timestamp: new Date().toISOString(),
        result,
      });
    },
    [appendMsg, removeProgressMsgs],
  );

  const handleError = useCallback(
    (message: string) => {
      setEditProgress(null);
      const page = editingPage ?? currentPage;
      setEditingPage(null);

      removeProgressMsgs(page);
      appendMsg(page, {
        id: nextId(),
        role: "assistant",
        content: `Error: ${message}`,
        timestamp: new Date().toISOString(),
      });
    },
    [editingPage, currentPage, appendMsg, removeProgressMsgs],
  );

  const { sendEdit: wsSendEdit, isConnected, isEditing } = useWebSocket(
    session?.session_id ?? null,
    {
      onProgress: handleProgress,
      onComplete: handleComplete,
      onError: handleError,
    },
  );

  // ---- public API ----

  const uploadPdf = useCallback(async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const result = await apiUploadPdf(file);
      setSession(result);
      setCurrentPage(1);
      setPageVersions({});
      setChatMessages({});
      setEditProgress(null);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, []);

  const selectPage = useCallback((page: number) => {
    setCurrentPage(page);
  }, []);

  const sendEdit = useCallback(
    (prompt: string) => {
      if (!session || isEditing) return;

      setEditingPage(currentPage);

      appendMsg(currentPage, {
        id: nextId(),
        role: "user",
        content: prompt,
        timestamp: new Date().toISOString(),
      });

      wsSendEdit(currentPage, prompt);
    },
    [session, currentPage, isEditing, wsSendEdit, appendMsg],
  );

  const currentPageVersion = pageVersions[currentPage];

  const getImageUrl = useCallback(
    (pageNum: number, version?: number) => {
      if (!session) return "";
      const v = version ?? pageVersions[pageNum];
      return getPageImageUrl(session.session_id, pageNum, v);
    },
    [session, pageVersions],
  );

  const currentMessages = useMemo(
    () => chatMessages[currentPage] ?? [],
    [chatMessages, currentPage],
  );

  return {
    session,
    currentPage,
    pageVersions,
    currentPageVersion,
    currentMessages,
    editProgress,
    isEditing,
    isConnected,
    uploading,
    uploadError,

    uploadPdf,
    selectPage,
    sendEdit,
    getImageUrl,
    setSession,
    setUploadError,
  };
}
