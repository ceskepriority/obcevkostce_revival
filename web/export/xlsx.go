// Package export writes the data of an obec and its comparison as an Excel workbook:
// an Info sheet, then a sheet per theme with one table per indicator and breakdown
// (a row per period, a column per territory). Indicators with a single period
// (land use) share one compact table with a row per indicator.
package export

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/xuri/excelize/v2"

	"obcevkostce/web/obce"
)

// Territory is one column: the obec or a comparison item.
type Territory struct {
	Label  string
	Kind   string // shown on the Info sheet: "vybraná obec", "skupina (704 obcí)", …
	Values []obce.Value
}

type Input struct {
	Title       string   // "Nové Město na Moravě"
	Facts       []string // lines under the title
	Territories []Territory
	Indicators  []obce.Indicator
	Series      []obce.Series
}

// ---------------------------------------------------------------- themes

// theme is a sheet; indicators are listed in their order on it, or matched by prefix.
type theme struct {
	name     string
	ids      []string
	prefixes []string
	compact  bool // one table, a row per indicator (for indicators with a single period)
}

var themes = []theme{
	{name: "Počet obyvatel", ids: []string{"czso.2406K", "czso.2406K2", "czso.2406P", "czso.9379W",
		"czso.5436", "czso.2406UAP891", "czso.2406UAP905"}},
	{name: "Pohyb obyvatel", ids: []string{"czso.4355ZN", "czso.5393", "czso.5401", "czso.5403", "czso.5404",
		"czso.5402", "czso.5402W"}},
	{name: "Nezaměstnanost", prefixes: []string{"czso.5973UAP"}},
	{name: "Bydlení", prefixes: []string{"czso.3103UAP", "czso.3113UAP"}},
	{name: "Využití půdy", prefixes: []string{"czso.4582UAP", "czso.5594UAP"}, compact: true},
}

const otherTheme = "Ostatní"

// themeOf returns the sheet of an indicator and its rank on it.
func themeOf(id string) (int, int) {
	for t, th := range themes {
		for i, x := range th.ids {
			if x == id {
				return t, i
			}
		}
		for _, p := range th.prefixes {
			if strings.HasPrefix(id, p) {
				return t, len(th.ids)
			}
		}
	}
	return len(themes), 0
}

// ---------------------------------------------------------------- labels

var methodLabel = map[string]string{
	"SUM":           "součet za obce",
	"MEAN":          "prostý průměr obcí",
	"WEIGHTED_MEAN": "vážený průměr obcí",
	"NONE":          "za skupiny se nepočítá",
}

func method(i obce.Indicator) string {
	if i.Method == "WEIGHTED_MEAN" && i.WeightName != "" {
		return fmt.Sprintf("vážený průměr obcí (váha: %s)", name(obce.Indicator{Name: i.WeightName}))
	}
	return methodLabel[i.Method]
}

// name of an indicator without ČSÚ's "113.10 " numbering and double spaces.
func name(i obce.Indicator) string {
	f := strings.Fields(i.Name)
	if len(f) > 1 && strings.Trim(f[0], "0123456789.") == "" {
		f = f[1:]
	}
	return strings.Join(f, " ")
}

type period struct{ start, end time.Time }

// label: a calendar year as a number, any other period as "1. 1. 2024–30. 6. 2024".
func (p period) label() any {
	if p.start.Month() == 1 && p.start.Day() == 1 && p.end.Equal(p.start.AddDate(1, 0, -1)) {
		return p.start.Year()
	}
	if p.start.Equal(p.end) {
		return p.start.Format("2. 1. 2006")
	}
	return p.start.Format("2. 1. 2006") + "–" + p.end.Format("2. 1. 2006")
}

func breakdownLabel(b map[string]string) string {
	if len(b) == 0 {
		return "celkem"
	}
	keys := make([]string, 0, len(b))
	for k := range b {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, len(keys))
	for i, k := range keys {
		parts[i] = k + ": " + b[k]
	}
	return strings.Join(parts, ", ")
}

func cell(col, row int) string {
	name, _ := excelize.CoordinatesToCellName(col, row)
	return name
}

func round(v float64) float64 { return math.Round(v*1e6) / 1e6 }

// ---------------------------------------------------------------- build

type styles struct{ bold, title, table, meta, head int }

func newStyles(f *excelize.File) (s styles, err error) {
	defs := []struct {
		dst *int
		def *excelize.Style
	}{
		{&s.bold, &excelize.Style{Font: &excelize.Font{Bold: true}}},
		{&s.title, &excelize.Style{Font: &excelize.Font{Bold: true, Size: 14}}},
		{&s.table, &excelize.Style{Font: &excelize.Font{Bold: true, Size: 12}}},
		{&s.meta, &excelize.Style{Font: &excelize.Font{Italic: true, Color: "666666"}}},
		{&s.head, &excelize.Style{Font: &excelize.Font{Bold: true},
			Alignment: &excelize.Alignment{WrapText: true, Vertical: "top"},
			Border:    []excelize.Border{{Type: "bottom", Color: "000000", Style: 1}}}},
	}
	for _, d := range defs {
		if *d.dst, err = f.NewStyle(d.def); err != nil {
			return
		}
	}
	return
}

