import type { Session, PageTextResponse, ExecutionPlan } from "../types";

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

export async function previewPlan(
  sessionId: string,
  pageNum: number,
  prompt: string,
): Promise<ExecutionPlan> {
  const res = await fetch(
    `${API_BASE}/edit/${sessionId}/page/${pageNum}/plan-preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    },
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Plan preview failed");
  }

  return res.json();
}

export async function exportPdf(sessionId: string, filename: string): Promise<void> {
  const res = await fetch(`${API_BASE}/pdf/${sessionId}/export`, {
    method: "POST",
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Export failed");
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;

  const stem = filename.replace(/\.pdf$/i, "");
  a.download = `${stem}_edited.pdf`;

  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
