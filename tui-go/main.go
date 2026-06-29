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
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
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

	// Layout reserves screen rows for: brand bar (1) + hairline (1) +
	// [Header (1, only when MCP configured)] +
	// chatlog (remainder) + [InfoBar (1, only when todos/subagents)] +
	// composer (4 = textarea 2 + hint 1 + border) + status bar (1).
	brandHeight    = 1
	composerHeight = 4
	statusHeight   = 1
)

func main() {
	var (
		workdir   = flag.String("workdir", ".", "Working directory passed to `loom cli serve`")
		pythonCmd = flag.String("python", "python", "Python interpreter to use")
		loomArgs  = flag.String("loom-args", "", "Extra args to pass to `loom cli serve` (e.g. --model X)")
		logFile   = flag.String("log-file", "loom-tui.log", "Where to write the Python server's stderr (loguru output). Empty disables logging.")
	)
	flag.Parse()

	// Build the python -m loom.cli serve command
	args := []string{"-u", "-m", "loom.cli", "serve", "--workdir", *workdir}
	if *loomArgs != "" {
		args = append(args, strings.Fields(*loomArgs)...)
	}
	cmd := exec.Command(*pythonCmd, args...)

	// The Python server emits loguru logs on stderr (loom/rpc/server.py
	// _configure_server_logging rewrites the sink to stderr to keep stdout
	// clean for the JSON-RPC wire format). We MUST NOT inherit our own
	// stderr for the child: under Bubble Tea's AltScreen our stderr is the
	// TTY, so child loguru output would interleave with the TUI and corrupt
	// the layout. Redirect it to a file instead.
	var logSink io.Writer = io.Discard
	if *logFile != "" {
		f, err := os.OpenFile(*logFile, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
		if err == nil {
			logSink = f
			defer func() { _ = f.Close() }()
		}
	}
	cmd.Stderr = logSink

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

	// Resolve workdir to an absolute path so the brand bar + status bar
	// show a consistent, ~-prefixed path regardless of where loom-tui was
	// launched from.
	absWorkdir, err := filepath.Abs(*workdir)
	if err != nil {
		absWorkdir = *workdir
	}

	p := tea.NewProgram(initialModel(client, absWorkdir), tea.WithAltScreen(), tea.WithMouseCellMotion())
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
	header     *view.Header
	infoBar    *view.InfoBar
	completer  *view.CommandCompleter

	width     int
	height    int
	workdir   string
	model     string // current model name, from EventSessionStarted
	ready     bool   // received event/session_started
	streaming bool   // between assistant_turn_start and assistant_turn_end
	quitting  bool
	errMsg    string
}

func initialModel(client *rpc.Client, workdir string) model {
	cl := view.NewChatLog(80, 20)
	comp := view.NewComposer()
	sb := view.NewStatusBar()
	pm := view.NewPermissionModal()
	hd := view.NewHeader()
	ib := view.NewInfoBar()
	cc := view.NewCommandCompleter()
	sb.SetWorkdir(workdir)
	sb.SetBranch(currentGitBranch(workdir))
	return model{
		client:     client,
		chatLog:    cl,
		composer:   comp,
		statusBar:  sb,
		permission: pm,
		header:     hd,
		infoBar:    ib,
		completer:  cc,
		workdir:    workdir,
	}
}

func (m model) Init() tea.Cmd {
	return tea.Batch(
		listenForEvents(m.client),
		tickClock(),
		m.composer.ConfigureCmd(),
	)
}

// tickClock emits a tickMsg once per second to refresh the status bar clock.
type tickMsg struct{}

func tickClock() tea.Cmd {
	return tea.Tick(time.Second, func(time.Time) tea.Msg { return tickMsg{} })
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		// When the permission modal is visible, it captures all keys:
		// a = allow once, A = allow always, d/Esc = deny.
		// Other keys are swallowed so the composer doesn't get them.
		// Mirrors loom/tui/screens.py::PermissionScreen BINDINGS.
		if m.permission.Visible() {
			reqID := m.permission.RequestID()
			switch msg.String() {
			case "a":
				m.permission.Hide()
				return m, sendPermissionResponse(m.client, reqID, "allow")
			case "A":
				m.permission.Hide()
				return m, sendPermissionResponse(m.client, reqID, "allow_always")
			case "d", "esc":
				m.permission.Hide()
				return m, sendPermissionResponse(m.client, reqID, "deny")
			}
			return m, nil
		}

		// When the CommandCompleter popup is visible, intercept navigation
		// keys (Up/Down/Tab/Esc/Enter) before they reach the composer.
		// Character keys fall through to the composer, then we refresh
		// the filter so the popup tracks what the user typed.
		if m.completer.Visible() {
			switch msg.Type {
			case tea.KeyUp:
				m.completer.Move(-1)
				return m, nil
			case tea.KeyDown:
				m.completer.Move(1)
				return m, nil
			case tea.KeyTab:
				// Complete: replace composer text with "/<name> " (trailing
				// space) so the user can type args, then hide the popup.
				if sel := m.completer.Selected(); sel != nil {
					m.composer.SetValue("/" + sel.Name + " ")
				}
				m.completer.Hide()
				return m, nil
			case tea.KeyEsc:
				m.completer.Hide()
				return m, nil
			case tea.KeyCtrlC:
				m.quitting = true
				return m, tea.Quit
			case tea.KeyEnter:
				// Execute the highlighted command directly — one-step run.
				// Dispatched locally via dispatchSlashCommand (mirrors the
				// original TUI's run_slash_command), NOT sent to the agent.
				if sel := m.completer.Selected(); sel != nil {
					text := "/" + sel.Name
					m.composer.Reset()
					m.completer.Hide()
					cmd, _ := m.dispatchSlashCommand(text)
					return m, cmd
				}
				m.completer.Hide()
				return m, nil
			}
			// Character keys (including "/") fall through to the composer.
			// After the composer processes the key, we refresh the filter
			// so the popup tracks the new text.
			cmd := m.composer.Update(msg)
			m.refreshCompleter()
			return m, cmd
		}

		switch msg.Type {
		case tea.KeyCtrlC, tea.KeyEsc:
			m.quitting = true
			return m, tea.Quit
		}

		// Chat history scrolling: PageUp/PageDown/Home/End, plus ↑/↓ when
		// the composer contains a single line (empty or no newlines). When
		// the user is editing multi-line text, ↑/↓ stay in the textarea for
		// cursor movement. Mouse wheel also scrolls (handled in tea.MouseMsg).
		switch msg.Type {
		case tea.KeyPgUp:
			m.chatLog.ScrollUp(m.chatLog.ViewportHeight() / 2)
			return m, nil
		case tea.KeyPgDown:
			m.chatLog.ScrollDown(m.chatLog.ViewportHeight() / 2)
			return m, nil
		case tea.KeyUp:
			if !strings.Contains(m.composer.Value(), "\n") {
				m.chatLog.ScrollUp(1)
				return m, nil
			}
		case tea.KeyDown:
			if !strings.Contains(m.composer.Value(), "\n") {
				m.chatLog.ScrollDown(1)
				return m, nil
			}
		case tea.KeyHome:
			m.chatLog.ScrollToTop()
			return m, nil
		case tea.KeyEnd:
			m.chatLog.ScrollToBottom()
			return m, nil
		}

		switch msg.String() {
		case "ctrl+t":
			// Toggle the ThinkingDisplay body expand/collapse.
			// Mirrors the original TUI's ThinkingMarker.on_click — the
			// spinner row is always visible; this expands the full body.
			// No-op when there's no thinking to show (ToggleThinking
			// returns false; we still consume the key to avoid inserting
			// a literal ^T into the composer).
			m.chatLog.ToggleThinking()
			return m, nil
		}

		switch msg.Type {
		case tea.KeyEnter:
			text, sent := m.composer.HandleKey(msg)
			if sent {
				// Slash commands execute locally — do NOT forward to agent.
				// Mirrors AgentTUIApp.on_composer_submitted (app.py:945-957):
				//   if user_msg.startswith("/"): run_slash_command(...)
				//   else: run_agent_turn(...)
				if strings.HasPrefix(text, "/") {
					cmd, _ := m.dispatchSlashCommand(text)
					return m, cmd
				}
				m.streaming = true
				m.syncStreaming()
				m.chatLog.AppendUserMessage(text)
				return m, sendMessage(m.client, text)
			}
			return m, nil
		}
		// Delegate other keys to the composer's textarea (blink, typing, etc.)
		cmd := m.composer.Update(msg)
		// After every keystroke, check if the text now starts with "/" —
		// if so, show the completer popup with the current filter.
		m.refreshCompleter()
		return m, cmd

	case eventMsg:
		mm, c := m.applyEvent(msg.event)
		m = mm
		return m, tea.Batch(c, listenForEvents(m.client))

	case sentMsg:
		// request/send_message or permission_response was ack'd; nothing to do.
		return m, nil

	case tickMsg:
		// Refresh the status bar clock + gear frame, advance the
		// ThinkingDisplay braille spinner, pulse the Header MCP
		// glyph (when in error), and advance InfoBar subagent
		// elapsed display + marquee scroll. All 1Hz.
		m.statusBar.Tick()
		m.chatLog.TickThinking()
		m.header.Tick()
		m.infoBar.Tick()
		return m, tickClock()

	case sessionsListedMsg:
		// /sessions command result — replace the "Loading sessions…" placeholder
		// or append if the placeholder is missing.
		m.chatLog.ReplaceLastSystemLine("*Loading sessions…*", formatSessionPanel(msg.sessions))
		return m, nil

	case newSessionMsg:
		// /new completed — clear chat + confirm (or report error).
		if msg.err != nil {
			m.chatLog.AppendSystemLine(fmt.Sprintf("*Failed to start new session: %v*", msg.err))
			return m, nil
		}
		m.chatLog.Clear()
		m.streaming = false
		m.syncStreaming()
		m.statusBar.SetTurns(0)
		m.chatLog.AppendSystemLine(fmt.Sprintf("*New session started: %s*", msg.sessionID))
		return m, nil

	case modelPickedMsg:
		// /model <name> completed — confirm in chat.
		if msg.err != nil {
			m.chatLog.AppendSystemLine(fmt.Sprintf("*Failed to switch model: %v*", msg.err))
		} else {
			m.statusBar.SetModel(msg.model)
			m.chatLog.AppendSystemLine(fmt.Sprintf("*Model changed to %s*", msg.model))
		}
		return m, nil

	case serverExitMsg:
		m.quitting = true
		m.errMsg = "loom server exited"
		return m, tea.Quit

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.relayout()
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

// relayout recomputes the per-widget widths + the chat viewport size
// after the screen resizes OR after any event that changes the Header
// or InfoBar content (which can grow/shrink their reserved rows). Must
// be called whenever m.width, m.height, m.header, or m.infoBar changes.
//
// Layout budget:
//
//	reserved = brand(1) + hairline(1) + composer(4) + status(1)
//	          + (header.Empty() ? 0 : 1)
//	          + infoBar.Height()
//	          + completer.Height()   (only when popup is visible)
//	chatHeight = height - reserved   (clamped to >=1)
//
// When InfoBar grows from 0→N rows mid-session (e.g. first subagent
// starts), without this call the chat viewport would keep its old
// (taller) height and push the composer off the bottom of the screen.
// The same applies to the slash-command completer popup above the composer.
func (m model) relayout() {
	m.composer.SetWidth(m.width)
	m.statusBar.SetWidth(m.width)
	m.completer.SetWidth(m.width)
	m.infoBar.SetWidth(m.width)
	// +2 covers brand(1) + hairline(1); do NOT add brandHeight again.
	reserved := composerHeight + statusHeight + 2
	if !m.header.Empty() {
		reserved++
	}
	reserved += m.infoBar.Height()
	reserved += m.completer.Height()
	chatHeight := m.height - reserved
	if chatHeight < 1 {
		chatHeight = 1
	}
	m.chatLog.SetSize(m.width, chatHeight)
}

// syncStreaming pushes the current streaming state into the composer and
// status bar so their borders/labels reflect the agent's busy state.
// Call after any change to m.streaming. Safe on a value receiver because
// composer/statusBar are pointer fields (*view.Composer / *view.StatusBar)
// — SetStreaming mutates the pointed-to widget, not the model value.
func (m model) syncStreaming() {
	m.composer.SetStreaming(m.streaming)
	m.statusBar.SetStreaming(m.streaming)
}

// refreshCompleter checks the composer's current text and shows or hides
// the slash-command popup. The popup shows when the text starts with "/"
// AND contains no space (matching composer.py:64,71). Any other input
// (empty, doesn't start with "/", or contains a space after the "/")
// hides the popup.
//
// Called after every keystroke that reaches the composer. Safe on a value
// receiver — completer is a *CommandCompleter so Show/Hide mutate the
// heap-allocated widget, not the model copy.
func (m model) refreshCompleter() {
	text := m.composer.Value()
	if strings.HasPrefix(text, "/") && !strings.Contains(text, " ") {
		m.completer.Show(text)
	} else {
		m.completer.Hide()
	}
	// The popup adds rows above the composer; relayout so the chat viewport
	// shrinks and the brand bar stays on screen.
	m.relayout()
}

func (m model) applyEvent(ev protocol.Event) (model, tea.Cmd) {
	switch ev.Method {
	case protocol.EventSessionStarted:
		m.ready = true
		m.statusBar.SetReady(true)
		m.statusBar.SetEngineState(view.StateIdle)
		var p protocol.SessionStartedParams
		if err := json.Unmarshal(ev.Params, &p); err == nil && p.Model != "" {
			m.model = p.Model
			m.statusBar.SetModel(p.Model)
			// Model name lives in the status bar, NOT the header.
			// MCP server status (if any) is populated via SetMCPServers
			// when the Python server exposes them; until then the MCP
			// tab stays hidden (no servers → mcpTotal=0 → hidden).
		}
	case protocol.EventAssistantTurnStart:
		m.streaming = true
		m.syncStreaming()
		m.statusBar.SetEngineState(view.StateStreaming)
		m.statusBar.IncTurns()
		m.chatLog.StartAssistantTurn()
	case protocol.EventAssistantMessageStart:
		// Per-LLM-call signal: reset the thinking buffer so round N+1's
		// reasoning doesn't append to round N's stale buffer. Critical
		// for DeepSeek thinking-mode where the final response may come
		// as reasoning_content in round 2+ (after tool_use returns).
		m.chatLog.StartNewLLMCall()
	case protocol.EventAssistantTurnEnd:
		m.streaming = false
		m.syncStreaming()
		m.statusBar.SetEngineState(view.StateIdle)
		m.chatLog.CompleteThinking()
		// DeepSeek thinking-mode fallback: if the model emitted its
		// response as reasoning_content (no text, no tools), promote
		// the thinking text to the assistant body so the user sees
		// the answer instead of an empty turn.
		m.chatLog.PromoteThinkingToContent()
		// AssistantSummary ▣ — model + duration, appended below the
		// final assistant message. Mirrors chat_log.py:1128-1130.
		var p protocol.AssistantTurnEndParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			modelName := m.model
			if modelName == "" {
				modelName = "unknown"
			}
			m.chatLog.AppendAssistantSummary(modelName, p.Duration)
		}
	case protocol.EventTextDelta:
		var p protocol.TextDeltaParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.AppendTextDelta(p.Text)
			// Streaming text implies we've moved past thinking.
			m.statusBar.SetEngineState(view.StateStreaming)
			m.estimateCtxTokens(p.Text)
		}
	case protocol.EventThinkingDelta:
		var p protocol.ThinkingDeltaParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.AppendThinkingDelta(p.Text)
			// Thinking deltas arrive before text deltas in a turn —
			// set the engine to "thinking" so the badge + gear frame
			// reflect the current phase.
			m.statusBar.SetEngineState(view.StateThinking)
			m.estimateCtxTokens(p.Text)
		}
	case protocol.EventToolUseStarted:
		var p protocol.ToolUseStartedParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.StartToolCall(p.ToolName, p.ToolInput, p.ToolUseID)
			// Tool execution phase — gear keeps spinning, badge switches
			// to ⊙ executing (mirrors on_tool_use → executing).
			m.statusBar.SetEngineState(view.StateExecuting)
			m.statusBar.IncToolCalls()
		}
	case protocol.EventToolUseCompleted:
		var p protocol.ToolUseCompletedParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.chatLog.CompleteToolCall(p.ToolUseID, p.Output, p.IsError)
			// After a tool call completes, the next LLM call may stream
			// more text — flip back to streaming so the badge doesn't
			// freeze on ⊙ executing between rounds.
			m.statusBar.SetEngineState(view.StateStreaming)
		}
	case protocol.EventCompactOccurred:
		// Compact is a brief phase — show ◌ compacting for ~1s then
		// return to whatever the prior state was. We just set it here;
		// the next text_delta / thinking_delta / assistant_turn_end
		// will transition back.
		m.statusBar.SetEngineState(view.StateCompacting)
	case protocol.EventTodoUpdate:
		var p protocol.TodoUpdateParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			// Convert wire TodoItem → view.TodoItem (view stays protocol-agnostic).
			items := make([]view.TodoItem, len(p.Todos))
			for i, t := range p.Todos {
				items[i] = view.TodoItem{ID: t.ID, Content: t.Content, Status: t.Status}
			}
			m.infoBar.SetTodos(items)
			// InfoBar height may have changed (0→N or N→0) — relayout
			// the chat viewport so the composer stays on-screen.
			m.relayout()
		}
	case protocol.EventSubagentStart:
		var p protocol.SubagentStartParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			// Register with InfoBar FIRST so the panel reserves space
			// before we render the chat marker below.
			m.infoBar.AddSubagent(p.SubagentID, p.AgentName, p.Description)
			m.relayout()
			// SubagentMarker — three-state color: ◐ (accent) running,
			// ◑ (success dim) done, ⊗ (error) failed.
			// Keyed by subagent_id so EventSubagentEnd can find + freeze
			// the right line (SubagentEndParams has no agent_name field).
			m.chatLog.AppendSubagentMarker(p.SubagentID, p.AgentName, p.Description)
		}
	case protocol.EventSubagentEnd:
		var p protocol.SubagentEndParams
		if err := json.Unmarshal(ev.Params, &p); err == nil {
			m.infoBar.RemoveSubagent(p.SubagentID)
			m.relayout()
			m.chatLog.CompleteSubagentMarker(p.SubagentID, p.Elapsed, p.State)
		}
	case protocol.EventError:
		var p struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(ev.Params, &p)
		m.errMsg = p.Message
		m.statusBar.SetEngineState(view.StateError)
		// Error system note with severity=error styling (✗ + $error bg).
		m.chatLog.AppendErrorNote(p.Message)
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

