package view

import (
	"fmt"

	"github.com/charmbracelet/lipgloss"
)

// StatusBar shows model / tokens / git branch / ready-state.
type StatusBar struct {
	model  string
	tokens int
	branch string
	ready  bool
}

// NewStatusBar returns a StatusBar with the model set to "unknown".
func NewStatusBar() *StatusBar {
	return &StatusBar{model: "unknown"}
}

// SetModel updates the displayed model name.
func (s *StatusBar) SetModel(model string) { s.model = model }

// SetTokens updates the displayed token count.
func (s *StatusBar) SetTokens(tokens int) { s.tokens = tokens }

// SetBranch updates the displayed git branch.
func (s *StatusBar) SetBranch(branch string) { s.branch = branch }

// SetReady toggles the connection indicator (● ready / ○ not ready).
func (s *StatusBar) SetReady(ready bool) { s.ready = ready }

// View renders the status bar.
func (s *StatusBar) View() string {
	status := "●"
	if !s.ready {
		status = "○"
	}
	text := fmt.Sprintf("%s %s | %d tokens | %s", status, s.model, s.tokens, s.branch)
	return statusStyle.Render(text)
}

var statusStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("245")).Italic(true)
