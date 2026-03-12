export interface Session {
  session_id: string;
  page_count: number;
  filename: string;
}

export interface PageInfo {
  page_num: number;
  version: number;
  image_url: string;
}

export interface EditProgress {
  stage: string;
  message: string;
  timestamp: string;
}

export interface EditResult {
  session_id: string;
  page_num: number;
  version: number;
  processing_time_ms: number;
  text_layer_preserved: boolean;
}

export interface PageTextBlock {
  text: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  font_name: string;
  font_size: number;
}

export interface PageTextResponse {
  session_id: string;
  page_number: number;
  full_text: string;
  blocks: PageTextBlock[];
}