// estimateCtxTokens accumulates a rough ctx_tokens estimate from
// streamed deltas. We don't have access to the Python agent_loop's
// real ctx_tokens counter (no event exposes it yet), so we approximate
// as chars/4 — the standard "1 token ≈ 4 chars of English text" rule.
// This is good enough for the gear rail's 14-cell granularity.
//
// TODO: when the Python protocol adds a ctx_tokens field to
// EventAssistantTurnEnd (or a dedicated EventCtxUpdate), replace this
// with SetTokens(real_count).
func (m *model) estimateCtxTokens(delta string) {
	m.statusBar.SetTokens(m.statusBarTokens() + len(delta)/4)
}

// statusBarTokens is a private accessor that reads the StatusBar's
// current token count back. We use it inside estimateCtxTokens to
// accumulate rather than re-scan history. Implemented as a method on
// *model so it can reach into the unexported view.StatusBar.tokens
// field via a sibling accessor (StatusBar.PeekTokens).
func (m model) statusBarTokens() int { return m.statusBar.PeekTokens() }

func (m model) View() string {
	if m.quitting {
		return ""
	}
	if !m.ready {
		// Subtle "◆ loom" + connecting hint — no big colored block.
		return brandMarkStyle.Render("◆ loom") + " " +
			brandAgentStyle.Render("织轴") + " " +
			brandHintStyle.Render("connecting to loom server…") + "\n"
	}
	brand := m.renderBrand()
	hairline := hairlineStyle.Render(strings.Repeat("─", maxInt(1, m.width)))
	header := m.header.View()
	// WelcomeBanner splash — shown in place of the empty chat area
	// before the user sends their first message. Mirrors the original
	// TUI's WelcomeBanner (3D "loom" wordmark + tagline + slash hints).
	// Dismissed as soon as the first user message is appended.
	var chat string
	if m.chatLog.IsEmpty() {
		chat = view.RenderWelcomeBanner(m.chatLog.ContentWidth(), m.chatLog.ContentHeight())
	} else {
		chat = m.chatLog.View()
	}
	// InfoBar — shown above the composer when todos or subagents are active.
	// Empty when both are zero (auto-hide: no row, no reserved height).
	infoBar := m.infoBar.View()
	// preComposer collects transient UI rendered directly above the composer
	// (error banner + slash-command completer). Each item is a multi-line
	// block; we join them with a single newline and only add the whole block
	// to the layout when non-empty. This keeps the reserved height in
	// relayout() equal to the actual rendered height (no trailing blank row).
	var preComposerParts []string
	if m.errMsg != "" {
		preComposerParts = append(preComposerParts, errorStyle.Render("⚠ "+m.errMsg))
	}
	// CommandCompleter popup — renders ABOVE the composer (mirrors the
	// original TUI's chrome layout: StatusBar → CommandCompleter → Composer).
	// Only shown when the composer text starts with "/" and has no space.
	if m.completer.Visible() {
		preComposerParts = append(preComposerParts, m.completer.View())
	}
	preComposer := strings.Join(preComposerParts, "\n")
	composer := m.composer.View()
	status := m.statusBar.View()
	// Stack rows vertically. Skip empty rows (Header when no MCP,
	// InfoBar when no todos + no subagents, preComposer when empty) so the
	// layout collapses and the chat area absorbs the freed row.
	rows := []string{brand, hairline}
	if header != "" {
		rows = append(rows, header)
	}
	rows = append(rows, chat)
	if infoBar != "" {
		rows = append(rows, infoBar)
	}
	if preComposer != "" {
		rows = append(rows, preComposer)
	}
	rows = append(rows, composer, status)
	base := strings.Join(rows, "\n")
	if m.permission.Visible() && m.width > 0 && m.height > 0 {
		// PermissionModal.Render already produces a full-screen centered
		// overlay (it uses lipgloss.Place to center the box inside a
		// width x height canvas). Return it directly so it covers the
		// chat instead of being appended below the base layout.
		return m.permission.Render(m.width, m.height)
	}
	return base
}

