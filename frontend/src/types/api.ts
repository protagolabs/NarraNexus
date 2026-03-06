/**
 * API response type definitions
 */

/** Base interface for all API responses */
export interface ApiResponse {
  success: boolean;
  error?: string;
}

// Job types
export type JobStatus = 'pending' | 'active' | 'running' | 'paused' | 'completed' | 'failed' | 'blocked' | 'cancelled';
export type JobType = 'one_off' | 'scheduled' | 'ongoing';

export interface TriggerConfig {
  trigger_type?: string;
  interval_seconds?: number;
  cron_expression?: string;
  timezone?: string;
  [key: string]: unknown;
}

export interface Job {
  job_id: string;
  agent_id: string;
  user_id: string;
  job_type: JobType;
  title: string;
  description?: string;
  status: JobStatus;
  payload?: string;
  trigger_config?: TriggerConfig;
  process?: string[];
  next_run_time?: string;
  last_run_time?: string;
  last_error?: string;
  notification_method?: string;
  created_at?: string;
  updated_at?: string;
  // Dependencies (fetched from module_instances table)
  instance_id?: string;
  depends_on?: string[];
  // New fields (Feature 2.2, 3.1)
  related_entity_id?: string;      // Target user ID (used as the principal identity during Job execution)
  narrative_id?: string;           // Associated Narrative ID (conversation context)
}

export interface JobListResponse extends ApiResponse {
  jobs: Job[];
  count: number;
}

export interface JobDetailResponse extends ApiResponse {
  job?: Job;
}

export interface CancelJobResponse extends ApiResponse {
  job_id?: string;
  previous_status?: string;
}

// Inbox types (User Inbox)
export interface MessageSource {
  type?: string;
  id?: string;
}

export interface InboxMessage {
  message_id: string;
  user_id: string;
  message_type: string;
  title: string;
  content: string;
  source?: MessageSource;
  event_id?: string;
  is_read: boolean;
  created_at?: string;
}

export interface InboxListResponse extends ApiResponse {
  messages: InboxMessage[];
  count: number;
  unread_count: number;
}

export interface MarkReadResponse extends ApiResponse {
  marked_count: number;
}

// Agent Inbox types
export type AgentMessageSourceType = 'user' | 'agent' | 'system';

export interface AgentInboxMessage {
  message_id: string;
  agent_id: string;
  source_type: AgentMessageSourceType;
  source_id: string;
  content: string;
  if_response: boolean;
  narrative_id?: string;
  event_id?: string;
  created_at?: string;
}

export interface AgentInboxListResponse extends ApiResponse {
  messages: AgentInboxMessage[];
  count: number;
  unresponded_count: number;
}

export interface MarkRespondedResponse extends ApiResponse {
  marked_count: number;
}

// Awareness types
export interface AwarenessResponse extends ApiResponse {
  awareness?: string;
  create_time?: string;
  update_time?: string;
}

// Clear history types
export interface ClearHistoryResponse extends ApiResponse {
  narrative_ids_deleted: string[];
  narratives_count: number;
  events_count: number;
}

// Social Network types
export interface SocialNetworkEntity {
  entity_id: string;
  entity_name?: string;
  entity_description?: string;
  entity_type: string;
  identity_info: Record<string, unknown>;
  contact_info: Record<string, unknown>;
  tags: string[];
  relationship_strength: number;
  interaction_count: number;
  last_interaction_time?: string;
  // New fields (Feature 2.2, 2.3)
  persona?: string;                // Communication style/characteristics
  related_job_ids?: string[];      // Associated Job IDs
  expertise_domains?: string[];    // Expertise domains
}

export interface SocialNetworkResponse extends ApiResponse {
  entity?: SocialNetworkEntity;
}

export interface SocialNetworkListResponse extends ApiResponse {
  entities: SocialNetworkEntity[];
  count: number;
}

// Semantic search response
export interface SocialNetworkSearchResponse extends ApiResponse {
  entities: Array<SocialNetworkEntity & { similarity_score?: number }>;
  count: number;
  search_type: 'keyword' | 'semantic';
}

