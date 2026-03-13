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

export interface PlanOperation {
  type: "text_replace" | "style_change" | "visual_regenerate";
  confidence: number;
  reasoning: string;
  original_text?: string;
  replacement_text?: string;
  match_strategy?: string;
  target_text?: string;
  changes?: Record<string, unknown>;
  prompt?: string;
  region?: string | null;
}

export interface ExecutionPlan {
  operations: PlanOperation[];
  execution_order: number[];
  summary: string;
  all_programmatic: boolean;
}

export interface OperationResult {
  op_index: number;
  op_type: "text_replace" | "style_change" | "visual_regenerate";
  success: boolean;
  time_ms: number;
  path: "programmatic" | "visual" | "fallback_visual";
  detail: string;
  error?: string;
}

export interface ExecutionResult {
  session_id: string;
  page_num: number;
  version: number;
  plan_summary: string;
  operations: OperationResult[];
  total_time_ms: number;
  programmatic_count: number;
  visual_count: number;
  text_layer_source: "original" | "programmatic_edit" | "mixed" | "ocr";
}

export interface EditProgress {
  stage: string;
  message: string;
  timestamp: string;
  plan?: ExecutionPlan;
  op_index?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "progress";
  content: string;
  timestamp: string;
  result?: ExecutionResult;
  plan?: ExecutionPlan;
  stage?: string;
  op_index?: number;
  total_ops?: number;
  isPlanPreview?: boolean;
  previewPrompt?: string;
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

export interface PageEditType {
  hasProgram: boolean;
  hasVisual: boolean;
}
