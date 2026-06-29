// Package view: ink & sage theme tokens.
//
// Mirrors loom/tui/app.py::_LOOM_INK_THEME — the single source of truth
// for the original Textual TUI's color palette. All view widgets reference
// these tokens instead of raw ANSI 256-color codes so the Go TUI stays
// visually consistent with the Python TUI.
//
// Hex values are preferred over lipgloss.Color("62")-style 256-color
// indices: hex renders identically across true-color and 256-color
// terminals (lipgloss down-samples automatically), while raw 256-color
// codes shift hue depending on the terminal's palette.
package view

import "github.com/charmbracelet/lipgloss"

// ink & sage palette (hex). See loom/tui/app.py::_LOOM_INK_THEME.
const (
	colBackground  = "#0c0e12" // main background
	colSurface     = "#0a0d11" // user message background
	colPanel       = "#13161c" // header / modal background
	colForeground  = "#c5cdd8" // default foreground
	colPrimary     = "#5b8a72" // sage (user label)
	colSecondary   = "#4a8a8a" // teal (tool name / subagent)
	colAccent      = "#5b8a72" // sage accent
	colWarning     = "#8a7a3b" // dim yellow
	colError       = "#8a3b3b" // dim red
	colSuccess     = "#4a8a5b" // dim green
	colTextMuted   = "#5c6570" // secondary text
	colTextFaint   = "#3a4048" // weakest text
	colBorder      = "#1e2328" // solid border
	colHairline    = "#1a1e24" // hairline divider
	colAccentDim   = "#2d4539" // dimmed accent (assistant left border)
	colAccentLight = "#84ad9a" // lightened accent (gear frame)
	colBoost       = "#14171d" // boost background (focus / thinking overlay)
)

// lipgloss.Color wrappers — pre-converted so styles can reference them
// without repeating the hex string at every call site.
var (
	colorBackground  = lipgloss.Color(colBackground)
	colorSurface     = lipgloss.Color(colSurface)
	colorPanel       = lipgloss.Color(colPanel)
	colorForeground  = lipgloss.Color(colForeground)
	colorPrimary     = lipgloss.Color(colPrimary)
	colorSecondary   = lipgloss.Color(colSecondary)
	colorAccent      = lipgloss.Color(colAccent)
	colorWarning     = lipgloss.Color(colWarning)
	colorError       = lipgloss.Color(colError)
	colorSuccess     = lipgloss.Color(colSuccess)
	colorTextMuted   = lipgloss.Color(colTextMuted)
	colorTextFaint   = lipgloss.Color(colTextFaint)
	colorBorder      = lipgloss.Color(colBorder)
	colorHairline    = lipgloss.Color(colHairline)
	colorAccentDim   = lipgloss.Color(colAccentDim)
	colorAccentLight = lipgloss.Color(colAccentLight)
	colorBoost       = lipgloss.Color(colBoost)
)

// Exported color accessors — let other packages (e.g. main) build their
// own lipgloss styles using the same ink & sage tokens without
// duplicating the hex values.
func ColorAccent() lipgloss.Color      { return colorAccent }
func ColorSecondary() lipgloss.Color   { return colorSecondary }
func ColorError() lipgloss.Color       { return colorError }
func ColorTextMuted() lipgloss.Color   { return colorTextMuted }
func ColorTextFaint() lipgloss.Color   { return colorTextFaint }
func ColorHairline() lipgloss.Color    { return colorHairline }
func ColorBackground() lipgloss.Color  { return colorBackground }
func ColorSurface() lipgloss.Color     { return colorSurface }
func ColorForeground() lipgloss.Color  { return colorForeground }
func ColorPrimary() lipgloss.Color     { return colorPrimary }
func ColorWarning() lipgloss.Color     { return colorWarning }
func ColorSuccess() lipgloss.Color     { return colorSuccess }
func ColorBorder() lipgloss.Color      { return colorBorder }
func ColorAccentDim() lipgloss.Color   { return colorAccentDim }
func ColorAccentLight() lipgloss.Color { return colorAccentLight }
func ColorPanel() lipgloss.Color       { return colorPanel }
