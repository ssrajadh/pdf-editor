const API_BASE = "/api";

export async function uploadPdf(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/pdf/upload`, { method: "POST", body: formData });
  return res.json();
}

export async function getPages(sessionId: string) {
  const res = await fetch(`${API_BASE}/pdf/${sessionId}/pages`);
  return res.json();
}

export async function submitEdit(sessionId: string, instruction: string, pageNumber?: number) {
  const res = await fetch(`${API_BASE}/edit/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, instruction, page_number: pageNumber }),
  });
  return res.json();
}
