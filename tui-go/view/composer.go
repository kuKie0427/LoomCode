package view

import (
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
)

// Composer wraps a textarea.Model and exposes a HandleKey that returns
// (text, true) on Enter when there is text to send. Shift+Enter for a
// newline is handled by the textarea's internal key bindings, so we
// only intercept plain Enter here.
type Composer struct {
	textarea textarea.Model
}

// NewComposer builds a focused, empty composer.
func NewComposer() *Composer {
	ta := textarea.New()
	ta.Placeholder = "Send a message... (Enter to send, Shift+Enter for newline, Ctrl+C to quit)"
	ta.Focus()
	ta.CharLimit = 0
	return &Composer{textarea: ta}
}

// Value returns the current text.
func (c *Composer) Value() string {
	return c.textarea.Value()
}

// Reset clears the input.
func (c *Composer) Reset() {
	c.textarea.Reset()
}

// SetWidth resizes the textarea.
func (c *Composer) SetWidth(width int) {
	c.textarea.SetWidth(width)
}

// Update forwards messages to the textarea (for blinking etc.).
// Returns the textarea's Cmd to allow composition.
func (c *Composer) Update(msg tea.Msg) tea.Cmd {
	m, cmd := c.textarea.Update(msg)
	c.textarea = m
	return cmd
}

// View returns the textarea's view.
func (c *Composer) View() string {
	return c.textarea.View()
}

// HandleKey inspects a KeyMsg: on plain Enter with non-empty text,
// returns (text, true) and resets the textarea. Otherwise returns ("", false)
// and the caller should delegate the key to the textarea via Update.
func (c *Composer) HandleKey(msg tea.KeyMsg) (string, bool) {
	if msg.Type == tea.KeyEnter {
		text := c.textarea.Value()
		if text != "" {
			c.textarea.Reset()
			return text, true
		}
	}
	return "", false
}
