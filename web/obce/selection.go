package obce

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// A selection is the ordered list of comparison items, written in URLs as
// comma-separated tokens: g.<group_id>, o.<obec id>, s.<comparison_set id>,
// e.g. "g.CZ063,g.6107,o.595209,s.3fa2c19e0b7d4a11".

const MaxItems = 6

type Item struct {
	Kind byte // 'g', 'o' or 's'
	ID   string
}

func (it Item) Token() string { return string(it.Kind) + "." + it.ID }

var (
	reGroup = regexp.MustCompile(`^[A-Za-z0-9_]{1,40}$`)
	reObec  = regexp.MustCompile(`^[1-9][0-9]{5}$`)
	reSet   = regexp.MustCompile(`^[0-9a-f]{16}$`)
)

var ErrSelection = errors.New("invalid selection")

func ParseSelection(s string) ([]Item, error) {
	if s == "" {
		return nil, fmt.Errorf("%w: empty", ErrSelection)
	}
	parts := strings.Split(s, ",")
	if len(parts) > MaxItems {
		return nil, fmt.Errorf("%w: more than %d items", ErrSelection, MaxItems)
	}
	seen := map[string]bool{}
	items := make([]Item, 0, len(parts))
	for _, p := range parts {
		kind, id, ok := strings.Cut(p, ".")
		if !ok || len(kind) != 1 {
			return nil, fmt.Errorf("%w: %q", ErrSelection, p)
		}
		var re *regexp.Regexp
		switch kind {
		case "g":
			re = reGroup
		case "o":
			re = reObec
		case "s":
			re = reSet
		default:
			return nil, fmt.Errorf("%w: %q", ErrSelection, p)
		}
		if !re.MatchString(id) {
			return nil, fmt.Errorf("%w: %q", ErrSelection, p)
		}
		if seen[p] {
			return nil, fmt.Errorf("%w: %q twice", ErrSelection, p)
		}
		seen[p] = true
		items = append(items, Item{Kind: kind[0], ID: id})
	}
	return items, nil
}

func FormatSelection(items []Item) string {
	tokens := make([]string, len(items))
	for i, it := range items {
		tokens[i] = it.Token()
	}
	return strings.Join(tokens, ",")
}

// Resolved is a selection item with its label and number of obce.
type Resolved struct {
	Item
	Label   string
	Members int
}

// Resolve looks up every item; an unknown one is an ErrSelection.
func Resolve(ctx context.Context, db *pgxpool.Pool, dir *Directory, items []Item) ([]Resolved, error) {
	out := make([]Resolved, 0, len(items))
	for _, it := range items {
		r := Resolved{Item: it}
		switch it.Kind {
		case 'g':
			var g Group
			err := db.QueryRow(ctx, `
				SELECT g.group_id, g.group_name, coalesce(g.group_type, ''),
				       (SELECT count(*) FROM municipality_group_member m WHERE m.group_id = g.group_id)::int
				FROM municipality_group g WHERE g.group_id = $1`, it.ID).Scan(&g.ID, &g.Name, &g.Type, &g.Members)
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, fmt.Errorf("%w: unknown group %s", ErrSelection, it.ID)
			} else if err != nil {
				return nil, err
			}
			r.Label, r.Members = g.Label(), g.Members
		case 'o':
			id, _ := strconv.Atoi(it.ID)
			o, ok := dir.Get(id)
			if !ok {
				return nil, fmt.Errorf("%w: unknown obec %s", ErrSelection, it.ID)
			}
			r.Label, r.Members = o.Label(), 1
		case 's':
			err := db.QueryRow(ctx, `
				SELECT coalesce(definition ->> 'name', 'Vlastní skupina'),
				       (SELECT count(*) FROM comparison_set_members($1))::int
				FROM comparison_set WHERE set_id = $1`, it.ID).Scan(&r.Label, &r.Members)
			if errors.Is(err, pgx.ErrNoRows) {
				return nil, fmt.Errorf("%w: unknown set %s", ErrSelection, it.ID)
			} else if err != nil {
				return nil, err
			}
		}
		out = append(out, r)
	}
	return out, nil
}

// ObceCount is "1 obec", "3 obce", "12 obcí".
func ObceCount(n int) string {
	switch {
	case n == 1:
		return "1 obec"
	case n >= 2 && n <= 4:
		return fmt.Sprintf("%d obce", n)
	}
	return fmt.Sprintf("%d obcí", n)
}
