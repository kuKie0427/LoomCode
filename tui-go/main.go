// Command loom-tui is a Bubble Tea frontend for the loom coding agent.
//
// It spawns `python -m loom.cli serve` as a child process and exchanges
// JSON-RPC messages over its stdin/stdout. See loom/rpc/protocol.py for
// the wire format and docs/superpowers/plans/2026-06-27-go-tui-migration.md
// for the migration plan.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lanf/loom-tui/protocol"
	"github.com/lanf/loom-tui/rpc"
	"github.com/lanf/loom-tui/view"
)

const (
	// sendTimeout is how long we wait for a Response to request/send_message.
	// The agent may take a while to start streaming, but the ack comes back
	// immediately, so 5s is plenty.
	sendTimeout = 5 * time.Second

	// composerHeight reserves screen rows for the composer + status bar +
	// gutters. The chatlog gets the remainder.
	composerHeight = 4
	statusHeight   = 1
)

func main() {
	var (
		workdir   = flag.String("workdir", ".", "Working directory passed to `loom cli serve`")
		pythonCmd = flag.String("python", "python", "Python interpreter to use")
		loomArgs  = flag.String("loom-args", "", "Extra args to pass to `loom cli serve` (e.g. --model X)")
	)
	flag.Parse()

	// Build the python -m loom.cli serve command
	args := []string{"-u", "-m", "loom.cli", "serve", "--workdir", *workdir}
	if *loomArgs != "" {
		args = append(args, strings.Fields(*loomArgs)...)
	}
	cmd := exec.Command(*pythonCmd, args...)
	cmd.Stderr = os.Stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		log.Fatalf("stdin pipe: %v", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		log.Fatalf("stdout pipe: %v", err)
	}
	if err := cmd.Start(); err != nil {
		log.Fatalf("failed to start loom server (did you `pip install -e .`?): %v", err)
	}
	defer func() {
		_ = stdin.Close()
		_ = cmd.Wait()
	}()

	client := rpc.NewClient(stdin, stdout)

	p := tea.NewProgram(initialModel(client), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		log.Fatalf("tui error: %v", err)
	}
	// Best-effort graceful shutdown
	_ = client.SendNoWait(protocol.NewShutdown("exit"))
}

// ---------------------------------------------------------------------------
// Bubble Tea model
// ---------------------------------------------------------------------------

type model struct {
	client     *rpc.Client
	chatLog    *view.ChatLog
	composer   *view.Composer
	statusBar  *view.StatusBar
	permission *view.PermissionModal

	width     int
	height    int
	ready     bool // received event/session_started
	streaming bool // between assistant_turn_start and assistant_turn_end
	quitting  bool
	errMsg    string
}

func initialModel(client *rpc.Client) model {
	cl := view.NewChatLog(80, 20)
	comp := view.NewComposer()
	sb := view.NewStatusBar()
	pm := view.NewPermissionModal()
	return model{
		client:     client,
		chatLog:    cl,
		composer:   comp,
		statusBar:  sb,
		permission: pm,
	}
}

func (m model) Init() tea.Cmd {
	return listenForEvents(m.client)
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		// When the permission modal is visible, it captures all keys:
		// Y = allow, N = deny, Esc = cancel (hide modal, no reply sent).
		// Other keys are swallowed so the composer doesn't get them.
		if m.permission.Visible() {
			switch msg.Type {
			case tea.KeyEsc:
				m.permission.Hide()
				return m, nil
			default:
				switch msg.String() {
				case "y", "Y":
					reqID := m.permission.RequestID()
					m.permission.Hide()
					return m, sendPermissionResponse(m.client, reqID, "allow")
				case "n", "N":
					reqID := m.permission.RequestID()
					m.permission.Hide()
					return m, sendPermissionResponse(m.client, reqID, "deny")
				}
				return m, nil
			}
		}
		switch msg.Type {
		case tea.KeyCtrlC, tea.KeyEsc:
			m.quitting = true
			return m, tea.Quit
		case tea.KeyEnter:
			text, sent := m.composer.HandleKey(msg)
			if sent {
				m.streaming = true
				m.chatLog.AppendUserMessage(text)
				return m, sendMessage(m.client, text)
			}
			return m, nil
		}
		// Delegate other keys to the composer's textarea (blink, typing, etc.)
		cmd := m.composer.Update(msg)
		return m, cmd

	case eventMsg:
		mm, c := m.applyEvent(msg.event)
		m = mm
		return m, tea.Batch(c, listenForEvents(m.client))

	case sentMsg:
		// request/send_message was ack'd; nothing else to do.
		return m, nil

	case serverExitMsg:
		m.quitting = true
		m.errMsg = "loom server exited"
		return m, tea.Quit

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.composer.SetWidth(msg.Width)
		chatHeight := msg.Height - composerHeight - statusHeight
		if chatHeight < 1 {
			chatHeight = 1
		}
		m.chatLog.SetSize(msg.Width, chatHeight)
		return m, nil

	case tea.MouseMsg:
		// Forward mouse-wheel events to the chatlog viewport.
		_, cmd := m.chatLog.Update(msg)
		return m, cmd
	}
	// Forward other messages (textarea blink etc.) to the composer.
	cmd := m.composer.Update(msg)
	return m, cmd
}

