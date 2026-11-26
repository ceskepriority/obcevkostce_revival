package dash

import "fmt"

// The demografie dashboard: a port of demo/build.py. Each chart of the obec is
// followed by its comparison with the selected territories, one line each.

const smooth = 3 // years in the trailing moving average of rates

var (
	ageKeys   = []string{"0 - 14 let", "15 - 64 let", "65 a více let"}
	ageLabel  = map[string]string{"0 - 14 let": "0–14 let", "15 - 64 let": "15–64 let", "65 a více let": "65 a více let"}
	ageTabs   = []string{"15 - 64 let", "65 a více let", "0 - 14 let"} // order of age-group tabs in comparison charts
	ageCol    = map[string]string{"0 - 14 let": "v0", "15 - 64 let": "v1", "65 a více let": "v2"}
	agePalett = []string{"#6250d6", "#eb6834", "#0f9aa8"} // age groups: apart from the growth chart's blue/red
)

// Section is a heading placed before the chart pair that opens it.
type Section struct{ ID, Title string }

// Pair is a chart of the obec and its comparison.
type Pair struct {
	Section *Section
	Chart   string
	Compare string
}

// Input of a dashboard: the obec, the comparison territories and the ČSÚ sources by dataset.
type Input struct {
	Obec    *Territory
	Peers   []*Territory
	Sources map[string]Source // OBY01B01, OBY02E, OBY06A
	CmpNote string            // describes the comparison territories
}