// Chat History types
export interface EventLogEntry {
  timestamp: string;
  type: string;
  content: unknown;
}

// Simple Chat History types (for displaying recent interactions)
export interface SimpleChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  narrative_id?: string;
}

export interface SimpleChatHistoryResponse extends ApiResponse {
  messages: SimpleChatMessage[];
  total_count: number;
}

export interface ChatHistoryEvent {
  event_id: string;
  narrative_id?: string;
  narrative_name?: string;
  trigger: string;
  trigger_source: string;
  user_id?: string;
  final_output: string;
  created_at: string;
  event_log: EventLogEntry[];
}

// Module Instance info for displaying in Narrative
export interface InstanceInfo {
  instance_id: string;
  module_class: string;
  description: string;
  status: string;
  dependencies: string[];
  config: Record<string, unknown>;
  created_at?: string;
  user_id?: string;  // Used to filter events by user_id
}

export interface ChatHistoryNarrative {
  narrative_id: string;
  name: string;
  description: string;
  current_summary: string;
  actors: Array<{ id: string; type: string }>;
  created_at: string;
  updated_at: string;
  instances: InstanceInfo[];  // Associated Module Instances
}

export interface ChatHistoryResponse extends ApiResponse {
  narratives: ChatHistoryNarrative[];
  events: ChatHistoryEvent[];
  narrative_count: number;
  event_count: number;
}

// Create Agent types
export interface CreateAgentRequest {
  agent_name?: string;
  agent_description?: string;
  created_by: string;
}

export interface AgentInfo {
  agent_id: string;
  name?: string;
  description?: string;
  status?: string;
  created_at?: string;
}

export interface CreateAgentResponse extends ApiResponse {
  agent?: AgentInfo;
}

export interface UpdateAgentRequest {
  agent_name?: string;
  agent_description?: string;
}

export interface UpdateAgentResponse extends ApiResponse {
  agent?: AgentInfo;
}

export interface DeleteAgentResponse extends ApiResponse {
  agent_id?: string;
  deleted_counts?: Record<string, number>;
}

// File Management types
export interface FileInfo {
  filename: string;
  size: number;
  modified_at: string;
}

export interface FileListResponse extends ApiResponse {
  files: FileInfo[];
  workspace_path: string;
}

export interface FileUploadResponse extends ApiResponse {
  filename?: string;
  size?: number;
  workspace_path?: string;
}

export interface FileDeleteResponse extends ApiResponse {
  filename?: string;
}

// MCP Management types
export interface MCPInfo {
  mcp_id: string;
  agent_id: string;
  user_id: string;
  name: string;
  url: string;
  description?: string;
  is_enabled: boolean;
  connection_status?: 'connected' | 'failed' | 'unknown' | null;
  last_check_time?: string;
  last_error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface MCPListResponse extends ApiResponse {
  mcps: MCPInfo[];
  count: number;
}

export interface MCPCreateRequest {
  name: string;
  url: string;
  description?: string;
  is_enabled?: boolean;
}

export interface MCPUpdateRequest {
  name?: string;
  url?: string;
  description?: string;
  is_enabled?: boolean;
}

export interface MCPResponse extends ApiResponse {
  mcp?: MCPInfo;
}

export interface MCPValidateResponse extends ApiResponse {
  mcp_id: string;
  connected: boolean;
}

export interface MCPValidateAllResponse extends ApiResponse {
  results: MCPValidateResponse[];
  total: number;
  connected: number;
  failed: number;
}

// RAG File Management types
export type RAGFileStatus = 'pending' | 'uploading' | 'completed' | 'failed';

export interface RAGFileInfo {
  filename: string;
  size: number;
  modified_at: string;
  upload_status: RAGFileStatus;
  error_message?: string;
}

export interface RAGFileListResponse extends ApiResponse {
  files: RAGFileInfo[];
  total_count: number;
  completed_count: number;
  pending_count: number;
}

export interface RAGFileUploadResponse extends ApiResponse {
  filename?: string;
  size?: number;
  upload_status?: string;
}

export interface RAGFileDeleteResponse extends ApiResponse {
  filename?: string;
}
