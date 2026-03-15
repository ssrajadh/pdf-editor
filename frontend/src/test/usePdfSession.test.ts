import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { usePdfSession } from "../hooks/usePdfSession";
import type { Session, SessionStateResponse } from "../types";

// ---- mocks ----

const mockUploadPdf = vi.fn<(file: File) => Promise<Session>>();
const mockGetSessionState = vi.fn<(id: string) => Promise<SessionStateResponse>>();
const mockGetPageHistory = vi.fn();
const mockRevertToStep = vi.fn();
const mockPreviewPlan = vi.fn();

vi.mock("../services/api", () => ({
  uploadPdf: (...args: unknown[]) => mockUploadPdf(args[0] as File),
  getPageImageUrl: (sid: string, page: number, step?: number) => {
    const base = `/api/pdf/${sid}/page/${page}/image`;
    return step !== undefined ? `${base}?step=${step}` : base;
  },
  getSessionState: (...args: unknown[]) => mockGetSessionState(args[0] as string),
  getPageHistory: (...args: unknown[]) => mockGetPageHistory(...args),
  revertToStep: (...args: unknown[]) => mockRevertToStep(...args),
  previewPlan: (...args: unknown[]) => mockPreviewPlan(...args),
}));

vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: () => ({
    sendEdit: vi.fn(),
    isConnected: true,
    isEditing: false,
    isReconnecting: false,
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  window.location.hash = "";
  window.history.replaceState(null, "", window.location.pathname);
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  window.location.hash = "";
  window.history.replaceState(null, "", window.location.pathname);
});

// ---- helpers ----

function makeSession(id = "abc00001", pages = 3, filename = "test.pdf"): Session {
  return { session_id: id, page_count: pages, filename };
}

// ---- tests ----

