// Package main: slash command dispatcher.
//
// Mirrors loom/tui/slash_commands.py::run_slash_command — when the user
// submits text starting with "/", the TUI executes the command locally
// instead of forwarding it to the agent as a user prompt.
//
// Commands handled purely client-side (clear/quit/help) execute
// synchronously. Commands that need server state (sessions/new/model/...)
// fire the corresponding RPC and let applyEvent handle the response.
package main

import (
	"encoding/json"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/lanf/loom-tui/protocol"
	"github.com/lanf/loom-tui/rpc"
)

// dispatchSlashCommand parses "/cmd args..." and dispatches locally.
// Returns (cmd, handled): when handled=true the caller MUST NOT append
// the text as a user message or send it to the agent via sendMessage.
//
// Mirrors AgentTUIApp.run_slash_command (app.py:959-974) + the per-command
// handlers in slash_commands.py.
func (m model) dispatchSlashCommand(text string) (tea.Cmd, bool) {
	cmdLine := strings.TrimPrefix(text, "/")
	parts := strings.SplitN(cmdLine, " ", 2)
	if len(parts) == 0 || parts[0] == "" {
		// Just "/" alone — no-op.
		return nil, true
	}
	cmd := strings.ToLower(parts[0])
	args := ""
	if len(parts) > 1 {
		args = strings.TrimSpace(parts[1])
	}

	switch cmd {
	case "clear":
		// handle_clear: clear chat log + reset streaming state.
		m.chatLog.Clear()
		m.streaming = false
		m.syncStreaming()
		m.statusBar.SetTurns(0)
		return nil, true

	case "quit", "q", "exit":
		// handle_quit: exit the app.
		m.quitting = true
		return tea.Quit, true

	case "help":
		// handle_help: list available commands.
		m.chatLog.AppendSystemLine(
			"**Commands:** /help, /clear, /init, /model <name>, " +
				"/connect, /thinking, /resume [id], /sessions, " +
				"/new, /status, /quit",
		)
		return nil, true

	case "sessions":
		// handle_sessions: list saved sessions in chat.
		m.chatLog.AppendSystemLine("*Loading sessions…*")
		return requestSessions(m.client), true

	case "new":
		// handle_new: start a fresh session via RPC.
		return requestNewSession(m.client), true

	case "model":
		// handle_model: /model <name> switches model via RPC.
		// No arg → not implemented (picker requires a modal we don't have yet).
		if args == "" {
			m.chatLog.AppendSystemLine("**Usage:** /model <provider/model>  (e.g. /model deepseek/deepseek-v4-flash)")
			return nil, true
		}
		return requestPickModel(m.client, args), true

	case "status":
		// handle_status: show session info. Server-side state is limited
		// over RPC; show what we know locally.
		m.chatLog.AppendSystemLine(fmt.Sprintf(
			"**Session Status**\n- Model: `%s`\n- Turns: %d",
			m.model, m.statusBar.PeekTurns(),
		))
		return nil, true

	case "thinking", "think", "thing":
		// handle_thinking: not yet wired to server-side provider options.
		m.chatLog.AppendSystemLine(
			"**/thinking** is not yet wired to the server. " +
				"Use `--loom-args` or environment to configure thinking mode.",
		)
		return nil, true

	case "init", "connect", "resume":
		// These require server-side Python execution (init_cmd, credential
		// flow, checkpoint load) that isn't exposed over RPC yet.
		m.chatLog.AppendSystemLine(fmt.Sprintf(
			"**/%s** is not yet available in the Go TUI. Run `loom cli` directly or use the Python TUI.",
			cmd,
		))
		return nil, true

	default:
		// find_command returns None → "Unknown command".
		m.chatLog.AppendSystemLine(fmt.Sprintf(
			"Unknown command: **/%s**. Try /help.", cmd,
		))
		return nil, true
	}
}

// requestNewSession fires a request/new_session RPC. Mirrors the Python
// server's _handle_new_session (rpc/server.py:410).
func requestNewSession(client *rpc.Client) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.Send(protocol.NewNewSession("new"), sendTimeout)
		if err != nil {
			return newSessionMsg{sessionID: "", err: err}
		}
		var res struct {
			SessionID string `json:"session_id"`
		}
		_ = json.Unmarshal(resp.Result, &res)
		return newSessionMsg{sessionID: res.SessionID}
	}
}

// requestPickModel fires a request/pick_model RPC. Mirrors the Python
// server's _handle_pick_model (server.py:362).
func requestPickModel(client *rpc.Client, model string) tea.Cmd {
	return func() tea.Msg {
		_, err := client.Send(protocol.NewPickModel("model", model), sendTimeout)
		return modelPickedMsg{model: model, err: err}
	}
}

// newSessionMsg is emitted when request/new_session completes.
type newSessionMsg struct {
	sessionID string
	err       error
}

// modelPickedMsg is emitted when request/pick_model completes.
type modelPickedMsg struct {
	model string
	err   error
}