// Demografie returns the charts by id and the page layout.
func Demografie(in Input) (map[string]*Chart, []Pair) {
	me := in.Obec
	terrs := append([]*Territory{me}, in.Peers...)
	charts := map[string]*Chart{}
	var pairs []Pair
	add := func(sec *Section, a, b *Chart) {
		charts[a.Spec.ID], charts[b.Spec.ID] = a, b
		pairs = append(pairs, Pair{Section: sec, Chart: a.Spec.ID, Compare: b.Spec.ID})
	}

	x := Aes{Type: "x", Field: "rok", Label: "Rok", Format: "year"}
	// territories by colour; the obec stands out (thicker line)
	byTerritory := []Aes{{Type: "colour", Field: "uzemi", Label: "Území"}, {Type: "highlight", Field: "obec"}}
	cmpCols := func(cols ...string) []string { return append([]string{"rok", "uzemi", "obec"}, cols...) }
	cmpRow := func(t *Territory, y int) map[string]any { return map[string]any{"rok": y, "uzemi": t.Label, "obec": t.Self} }
	srcOby, srcAge, srcMig := in.Sources["OBY01B01"], in.Sources["OBY02E"], in.Sources["OBY06A"]
	smoothNote := fmt.Sprintf("Míry jsou vyhlazené %dletým klouzavým průměrem (součet událostí za %d roky / součet středních stavů).", smooth, smooth)
	cmpNote := in.CmpNote

	// ---------- 1. population
	pop := me.Pop()
	var rows []map[string]any
	for _, y := range years(pop) {
		rows = append(rows, map[string]any{"rok": y, "obyvatele": pop[y]})
	}
	a := newChart("obyvatele", []string{"rok", "obyvatele"}, rows, chartArgs{
		typ: "line", title: "Počet obyvatel", subtitle: "Počet obyvatel k 31. 12. daného roku",
		aes:     []Aes{x, {Type: "y", Field: "obyvatele", Label: "Počet obyvatel"}},
		sources: []Source{srcOby}, options: map[string]any{"y_zero": false}})

	rows = nil
	for _, t := range terrs {
		p := t.Pop()
		ys := years(p)
		if len(ys) == 0 {
			continue
		}
		base, ok := p[2000]
		if !ok {
			base = p[ys[0]]
		}
		if base == 0 {
			continue
		}
		for _, y := range ys {
			r := cmpRow(t, y)
			r["index"] = 100 * p[y] / base
			rows = append(rows, r)
		}
	}
	b := newChart("obyvatele-srovnani", cmpCols("index"), rows, chartArgs{
		typ: "line", title: "Vývoj počtu obyvatel ve srovnání",
		subtitle: "Počet obyvatel k 31. 12., index (rok 2000 = 100)",
		aes:      append([]Aes{x, {Type: "y", Field: "index", Label: "Index (2000 = 100)"}}, byTerritory...),
		sources:  []Source{srcOby}, options: map[string]any{"y_zero": false, "annotations": []map[string]any{{"y": 100}}},
		note:     cmpNote})
	add(&Section{"pocet-obyvatel", "Počet obyvatel"}, a, b)

	// ---------- 2. growth
	nat, mig, mid := me.Natural(), me.Migration(), me.Mid()
	rows = nil
	for _, y := range years(nat, mig) {
		for _, s := range []struct {
			name string
			v    Values
		}{{"Přirozený přírůstek", nat}, {"Čistá migrace", mig}} {
			rows = append(rows, map[string]any{"rok": y, "slozka": s.name, "osoby": s.v[y], "podil": div(s.v[y], mid, y)})
		}
	}
	a = newChart("prirustek", []string{"rok", "slozka", "osoby", "podil"}, rows, chartArgs{
		typ: "bar", title: "Populační růst",
		subtitle: "Změna počtu obyvatel rozdělená na [přirozený přírůstek::Živě narození − zemřelí] " +
			"a [čistou migraci::Přistěhovalí − vystěhovalí]",
		tabs: []Tab{{"abs", "Osoby"}, {"pct", "% obyvatel"}},
		aes: []Aes{x, {Type: "y", Field: "osoby", Label: "Změna", Unit: "os.", Tab: "abs"},
			{Type: "y", Field: "podil", Label: "Změna", Format: "percent", Tab: "pct"},
			{Type: "fill", Field: "slozka", Label: "Složka"}},
		sources: []Source{srcOby}, note: "Podíl = změna / počet obyvatel k 1. 7."})

	rows = nil
	for _, t := range terrs {
		natT, migT := t.Natural(), t.Migration()
		both := Values{}
		for _, y := range years(natT, migT) {
			both[y] = natT[y] + migT[y]
		}
		n, m, c := Rate(natT, t.Mid(), 1000, smooth), Rate(migT, t.Mid(), 1000, smooth), Rate(both, t.Mid(), 1000, smooth)
		for _, y := range yearsAny(n, m) {
			r := cmpRow(t, y)
			r["celkem"], r["prirozeny"], r["migrace"] = opt(c, y), opt(n, y), opt(m, y)
			rows = append(rows, r)
		}
	}
	b = newChart("prirustek-srovnani", cmpCols("celkem", "prirozeny", "migrace"), rows, chartArgs{
		typ: "line", title: "Populační růst ve srovnání",
		subtitle: fmt.Sprintf("Na 1 000 obyvatel, %dletý klouzavý průměr", smooth),
		tabs:     []Tab{{"tot", "Celkový přírůstek"}, {"nat", "Přirozený přírůstek"}, {"mig", "Čistá migrace"}},
		aes: append([]Aes{x,
			{Type: "y", Field: "celkem", Label: "Celkový přírůstek", Unit: "‰", Tab: "tot"},
			{Type: "y", Field: "prirozeny", Label: "Přirozený přírůstek", Unit: "‰", Tab: "nat"},
			{Type: "y", Field: "migrace", Label: "Čistá migrace", Unit: "‰", Tab: "mig"}}, byTerritory...),
		sources: []Source{srcOby}, note: smoothNote + " " + cmpNote})
	add(nil, a, b)

	// ---------- 3. age groups
	total := me.AgeEnd("")
	rows = nil
	for _, y := range years(total, me.AgeEnd(ageKeys[0]), me.AgeEnd(ageKeys[1]), me.AgeEnd(ageKeys[2])) {
		for _, k := range ageKeys {
			v := me.AgeEnd(k)[y]
			rows = append(rows, map[string]any{"rok": y, "skupina": ageLabel[k], "osoby": v, "podil": div(v, total, y)})
		}
	}
	a = newChart("vek-skupiny", []string{"rok", "skupina", "osoby", "podil"}, rows, chartArgs{
		typ: "line", title: "Věkové složení", subtitle: "Obyvatelé v hlavních věkových skupinách k 31. 12.",
		tabs: []Tab{{"pct", "Podíl"}, {"abs", "Osoby"}},
		aes: []Aes{x, {Type: "y", Field: "podil", Label: "Podíl", Format: "percent", Tab: "pct"},
			{Type: "y", Field: "osoby", Label: "Počet obyvatel", Tab: "abs"},
			{Type: "colour", Field: "skupina", Label: "Věková skupina"}},
		sources: []Source{srcAge}, options: map[string]any{"palette": agePalett}})

	var ageTabList []Tab
	for _, k := range ageTabs {
		ageTabList = append(ageTabList, Tab{ageCol[k], ageLabel[k]})
	}
	ageAes := func(label func(k string) string, unit, format string) []Aes {
		out := []Aes{x}
		for _, k := range ageKeys {
			out = append(out, Aes{Type: "y", Field: ageCol[k], Label: label(k), Unit: unit, Format: format, Tab: ageCol[k]})
		}
		return append(out, byTerritory...)
	}
	rows = nil
	for _, t := range terrs {
		tot := t.AgeEnd("")
		for _, y := range years(tot) {
			r := cmpRow(t, y)
			for _, k := range ageKeys {
				r[ageCol[k]] = nil
				if v, ok := t.AgeEnd(k)[y]; ok {
					r[ageCol[k]] = div(v, tot, y)
				}
			}
			rows = append(rows, r)
		}
	}
	b = newChart("vek-skupiny-srovnani", cmpCols("v0", "v1", "v2"), rows, chartArgs{
		typ: "line", title: "Věkové složení ve srovnání", subtitle: "Podíl obyvatel věkové skupiny k 31. 12., %",
		tabs:    ageTabList,
		aes:     ageAes(func(k string) string { return "Podíl " + ageLabel[k] }, "", "percent"),
		sources: []Source{srcAge}, options: map[string]any{"y_zero": false}, note: cmpNote})
	add(&Section{"vekova-struktura", "Věková struktura"}, a, b)

	// ---------- 4. dependency
	type ratio struct {
		name, col string
		f         func(k, w, o float64) float64
	}
	ratios := []ratio{ // order of the obec's chart
		{"Dětská", "detska", func(k, w, o float64) float64 { return 100 * k / w }},
		{"Seniorská", "seniorska", func(k, w, o float64) float64 { return 100 * o / w }},
		{"Celková", "celkova", func(k, w, o float64) float64 { return 100 * (k + o) / w }},
	}
	depYears := func(t *Territory) (ys []int, k, w, o Values) {
		k, w, o = t.AgeEnd(ageKeys[0]), t.AgeEnd(ageKeys[1]), t.AgeEnd(ageKeys[2])
		for _, y := range years(k, w, o) {
			if w[y] != 0 {
				ys = append(ys, y)
			}
		}
		return
	}
	depNote := "Vypočteno z počtu obyvatel podle základních věkových skupin. Dětská = (0–14) / (15–64), " +
		"seniorská = (65+) / (15–64), celková = součet obou; vše × 100."
	rows = nil
	ys, k, w, o := depYears(me)
	for _, r := range ratios {
		for _, y := range ys {
			rows = append(rows, map[string]any{"rok": y, "index": r.name, "hodnota": r.f(k[y], w[y], o[y])})
		}
	}
	a = newChart("zavislost", []string{"rok", "index", "hodnota"}, rows, chartArgs{
		typ: "line", title: "Index závislosti",
		subtitle: "Počet [dětí::0–14 let] a [seniorů::65 a více let] na 100 obyvatel " +
			"v produktivním věku (15–64 let), k 31. 12.",
		aes: []Aes{x, {Type: "y", Field: "hodnota", Label: "Na 100 osob 15–64 let"},
			{Type: "colour", Field: "index", Label: "Závislost"}},
		sources: []Source{srcAge}, note: depNote})

	cmpRatios := []ratio{ratios[2], ratios[0], ratios[1]} // tabs: Celková, Dětská, Seniorská
	rows = nil
	for _, t := range terrs {
		ys, k, w, o := depYears(t)
		for _, y := range ys {
			r := cmpRow(t, y)
			for _, q := range cmpRatios {
				r[q.col] = q.f(k[y], w[y], o[y])
			}
			rows = append(rows, r)
		}
	}
	depAes := []Aes{x}
	var depTabs []Tab
	for _, q := range cmpRatios {
		depTabs = append(depTabs, Tab{q.col, q.name})
		depAes = append(depAes, Aes{Type: "y", Field: q.col, Label: q.name + " závislost", Tab: q.col})
	}
	b = newChart("zavislost-srovnani", cmpCols("celkova", "detska", "seniorska"), rows, chartArgs{
		typ: "line", title: "Index závislosti ve srovnání", subtitle: "Na 100 obyvatel ve věku 15–64 let, k 31. 12.",
		tabs: depTabs, aes: append(depAes, byTerritory...),
		sources: []Source{srcAge}, options: map[string]any{"y_zero": false}, note: depNote + " " + cmpNote})
	add(nil, a, b)

	// ---------- 5. net migration by age
	rows = nil
	for _, y := range years(me.MigrationAge(ageKeys[0]), me.MigrationAge(ageKeys[1]), me.MigrationAge(ageKeys[2])) {
		for _, k := range ageKeys {
			v := me.MigrationAge(k)[y]
			rows = append(rows, map[string]any{"rok": y, "skupina": ageLabel[k], "osoby": v, "podil": div(v, mid, y)})
		}
	}
	a = newChart("migrace-vek", []string{"rok", "skupina", "osoby", "podil"}, rows, chartArgs{
		typ: "bar", title: "Čistá migrace podle věku",
		subtitle: "Přistěhovalí − vystěhovalí v hlavních věkových skupinách",
		tabs:     []Tab{{"abs", "Osoby"}, {"pct", "% obyvatel"}},
		aes: []Aes{x, {Type: "y", Field: "osoby", Label: "Saldo", Unit: "os.", Tab: "abs"},
			{Type: "y", Field: "podil", Label: "Saldo", Format: "percent", Tab: "pct"},
			{Type: "fill", Field: "skupina", Label: "Věková skupina"}},
		sources: []Source{srcMig}, options: map[string]any{"palette": agePalett},
		note: "Podíl = saldo věkové skupiny / všichni obyvatelé k 1. 7., takže sloupec dává celkovou míru čisté migrace."})

	rows = nil
	for _, t := range terrs {
		rates := map[string]Values{}
		var all []Values
		for _, k := range ageKeys {
			rates[k] = Rate(t.MigrationAge(k), t.AgeMid(k), 1000, smooth)
			all = append(all, rates[k])
		}
		for _, y := range yearsAny(all...) {
			r := cmpRow(t, y)
			for _, k := range ageKeys {
				r[ageCol[k]] = opt(rates[k], y)
			}
			rows = append(rows, r)
		}
	}
	b = newChart("migrace-srovnani", cmpCols("v0", "v1", "v2"), rows, chartArgs{
		typ: "line", title: "Míra čisté migrace podle věku ve srovnání",
		subtitle: fmt.Sprintf("Saldo migrace na 1 000 obyvatel dané věkové skupiny, %dletý klouzavý průměr", smooth),
		tabs:     ageTabList,
		aes:      ageAes(func(k string) string { return "Čistá migrace " + ageLabel[k] }, "‰", ""),
		sources:  []Source{srcMig, srcAge},
		note:     "Obyvatelé věkové skupiny = průměr stavů k 1. 1. a 31. 12. " + smoothNote + " " + cmpNote})
	add(&Section{"migrace", "Migrace"}, a, b)

	return charts, pairs
}

// Datasets whose sources the dashboard cites.
var Datasets = []string{"OBY01B01", "OBY02E", "OBY06A"}
