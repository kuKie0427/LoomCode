package rpc

import (
	"bufio"
	"encoding/json"
	"io"
	"net"
	"testing"
	"time"

	"github.com/lanf/loom-tui/protocol"
)

// fakeServer simulates the Python server side for client tests. It reads
// Requests from its stdin (a pipe we hold the write-end of) and writes
// canned Events / Responses to its stdout (a pipe the Client reads from).
type fakeServer struct {
	t       *testing.T
	reqCh   chan protocol.Request
	writeCh chan<- string
}

func startFakeServer(t *testing.T) (*Client, *fakeServer, func()) {
	t.Helper()
	// Two pipes: client -> server (reqs), server -> client (events/resps)
	serverRead, clientWrite := net.Pipe()
	clientRead, serverWrite := net.Pipe()

	srv := &fakeServer{
		t:       t,
		reqCh:   make(chan protocol.Request, 8),
		writeCh: nil,
	}
	// Reader goroutine: parse requests from client -> reqCh
	go func() {
		scanner := bufio.NewScanner(serverRead)
		scanner.Buffer(make([]byte, 0, 1024*1024), 10*1024*1024)
		for scanner.Scan() {
			line := scanner.Bytes()
			if len(line) == 0 {
				continue
			}
			var req protocol.Request
			if err := json.Unmarshal(line, &req); err != nil {
				t.Errorf("fake server: parse request: %v", err)
				return
			}
			srv.reqCh <- req
		}
		close(srv.reqCh)
	}()
	// Writer goroutine: srv.writeCh -> serverWrite
	writeCh := make(chan string, 8)
	srv.writeCh = writeCh
	go func() {
		for line := range writeCh {
			if _, err := io.WriteString(serverWrite, line+"\n"); err != nil {
				return
			}
		}
		serverWrite.Close()
	}()

	// Wrap the net.Pipe in something the Client expects (io.WriteCloser / io.Reader).
	// net.Pipe's read-side is io.Reader; the write-side is io.WriteCloser.
	client := NewClient(pipeWriteCloser{clientWrite}, clientRead)

	cleanup := func() {
		close(writeCh)
		clientWrite.Close()
		serverRead.Close()
		clientRead.Close()
	}
	return client, srv, cleanup
}

// pipeWriteCloser adapts net.Conn to io.WriteCloser.
type pipeWriteCloser struct{ conn net.Conn }

func (p pipeWriteCloser) Write(b []byte) (int, error) { return p.conn.Write(b) }
func (p pipeWriteCloser) Close() error                { return p.conn.Close() }

// emit writes a JSON line from the "server" to the client.
func (s *fakeServer) emit(msg any) {
	data, err := json.Marshal(msg)
	if err != nil {
		s.t.Fatalf("fake server: marshal: %v", err)
	}
	s.writeCh <- string(data)
}

func (s *fakeServer) nextRequest(t *testing.T, timeout time.Duration) protocol.Request {
	t.Helper()
	select {
	case req, ok := <-s.reqCh:
		if !ok {
			t.Fatal("fake server: request channel closed")
		}
		return req
	case <-time.After(timeout):
		t.Fatal("fake server: no request received in time")
		return protocol.Request{}
	}
}

// TestClientSendAndReceive verifies the round-trip: Client.Send() writes a
// Request, fake server sees it, replies with Response, Client.Send()
// returns the response.
func TestClientSendAndReceive(t *testing.T) {
	client, srv, cleanup := startFakeServer(t)
	defer cleanup()

	// Drain the session_started-style event from the channel so it doesn't
	// fill up. (The Client starts a read loop immediately.)
	// We'll test events separately.

	// Send a request via a goroutine; the server side emits a response.
	go func() {
		req := srv.nextRequest(t, 2*time.Second)
		if req.Method != protocol.RequestMethodSendMessage {
			t.Errorf("got method %q, want %q", req.Method, protocol.RequestMethodSendMessage)
		}
		// Reply with Response.ok
		ackJSON, _ := json.Marshal(map[string]any{"ack": true})
		srv.emit(struct {
			Jsonrpc string          `json:"jsonrpc"`
			ID      string          `json:"id"`
			Result  json.RawMessage `json:"result"`
		}{
			Jsonrpc: "2.0",
			ID:      req.ID,
			Result:  ackJSON,
		})
	}()

	resp, err := client.Send(protocol.NewSendMessage("", "hello"), 2*time.Second)
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	if resp.ID == "" {
		t.Error("response ID is empty")
	}
	if resp.Result == nil {
		t.Fatal("response Result is nil")
	}
}

// TestClientEventStream verifies that streamed events are delivered to the
// Events channel.
func TestClientEventStream(t *testing.T) {
	client, srv, cleanup := startFakeServer(t)
	defer cleanup()

	// Emit a text_delta event
	srv.emit(struct {
		Jsonrpc string          `json:"jsonrpc"`
		Method  string          `json:"method"`
		Params  json.RawMessage `json:"params"`
	}{
		Jsonrpc: "2.0",
		Method:  protocol.EventTextDelta,
		Params:  json.RawMessage(`{"text":"hi"}`),
	})

	select {
	case ev := <-client.Events():
		if ev.Method != protocol.EventTextDelta {
			t.Errorf("got method %q, want %q", ev.Method, protocol.EventTextDelta)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("did not receive event in time")
	}
}

// TestClientServerInitiatedPermission verifies that a server-initiated
// request/permission message (with id + method) is dispatched to the
// Events channel, NOT matched to a pending Send().
func TestClientServerInitiatedPermission(t *testing.T) {
	client, srv, cleanup := startFakeServer(t)
	defer cleanup()

	// Server emits a permission prompt
	srv.emit(struct {
		Jsonrpc string          `json:"jsonrpc"`
		ID      string          `json:"id"`
		Method  string          `json:"method"`
		Params  json.RawMessage `json:"params"`
	}{
		Jsonrpc: "2.0",
		ID:      "p1",
		Method:  "request/permission",
		Params:  json.RawMessage(`{"tool_name":"bash","reason":"destructive"}`),
	})

	select {
	case ev := <-client.Events():
		if ev.Method != "request/permission" {
			t.Errorf("got method %q, want request/permission", ev.Method)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("did not receive permission prompt as event")
	}
}
