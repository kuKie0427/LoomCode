package view

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// InfoBar is a multi-line panel shown above the Composer that displays
// the live todo list and active subagents. Auto-hides when both empty.
//
// Layout rules:
//   - todos==0 && subagents==0: returns "" (panel auto-hides, no space)
//   - only todos:     full width, 1 line per todo (max 5 visible)
//   - only subagents: full width, 2 lines per subagent (header + scrolling desc)
//   - both:           split — todos on the left, subagents on the right,
//     each column gets ~half the terminal width; height = max(todo, sub lines)
//
// Todo line format (per entry):
//
//	"◐ {content}"   — in_progress  (accent glyph)
//	"○ {content}"   — pending      (faint glyph)
//	"✓ {content}"   — completed    (success glyph, dim body)
//
// Subagent block format (2 lines):
//
//	"◐ {agent_name} · running · {elapsed}"   — header (accent glyph)
//	"  {description marquee scroll}"         — body (text-muted)
//
// Elapsed updates live via Tick() at 1Hz. Description scrolls when it
// exceeds the column width (marquee at ~1 char/sec).
type InfoBar struct {
	todos     []TodoItem
	subagents []subagentEntry
	width     int
}

// subagentEntry is a single running subagent's display state.
type subagentEntry struct {
	id          string
	agentName   string
	description string
	startedAt   time.Time
	scrollPhase int // marquee offset (advances 1 char/tick when text overflows)
	scrollLen   int // total length of the marquee cycle (text + separator). 0 = no scroll.
}

// TodoItem mirrors protocol.TodoItem (id/content/status). Re-declared
// here to break the view→protocol dependency (view must stay agnostic
// of the wire format).
type TodoItem struct {
	ID      string
	Content string
	Status  string // "pending" | "in_progress" | "completed"
}

// maxVisibleTodos caps the displayed todo list — more than 5 lines
// would crowd the composer area. Mirrors the original TUI's
// header.py::MAX_VISIBLE_TODOS = 5.
const maxVisibleTodos = 5

// NewInfoBar returns an empty InfoBar (auto-hidden until todos/subagents arrive).
func NewInfoBar() *InfoBar { return &InfoBar{} }

// SetTodos replaces the visible todo list. If the list exceeds
// maxVisibleTodos, only the first 5 are shown.
func (i *InfoBar) SetTodos(todos []TodoItem) {
	if len(todos) > maxVisibleTodos {
		i.todos = append([]TodoItem(nil), todos[:maxVisibleTodos]...)
		return
	}
	i.todos = append([]TodoItem(nil), todos...)
}

// AddSubagent registers a new running subagent. No-op if id already
// present (defensive against duplicate events).
func (i *InfoBar) AddSubagent(id, agentName, description string) {
	for _, s := range i.subagents {
		if s.id == id {
			return
		}
	}
	// Normalize the description to a single line: collapse any newlines
	// (or carriage returns) into a single space so the InfoBar body never
	// wraps inside the fixed-height subagent block.
	description = strings.Join(strings.FieldsFunc(description, func(r rune) bool {
		return r == '\n' || r == '\r'
	}), " ")
	i.subagents = append(i.subagents, subagentEntry{
		id:          id,
		agentName:   agentName,
		description: description,
		startedAt:   time.Now(),
	})
}

// RemoveSubagent removes a subagent by id (called on EventSubagentEnd).
// Filter-in-place to avoid allocs on the hot path.
func (i *InfoBar) RemoveSubagent(id string) {
	out := i.subagents[:0]
	for _, s := range i.subagents {
		if s.id != id {
			out = append(out, s)
		}
	}
	i.subagents = out
}

// SetWidth reserves the terminal width so the split layout can compute
// the spacer between left/right columns and the marquee scroll window.
func (i *InfoBar) SetWidth(w int) { i.width = w }

// Tick advances elapsed display + marquee scroll phase for active
// subagents. Called from the top-level model's tick handler at 1Hz.
// Per-tick cost is O(len(subagents)) — typically 0-2 entries.
func (i *InfoBar) Tick() {
	for idx := range i.subagents {
		s := &i.subagents[idx]
		if s.scrollLen > 0 {
			s.scrollPhase = (s.scrollPhase + 1) % s.scrollLen
		}
	}
}

// Empty reports whether the bar has nothing to show (no todos, no subagents).
// When true, the caller should NOT reserve any rows for the bar.
func (i *InfoBar) Empty() bool {
	return len(i.todos) == 0 && len(i.subagents) == 0
}

