package dash

import (
	"math"
	"testing"
)

func TestRate(t *testing.T) {
	ev := Values{2000: 1, 2001: 2, 2002: 3, 2003: 4}
	base := Values{2000: 100, 2001: 100, 2002: 100, 2003: 0}
	got := Rate(ev, base, 1000, 3)
	// 2002: (1+2+3)/(300) * 1000 = 20; 2003: (2+3+4)/200 * 1000 = 45; 2000-2001: incomplete window
	if len(got) != 2 || math.Abs(got[2002]-20) > 1e-9 || math.Abs(got[2003]-45) > 1e-9 {
		t.Fatalf("got %v", got)
	}
}

func TestTerritorySumsAndBreakdown(t *testing.T) {
	tr := NewTerritory("x", false, []Row{
		{"czso.2406K2", map[string]string{"věk": "0 - 14 let"}, 2020, 5},
		{"czso.2406K2", map[string]string{"věk": "0 - 14 let"}, 2020, 2},
		{"czso.2406K2", map[string]string{}, 2020, 100},
	})
	if v := tr.AgeEnd("0 - 14 let")[2020]; v != 7 {
		t.Errorf("age group: %v", v)
	}
	if v := tr.AgeEnd("")[2020]; v != 100 {
		t.Errorf("total: %v", v)
	}
}

func TestCSV(t *testing.T) {
	c := newChart("t", []string{"rok", "uzemi", "obec", "v"}, []map[string]any{
		{"rok": 2020, "uzemi": "A, B", "obec": true, "v": 1.0 / 3},
		{"rok": 2021, "uzemi": "C", "obec": false, "v": nil},
		{"rok": 2022, "uzemi": "D", "obec": false, "v": math.NaN()},
	}, chartArgs{typ: "line"})
	b, err := c.CSV()
	if err != nil {
		t.Fatal(err)
	}
	want := "rok,uzemi,obec,v\n2020,\"A, B\",1,0.333333\n2021,C,0,\n2022,D,0,\n"
	if string(b) != want {
		t.Fatalf("got\n%s", b)
	}
	if c.Spec.Options["timeline"] != false || c.Spec.Data != "t.csv" || c.Spec.Rows != 3 {
		t.Fatalf("spec: %+v", c.Spec)
	}
}