// table of contents entry on the Info sheet
type tocEntry struct {
	sheet, title string
	row          int
	ind          obce.Indicator
}

func Build(in Input) (*excelize.File, error) {
	f := excelize.NewFile()
	st, err := newStyles(f)
	if err != nil {
		return nil, err
	}
	nt := len(in.Territories)

	// values[territory][series][period]
	values := make([]map[int]map[period]float64, nt)
	for t, tr := range in.Territories {
		values[t] = map[int]map[period]float64{}
		for _, v := range tr.Values {
			if values[t][v.Series] == nil {
				values[t][v.Series] = map[period]float64{}
			}
			values[t][v.Series][period{v.Start, v.End}] = v.Value
		}
	}
	// periods with a value, per series
	periodsOf := func(s obce.Series) []period {
		seen := map[period]bool{}
		var out []period
		for t := range in.Territories {
			for p := range values[t][s.Key] {
				if !seen[p] {
					seen[p] = true
					out = append(out, p)
				}
			}
		}
		sort.Slice(out, func(i, j int) bool {
			if !out[i].start.Equal(out[j].start) {
				return out[i].start.Before(out[j].start)
			}
			return out[i].end.Before(out[j].end)
		})
		return out
	}
	byIndicator := map[string][]obce.Series{}
	for _, s := range in.Series {
		byIndicator[s.Indicator] = append(byIndicator[s.Indicator], s)
	}
	for _, ss := range byIndicator {
		sort.Slice(ss, func(i, j int) bool {
			a, b := breakdownLabel(ss[i].Breakdown), breakdownLabel(ss[j].Breakdown)
			if (a == "celkem") != (b == "celkem") {
				return a == "celkem"
			}
			return a < b
		})
	}

	// indicators by sheet, in sheet order
	sheetInds := make([][]obce.Indicator, len(themes)+1)
	inds := append([]obce.Indicator(nil), in.Indicators...)
	sort.SliceStable(inds, func(i, j int) bool {
		ti, ri := themeOf(inds[i].ID)
		tj, rj := themeOf(inds[j].ID)
		if ti != tj {
			return ti < tj
		}
		if ri != rj {
			return ri < rj
		}
		return inds[i].ID < inds[j].ID
	})
	for _, ind := range inds {
		t, _ := themeOf(ind.ID)
		sheetInds[t] = append(sheetInds[t], ind)
	}

	var toc []tocEntry
	for t, list := range sheetInds {
		sheet, compact := otherTheme, false
		if t < len(themes) {
			sheet, compact = themes[t].name, themes[t].compact
		}
		// leave out series (and so indicators, sheets) without any value
		type part struct {
			ind     obce.Indicator
			series  obce.Series
			periods []period
			multi   bool // the indicator has more than one breakdown
		}
		var parts []part
		for _, ind := range list {
			var ps []part
			for _, s := range byIndicator[ind.ID] {
				if p := periodsOf(s); len(p) > 0 {
					ps = append(ps, part{ind: ind, series: s, periods: p})
				}
			}
			for i := range ps {
				ps[i].multi = len(ps) > 1
			}
			parts = append(parts, ps...)
		}
		if len(parts) == 0 {
			continue
		}
		if _, err := f.NewSheet(sheet); err != nil {
			return nil, err
		}
		f.SetCellValue(sheet, "A1", sheet)
		f.SetCellStyle(sheet, "A1", "A1", st.title)
		f.SetCellValue(sheet, "A2", "Obsah a vysvětlivky: list Info. Hodnoty skupin jsou spočteny z hodnot jejich obcí; obce bez údaje se nezapočítávají.")
		f.SetCellStyle(sheet, "A2", "A2", st.meta)
		r := 4

		if compact {
			// one table: a row per indicator × breakdown × period
			cols := []string{"Ukazatel", "Období", "Jednotka"}
			for _, tr := range in.Territories {
				cols = append(cols, tr.Label)
			}
			cols = append(cols, "Hodnoty skupin", "Kód", "Zdroj (ČSÚ)")
			for c, h := range cols {
				f.SetCellValue(sheet, cell(c+1, r), h)
			}
			f.SetCellStyle(sheet, cell(1, r), cell(len(cols), r), st.head)
			f.SetPanes(sheet, &excelize.Panes{Freeze: true, XSplit: 1, YSplit: r, TopLeftCell: cell(2, r+1), ActivePane: "bottomRight"})
			for _, p := range parts {
				label := name(p.ind)
				if p.multi {
					label += " – " + breakdownLabel(p.series.Breakdown)
				}
				toc = append(toc, tocEntry{sheet, label, r + 1, p.ind})
				for _, per := range p.periods {
					r++
					f.SetCellValue(sheet, cell(1, r), label)
					f.SetCellValue(sheet, cell(2, r), per.label())
					f.SetCellValue(sheet, cell(3, r), p.ind.Unit)
					for t := range in.Territories {
						if v, ok := values[t][p.series.Key][per]; ok {
							f.SetCellValue(sheet, cell(4+t, r), round(v))
						}
					}
					f.SetCellValue(sheet, cell(4+nt, r), method(p.ind))
					f.SetCellValue(sheet, cell(5+nt, r), p.ind.ID)
					f.SetCellValue(sheet, cell(6+nt, r), p.ind.Source)
				}
			}
			f.SetColWidth(sheet, "A", "A", 52)
			f.SetColWidth(sheet, "B", "C", 11)
			last, _ := excelize.ColumnNumberToName(3 + nt)
			f.SetColWidth(sheet, "D", last, 16)
			continue
		}

		// stacked tables: a row per period, a column per territory
		for _, p := range parts {
			title := name(p.ind)
			if p.multi {
				title += " – " + breakdownLabel(p.series.Breakdown)
			}
			toc = append(toc, tocEntry{sheet, title, r, p.ind})
			f.SetCellValue(sheet, cell(1, r), title)
			f.SetCellStyle(sheet, cell(1, r), cell(1, r), st.table)
			r++
			meta := fmt.Sprintf("Jednotka: %s · Hodnoty skupin: %s · Kód: %s", p.ind.Unit, method(p.ind), p.ind.ID)
			if p.ind.Source != "" {
				meta += " · Zdroj: ČSÚ, " + p.ind.Source
			}
			f.SetCellValue(sheet, cell(1, r), meta)
			f.SetCellStyle(sheet, cell(1, r), cell(1, r), st.meta)
			r++
			if p.ind.Method == "NONE" {
				f.SetCellValue(sheet, cell(1, r), "Ukazatel se za skupiny obcí nepočítá, sloupce skupin jsou prázdné.")
				f.SetCellStyle(sheet, cell(1, r), cell(1, r), st.meta)
				r++
			}
			f.SetCellValue(sheet, cell(1, r), "Období")
			for t, tr := range in.Territories {
				f.SetCellValue(sheet, cell(2+t, r), tr.Label)
			}
			f.SetCellStyle(sheet, cell(1, r), cell(1+nt, r), st.head)
			for _, per := range p.periods {
				r++
				f.SetCellValue(sheet, cell(1, r), per.label())
				for t := range in.Territories {
					if v, ok := values[t][p.series.Key][per]; ok {
						f.SetCellValue(sheet, cell(2+t, r), round(v))
					}
				}
			}
			r += 3
		}
		f.SetPanes(sheet, &excelize.Panes{Freeze: true, XSplit: 1, TopLeftCell: "B1", ActivePane: "topRight"})
		last, _ := excelize.ColumnNumberToName(1 + nt)
		f.SetColWidth(sheet, "A", "A", 14)
		f.SetColWidth(sheet, "B", last, 20)
	}

	// ---------- Info
	const info = "Info"
	f.SetSheetName("Sheet1", info)
	r := 1
	put := func(vals ...any) {
		for c, v := range vals {
			f.SetCellValue(info, cell(c+1, r), v)
		}
		r++
	}
	put("Obce v kostce: " + in.Title)
	f.SetCellStyle(info, "A1", "A1", st.title)
	put("Vytvořeno " + time.Now().Format("2. 1. 2006"))
	for _, l := range in.Facts {
		put(l)
	}
	r++
	put("Území (sloupce tabulek)")
	f.SetCellStyle(info, cell(1, r-1), cell(1, r-1), st.bold)
	for _, t := range in.Territories {
		put(t.Label, t.Kind)
	}
	r++
	put("Hodnoty skupin jsou spočteny z hodnot jejich obcí podle povahy ukazatele (sloupec „Hodnoty skupin“); " +
		"obce bez údaje se nezapočítávají. Skupiny zahrnují i vybranou obec.")
	r++
	put("Tabulka", "List", "Jednotka", "Hodnoty skupin", "Kód", "Zdroj (ČSÚ)")
	f.SetCellStyle(info, cell(1, r-1), cell(6, r-1), st.head)
	for _, e := range toc {
		put(e.title, e.sheet, e.ind.Unit, method(e.ind), e.ind.ID, e.ind.Source)
		f.SetCellHyperLink(info, cell(1, r-1), fmt.Sprintf("'%s'!%s", e.sheet, cell(1, e.row)), "Location")
	}
	f.SetColWidth(info, "A", "A", 60)
	f.SetColWidth(info, "B", "B", 18)
	f.SetColWidth(info, "C", "E", 20)
	f.SetActiveSheet(0)
	return f, nil
}
