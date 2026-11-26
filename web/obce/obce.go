// Package obce reads municipalities, their groups and comparison selections from the database.
package obce

import (
	"context"
	"fmt"
	"math/rand/v2"
	"sort"
	"strings"
	"unicode"

	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/text/unicode/norm"
)

// Obec is one municipality as listed in search results.
type Obec struct {
	ID   int
	Name string
	ORP  string // name of its SO ORP
	Type string // municipality_type
	fold string
}

// Label is "Name (ORP X)", which tells apart obce of the same name.
func (o Obec) Label() string {
	if o.ORP == "" {
		return o.Name
	}
	return fmt.Sprintf("%s (ORP %s)", o.Name, o.ORP)
}

// Group is a preset group of obce (stát, kraj, okres, SO ORP, size band).
type Group struct {
	ID, Name, Type string
	Members        int
}

// Label of a group on the charts: "SO ORP Jihlava", "okres Jihlava", "Kraj Vysočina".
func (g Group) Label() string {
	switch g.Type {
	case "SO ORP":
		return "SO ORP " + g.Name
	case "okres":
		return "okres " + g.Name
	}
	return g.Name
}

// Directory holds all obce in memory for search (about 6 300 rows).
type Directory struct {
	list []Obec
	byID map[int]Obec
}

// fold lowercases and strips diacritics, so "zdar" finds "Žďár".
func fold(s string) string {
	var b strings.Builder
	for _, r := range norm.NFD.String(strings.ToLower(s)) {
		if !unicode.Is(unicode.Mn, r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func LoadDirectory(ctx context.Context, db *pgxpool.Pool) (*Directory, error) {
	rows, err := db.Query(ctx, `
		SELECT m.municipality_id, m.municipality_name, coalesce(min(g.group_name), ''),
		       coalesce(m.municipality_type::text, '')
		FROM municipality m
		LEFT JOIN municipality_group_member gm ON gm.municipality_id = m.municipality_id
		LEFT JOIN municipality_group g ON g.group_id = gm.group_id AND g.group_type = 'SO ORP'
		GROUP BY 1, 2, 4`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	d := &Directory{byID: map[int]Obec{}}
	for rows.Next() {
		var o Obec
		if err := rows.Scan(&o.ID, &o.Name, &o.ORP, &o.Type); err != nil {
			return nil, err
		}
		o.fold = fold(o.Name)
		d.list = append(d.list, o)
		d.byID[o.ID] = o
	}
	sort.Slice(d.list, func(i, j int) bool { return d.list[i].fold < d.list[j].fold })
	return d, rows.Err()
}

func (d *Directory) Get(id int) (Obec, bool) { o, ok := d.byID[id]; return o, ok }

// Search: names starting with q first, then names containing it; at most limit.
func (d *Directory) Search(q string, limit int) []Obec {
	q = fold(strings.TrimSpace(q))
	if q == "" {
		return nil
	}
	var first, rest []Obec
	for _, o := range d.list {
		switch {
		case strings.HasPrefix(o.fold, q):
			first = append(first, o)
		case strings.Contains(o.fold, q):
			rest = append(rest, o)
		}
		if len(first) >= limit {
			break
		}
	}
	out := append(first, rest...)
	if len(out) > limit {
		out = out[:limit]
	}
	return out
}

// Info is what the pages show about the obec.
type Info struct {
	Obec
	Type       string // municipality_type
	Population float64
	PopYear    int
	Groups     []Group // its preset groups: SO ORP, okres, kraj, size band, stát
}

var typeLabel = map[string]string{
	"obec": "Obec", "mestys": "Městys", "mesto": "Město", "statutarni_mesto": "Statutární město",
	"krajske_mesto": "Krajské město", "hlavni_mesto": "Hlavní město",
}

func (i Info) TypeLabel() string {
	if l, ok := typeLabel[i.Type]; ok {
		return l
	}
	return "Obec"
}

func (i Info) Group(typ string) (Group, bool) {
	for _, g := range i.Groups {
		if g.Type == typ {
			return g, true
		}
	}
	return Group{}, false
}

var groupOrder = map[string]int{"SO ORP": 0, "okres": 1, "kraj": 2, "velikost": 3, "stát": 4}

func GetInfo(ctx context.Context, db *pgxpool.Pool, dir *Directory, id int) (*Info, error) {
	o, ok := dir.Get(id)
	if !ok {
		return nil, nil
	}
	info := &Info{Obec: o}
	err := db.QueryRow(ctx, `
		SELECT coalesce(m.municipality_type::text, ''),
		       coalesce((SELECT c.value::float8 FROM current_observation c JOIN series s USING (series_key)
		                 WHERE s.series_id = 'czso.2406K' AND c.municipality_id = m.municipality_id
		                 ORDER BY lower(c.period) DESC LIMIT 1), 0),
		       coalesce((SELECT extract(year FROM lower(c.period))::int FROM current_observation c JOIN series s USING (series_key)
		                 WHERE s.series_id = 'czso.2406K' AND c.municipality_id = m.municipality_id
		                 ORDER BY lower(c.period) DESC LIMIT 1), 0)
		FROM municipality m WHERE m.municipality_id = $1`, id).Scan(&info.Type, &info.Population, &info.PopYear)
	if err != nil {
		return nil, err
	}
	rows, err := db.Query(ctx, `
		SELECT g.group_id, g.group_name, coalesce(g.group_type, ''),
		       (SELECT count(*) FROM municipality_group_member x WHERE x.group_id = g.group_id)::int
		FROM municipality_group_member m JOIN municipality_group g USING (group_id)
		WHERE m.municipality_id = $1`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var g Group
		if err := rows.Scan(&g.ID, &g.Name, &g.Type, &g.Members); err != nil {
			return nil, err
		}
		info.Groups = append(info.Groups, g)
	}
	sort.SliceStable(info.Groups, func(i, j int) bool {
		return groupOrder[info.Groups[i].Type] < groupOrder[info.Groups[j].Type]
	})
	return info, rows.Err()
}

// SaveSet stores a named custom group of obce and returns its id.
func SaveSet(ctx context.Context, db *pgxpool.Pool, name string, obce []int) (string, error) {
	def := map[string]any{"name": name, "obce": obce}
	var id string
	err := db.QueryRow(ctx, `SELECT comparison_set_save($1::jsonb)`, def).Scan(&id)
	return id, err
}

func (d *Directory) All() []Obec { return d.list }

// Featured: Praha and the krajská města, by name.
func (d *Directory) Featured() []Obec {
	var out []Obec
	for _, o := range d.list {
		if o.Type == "hlavni_mesto" || o.Type == "krajske_mesto" {
			out = append(out, o)
		}
	}
	return out
}

// Random: n obce picked at random, sorted by name.
func (d *Directory) Random(n int) []Obec {
	if n > len(d.list) {
		n = len(d.list)
	}
	idx := rand.Perm(len(d.list))[:n]
	sort.Ints(idx) // d.list is sorted by name
	out := make([]Obec, n)
	for i, j := range idx {
		out[i] = d.list[j]
	}
	return out
}
