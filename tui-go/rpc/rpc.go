// Package rpc manages the JSON-RPC connection to the Python loom server.
//
// The Python server (loom rpc) reads JSON Lines from its stdin and writes
// JSON Lines to its stdout. This client wraps the child process's stdin
// (for sending Requests) and stdout (for receiving Events + Responses).
//
// Dispatch rules:
//   - Messages with method "event/*" -> Events channel (fire-and-forget)
//   - Messages with id + (result|error) -> matched to pending Send() caller
//   - Messages with method "request/*" + id (server-initiated, e.g.
//     permission prompts) -> also sent to Events channel so the UI can
//     prompt the user and call SendPermissionResponse() in reply.
package rpc

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/lanf/loom-tui/protocol"
)

// Client is a JSON-RPC client that talks to the Python loom server.
type Client struct {
	stdin  io.WriteCloser
	stdout io.Reader
	mu     sync.Mutex // guards stdin writes

	// pending maps request ID -> channel that receives the Response.
	pending   map[string]chan protocol.Response
	pendingMu sync.Mutex

	// eventCh carries both streamed Events and server-initiated requests
	// (which the UI must reply to). Buffered to absorb bursts.
	eventCh chan protocol.Event

	// done is closed when the read loop exits (stdout EOF).
	done chan struct{}
}

// NewClient constructs a Client and starts the background read loop.
func NewClient(stdin io.WriteCloser, stdout io.Reader) *Client {
	c := &Client{
		stdin:   stdin,
		stdout:  stdout,
		pending: make(map[string]chan protocol.Response),
		eventCh: make(chan protocol.Event, 1000),
		done:    make(chan struct{}),
	}
	go c.readLoop()
	return c
}

// Events returns a read-only channel of streamed events + server-initiated
// requests. Callers must drain this channel; if it fills up, messages are
// dropped (logged) rather than blocking the read loop.
func (c *Client) Events() <-chan protocol.Event {
	return c.eventCh
}

// Done returns a channel that is closed when the Python server's stdout
// reaches EOF (server exited). Useful for triggering a tea.Quit.
func (c *Client) Done() <-chan struct{} {
	return c.done
}

// Send writes a request and waits for the response (with timeout).
// If req.ID is empty, a fresh UUID-derived ID is assigned.
func (c *Client) Send(req protocol.Request, timeout time.Duration) (protocol.Response, error) {
	if req.ID == "" {
		req.ID = uuid.NewString()[:8]
	}
	respCh := make(chan protocol.Response, 1)
	c.pendingMu.Lock()
	c.pending[req.ID] = respCh
	c.pendingMu.Unlock()
	defer func() {
		c.pendingMu.Lock()
		delete(c.pending, req.ID)
		c.pendingMu.Unlock()
	}()

	if err := c.write(req); err != nil {
		return protocol.Response{}, err
	}

	select {
	case resp := <-respCh:
		return resp, nil
	case <-time.After(timeout):
		return protocol.Response{}, fmt.Errorf("request %s timed out after %s", req.ID, timeout)
	}
}

// SendNoWait writes a request without waiting for a response (fire-and-forget).
func (c *Client) SendNoWait(req protocol.Request) error {
	if req.ID == "" {
		req.ID = uuid.NewString()[:8]
	}
	return c.write(req)
}

func (c *Client) write(msg any) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	c.mu.Lock()
	defer c.mu.Unlock()
	_, err = c.stdin.Write(data)
	return err
}

// readLoop parses one JSON Line per iteration and dispatches.
//
// Dispatch order matters: a message with both "id" and "method" is a
// server-initiated request (permission prompt) — those are forwarded to
// the Events channel, NOT matched to a pending Send() caller. Only
// messages with "id" + ("result" or "error") are Response objects.
func (c *Client) readLoop() {
	defer close(c.done)
	defer close(c.eventCh)
	scanner := bufio.NewScanner(c.stdout)
	// Increase buffer size — tool results can be large.
	scanner.Buffer(make([]byte, 0, 1024*1024), 10*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		// First try as Response: id + (result | error | method).
		var resp protocol.Response
		if err := json.Unmarshal(line, &resp); err == nil && resp.ID != "" {
			if resp.Method != "" {
				// Server-initiated request (e.g. request/permission).
				// Forward to Events channel so UI can prompt + reply.
				// Carry the request ID so the UI can reference it in the
				// permission_response reply.
				c.dispatchEvent(protocol.Event{
					Jsonrpc: "2.0",
					Method:  resp.Method,
					Params:  resp.Params,
					ID:      resp.ID,
				})
				continue
			}
			if resp.Result != nil || resp.Error != nil {
				// It's a Response — match to a pending Send() caller.
				c.pendingMu.Lock()
				ch, ok := c.pending[resp.ID]
				c.pendingMu.Unlock()
				if ok {
					select {
					case ch <- resp:
					default:
						log.Printf("rpc: dropped response for %s (channel full)", resp.ID)
					}
					continue
				}
				// No pending caller — fall through to log.
				log.Printf("rpc: response with no pending caller id=%s", resp.ID)
				continue
			}
		}
		// Otherwise it's a streamed Event.
		var ev protocol.Event
		if err := json.Unmarshal(line, &ev); err != nil {
			log.Printf("rpc: failed to parse line: %v", err)
			continue
		}
		c.dispatchEvent(ev)
	}
	if err := scanner.Err(); err != nil {
		log.Printf("rpc: scanner error: %v", err)
	}
}

func (c *Client) dispatchEvent(ev protocol.Event) {
	select {
	case c.eventCh <- ev:
	default:
		log.Printf("rpc: dropped event %s (channel full)", ev.Method)
	}
}

// Close closes the stdin pipe to the Python server, signalling shutdown.
func (c *Client) Close() error {
	return c.stdin.Close()
}
