// Package view implements the TUI widgets.
package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/lanf/loom-tui/markdown"
)

// ChatLog is the scrolling transcript viewport: user/assistant messages,
// streaming overlay, thinking display, and tool call markers.
type ChatLog struct {
	viewport  viewport.Model
	lines     []chatLine
	streamBuf strings.Builder
	thinking  strings.Builder
	toolCalls []toolCall
}

type chatLine struct {
	role    string // "user", "assistant", "tool", "thinking", "system"
	content string
}

type toolCall struct {
	id      string
	name    string
	input   map[string]any
	output  string
	isError bool
	done    bool
}

// NewChatLog builds a ChatLog sized to width x height.
func NewChatLog(width, height int) *ChatLog {
	vp := viewport.New(width, height)
	return &ChatLog{viewport: vp}
}

// SetSize resizes the underlying viewport.
func (c *ChatLog) SetSize(width, height int) {
	c.viewport.Width = width
	c.viewport.Height = height
	c.render()
}

// AppendUserMessage appends a "You: ..." line.
func (c *ChatLog) AppendUserMessage(text string) {
	c.lines = append(c.lines, chatLine{role: "user", content: text})
	c.render()
}

// StartAssistantTurn resets the streaming buffer and starts a fresh assistant line.
func (c *ChatLog) StartAssistantTurn() {
	c.streamBuf.Reset()
	c.lines = append(c.lines, chatLine{role: "assistant", content: ""})
	c.render()
}

// AppendTextDelta appends a streamed text chunk to the current assistant line.
func (c *ChatLog) AppendTextDelta(text string) {
	c.streamBuf.WriteString(text)
	if len(c.lines) > 0 && c.lines[len(c.lines)-1].role == "assistant" {
		c.lines[len(c.lines)-1].content = c.streamBuf.String()
	}
	c.render()
}

// AppendThinkingDelta accumulates thinking text (not yet displayed inline).
func (c *ChatLog) AppendThinkingDelta(text string) {
	c.thinking.WriteString(text)
}

// StartToolCall adds a "in progress" tool call marker.
func (c *ChatLog) StartToolCall(name string, input map[string]any, id string) {
	c.toolCalls = append(c.toolCalls, toolCall{
		id: id, name: name, input: input,
	})
	c.lines = append(c.lines, chatLine{
		role:    "tool",
		content: fmt.Sprintf("◐ %s(%v)", name, input),
	})
	c.render()
}

// CompleteToolCall marks a tool call done and updates its marker line.
func (c *ChatLog) CompleteToolCall(id string, output string, isError bool) {
	idx := -1
	for i := range c.toolCalls {
		if c.toolCalls[i].id == id {
			c.toolCalls[i].output = output
			c.toolCalls[i].isError = isError
			c.toolCalls[i].done = true
			idx = i
			break
		}
	}
	if idx < 0 {
		return
	}
	name := c.toolCalls[idx].name
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "tool" && strings.Contains(c.lines[i].content, id) {
			mark := "✓"
			if isError {
				mark = "✗"
			}
			c.lines[i].content = fmt.Sprintf("%s %s → %s", mark, name, truncate(output, 100))
			break
		}
	}
	c.render()
}

// View returns the rendered viewport content.
func (c *ChatLog) View() string {
	return c.viewport.View()
}

// GotoBottom scrolls the viewport to the bottom (call after each append).
func (c *ChatLog) GotoBottom() {
	c.viewport.GotoBottom()
}

// Update forwards messages to the viewport (for mouse-wheel scrolling).
func (c *ChatLog) Update(msg tea.Msg) (viewport.Model, tea.Cmd) {
	return c.viewport.Update(msg)
}

// render rebuilds the viewport content from the accumulated lines.
//
// Markdown rendering is applied to assistant messages; user/tool/system lines
// are styled directly. The viewport is then re-set with the rendered string.
func (c *ChatLog) render() {
	var b strings.Builder
	for _, line := range c.lines {
		switch line.role {
		case "user":
			b.WriteString(userStyle.Render("You: "+line.content) + "\n\n")
		case "assistant":
			if line.content == "" {
				continue
			}
			rendered, err := markdown.Render(line.content)
			if err != nil || rendered == "" {
				b.WriteString(line.content + "\n\n")
			} else {
				b.WriteString(rendered)
			}
		case "tool":
			b.WriteString(toolStyle.Render(line.content) + "\n")
		case "system":
			b.WriteString(systemStyle.Render(line.content) + "\n\n")
		}
	}
	c.viewport.SetContent(b.String())
	c.viewport.GotoBottom()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

var (
	userStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("36"))
	toolStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("33")).Italic(true)
	systemStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("90")).Italic(true)
)
