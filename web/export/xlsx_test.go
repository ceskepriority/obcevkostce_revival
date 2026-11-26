package export

import (
	"testing"
	"time"

	"obcevkostce/web/obce"
)

func d(s string) time.Time { t, _ := time.Parse("2006-01-02", s); return t }

func TestPeriodLabel(t *testing.T) {
	for _, c := range []struct {
		p    period
		want any
	}{
		{period{d("2024-01-01"), d("2024-12-31")}, 2024},
		{period{d("2024-12-31"), d("2024-12-31")}, "31. 12. 2024"},
		{period{d("2024-01-01"), d("2024-06-30")}, "1. 1. 2024–30. 6. 2024"},
	} {
		if got := c.p.label(); got != c.want {
			t.Errorf("%v: got %v, want %v", c.p, got, c.want)
		}
	}
}

func TestNamesAndThemes(t *testing.T) {
	if got := name(obce.Indicator{Name: "113.10  Chmelnice (k 31. 12., ha)"}); got != "Chmelnice (k 31. 12., ha)" {
		t.Errorf("name %q", got)
	}
	if got := name(obce.Indicator{Name: "Počet obyvatel k 31. 12."}); got != "Počet obyvatel k 31. 12." {
		t.Errorf("name %q", got)
	}
	if got := breakdownLabel(map[string]string{"věk": "0 - 14 let", "pohlaví": "Muži"}); got != "pohlaví: Muži, věk: 0 - 14 let" {
		t.Errorf("breakdown %q", got)
	}
	if breakdownLabel(nil) != "celkem" {
		t.Error("empty breakdown")
	}
	if th, rank := themeOf("czso.2406K2"); themes[th].name != "Počet obyvatel" || rank != 1 {
		t.Errorf("2406K2: %d %d", th, rank)
	}
	if th, _ := themeOf("czso.4582UAP1137"); !themes[th].compact {
		t.Error("land use should be compact")
	}
	if th, _ := themeOf("czso.9999"); th != len(themes) {
		t.Error("unknown indicator should go to Ostatní")
	}
}
