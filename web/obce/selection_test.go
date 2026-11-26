package obce

import (
	"errors"
	"testing"
)

func TestParseSelection(t *testing.T) {
	good := "g.CZ063,g.velikost_5000_10000,o.595209,s.3fa2c19e0b7d4a11"
	items, err := ParseSelection(good)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 4 || items[2] != (Item{'o', "595209"}) {
		t.Fatalf("got %v", items)
	}
	if got := FormatSelection(items); got != good {
		t.Fatalf("round trip: %q", got)
	}
	for _, bad := range []string{
		"", "x.1", "g", "g.", "o.12345", "o.012345", "s.XYZ", "s.3fa2c19e0b7d4a1",
		"g.CZ063,g.CZ063", "g.a b", "g.a/b", "g.1,g.2,g.3,g.4,g.5,g.6,g.7",
	} {
		if _, err := ParseSelection(bad); !errors.Is(err, ErrSelection) {
			t.Errorf("%q: want ErrSelection, got %v", bad, err)
		}
	}
}

func TestFoldAndObceCount(t *testing.T) {
	if got := fold("Žďár nad Sázavou"); got != "zdar nad sazavou" {
		t.Errorf("fold: %q", got)
	}
	for n, want := range map[int]string{1: "1 obec", 3: "3 obce", 5: "5 obcí", 0: "0 obcí"} {
		if got := ObceCount(n); got != want {
			t.Errorf("ObceCount(%d) = %q", n, got)
		}
	}
}
