// Package protocol mirrors loom/rpc/protocol.py — the JSON-RPC message
// types shared between the Python core and this Go TUI.
package protocol

import "encoding/json"

// Event is a streamed event from Python to TUI (no response expected).
//
// For server-initiated requests (e.g. permission prompts) forwarded via the
// Events channel, ID carries the request id the TUI must reference in its
// reply. For ordinary fire-and-forget events ID is empty.
type Event struct {
	Jsonrpc string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
	ID      string          `json:"id,omitempty"`
}

// Request is a request from TUI to Python (expects a Response).
type Request struct {
	Jsonrpc string         `json:"jsonrpc"`
	Method  string         `json:"method"`
	ID      string         `json:"id"`
	Params  map[string]any `json:"params"`
}

// Response is a reply from Python to TUI (matches a prior Request by ID).
// Also used for server-initiated requests (permission prompts): in that
// case Method is set (e.g. "request/permission") and Params carries the
// prompt details.
type Response struct {
	Jsonrpc string          `json:"jsonrpc"`
	ID      string          `json:"id"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *RPCError       `json:"error,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
}

// RPCError is the JSON-RPC error object on error responses.
type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Event method constants — must match loom/rpc/protocol.py EVENT_TYPES.
const (
	EventAssistantTurnStart = "event/assistant_turn_start"
	EventAssistantTurnEnd   = "event/assistant_turn_end"
	EventTextDelta          = "event/text_delta"
	EventThinkingDelta      = "event/thinking_delta"
	EventToolUseStarted     = "event/tool_use_started"
	EventToolUseCompleted   = "event/tool_use_completed"
	EventCompactOccurred    = "event/compact_occurred"
	EventTodoUpdate         = "event/todo_update"
	EventSubagentStart      = "event/subagent_start"
	EventSubagentEnd        = "event/subagent_end"
	EventShowNotification   = "event/show_notification"
	EventSessionStarted     = "event/session_started"
	EventSessionEnded       = "event/session_ended"
	EventError              = "event/error"
)

// Request method constants — must match loom/rpc/protocol.py REQUEST_TYPES.
const (
	RequestMethodSendMessage        = "request/send_message"
	RequestMethodCancel             = "request/cancel"
	RequestMethodPermissionResponse = "request/permission_response"
	RequestMethodPickModel          = "request/pick_model"
	RequestMethodListSessions       = "request/list_sessions"
	RequestMethodLoadSession        = "request/load_session"
	RequestMethodNewSession         = "request/new_session"
	RequestMethodShutdown           = "request/shutdown"
)

// Server-request method constants — server-initiated requests (Python -> TUI,
// but require a reply via the corresponding request/*_response method).
// Must match loom/rpc/protocol.py SERVER_REQUEST_TYPES.
const (
	ServerRequestMethodPermission = "request/permission"
)

// Event param structs for typed access.

// TextDeltaParams carries a streamed text chunk.
type TextDeltaParams struct {
	Text string `json:"text"`
}

// ThinkingDeltaParams carries a streamed thinking chunk.
type ThinkingDeltaParams struct {
	Text string `json:"text"`
}

// ToolUseStartedParams carries tool invocation start info.
type ToolUseStartedParams struct {
	ToolName  string         `json:"tool_name"`
	ToolInput map[string]any `json:"tool_input"`
	ToolUseID string         `json:"tool_use_id"`
}

// ToolUseCompletedParams carries the tool result + error flag.
type ToolUseCompletedParams struct {
	ToolUseID string `json:"tool_use_id"`
	Output    string `json:"output"`
	IsError   bool   `json:"is_error"`
}

// AssistantTurnEndParams carries turn summary stats.
type AssistantTurnEndParams struct {
	ToolCalls     int     `json:"tool_calls"`
	TotalMessages int     `json:"total_messages"`
	Duration      float64 `json:"duration"`
}

// SessionStartedParams carries the initial session handshake.
type SessionStartedParams struct {
	SessionID string `json:"session_id"`
	Model     string `json:"model"`
}

// SubagentStartParams carries subagent launch info.
type SubagentStartParams struct {
	SubagentID  string `json:"subagent_id"`
	Description string `json:"description"`
	AgentName   string `json:"agent_name"`
}

// SubagentEndParams carries subagent completion info.
type SubagentEndParams struct {
	SubagentID string  `json:"subagent_id"`
	Elapsed    float64 `json:"elapsed"`
	State      string  `json:"state"`
}

// PermissionParams carries a server-initiated permission prompt.
type PermissionParams struct {
	ToolName  string         `json:"tool_name"`
	ToolInput map[string]any `json:"tool_input"`
	Reason    string         `json:"reason"`
}

// NewSendMessage constructs a request/send_message.
func NewSendMessage(id, text string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodSendMessage,
		ID:      id,
		Params:  map[string]any{"text": text},
	}
}

// NewCancel constructs a request/cancel.
func NewCancel(id string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodCancel,
		ID:      id,
		Params:  map[string]any{},
	}
}

// NewPermissionResponse constructs a reply to a server-initiated permission prompt.
func NewPermissionResponse(id, requestID, decision string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodPermissionResponse,
		ID:      id,
		Params:  map[string]any{"request_id": requestID, "decision": decision},
	}
}

// NewShutdown constructs a request/shutdown.
func NewShutdown(id string) Request {
	return Request{
		Jsonrpc: "2.0",
		Method:  RequestMethodShutdown,
		ID:      id,
		Params:  map[string]any{},
	}
}