// Height returns the rendered row count. Used by main.go to compute
// how many lines to reserve in the layout. Returns 0 when Empty().
//
// Math:
//   - todos: len(todos), capped at maxVisibleTodos (enforced by SetTodos)
//   - subagents: 2 * len(subagents) (header line + scrolling desc line)
//   - split mode: max of the two
func (i *InfoBar) Height() int {
	if i.Empty() {
		return 0
	}
	todoLines := len(i.todos)
	if todoLines > maxVisibleTodos {
		todoLines = maxVisibleTodos
	}
	subLines := 2 * len(i.subagents)
	h := todoLines
	if subLines > h {
		h = subLines
	}
	return h
}

// View renders the InfoBar. Returns "" when Empty() — caller should skip
// the row entirely so the layout collapses.
//
// Rendering branches on content:
//   - only todos:     renderTodos(fullWidth)
//   - only subagents: renderSubagents(fullWidth)
//   - both:           split — left=todos, right=subagents, joined horizontally
func (i *InfoBar) View() string {
	if i.Empty() {
		return ""
	}
	hasTodos := len(i.todos) > 0
	hasSubs := len(i.subagents) > 0
	if hasTodos && hasSubs {
		// Split: each column gets ~half the width. Reserve 1 col for divider.
		colW := (i.width - 1) / 2
		if colW < 10 {
			colW = 10 // hard floor — below this, content is unreadable anyway
		}
		leftLines := strings.Split(i.renderTodos(colW), "\n")
		rightLines := strings.Split(i.renderSubagents(colW), "\n")
		h := i.Height()
		// Pad shorter column with blank lines so each row has a left + right.
		for len(leftLines) < h {
			leftLines = append(leftLines, strings.Repeat(" ", colW))
		}
		for len(rightLines) < h {
			rightLines = append(rightLines, strings.Repeat(" ", colW))
		}
		divider := lipgloss.NewStyle().Foreground(colorHairline).Render("│")
		var rows []string
		for idx := 0; idx < h; idx++ {
			rows = append(rows, leftLines[idx]+divider+rightLines[idx])
		}
		return strings.Join(rows, "\n")
	}
	if hasTodos {
		return i.renderTodos(i.width)
	}
	return i.renderSubagents(i.width)
}

