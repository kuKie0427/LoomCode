package protocol

import (
	"encoding/json"
	"testing"
)

// TestEventRoundTrip verifies that an Event serialized by Python (as a JSON
// line) can be parsed by Go into the right struct, and that the method
// constant matches.
func TestEventRoundTrip(t *testing.T) {
	cases := []struct {
		name       string
		line       string // exactly as Python would emit
		wantMethod string
	}{
		{
			name:       "text_delta",
			line:       `{"jsonrpc":"2.0","method":"event/text_delta","params":{"text":"hi"}}`,
			wantMethod: EventTextDelta,
		},
		{
			name:       "session_started",
			line:       `{"jsonrpc":"2.0","method":"event/session_started","params":{"session_id":"abc","model":"stub/test"}}`,
			wantMethod: EventSessionStarted,
		},
		{
			name:       "assistant_turn_end",
			line:       `{"jsonrpc":"2.0","method":"event/assistant_turn_end","params":{"tool_calls":3,"total_messages":10,"duration":2.5}}`,
			wantMethod: EventAssistantTurnEnd,
		},
		{
			name:       "cjk_agent_name",
			line:       `{"jsonrpc":"2.0","method":"event/assistant_turn_start","params":{"agent_name":"织轴"}}`,
			wantMethod: EventAssistantTurnStart,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var ev Event
			if err := json.Unmarshal([]byte(tc.line), &ev); err != nil {
				t.Fatalf("unmarshal: %v", err)
			}
			if ev.Method != tc.wantMethod {
				t.Errorf("method = %q, want %q", ev.Method, tc.wantMethod)
			}
			if ev.Jsonrpc != "2.0" {
				t.Errorf("jsonrpc = %q, want 2.0", ev.Jsonrpc)
			}
		})
	}
}

// TestResponseWithResult verifies that a normal Response (with result) is
// parsed correctly and dispatched via ID.
func TestResponseWithResult(t *testing.T) {
	line := `{"jsonrpc":"2.0","id":"r1","result":{"ack":true}}`
	var resp Response
	if err := json.Unmarshal([]byte(line), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if resp.ID != "r1" {
		t.Errorf("id = %q, want r1", resp.ID)
	}
	if resp.Result == nil {
		t.Fatal("result is nil")
	}
	var ack struct {
		Ack bool `json:"ack"`
	}
	if err := json.Unmarshal(resp.Result, &ack); err != nil {
		t.Fatalf("unmarshal result: %v", err)
	}
	if !ack.Ack {
		t.Error("ack = false, want true")
	}
}

// TestResponseWithError verifies error responses carry code + message.
func TestResponseWithError(t *testing.T) {
	line := `{"jsonrpc":"2.0","id":"r1","error":{"code":-32601,"message":"unknown method"}}`
	var resp Response
	if err := json.Unmarshal([]byte(line), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if resp.Error == nil {
		t.Fatal("error is nil")
	}
	if resp.Error.Code != -32601 {
		t.Errorf("code = %d, want -32601", resp.Error.Code)
	}
	if resp.Error.Message != "unknown method" {
		t.Errorf("message = %q, want 'unknown method'", resp.Error.Message)
	}
}

// TestServerInitiatedPermissionRequest verifies that a server-initiated
// request/permission message is parsed with both Method and Params set
// (this is the dispatch discriminator the RPC client uses).
func TestServerInitiatedPermissionRequest(t *testing.T) {
	line := `{"jsonrpc":"2.0","id":"p1","method":"request/permission","params":{"tool_name":"bash","tool_input":{"command":"rm -rf /"},"reason":"destructive"}}`
	var resp Response
	if err := json.Unmarshal([]byte(line), &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if resp.ID != "p1" {
		t.Errorf("id = %q, want p1", resp.ID)
	}
	if resp.Method != "request/permission" {
		t.Errorf("method = %q, want request/permission", resp.Method)
	}
	if resp.Result != nil {
		t.Error("result should be nil for server-initiated request")
	}
	var p PermissionParams
	if err := json.Unmarshal(resp.Params, &p); err != nil {
		t.Fatalf("unmarshal params: %v", err)
	}
	if p.ToolName != "bash" {
		t.Errorf("tool_name = %q, want bash", p.ToolName)
	}
	if p.Reason != "destructive" {
		t.Errorf("reason = %q, want destructive", p.Reason)
	}
}

// TestNewSendMessage verifies the Request constructor produces wire-compatible JSON.
func TestNewSendMessage(t *testing.T) {
	req := NewSendMessage("r1", "hello")
	if req.Jsonrpc != "2.0" {
		t.Errorf("jsonrpc = %q, want 2.0", req.Jsonrpc)
	}
	if req.Method != RequestMethodSendMessage {
		t.Errorf("method = %q, want %q", req.Method, RequestMethodSendMessage)
	}
	if req.ID != "r1" {
		t.Errorf("id = %q, want r1", req.ID)
	}
	if req.Params["text"] != "hello" {
		t.Errorf("params[text] = %v, want hello", req.Params["text"])
	}

	// Round-trip: marshal then unmarshal as the Python side would see it.
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got["jsonrpc"] != "2.0" {
		t.Errorf("wire jsonrpc = %v, want 2.0", got["jsonrpc"])
	}
	if got["method"] != "request/send_message" {
		t.Errorf("wire method = %v, want request/send_message", got["method"])
	}
}

// TestAllEventConstantsMatchPython ensures the Go constants stay in sync
// with loom/rpc/protocol.py EVENT_TYPES. If you add an event type in
// Python, this test will catch the missing Go constant once you add the
// corresponding case here.
func TestAllEventConstantsMatchPython(t *testing.T) {
	// These must match loom/rpc/protocol.py EVENT_TYPES exactly.
	want := []string{
		EventAssistantTurnStart,
		EventAssistantTurnEnd,
		EventTextDelta,
		EventThinkingDelta,
		EventToolUseStarted,
		EventToolUseCompleted,
		EventCompactOccurred,
		EventTodoUpdate,
		EventSubagentStart,
		EventSubagentEnd,
		EventShowNotification,
		EventSessionStarted,
		EventSessionEnded,
		EventError,
	}
	seen := make(map[string]bool)
	for _, m := range want {
		if seen[m] {
			t.Errorf("duplicate event constant: %s", m)
		}
		seen[m] = true
		if m[:6] != "event/" {
			t.Errorf("event constant %q does not start with 'event/'", m)
		}
	}
	if len(want) != 14 {
		t.Errorf("got %d event constants, want 14 (update this test if you added one)", len(want))
	}
}
