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

	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lanf/loom-tui/protocol"
	"github.com/lanf/loom-tui/rpc"
)

const (
	// sendTimeout is how long we wait for a Response to request/send_message.
	// The agent may take a while to start streaming, but the ack comes back
	// immediately, so 5s is plenty.
	sendTimeout = 5 * time.Second
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
	client    *rpc.Client
	messages  []string // accumulated assistant text per turn
	input     textarea.Model
	ready     bool // received event/session_started
	streaming bool // between assistant_turn_start and assistant_turn_end
	quitting  bool
	errMsg    string
}

func initialModel(client *rpc.Client) model {
	ta := textarea.New()
	ta.Placeholder = "Send a message... (Enter to send, Shift+Enter for newline, Ctrl+C to quit)"
	ta.Focus()
	ta.CharLimit = 0
	return model{client: client, input: ta}
}

func (m model) Init() tea.Cmd {
	return tea.Batch(listenForEvents(m.client), textarea.Blink)
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyCtrlC, tea.KeyEsc:
			m.quitting = true
			return m, tea.Quit
		case tea.KeyEnter:
			// Plain Enter = send (Shift+Enter = newline is handled by
			// textarea internally via its "shift+enter" key binding, so we
			// only intercept plain Enter here).
			text := strings.TrimRight(m.input.Value(), "\n")
			if text != "" {
				m.input.Reset()
				m.streaming = true
				return m, sendMessage(m.client, text)
			}
			return m, nil
		}
		// Delegate other keys to the textarea.
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd

	case eventMsg:
		var cmd tea.Cmd
		mm, c := m.applyEvent(msg.event)
		m = mm
		cmd = c
		// Keep listening after each event.
		return m, tea.Batch(cmd, listenForEvents(m.client))

	case sentMsg:
		// request/send_message was ack'd; nothing else to do.
		return m, nil

	case serverExitMsg:
		m.quitting = true
		m.errMsg = "loom server exited"
		return m, tea.Quit

	case tea.WindowSizeMsg:
		// Resize the textarea to fit.
		m.input.SetWidth(msg.Width)
		return m, nil
	}
	// Forward to textarea for blinking etc.
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	return m, cmd
}

func (m model) applyEvent(ev protocol.Event) (model, tea.Cmd) {
	switch ev.Method {
	case protocol.EventSessionStarted:
		m.ready = true
	case protocol.EventAssistantTurnStart:
		m.streaming = true
		m.messages = append(m.messages, "")
	case protocol.EventAssistantTurnEnd:
		m.streaming = false
	case protocol.EventTextDelta:
		var p protocol.TextDeltaParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			if len(m.messages) == 0 {
				m.messages = append(m.messages, "")
			}
			m.messages[len(m.messages)-1] += p.Text
		}
	case protocol.EventError:
		var p struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(ev.Params, &p)
		m.errMsg = p.Message
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
	b.WriteString(title + "\n\n")
	for _, msg := range m.messages {
		if msg != "" {
			b.WriteString(msg)
			b.WriteString("\n\n")
		}
	}
	if m.streaming {
		b.WriteString("▎\n")
	}
	if m.errMsg != "" {
		b.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color("203")).Render("⚠ "+m.errMsg) + "\n\n")
	}
	b.WriteString(m.input.View())
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

// unused import guard (format is used in error messages above)
var _ = fmt.Sprintf
