package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/charmbracelet/bubbles/help"
	"github.com/charmbracelet/bubbles/key"
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/table"
	textinput "github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	_ "github.com/mattn/go-sqlite3"
)

// ----- Styling -----
var (
	colorBackground = lipgloss.Color("#000000") // black
	colorText       = lipgloss.Color("#00ff00") // matrix green
	colorAccent     = lipgloss.Color("#00ff00") // matrix green accent
	borderStyle     = lipgloss.ThickBorder()
	styleBase       = lipgloss.NewStyle().Background(colorBackground).Foreground(colorText)
	styleBox        = styleBase.Border(borderStyle, true).BorderForeground(colorAccent).Padding(1, 2)
	styleTitle      = styleBase.Bold(true).Foreground(colorAccent).Align(lipgloss.Center)
	styleCenterText = styleBase.Align(lipgloss.Center)
)

// ----- Key Bindings -----
type keyMap struct {
	Upload key.Binding
	Search key.Binding
	Quit   key.Binding
}

var keys = keyMap{
	Upload: key.NewBinding(key.WithKeys("u"), key.WithHelp("u", "upload PDF")),
	Search: key.NewBinding(key.WithKeys("s"), key.WithHelp("s", "search PO")),
	Quit:   key.NewBinding(key.WithKeys("q"), key.WithHelp("q", "quit")),
}

func (k keyMap) ShortHelp() []key.Binding {
	return []key.Binding{k.Upload, k.Search, k.Quit}
}

func (k keyMap) FullHelp() [][]key.Binding {
	return [][]key.Binding{
		{k.Upload, k.Search},
		{k.Quit},
	}
}

// ----- Model -----
type tab int

const (
	tabUpload tab = iota
	tabSearch
)

type model struct {
	activeTab tab
	status    string
	output    string
	spinner   spinner.Model
	table     table.Model
	help      help.Model
	loading   bool

	searchInput textinput.Model
	searchResult string
	pdfPath      string
	width        int
	height       int
}

func (m model) Init() tea.Cmd {
	return nil
}

func initialModel() model {
	columns := []table.Column{
		{Title: "Field", Width: 15},
		{Title: "Value", Width: 30},
	}
	t := table.New(table.WithColumns(columns))
	t.SetStyles(table.DefaultStyles())

	sp := spinner.New()
	sp.Style = styleBase.Foreground(colorAccent)

	si := textinput.New()
	si.Placeholder = "Enter PO number..."
	si.Focus()
	si.CharLimit = 20
	si.Width = 30

	return model{
		activeTab: tabUpload,
		status:    "Press 'u' to upload a PDF...",
		spinner:   sp,
		help:      help.New(),
		table:     t,
		searchInput: si,
	}
}

// ----- Msg Types -----
type fileSelectedMsg string

type parseResultMsg struct {
	Output string
	Err    error
}

type searchResultMsg struct {
	Result string
	PDF    string
	Err    error
}

func openFileDialog() tea.Msg {
	cmd := exec.Command("zenity", "--file-selection", "--file-filter=PDF files (pdf) | *.pdf")
	out, err := cmd.Output()
	if err != nil {
		return fileSelectedMsg("")
	}
	return fileSelectedMsg(strings.TrimSpace(string(out)))
}

func runPythonParser(filePath string) tea.Cmd {
	return func() tea.Msg {
		cmd := exec.Command("python3", "parse_cli.py", filePath)
		out, err := cmd.CombinedOutput()
		if err != nil {
			return parseResultMsg{"", fmt.Errorf("Python error: %v\nOutput: %s", err, string(out))}
		}
		var jsonObj map[string]interface{}
		err = json.Unmarshal(out, &jsonObj)
		if err != nil {
			return parseResultMsg{"", fmt.Errorf("JSON parse error: %v\nOutput: %s", err, string(out))}
		}
		formatted, _ := json.MarshalIndent(jsonObj, "", "  ")
		return parseResultMsg{string(formatted), nil}
	}
}

