export interface Session {
  session_id: string;
  page_count: number;
  filename: string;
}

export interface SessionListItem {
  session_id: string;
  filename: string;
  page_count: number;
  created_at: string;
  last_edit_at: string;
  total_edits: number;
}

export interface SessionStatePage {
  page_num: number;
  current_step: number;
  total_steps: number;
  image_url: string;
  has_edits: boolean;
  edit_types: Array<"programmatic" | "visual">;
}

export interface SessionStateResponse {
  session_id: string;
  filename: string;
  page_count: number;
  current_page: number;
  pages: SessionStatePage[];
  conversations: Record<string, ChatMessage[]>;
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
  context_before?: string | null;
  context_after?: string | null;
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
  page_analysis?: string;
}

export interface OperationResult {
  op_index: number;
  op_type: "text_replace" | "style_change" | "visual_regenerate";
  success: boolean;
  time_ms: number;
  path: "programmatic" | "visual" | "fallback_visual" | "blocked";
  detail: string;
  error?: string;
  risk_assessment?: RegenRiskAssessment;
}

export interface RegenRiskAssessment {
  risk_level: "low" | "medium" | "high" | "critical";
  text_density: number;
  text_block_count: number;
  recommendation: string;
  safe_to_proceed: boolean;
  override_available: boolean;
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
  blocked_count: number;
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
  editPrompt?: string;
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

export interface PageSnapshotResponse {
  step: number;
  timestamp: string;
  prompt: string | null;
  plan_summary: string | null;
  operations_summary: OperationResult[] | null;
  image_url: string;
  text_layer_source: string;
  is_current: boolean;
}

export interface PageHistoryResponse {
  session_id: string;
  page_num: number;
  current_step: number;
  total_steps: number;
  snapshots: PageSnapshotResponse[];
}