// renderBrand builds the top brand row: "◆ loom" mark + agent name +
// right-aligned workdir. The "◆" mark mirrors WelcomeBanner's brand
// Static("◆ loom") — no purple background block, just sage text.
func (m model) renderBrand() string {
	left := brandMarkStyle.Render("◆ loom") + " " + brandAgentStyle.Render("织轴")
	right := brandWorkdirStyle.Render(m.workdir)
	middle := strings.Repeat(" ", maxInt(0, m.width-len(stripANSI(left))-len(stripANSI(right))-2))
	return left + middle + right
}

func stripANSI(s string) string {
	out := make([]byte, 0, len(s))
	i := 0
	for i < len(s) {
		if s[i] == 0x1b && i+1 < len(s) && s[i+1] == '[' {
			j := i + 2
			for j < len(s) {
				c := s[j]
				if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') {
					j++
					break
				}
				j++
			}
			i = j
			continue
		}
		out = append(out, s[i])
		i++
	}
	return string(out)
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// currentGitBranch returns the current branch name in workdir, or "" if it
// can't be determined (not a git repo, git not installed, etc.). Used to
// populate the status bar at startup without spawning a separate RPC roundtrip.
func currentGitBranch(workdir string) string {
	out, err := exec.Command("git", "-C", workdir, "rev-parse", "--abbrev-ref", "HEAD").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// ---------------------------------------------------------------------------
// tea.Msg types + cmd builders
// ---------------------------------------------------------------------------

type eventMsg struct{ event protocol.Event }
type sentMsg struct{}
type serverExitMsg struct{}
type sessionsListedMsg struct{ sessions []protocol.SessionMeta }

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

// requestSessions calls request/list_sessions and posts the result back to
// the program as a sessionsListedMsg. Errors return an empty slice — the
// panel will show "no sessions / error".
func requestSessions(client *rpc.Client) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.Send(protocol.NewListSessions(""), sendTimeout)
		if err != nil {
			return sessionsListedMsg{sessions: nil}
		}
		var res protocol.ListSessionsResult
		if err := json.Unmarshal(resp.Result, &res); err != nil {
			return sessionsListedMsg{sessions: nil}
		}
		return sessionsListedMsg{sessions: res.Sessions}
	}
}

