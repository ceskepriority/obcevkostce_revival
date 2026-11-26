// Package dash builds the charts of a dashboard (Charta specs + CSV) from the
// annual values of an obec and its comparison territories.
package dash

import (
	"slices"
	"sort"
	"strings"
)

// Values are annual values by year.
type Values map[int]float64

// Row is one annual value of one series of a territory.
type Row struct {
	Indicator string
	Breakdown map[string]string
	Year      int
	Value     float64
}

// Territory is the obec or one comparison item (a group, an obec, a custom set).
type Territory struct {
	Label string
	Self  bool // the obec the dashboard is about
	data  map[string]Values
}

func seriesKey(indicator string, breakdown map[string]string) string {
	keys := make([]string, 0, len(breakdown))
	for k := range breakdown {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	b.WriteString(indicator)
	for _, k := range keys {
		b.WriteString("|" + k + "=" + breakdown[k])
	}
	return b.String()
}

// NewTerritory indexes rows by series; rows of the same series and year are summed.
func NewTerritory(label string, self bool, rows []Row) *Territory {
	t := &Territory{Label: label, Self: self, data: map[string]Values{}}
	for _, r := range rows {
		k := seriesKey(r.Indicator, r.Breakdown)
		if t.data[k] == nil {
			t.data[k] = Values{}
		}
		t.data[k][r.Year] += r.Value
	}
	return t
}

// Get returns the values of one series (an empty map if there are none).
func (t *Territory) Get(indicator string, breakdown map[string]string) Values {
	if v := t.data[seriesKey(indicator, breakdown)]; v != nil {
		return v
	}
	return Values{}
}

// Indicators read by the demografie dashboard.
var Indicators = []string{
	"czso.2406K", "czso.2406K2", "czso.2406P", "czso.9379W", "czso.5401", "czso.5402", "czso.5402W",
}

func age(a string) map[string]string {
	if a == "" {
		return nil
	}
	return map[string]string{"věk": a}
}

func (t *Territory) Pop() Values       { return t.Get("czso.2406K", nil) } // 31. 12.
func (t *Territory) Mid() Values       { return t.Get("czso.9379W", nil) } // 1. 7.
func (t *Territory) Natural() Values   { return t.Get("czso.5401", nil) }
func (t *Territory) Migration() Values { return t.Get("czso.5402", nil) }

// AgeEnd: 31. 12., by basic age group ("" = total).
func (t *Territory) AgeEnd(a string) Values { return t.Get("czso.2406K2", age(a)) }

// AgeStart: 1. 1., by basic age group.
func (t *Territory) AgeStart(a string) Values { return t.Get("czso.2406P", age(a)) }

// AgeMid: mean of 1. 1. and 31. 12.
func (t *Territory) AgeMid(a string) Values {
	s, e := t.AgeStart(a), t.AgeEnd(a)
	out := Values{}
	for y, v := range s {
		if w, ok := e[y]; ok {
			out[y] = (v + w) / 2
		}
	}
	return out
}

func (t *Territory) MigrationAge(a string) Values { return t.Get("czso.5402W", age(a)) }

// Rate is events / base * per over a trailing window of `smooth` years (ratio of sums).
func Rate(events, base Values, per float64, smooth int) Values {
	out := Values{}
	for _, y := range years(events, base) {
		var e, b float64
		complete := true
		for w := y - smooth + 1; w <= y; w++ {
			ev, ok1 := events[w]
			bv, ok2 := base[w]
			if !ok1 || !ok2 {
				complete = false
				break
			}
			e, b = e+ev, b+bv
		}
		if complete && b != 0 {
			out[y] = per * e / b
		}
	}
	return out
}

// years returns the sorted years present in all of vs.
func years(vs ...Values) []int {
	if len(vs) == 0 {
		return nil
	}
	var out []int
	for y := range vs[0] {
		in := true
		for _, v := range vs[1:] {
			if _, ok := v[y]; !ok {
				in = false
				break
			}
		}
		if in {
			out = append(out, y)
		}
	}
	slices.Sort(out)
	return out
}

// yearsAny returns the sorted years present in any of vs.
func yearsAny(vs ...Values) []int {
	seen := map[int]bool{}
	var out []int
	for _, v := range vs {
		for y := range v {
			if !seen[y] {
				seen[y] = true
				out = append(out, y)
			}
		}
	}
	slices.Sort(out)
	return out
}

// opt is v[y], or nil (an empty CSV cell) when there is none.
func opt(v Values, y int) any {
	if x, ok := v[y]; ok {
		return x
	}
	return nil
}

// div is a / b, or nil when b is missing or zero.
func div(a float64, b Values, y int) any {
	if x, ok := b[y]; ok && x != 0 {
		return a / x
	}
	return nil
}
