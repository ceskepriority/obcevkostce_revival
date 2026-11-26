package dash

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"time"
)

// Aes is one aesthetic of a Charta chart.
type Aes struct {
	Type   string `json:"type"`
	Field  string `json:"field"`
	Label  string `json:"label,omitempty"`
	Format string `json:"format,omitempty"`
	Unit   string `json:"unit,omitempty"`
	Tab    string `json:"tab,omitempty"`
}

type Tab struct {
	ID    string `json:"id"`
	Label string `json:"label"`
}

type Source struct {
	Name    string `json:"name"`
	Dataset string `json:"dataset"`
	URL     string `json:"url"`
}

// Spec is the {id}.json of a chart, as write_chart() in demo/build.py writes it.
type Spec struct {
	ID          string         `json:"id"`
	Data        string         `json:"data"`
	Type        string         `json:"type"`
	Title       string         `json:"title"`
	Subtitle    string         `json:"subtitle"`
	Aes         []Aes          `json:"aes"`
	Source      []Source       `json:"source"`
	DataNote    string         `json:"data_note"`
	Options     map[string]any `json:"options"`
	LastUpdated string         `json:"last_updated"`
	Rows        int            `json:"rows"`
	Tabs        []Tab          `json:"tabs,omitempty"`
}

// Chart is a spec with its data; Cols orders the CSV columns.
type Chart struct {
	Spec Spec
	Cols []string
	Rows []map[string]any
}

type chartArgs struct {
	typ, title, subtitle, note string
	aes                        []Aes
	sources                    []Source
	options                    map[string]any
	tabs                       []Tab
}

func newChart(id string, cols []string, rows []map[string]any, a chartArgs) *Chart {
	opts := map[string]any{"timeline": false} // no year slider: the series are short
	for k, v := range a.options {
		opts[k] = v
	}
	return &Chart{
		Spec: Spec{ID: id, Data: id + ".csv", Type: a.typ, Title: a.title, Subtitle: a.subtitle,
			Aes: a.aes, Source: a.sources, DataNote: a.note, Options: opts,
			LastUpdated: time.Now().Format("2006-01-02"), Rows: len(rows), Tabs: a.tabs},
		Cols: cols, Rows: rows,
	}
}

func (c *Chart) JSON() ([]byte, error) { return json.MarshalIndent(c.Spec, "", "  ") }

func (c *Chart) CSV() ([]byte, error) {
	var buf bytes.Buffer
	w := csv.NewWriter(&buf)
	if err := w.Write(c.Cols); err != nil {
		return nil, err
	}
	rec := make([]string, len(c.Cols))
	for _, r := range c.Rows {
		for i, col := range c.Cols {
			rec[i] = cell(r[col])
		}
		if err := w.Write(rec); err != nil {
			return nil, err
		}
	}
	w.Flush()
	return buf.Bytes(), w.Error()
}

func cell(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return ""
		}
		return strconv.FormatFloat(math.Round(x*1e6)/1e6, 'f', -1, 64)
	case int:
		return strconv.Itoa(x)
	case bool:
		if x {
			return "1"
		}
		return "0"
	case string:
		return x
	default:
		return fmt.Sprint(x)
	}
}
