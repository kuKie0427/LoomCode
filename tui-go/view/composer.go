package view

import (
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Composer wraps a textarea.Model with the ink & sage composer styling.
//
// Visual language mirrors loom/tui/composer.py + app.py #composer CSS:
//   - NO border (border: none in #composer)
//   - transparent background (background: transparent)
//   - placeholder: "Type a prompt, / for commands"
//   - cursor: $accent background, $background foreground
//
// The chrome container (defined in main.go) provides the $surface
// background the composer sits inside — the composer itself draws no
// background so it blends into the chrome.
//
// Note on textarea.Focus() aliasing: see ConfigureCmd — Focus is NOT
// called in NewComposer because it sets m.style = &m.FocusedStyle (a
// pointer into the local textarea value), and copying that value into
// Composer.textarea leaves the pointer dangling. ConfigureCmd runs the
// focus + height setup as a tea.Cmd from the top-level Init() so it
// executes after the Composer is wired into the heap-allocated model.
type Composer struct {
	textarea  textarea.Model
	width     int
	streaming bool
}

// NewComposer builds a focused, empty composer.
func NewComposer() *Composer {
	ta := textarea.New()
	ta.Placeholder = "Type a prompt, / for commands"
	ta.CharLimit = 0
	ta.Prompt = ""             // no leading "│ " — transparent bg makes a prompt stick out.
	ta.ShowLineNumbers = false // mirrors Composer.py kwargs.setdefault("show_line_numbers", False)
	// Cursor styling: bubbles/cursor unconditionally applies .Reverse(true),
	// so to get the final visual of "sage bg + dark fg" (mirrors the
	// original #composer .text-area--cursor { background: $accent;
	// color: $background }), we set the swapped pair here: Bg=background,
	// Fg=accent — Reverse then yields Bg=accent, Fg=background.
	ta.Cursor.Style = lipgloss.NewStyle().
		Background(colorBackground).
		Foreground(colorAccent)
	// NOTE: do NOT call SetHeight or Focus here. Doing so mutates internal
	// pointers (style, viewport) that alias the local `ta` value; once
	// `ta` is copied into Composer.textarea those pointers dangle and the
	// next SetWidth / Update crashes with nil deref on m.style.Base.
	// Configure height + focus from the model's Init() via tea.Cmds after
	// the Composer is wired into the heap-allocated model tree.
	return &Composer{textarea: ta}
}

// FocusCmd returns a tea.Cmd that focuses the textarea. Call from the
// top-level model's Init() so the focus happens after the Composer is
// mounted, avoiding the local-value aliasing issue described above.
func (c *Composer) FocusCmd() tea.Cmd {
	return func() tea.Msg {
		c.textarea.Focus()
		return nil
	}
}

// ConfigureCmd returns a tea.Cmd that performs post-construction setup
// (height, focus) on the textarea. Must run after the Composer is part
// of the heap-allocated model — calling these in NewComposer leaves
// dangling internal pointers in the textarea value's copy.
func (c *Composer) ConfigureCmd() tea.Cmd {
	return func() tea.Msg {
		c.textarea.SetHeight(2)
		c.textarea.Focus()
		return nil
	}
}

// Value returns the current text.
func (c *Composer) Value() string {
	return c.textarea.Value()
}

// Reset clears the input.
func (c *Composer) Reset() {
	c.textarea.Reset()
}

// SetValue replaces the textarea content and moves the cursor to the end.
// Used by the CommandCompleter's Tab-completion path to insert "/<name> "
// into the composer without the user typing it character-by-character.
func (c *Composer) SetValue(text string) {
	c.textarea.SetValue(text)
}

// SetWidth resizes the textarea. The composer has no border, so the
// full width is available (minus 2 columns of left/right padding to
// match the original #composer padding: 0 1 1 1).
func (c *Composer) SetWidth(width int) {
	c.width = width
	inner := width - 2 // 1 left + 1 right padding
	if inner < 1 {
		inner = 1
	}
	c.textarea.SetWidth(inner)
}

// SetStreaming toggles the hint text color while the agent is busy.
// The composer has no border to recolor, so streaming state only
// affects the inline hint below the textarea.
func (c *Composer) SetStreaming(busy bool) {
	c.streaming = busy
}

// Update forwards messages to the textarea (for blinking etc.).
// Returns the textarea's Cmd to allow composition.
func (c *Composer) Update(msg tea.Msg) tea.Cmd {
	m, cmd := c.textarea.Update(msg)
	c.textarea = m
	return cmd
}

// View returns the textarea + a one-line hint row.
//
// Mirrors the original #composer (transparent bg, no border, padded
// 0 1 1 1) and the StatusBar-separate hint from the chrome container.
// The hint row is muted ($text-muted) when idle, accent ($accent) when
// streaming, so the user gets a subtle visual cue without a border
// color shift.
func (c *Composer) View() string {
	inner := c.textarea.View()
	hint := composerHintStyle.Render(c.hintText())
	if c.width > 0 {
		// Pad the textarea row to the inner width so the right padding
		// aligns with the chrome edge.
		inner = padRightBlock(inner, c.width-2)
	}
	return inner + "\n" + hint
}

func (c *Composer) hintText() string {
	if c.streaming {
		return "▸ streaming…  Ctrl+C aborts"
	}
	return " Enter send  Shift+Enter newline  Ctrl+C quit  PgUp/PgDn scroll"
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

// composerHintStyle — muted text when idle, accent when streaming.
// No background (transparent), no border (mirrors #composer CSS).
var (
	composerHintStyle = lipgloss.NewStyle().
				Foreground(colorTextMuted).
				Italic(true).
				Padding(0, 1)

	// composerStreamingHintStyle is a separate style used by View() when
	// streaming — kept as a var so it can be tweaked independently of
	// the idle hint color.
	composerStreamingHintStyle = lipgloss.NewStyle().
					Foreground(colorAccent).
					Bold(true).
					Padding(0, 1)
)