func (m model) applyEvent(ev protocol.Event) (model, tea.Cmd) {
	switch ev.Method {
	case protocol.EventSessionStarted:
		m.ready = true
		m.statusBar.SetReady(true)
		var p struct {
			Model string `json:"model"`
		}
		_ = json.Unmarshal(ev.Params, &p)
		if p.Model != "" {
			m.statusBar.SetModel(p.Model)
		}
	case protocol.EventAssistantTurnStart:
		m.streaming = true
		m.chatLog.StartAssistantTurn()
	case protocol.EventAssistantTurnEnd:
		m.streaming = false
	case protocol.EventTextDelta:
		var p protocol.TextDeltaParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.AppendTextDelta(p.Text)
		}
	case protocol.EventThinkingDelta:
		var p protocol.ThinkingDeltaParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.AppendThinkingDelta(p.Text)
		}
	case protocol.EventToolUseStarted:
		var p protocol.ToolUseStartedParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.StartToolCall(p.ToolName, p.ToolInput, p.ToolUseID)
		}
	case protocol.EventToolUseCompleted:
		var p protocol.ToolUseCompletedParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.CompleteToolCall(p.ToolUseID, p.Output, p.IsError)
		}
	case protocol.EventError:
		var p struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(ev.Params, &p)
		m.errMsg = p.Message
	case protocol.ServerRequestMethodPermission:
		// Server-initiated permission prompt: ev.ID is the request id we
		// must reference in the permission_response reply.
		var p protocol.PermissionParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.permission.Show(ev.ID, p.ToolName, p.ToolInput, p.Reason)
		}
	}
	return m, nil
}

func (m model) View() string {
	if m.quitting {
		return ""
	}
	if !m.ready {
		return "Connecting to loom...\n"
	}
	var b strings.Builder
	title := lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("63")).Render("loom")
	b.WriteString(title + "\n")
	b.WriteString(m.chatLog.View())
	if m.streaming {
		b.WriteString("▎\n")
	}
	if m.errMsg != "" {
		b.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color("203")).Render("⚠ "+m.errMsg) + "\n")
	}
	b.WriteString("\n" + m.composer.View() + "\n")
	b.WriteString(m.statusBar.View())
	if m.permission.Visible() {
		b.WriteString("\n" + m.permission.View())
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// tea.Msg types + cmd builders
// ---------------------------------------------------------------------------

type eventMsg struct{ event protocol.Event }
type sentMsg struct{}
type serverExitMsg struct{}

func listenForEvents(client *rpc.Client) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-client.Events()
		if !ok {
			return serverExitMsg{}
		}
		return eventMsg{event: ev}
	}
}

func sendMessage(client *rpc.Client, text string) tea.Cmd {
	return func() tea.Msg {
		_, err := client.Send(protocol.NewSendMessage("", text), sendTimeout)
		if err != nil {
			return sentMsg{} // swallow — agent_loop will stream events or error event
		}
		return sentMsg{}
	}
}

// sendPermissionResponse replies to a server-initiated permission prompt.
// requestID is the server-initiated request id (from ev.ID), decision is
// "allow" or "deny". We use SendNoWait: the server does not send a Response
// to the permission_response (the original request/permission will be
// resolved server-side from this reply).
func sendPermissionResponse(client *rpc.Client, requestID, decision string) tea.Cmd {
	return func() tea.Msg {
		_ = client.SendNoWait(protocol.NewPermissionResponse("", requestID, decision))
		return sentMsg{}
	}
}

// unused import guard (format is used in error messages above)
var _ = fmt.Sprintf
