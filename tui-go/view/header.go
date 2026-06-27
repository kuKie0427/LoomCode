package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Header section identifiers.
const (
	SectionNone     = ""
	SectionTodos    = "todos"
	SectionSubagent = "subagents"
	SectionSessions = "sessions"
	SectionModels   = "models"
)

// Header renders a one-line tab bar with toggleable sections and an optional
// expanded panel showing the section's content (e.g. session list).
//
// Toggle via Toggle(section) — only one section can be expanded at a time;
// toggling the active section collapses it.
type Header struct {
	active    string
	content   string // body of the expanded section (rendered under the tabs)
	todoCount int
	subCount  int
	sessCount int
	models    []string
	currModel string
}

// NewHeader returns a Header with no section expanded.
func NewHeader() *Header {
	return &Header{}
}

// Toggle expands section if not active, collapses if active.
func (h *Header) Toggle(section string) {
	if h.active == section {
		h.active = SectionNone
		h.content = ""
	} else {
		h.active = section
	}
}

// Active returns the currently expanded section ("" if none).
func (h *Header) Active() string { return h.active }

// SetContent sets the body shown under the tabs for the active section.
// Caller is responsible for refreshing this when the active section changes.
func (h *Header) SetContent(content string) { h.content = content }

// SetTodoCount updates the Todos badge count.
func (h *Header) SetTodoCount(n int) { h.todoCount = n }

// SetSubagentCount updates the Subagents badge count.
func (h *Header) SetSubagentCount(n int) { h.subCount = n }

// SetSessionCount updates the Sessions badge count.
func (h *Header) SetSessionCount(n int) { h.sessCount = n }

// SetModels updates the available model list and the current selection.
func (h *Header) SetModels(models []string, current string) {
	h.models = models
	h.currModel = current
}

// View renders the header. Returns two visual rows when a section is expanded
// (tabs + body), or just the tab row when collapsed.
func (h *Header) View() string {
	tabs := []struct {
		name  string
		label string
		count int
	}{
		{SectionTodos, "Todos", h.todoCount},
		{SectionSubagent, "Subagents", h.subCount},
		{SectionSessions, "Sessions", h.sessCount},
		{SectionModels, "Models", len(h.models)},
	}
	var cells []string
	for _, t := range tabs {
		label := fmt.Sprintf(" %s ", t.label)
		if t.count > 0 {
			label = fmt.Sprintf(" %s:%d ", t.label, t.count)
		}
		if h.active == t.name {
			cells = append(cells, activeTabStyle.Render(label))
		} else {
			cells = append(cells, tabStyle.Render(label))
		}
	}
	row := lipgloss.JoinHorizontal(lipgloss.Left, cells...)
	if h.currModel != "" {
		row = lipgloss.JoinHorizontal(lipgloss.Left, row,
			lipgloss.NewStyle().Foreground(lipgloss.Color("245")).Padding(0, 1).Render("model: "+h.currModel))
	}
	var b strings.Builder
	b.WriteString(row)
	if h.active != SectionNone && h.content != "" {
		b.WriteString("\n" + bodyStyle.Render(h.content))
	}
	return b.String()
}

var (
	tabStyle       = lipgloss.NewStyle().Foreground(lipgloss.Color("250")).Background(lipgloss.Color("236"))
	activeTabStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("15")).Background(lipgloss.Color("62"))
	bodyStyle      = lipgloss.NewStyle().Foreground(lipgloss.Color("245")).Padding(0, 1).BorderLeft(true).BorderStyle(lipgloss.NormalBorder()).BorderForeground(lipgloss.Color("62"))
)