describe("usePdfSession", () => {
  describe("initial state", () => {
    it("starts with no session", () => {
      const { result } = renderHook(() => usePdfSession());
      expect(result.current.session).toBeNull();
      expect(result.current.currentPage).toBe(1);
      expect(result.current.pageVersions).toEqual({});
      expect(result.current.editCount).toBe(0);
    });
  });

  describe("uploadPdf", () => {
    it("sets session on successful upload", async () => {
      const session = makeSession("aaa00001");
      mockUploadPdf.mockResolvedValueOnce(session);

      const { result } = renderHook(() => usePdfSession());

      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(result.current.session).toEqual(session);
      expect(result.current.currentPage).toBe(1);
      expect(result.current.uploading).toBe(false);
      expect(result.current.uploadError).toBeNull();
    });

    it("stores session id in sessionStorage", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession("aaa00002"));

      const { result } = renderHook(() => usePdfSession());

      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(sessionStorage.getItem("nano_pdf_session_id")).toBe("aaa00002");
    });

    it("resets all state on second upload", async () => {
      const { result } = renderHook(() => usePdfSession());

      // First upload
      mockUploadPdf.mockResolvedValueOnce(makeSession("aaa00003"));
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "a.pdf"));
      });
      expect(result.current.session?.session_id).toBe("aaa00003");

      // Second upload — everything should reset
      mockUploadPdf.mockResolvedValueOnce(makeSession("bbb00004", 5, "b.pdf"));
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "b.pdf"));
      });

      expect(result.current.session?.session_id).toBe("bbb00004");
      expect(result.current.session?.page_count).toBe(5);
      expect(result.current.currentPage).toBe(1);
      expect(result.current.pageVersions).toEqual({});
      expect(result.current.editCount).toBe(0);
      expect(result.current.currentMessages).toEqual([]);
    });

    it("sets uploadError on failure", async () => {
      mockUploadPdf.mockRejectedValueOnce(new Error("Too large"));

      const { result } = renderHook(() => usePdfSession());

      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "huge.pdf"));
      });

      expect(result.current.session).toBeNull();
      expect(result.current.uploadError).toBe("Too large");
      expect(result.current.uploading).toBe(false);
    });

    it("updates URL hash with new session id", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession("aaa00005"));

      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(window.location.hash).toContain("session=aaa00005");
    });
  });

  describe("selectPage", () => {
    it("changes current page", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession());
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      act(() => result.current.selectPage(2));
      expect(result.current.currentPage).toBe(2);
    });
  });

  describe("getImageUrl", () => {
    it("returns empty string when no session", () => {
      const { result } = renderHook(() => usePdfSession());
      expect(result.current.getImageUrl(1)).toBe("");
    });

    it("returns URL without step when no edits", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession("ccc00001"));
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      const url = result.current.getImageUrl(1);
      expect(url).toBe("/api/pdf/ccc00001/page/1/image");
    });

    it("includes step param when version is explicitly provided", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession("ccc00002"));
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      const url = result.current.getImageUrl(1, 2);
      expect(url).toContain("step=2");
    });
  });

  describe("restoreSession (via hash)", () => {
    // Use hex-only IDs so the hash regex /session=([a-f0-9-]+)/i matches
    it("restores session from backend state", async () => {
      const stateResponse: SessionStateResponse = {
        session_id: "aabbccdd-1234",
        filename: "doc.pdf",
        page_count: 5,
        current_page: 3,
        pages: [
          { page_num: 1, current_step: 0, total_steps: 1, image_url: "", has_edits: false, edit_types: [] },
          { page_num: 2, current_step: 2, total_steps: 3, image_url: "", has_edits: true, edit_types: ["programmatic"] },
          { page_num: 3, current_step: 0, total_steps: 1, image_url: "", has_edits: false, edit_types: [] },
        ],
        conversations: {},
      };
      mockGetSessionState.mockResolvedValueOnce(stateResponse);

      window.location.hash = "#session=aabbccdd-1234";

      const { result } = renderHook(() => usePdfSession());

      await vi.waitFor(
        () => {
          expect(result.current.session?.session_id).toBe("aabbccdd-1234");
        },
        { timeout: 3000 },
      );

      expect(result.current.session?.page_count).toBe(5);
      expect(result.current.currentPage).toBe(3);
      expect(result.current.pageVersions[2]).toBe(2);
      expect(result.current.pageEditTypes[2]).toEqual({
        hasProgram: true,
        hasVisual: false,
      });
    });

    it("restores from sessionStorage when no hash", async () => {
      const stateResponse: SessionStateResponse = {
        session_id: "ddeeff00",
        filename: "doc.pdf",
        page_count: 1,
        current_page: 1,
        pages: [
          { page_num: 1, current_step: 0, total_steps: 1, image_url: "", has_edits: false, edit_types: [] },
        ],
        conversations: {},
      };
      mockGetSessionState.mockResolvedValueOnce(stateResponse);
      sessionStorage.setItem("nano_pdf_session_id", "ddeeff00");

      const { result } = renderHook(() => usePdfSession());

      await vi.waitFor(
        () => {
          expect(result.current.session?.session_id).toBe("ddeeff00");
        },
        { timeout: 3000 },
      );
    });

    it("clears state if backend restore fails", async () => {
      mockGetSessionState.mockRejectedValueOnce(new Error("Not found"));

      window.location.hash = "#session=deadbeef";

      const { result } = renderHook(() => usePdfSession());

      await vi.waitFor(
        () => {
          expect(result.current.isRestoring).toBe(false);
        },
        { timeout: 3000 },
      );

      expect(result.current.session).toBeNull();
      expect(sessionStorage.getItem("nano_pdf_session_id")).toBeNull();
    });

    it("merges sessionStorage chats with backend conversations", async () => {
      const stateResponse: SessionStateResponse = {
        session_id: "aabb0011",
        filename: "doc.pdf",
        page_count: 2,
        current_page: 1,
        pages: [
          { page_num: 1, current_step: 1, total_steps: 2, image_url: "", has_edits: true, edit_types: ["programmatic"] },
          { page_num: 2, current_step: 0, total_steps: 1, image_url: "", has_edits: false, edit_types: [] },
        ],
        conversations: {
          "1": [{ id: "m1", role: "user", content: "Backend msg", timestamp: "" }],
        },
      };
      mockGetSessionState.mockResolvedValueOnce(stateResponse);
      mockGetPageHistory.mockResolvedValue({
        session_id: "aabb0011",
        page_num: 1,
        current_step: 1,
        total_steps: 2,
        snapshots: [],
      });

      // Put more messages in sessionStorage
      sessionStorage.setItem(
        "nano_pdf_chats_aabb0011",
        JSON.stringify({
          "1": [
            { id: "m1", role: "user", content: "Backend msg", timestamp: "" },
            { id: "m2", role: "assistant", content: "Reply", timestamp: "" },
          ],
        }),
      );

      window.location.hash = "#session=aabb0011";
      const { result } = renderHook(() => usePdfSession());

      await vi.waitFor(
        () => {
          expect(result.current.session?.session_id).toBe("aabb0011");
        },
        { timeout: 3000 },
      );

      // Should use sessionStorage version (has more messages)
      expect(result.current.currentMessages.length).toBe(2);
    });
  });

  describe("currentPageVersion", () => {
    it("is undefined when no edits exist", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession());
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(result.current.currentPageVersion).toBeUndefined();
    });
  });

  describe("currentMessages", () => {
    it("returns empty array for page with no messages", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession());
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(result.current.currentMessages).toEqual([]);
    });

    it("returns messages for current page only", async () => {
      mockUploadPdf.mockResolvedValueOnce(makeSession());
      const { result } = renderHook(() => usePdfSession());
      await act(async () => {
        await result.current.uploadPdf(new File(["pdf"], "doc.pdf"));
      });

      expect(result.current.currentMessages).toEqual([]);

      act(() => result.current.selectPage(2));
      expect(result.current.currentMessages).toEqual([]);
    });
  });
});