// renderTodos builds the left column — 1 line per todo, status glyph prefix.
//
// Glyph vocabulary (mirrors the original TUI's TodoItem render):
//   - ◐ in_progress  (accent, bold) — currently being worked
//   - ○ pending      (text-faint)   — queued
//   - ✓ completed    (success)      — done (body also dimmed + strikethrough)
func (i *InfoBar) renderTodos(colWidth int) string {
	if len(i.todos) == 0 {
		return ""
	}
	// " ◯ {content}" = 3 visible cols of prefix
	const prefix = 3
	contentW := colWidth - prefix
	if contentW < 1 {
		contentW = 1
	}
	var lines []string
	for _, t := range i.todos {
		var glyph, body string
		switch t.Status {
		case "in_progress":
			glyph = infoBarTodoActiveGlyph.Render("◐")
			body = infoBarBodyStyle.Render(" " + truncateRunes(t.Content, contentW))
		case "completed":
			glyph = infoBarTodoDoneGlyph.Render("✓")
			body = infoBarBodyDoneStyle.Render(" " + truncateRunes(t.Content, contentW))
		default: // "pending" or unknown
			glyph = infoBarTodoIdleGlyph.Render("○")
			body = infoBarBodyStyle.Render(" " + truncateRunes(t.Content, contentW))
		}
		// Pad line to exactly colWidth so JoinHorizontal aligns the
		// divider consistently — without this, lines with shorter
		// content would shift the right column leftward.
		line := glyph + body
		if pad := colWidth - lipgloss.Width(line); pad > 0 {
			line += strings.Repeat(" ", pad)
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

// renderSubagents builds the right column — 2 lines per subagent.
// Line 1: "◐ {agent_name} · running · {elapsed}"
// Line 2: "  {description}"  (marquee scroll when text overflows colWidth-2)
func (i *InfoBar) renderSubagents(colWidth int) string {
	if len(i.subagents) == 0 {
		return ""
	}
	var lines []string
	const bodyIndent = 2
	bodyW := colWidth - bodyIndent
	if bodyW < 1 {
		bodyW = 1
	}
	for idx := range i.subagents {
		s := &i.subagents[idx]
		elapsed := formatSubagentElapsed(time.Since(s.startedAt).Seconds())
		glyph := infoBarSubGlyph.Render("◐")
		header := infoBarSubHeaderStyle.Render(fmt.Sprintf(" %s · running · %s", s.agentName, elapsed))
		headerLine := glyph + header
		if pad := colWidth - lipgloss.Width(headerLine); pad > 0 {
			headerLine += strings.Repeat(" ", pad)
		}
		lines = append(lines, headerLine)
		// Body: marquee scroll if description overflows bodyW; else static.
		body := renderMarquee(s.description, bodyW, s.scrollPhase)
		// Lazily compute scrollLen on first render so the caller can
		// pass scrollPhase=0 on the very first tick without us having
		// to pre-compute it in AddSubagent.
		if s.scrollLen == 0 && lipgloss.Width(s.description) > bodyW {
			// marquee cycle = text + " · " separator (3 visible cols)
			s.scrollLen = lipgloss.Width(s.description) + 3
		}
		bodyLine := infoBarSubBodyStyle.Render(strings.Repeat(" ", bodyIndent) + body)
		if pad := colWidth - lipgloss.Width(bodyLine); pad > 0 {
			bodyLine += strings.Repeat(" ", pad)
		}
		lines = append(lines, bodyLine)
	}
	return strings.Join(lines, "\n")
}

// renderMarquee returns a string of exactly width visible cols from text.
// When text fits (visual width <= width), returns text padded right with
// spaces so the column is visually stable. When text overflows, returns
// a sliding window starting at phase offset in the cycle text+" · ".
func renderMarquee(text string, width, phase int) string {
	w := lipgloss.Width(text)
	if w <= width {
		return text + strings.Repeat(" ", width-w)
	}
	// Build a cycle: text + " · " (separator). Total cycle width = w + 3.
	sep := " · "
	cycle := text + sep
	cycleW := w + lipgloss.Width(sep)
	phase = phase % cycleW
	// Replicate cycle enough times to cover any start offset + width.
	repeats := (phase+width)/cycleW + 2
	buf := strings.Builder{}
	for r := 0; r < repeats; r++ {
		buf.WriteString(cycle)
	}
	// Slice by visible width, not bytes — text may contain CJK chars (2 cols each).
	return sliceByVisibleWidth(buf.String(), phase, width)
}

// sliceByVisibleWidth returns the substring of s starting at visible
// column `start` and spanning exactly `width` visible columns. Used by
// renderMarquee — slicing by bytes would split CJK glyphs and corrupt
// the terminal. Walks runes, accumulating visible width via runewidth.
//
// Straddling runes (start/end falls inside a double-width glyph) are
// replaced with a space placeholder so the total visible width is
// always exactly `width`.
func sliceByVisibleWidth(s string, start, width int) string {
	if start < 0 {
		start = 0
	}
	if width < 1 {
		return ""
	}
	var out strings.Builder
	vis := 0
	target := start + width
	for _, r := range s {
		rw := runeWidth(r)
		if vis+rw <= start {
			vis += rw
			continue
		}
		if vis < start {
			// Straddling rune at start boundary — emit space placeholder.
			out.WriteRune(' ')
			vis = start + 1
			continue
		}
		if vis+rw > target {
			// Straddling rune at end boundary — emit space, stop.
			out.WriteRune(' ')
			vis = target
			break
		}
		out.WriteRune(r)
		vis += rw
	}
	result := out.String()
	pad := width - lipgloss.Width(result)
	if pad > 0 {
		result += strings.Repeat(" ", pad)
	}
	return result
}

// truncateRunes returns the prefix of s whose visible width fits in
// `maxWidth` cols. If truncation occurs, appends "…" (1 col). Used by
// todo line rendering — keeps content from wrapping.
func truncateRunes(s string, maxWidth int) string {
	if maxWidth < 1 {
		return ""
	}
	if lipgloss.Width(s) <= maxWidth {
		return s
	}
	var out strings.Builder
	vis := 0
	for _, r := range s {
		rw := runeWidth(r)
		if vis+rw > maxWidth-1 { // reserve 1 for "…"
			break
		}
		out.WriteRune(r)
		vis += rw
	}
	out.WriteRune('…')
	return out.String()
}

// formatSubagentElapsed is declared in chatlog.go (float64 seconds → "5s"/"1m5s").
// Reused here for InfoBar subagent headers — same wire format.

// ── ink & sage infobar styles ───────────────────────────────────────
//
// Mirrors the original TUI's #info-bar DEFAULT_CSS:
//
//	InfoBar { color: $text-muted; background: transparent; padding: 0 1 }
//	InfoBar > .glyph { color: $accent }
//	InfoBar > .glyph.done { color: $success }
//	InfoBar > .glyph.idle { color: $text-faint }
var (
	infoBarTodoActiveGlyph = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	infoBarTodoDoneGlyph   = lipgloss.NewStyle().Foreground(colorSuccess).Bold(true)
	infoBarTodoIdleGlyph   = lipgloss.NewStyle().Foreground(colorTextFaint)

	infoBarSubGlyph       = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	infoBarSubHeaderStyle = lipgloss.NewStyle().Foreground(colorForeground)
	infoBarSubBodyStyle   = lipgloss.NewStyle().Foreground(colorTextMuted)

	infoBarBodyStyle     = lipgloss.NewStyle().Foreground(colorTextMuted)
	infoBarBodyDoneStyle = lipgloss.NewStyle().Foreground(colorTextFaint).Strikethrough(true)
)
