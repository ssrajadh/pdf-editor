export interface PageInfo {
  page_number: number;
  width: number;
  height: number;
}

export interface EditRequest {
  session_id: string;
  instruction: string;
  page_number?: number;
}

export interface EditProgress {
  session_id: string;
  status: string;
  progress: number;
  message: string;
}

export interface EditResult {
  session_id: string;
  status: string;
  modified_pages: number[];
}

export interface UploadResponse {
  session_id: string;
  filename: string;
  page_count: number;
}
