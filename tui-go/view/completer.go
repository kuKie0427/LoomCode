package view

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// SlashCommand mirrors one entry in loom/tui/slash_commands.py::_COMMANDS.
// Aliases are alternate names that also match the filter (e.g. /think, /thing
// for /thinking). Description is the only hint shown in the popup.
type SlashCommand struct {
	Name        string
	Aliases     []string
	Description string
}

// slashCommands is the registry of 11 commands, in display order.
// Mirrors loom/tui/slash_commands.py (lines 222-280).
var slashCommands = []SlashCommand{
	{Name: "help", Description: "Show available commands"},
	{Name: "init", Description: "Scaffold or update harness files (--force to overwrite)"},
	{Name: "clear", Description: "Clear conversation history"},
	{Name: "model", Description: "Switch model or open the model picker"},
	{Name: "sessions", Description: "Open the session picker to switch or delete sessions"},
	{Name: "new", Description: "Start a new session (saves the current one first)"},
	{Name: "connect", Description: "Connect a provider or enter an API key"},
	{Name: "resume", Description: "Resume a session: /resume <id> or /resume for last checkpoint"},
	{Name: "status", Description: "Show session status and provider credentials"},
	{Name: "thinking", Aliases: []string{"think", "thing"}, Description: "Set thinking mode/effort: on|off|high|max"},
	{Name: "quit", Aliases: []string{"q", "exit"}, Description: "Quit the application"},
}

// maxCompleterRows caps the popup height — mirrors completer.py max-height: 8.
const maxCompleterRows = 8

// CommandCompleter is the slash-command popup that appears above the
// composer when the input starts with "/" and contains no space.
//
// Visual language mirrors loom/tui/completer.py:
//   - background: $panel 97% (approximated by colorPanel)
//   - border-top: solid $border
//   - max 8 rows
//   - selected row: "▸ /name (aliases) — desc" with $accent 30% bg + bold
//   - unselected row: "  /name (aliases) — desc" with description dimmed
//
// The popup is shown/hidden via the visible flag; main.go's key handler
// calls Show(query) after every keystroke that changes the composer text,
// and Hide() on Tab/Enter/Esc/space.
type CommandCompleter struct {
	visible  bool
	query    string
	selected int
	filtered []SlashCommand
	width    int
}

// NewCommandCompleter returns a hidden completer.
func NewCommandCompleter() *CommandCompleter {
	return &CommandCompleter{}
}

// SetWidth is called from WindowSizeMsg so row rendering can truncate
// long descriptions to fit the terminal width.
func (c *CommandCompleter) SetWidth(w int) { c.width = w }

// Visible reports whether the popup is currently shown.
func (c *CommandCompleter) Visible() bool { return c.visible }

// Show filters the command list by query (minus the leading "/") and
// makes the popup visible. An empty query shows the first 8 commands.
// Selection resets to 0 on each Show call.
//
// Filter logic mirrors completer.py:20-54 — prefix match on name OR
// alias first. The difflib fuzzy fallback is omitted (Go has no stdlib
// equivalent); prefix match covers the common case.
func (c *CommandCompleter) Show(query string) {
	// Strip leading "/" if present — the caller may pass the raw composer text.
	query = strings.TrimPrefix(query, "/")
	c.query = query
	c.visible = true
	c.selected = 0
	c.filtered = filterCommands(query)
}

// Hide hides the popup.
func (c *CommandCompleter) Hide() {
	c.visible = false
	c.selected = 0
}

// Move changes the selection by delta (±1), wrapping around. No-op if
// the filtered list is empty. Mirrors CompletionMove(±1) modulo wrap.
func (c *CommandCompleter) Move(delta int) {
	n := len(c.filtered)
	if n == 0 {
		return
	}
	c.selected = ((c.selected+delta)%n + n) % n
}

// Selected returns the currently highlighted command, or nil if the
// filtered list is empty. Used by main.go for Tab completion + Enter execute.
func (c *CommandCompleter) Selected() *SlashCommand {
	if len(c.filtered) == 0 || c.selected < 0 || c.selected >= len(c.filtered) {
		return nil
	}
	return &c.filtered[c.selected]
}

