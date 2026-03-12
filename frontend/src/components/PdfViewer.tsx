import { useState } from "react";
import { Loader2 } from "lucide-react";
import type { Session } from "../types";
import { getPageImageUrl } from "../services/api";

interface Props {
  session: Session;
  currentPage: number;
  pageVersion?: number;
}

export default function PdfViewer({ session, currentPage, pageVersion }: Props) {
  const [loading, setLoading] = useState(true);

  const imageUrl = getPageImageUrl(session.session_id, currentPage, pageVersion);

  return (
    <div className="flex-1 flex flex-col items-center bg-gray-100 overflow-auto p-6">
      <div className="mb-3 text-sm text-gray-500 font-medium">
        Page {currentPage} of {session.page_count}
      </div>

      <div className="relative w-full max-w-3xl">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 rounded-lg z-10">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        )}

        <img
          key={imageUrl}
          src={imageUrl}
          alt={`Page ${currentPage}`}
          className="w-full h-auto rounded-lg shadow-md bg-white"
          onLoad={() => setLoading(false)}
          onError={() => setLoading(false)}
        />
      </div>
    </div>
  );
}
