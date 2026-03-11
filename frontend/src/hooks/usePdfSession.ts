import { useState } from "react";

export function usePdfSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);

  const uploadPdf = async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/pdf/upload", { method: "POST", body: formData });
    const data = await res.json();
    setSessionId(data.session_id);
    setPageCount(data.page_count ?? 0);
    setCurrentPage(1);
  };

  return { sessionId, pageCount, currentPage, setCurrentPage, uploadPdf };
}
