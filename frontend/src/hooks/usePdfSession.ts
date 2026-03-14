import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import type {
  Session,
  ChatMessage,
  EditProgress,
  ExecutionResult,
  ExecutionPlan,
  PageEditType,
  PageHistoryResponse,
} from "../types";
import {
  uploadPdf as apiUploadPdf,
  getPageImageUrl,
  previewPlan as apiPreviewPlan,
  getPageHistory as apiGetPageHistory,
  revertToStep as apiRevertToStep,
} from "../services/api";
import { useWebSocket } from "./useWebSocket";

let msgCounter = 0;
function nextId() {
  return `msg-${++msgCounter}-${Date.now()}`;
}

export function usePdfSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageVersions, setPageVersions] = useState<Record<number, number>>({});
  const [pageEditTypes, setPageEditTypes] = useState<Record<number, PageEditType>>({});
  const [chatMessages, setChatMessages] = useState<Record<number, ChatMessage[]>>({});
  const [editProgress, setEditProgress] = useState<EditProgress | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [editCount, setEditCount] = useState(0);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [pageHistories, setPageHistories] = useState<Record<number, PageHistoryResponse>>({});
  const [isReverting, setIsReverting] = useState(false);
  const sessionStartRef = useRef<number | null>(null);

  const currentPlanRef = useRef<ExecutionPlan | null>(null);
  const fetchHistoryRef = useRef<(page: number) => void>(() => {});

  const appendMsg = useCallback((page: number, msg: ChatMessage) => {
    setChatMessages((prev) => ({
      ...prev,
      [page]: [...(prev[page] ?? []), msg],
    }));
  }, []);

  const replaceProgressMsg = useCallback((page: number, msg: ChatMessage) => {
    setChatMessages((prev) => {
      const list = prev[page] ?? [];
      const idx = list.findLastIndex((m: ChatMessage) => m.role === "progress");
      if (idx === -1) return { ...prev, [page]: [...list, msg] };
      const updated = [...list];
      updated[idx] = msg;
      return { ...prev, [page]: updated };
    });
  }, []);

  const removeProgressMsgs = useCallback((page: number) => {
    setChatMessages((prev) => ({
      ...prev,
      [page]: (prev[page] ?? []).filter((m) => m.role !== "progress"),
    }));
  }, []);

  const [editingPage, setEditingPage] = useState<number | null>(null);
  const lastPromptRef = useRef<{ page: number; prompt: string } | null>(null);

  const handleProgress = useCallback(
    (progress: EditProgress) => {
      setEditProgress(progress);

      if (progress.plan) {
        currentPlanRef.current = progress.plan;
      }

      const page = editingPage ?? currentPage;
      const totalOps = currentPlanRef.current?.operations.length;

      replaceProgressMsg(page, {
        id: "progress-live",
        role: "progress",
        content: progress.message,
        timestamp: progress.timestamp,
        stage: progress.stage,
        op_index: progress.op_index,
        total_ops: totalOps,
      });
    },
    [editingPage, currentPage, replaceProgressMsg],
  );

  const handleComplete = useCallback(
    (result: ExecutionResult) => {
      setEditProgress(null);
      const page = result.page_num;
      const plan = currentPlanRef.current;
      currentPlanRef.current = null;
      setEditingPage(null);
      lastPromptRef.current = null;

      setPageVersions((prev) => ({ ...prev, [page]: result.version }));

      setPageEditTypes((prev) => {
        const existing = prev[page] ?? { hasProgram: false, hasVisual: false };
        return {
          ...prev,
          [page]: {
            hasProgram: existing.hasProgram || result.programmatic_count > 0,
            hasVisual: existing.hasVisual || result.visual_count > 0,
          },
        };
      });

      setEditCount((c) => c + 1);
      removeProgressMsgs(page);

      const allProgrammatic = result.visual_count === 0 && result.programmatic_count > 0;
      const content = allProgrammatic
        ? `Edit applied in ${result.total_time_ms}ms`
        : `Edit applied in ${(result.total_time_ms / 1000).toFixed(1)}s`;

      appendMsg(page, {
        id: nextId(),
        role: "assistant",
        content,
        timestamp: new Date().toISOString(),
        result,
        plan: plan ?? undefined,
      });

      // Refresh history after edit
      fetchHistoryRef.current(page);
    },
    [appendMsg, removeProgressMsgs],
  );

  const handleError = useCallback(
    (message: string) => {
      setEditProgress(null);
      const page = editingPage ?? currentPage;
      setEditingPage(null);
      currentPlanRef.current = null;

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

  const { sendEdit: wsSendEdit, isConnected, isEditing, isReconnecting } = useWebSocket(
    session?.session_id ?? null,
    {
      onProgress: handleProgress,
      onComplete: handleComplete,
      onError: handleError,
    },
  );

  const uploadPdf = useCallback(async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const result = await apiUploadPdf(file);
      setSession(result);
      setCurrentPage(1);
      setPageVersions({});
      setPageEditTypes({});
      setChatMessages({});
      setPageHistories({});
      setEditProgress(null);
      setEditCount(0);
      sessionStartRef.current = Date.now();
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
      lastPromptRef.current = { page: currentPage, prompt };

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

  const previewPlan = useCallback(
    async (prompt: string) => {
      if (!session || isEditing || isPreviewing) return;

      setIsPreviewing(true);

      appendMsg(currentPage, {
        id: nextId(),
        role: "user",
        content: prompt,
        timestamp: new Date().toISOString(),
      });

      appendMsg(currentPage, {
        id: "preview-loading",
        role: "progress",
        content: "Generating execution plan...",
        timestamp: new Date().toISOString(),
        stage: "planning",
      });

      try {
        const plan = await apiPreviewPlan(session.session_id, currentPage, prompt);

        removeProgressMsgs(currentPage);

        const progCount = plan.operations.filter(
          (op) => op.type !== "visual_regenerate",
        ).length;
        const visCount = plan.operations.filter(
          (op) => op.type === "visual_regenerate",
        ).length;

        appendMsg(currentPage, {
          id: nextId(),
          role: "assistant",
          content: `Plan: ${plan.operations.length} operations — ${progCount} programmatic, ${visCount} visual`,
          timestamp: new Date().toISOString(),
          plan,
          isPlanPreview: true,
          previewPrompt: prompt,
        });
      } catch (err) {
        removeProgressMsgs(currentPage);
        appendMsg(currentPage, {
          id: nextId(),
          role: "assistant",
          content: `Error: ${err instanceof Error ? err.message : "Plan preview failed"}`,
          timestamp: new Date().toISOString(),
        });
      } finally {
        setIsPreviewing(false);
      }
    },
    [session, currentPage, isEditing, isPreviewing, appendMsg, removeProgressMsgs],
  );

  const executePlanEdit = useCallback(
    (prompt: string) => {
      if (!session || isEditing) return;

      setEditingPage(currentPage);
      lastPromptRef.current = { page: currentPage, prompt };
      wsSendEdit(currentPage, prompt);
    },
    [session, currentPage, isEditing, wsSendEdit],
  );

  const retryLastEdit = useCallback(() => {
    const last = lastPromptRef.current;
    if (!last || isEditing) return;
    setEditingPage(last.page);
    wsSendEdit(last.page, last.prompt);
  }, [isEditing, wsSendEdit]);

  const fetchPageHistory = useCallback(
    async (pageNum: number) => {
      if (!session) return;
      try {
        const history = await apiGetPageHistory(session.session_id, pageNum);
        setPageHistories((prev) => ({ ...prev, [pageNum]: history }));
      } catch {
        // silently ignore — history panel just won't show
      }
    },
    [session],
  );

  // Keep ref in sync for use in handleComplete (defined before fetchPageHistory)
  fetchHistoryRef.current = fetchPageHistory;

  const revertToStep = useCallback(
    async (pageNum: number, step: number) => {
      if (!session || isReverting) return;
      setIsReverting(true);
      try {
        await apiRevertToStep(session.session_id, pageNum, step);

        // Update version to the reverted step
        setPageVersions((prev) => ({ ...prev, [pageNum]: step }));

        // Add a system message in chat
        appendMsg(pageNum, {
          id: nextId(),
          role: "assistant",
          content: `Reverted to step ${step}`,
          timestamp: new Date().toISOString(),
        });

        // Refresh history
        await fetchPageHistory(pageNum);
      } catch (err) {
        appendMsg(pageNum, {
          id: nextId(),
          role: "assistant",
          content: `Error: ${err instanceof Error ? err.message : "Revert failed"}`,
          timestamp: new Date().toISOString(),
        });
      } finally {
        setIsReverting(false);
      }
    },
    [session, isReverting, appendMsg, fetchPageHistory],
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

  // Auto-fetch history when page changes and has edits
  useEffect(() => {
    const v = pageVersions[currentPage];
    if (session && v !== undefined && v > 0) {
      fetchPageHistory(currentPage);
    }
  }, [session, currentPage, pageVersions, fetchPageHistory]);

  const currentHistory = pageHistories[currentPage] ?? null;

  const currentMessages = useMemo(
    () => chatMessages[currentPage] ?? [],
    [chatMessages, currentPage],
  );

  const sessionDuration = sessionStartRef.current
    ? Math.floor((Date.now() - sessionStartRef.current) / 60000)
    : 0;

  return {
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
    isConnected,
    isReconnecting,
    uploading,
    uploadError,
    editCount,
    sessionDuration,

    uploadPdf,
    selectPage,
    sendEdit,
    previewPlan,
    executePlanEdit,
    retryLastEdit,
    revertToStep,
    fetchPageHistory,
    getImageUrl,
    setSession,
    setUploadError,
  };
}