// Height returns the rendered row count of the popup (0 when hidden).
// Used by main.go's relayout to reserve space so the popup doesn't push
// the brand bar off the top of the screen.
func (c *CommandCompleter) Height() int {
	if !c.visible || len(c.filtered) == 0 {
		return 0
	}
	rows := len(c.filtered)
	if rows > maxCompleterRows {
		rows = maxCompleterRows
	}
	// +1 for the top border drawn by completerBorderStyle.
	return rows + 1
}

// View renders the popup as a bordered strip with up to 8 rows.
// Returns "" when not visible.
func (c *CommandCompleter) View() string {
	if !c.visible || len(c.filtered) == 0 {
		return ""
	}
	rows := c.filtered
	if len(rows) > maxCompleterRows {
		rows = rows[:maxCompleterRows]
	}
	var b strings.Builder
	for i, cmd := range rows {
		selected := i == c.selected
		b.WriteString(c.renderRow(cmd, selected))
		if i < len(rows)-1 {
			b.WriteString("\n")
		}
	}
	inner := b.String()
	// Border-top: solid $border. Background: $panel.
	return completerBorderStyle.
		BorderTop(true).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(colorBorder).
		Background(colorPanel).
		Padding(0, 1).
		Render(inner)
}

// renderRow builds one row of the popup.
//
//	selected: "▸ /name (alias1, alias2) — description"
//	normal:   "  /name (alias1, alias2) — description"  (description dimmed)
//
// Mirrors completer.py:118-128. The aliases section " (a, b)" is only
// rendered when the command has aliases.
func (c *CommandCompleter) renderRow(cmd SlashCommand, selected bool) string {
	glyph := "  "
	style := completerRowStyle
	if selected {
		glyph = "▸ "
		style = completerSelectedStyle
	}

	namePart := "/" + cmd.Name
	aliasPart := ""
	if len(cmd.Aliases) > 0 {
		aliasPart = " (" + strings.Join(cmd.Aliases, ", ") + ")"
	}

	// For selected rows, the whole line gets the selected style (accent
	// bg + bold). For unselected rows, name+aliases are normal and
	// description is dimmed — we achieve this by rendering the description
	// separately with the dim style.
	if selected {
		line := glyph + namePart + aliasPart + " — " + cmd.Description
		return style.Render(line)
	}
	// Unselected: name+aliases in default, description dimmed.
	left := namePart + aliasPart + " — "
	descStyle := completerDimDescStyle
	return "  " + left + descStyle.Render(cmd.Description)
}

// filterCommands returns commands whose name or any alias starts with
// query (case-insensitive). Caps at maxCompleterRows. Empty query returns
// the first 8 commands in registry order.
func filterCommands(query string) []SlashCommand {
	if query == "" {
		n := maxCompleterRows
		if n > len(slashCommands) {
			n = len(slashCommands)
		}
		return slashCommands[:n]
	}
	q := strings.ToLower(query)
	var out []SlashCommand
	for _, cmd := range slashCommands {
		if strings.HasPrefix(strings.ToLower(cmd.Name), q) {
			out = append(out, cmd)
			continue
		}
		for _, a := range cmd.Aliases {
			if strings.HasPrefix(strings.ToLower(a), q) {
				out = append(out, cmd)
				break
			}
		}
		if len(out) >= maxCompleterRows {
			break
		}
	}
	return out
}

// ── completer styles ─────────────────────────────────────────────────
//
// Mirrors completer.py CSS:
//
//	.row.selected { background: $accent 30%; color: $text; text-style: bold }
//	.row          { height: 1 }
//
// $accent 30% over $panel ≈ colorAccentDim (#2d4539) — the same token
// used for the assistant message's left border.
var (
	completerRowStyle = lipgloss.NewStyle()

	completerSelectedStyle = lipgloss.NewStyle().
				Background(colorAccentDim).
				Foreground(colorForeground).
				Bold(true)

	completerDimDescStyle = lipgloss.NewStyle().Foreground(colorTextMuted)

	completerBorderStyle = lipgloss.NewStyle()
)