func searchDatabase(po string) tea.Cmd {
	return func() tea.Msg {
		db, err := sql.Open("sqlite3", "warehouse.db")
		if err != nil {
			return searchResultMsg{"", "", fmt.Errorf("DB open error: %v", err)}
		}
		defer db.Close()

		var pdfPath string
		err = db.QueryRow("SELECT pdf_path FROM purchase_orders WHERE po_number = ?", po).Scan(&pdfPath)
		if err == sql.ErrNoRows {
			return searchResultMsg{"PO not found.", "", nil}
		} else if err != nil {
			return searchResultMsg{"", "", fmt.Errorf("DB query error: %v", err)}
		}
		return searchResultMsg{fmt.Sprintf("PDF found: %s", pdfPath), pdfPath, nil}
	}
}

func openPDF(pdfPath string) tea.Cmd {
	return func() tea.Msg {
		exec.Command("xdg-open", pdfPath).Start()
		return nil
	}
}

// ----- Update -----
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch {
		case key.Matches(msg, keys.Quit):
			return m, tea.Quit
		case key.Matches(msg, keys.Upload):
			m.activeTab = tabUpload
			m.status = "Opening file picker..."
			m.loading = true
			return m, tea.Batch(openFileDialog, m.spinner.Tick)
		case key.Matches(msg, keys.Search):
			m.activeTab = tabSearch
			m.status = "Search active. Type PO and press Enter."
			return m, nil
		case msg.String() == "enter" && m.activeTab == tabSearch:
			po := m.searchInput.Value()
			m.status = "Searching database..."
			m.loading = true
			return m, tea.Batch(searchDatabase(po), m.spinner.Tick)
		case msg.String() == "o" && m.activeTab == tabSearch && m.pdfPath != "":
			m.status = "Opening PDF..."
			return m, openPDF(m.pdfPath)
		}
	case fileSelectedMsg:
		if msg == "" {
			m.status = "No file selected."
			m.loading = false
			return m, nil
		}
		m.status = "Parsing file..."
		return m, runPythonParser(string(msg))
	case parseResultMsg:
		m.loading = false
		if msg.Err != nil {
			m.status = "Error parsing file."
			m.output = msg.Err.Error()
			return m, nil
		}
		m.status = "Parsing complete."
		m.output = msg.Output
		var parsed map[string]interface{}
		_ = json.Unmarshal([]byte(msg.Output), &parsed)
		rows := []table.Row{}
		for k, v := range parsed {
			rows = append(rows, table.Row{k, fmt.Sprintf("%v", v)})
		}
		m.table.SetRows(rows)
		return m, nil
	case searchResultMsg:
		m.loading = false
		if msg.Err != nil {
			m.status = "Search error."
			m.searchResult = msg.Err.Error()
			m.pdfPath = ""
			return m, nil
		}
		m.status = "Search complete. Press 'o' to open PDF."
		m.searchResult = msg.Result
		m.pdfPath = msg.PDF
		return m, nil
	case spinner.TickMsg:
		if m.loading {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			return m, cmd
		}
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
	}
	var cmd tea.Cmd
	m.searchInput, cmd = m.searchInput.Update(msg)
	return m, cmd
}

// ----- View -----
func (m model) View() string {
	tabTitle := "[ Upload Tab ]"
	if m.activeTab == tabSearch {
		tabTitle = "[ Search Tab ]"
	}
	top := styleTitle.Width(m.width).Render("PDF PARSER TERMINAL UI") + "\n" + styleTitle.Width(m.width).Render(tabTitle) + "\n\n"
	status := styleCenterText.Width(m.width).Render("Status: " + m.status)
	content := ""

	if m.activeTab == tabUpload {
		if m.loading {
			content = styleCenterText.Width(m.width).Render(m.spinner.View() + " Parsing...")
		} else if m.output != "" {
			content = m.table.View()
		} else {
			content = styleCenterText.Width(m.width).Render("No output yet.")
		}
	} else if m.activeTab == tabSearch {
		content = styleCenterText.Width(m.width).Render("Search PO:") + "\n" + m.searchInput.View() + "\n\n" + styleCenterText.Width(m.width).Render(m.searchResult)
	}

	footer := styleCenterText.Width(m.width).Render(m.help.View(keys))
	box := styleBox.Width(m.width - 4).Height(m.height - 4).Render(top + content + "\n\n" + status + "\n\n" + footer)
	return box
}

func main() {
	p := tea.NewProgram(initialModel(), tea.WithAltScreen())
	if err := p.Start(); err != nil {
		fmt.Println("Error:", err)
		os.Exit(1)
	}
}


