import type { Session, PageTextResponse } from "../types";

const API_BASE = "/api";

export async function uploadPdf(file: File): Promise<Session> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE}/pdf/upload`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Upload failed");
  }

  return res.json();
}

export function getPageImageUrl(
  sessionId: string,
  pageNum: number,
  version?: number,
): string {
  const base = `${API_BASE}/pdf/${sessionId}/page/${pageNum}/image`;
  const cacheBust = version !== undefined ? `?v=${version}` : "";
  return `${base}${cacheBust}`;
}

export async function getPageText(
  sessionId: string,
  pageNum: number,
): Promise<PageTextResponse> {
  const res = await fetch(
    `${API_BASE}/pdf/${sessionId}/page/${pageNum}/text`,
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to get page text");
  }

  return res.json();
}

export async function getSessionInfo(sessionId: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/pdf/${sessionId}/info`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Session not found");
  }

  return res.json();
}