// formatSessionPanel builds the body text shown under the Sessions tab.
func formatSessionPanel(sessions []protocol.SessionMeta) string {
	if len(sessions) == 0 {
		return "no sessions"
	}
	var b strings.Builder
	for i, s := range sessions {
		fmt.Fprintf(&b, "%d. %s  [%d msgs]  %s\n", i+1, s.Name, s.MessageCount, s.UpdatedAt)
	}
	return strings.TrimRight(b.String(), "\n")
}

// Brand bar styles — top-of-screen identity row.
//
// Mirrors WelcomeBanner's Static("◆ loom") + the agent name's
// weaving-themed display. Uses ink & sage tokens from view/theme.go:
//   - brand mark: $accent (sage), bold — the "◆ loom" wordmark
//   - agent name: $secondary (teal) — distinguishes 织轴 from the mark
//   - workdir:    $text-muted, italic — secondary info
//   - hint:       $text-faint, italic — barely-visible connecting state
//   - error:      $error, bold — pre-composer error banner
//   - hairline:   $hairline — thin top border on the chat log
var (
	brandMarkStyle    = lipgloss.NewStyle().Bold(true).Foreground(view.ColorAccent())
	brandAgentStyle   = lipgloss.NewStyle().Bold(true).Foreground(view.ColorSecondary())
	brandWorkdirStyle = lipgloss.NewStyle().Foreground(view.ColorTextMuted()).Italic(true)
	brandHintStyle    = lipgloss.NewStyle().Foreground(view.ColorTextFaint()).Italic(true)
	errorStyle        = lipgloss.NewStyle().Foreground(view.ColorError()).Bold(true).Padding(0, 1)
	hairlineStyle     = lipgloss.NewStyle().Foreground(view.ColorHairline())
)
