package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/a-h/templ"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"obcevkostce/web/dash"
	"obcevkostce/web/export"
	"obcevkostce/web/obce"
	"obcevkostce/web/views"
)

type app struct {
	db    *pgxpool.Pool
	dir   *obce.Directory
	cache *chartCache
}

func render(w http.ResponseWriter, r *http.Request, c templ.Component) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := c.Render(r.Context(), w); err != nil {
		log.Printf("render %s: %v", r.URL.Path, err)
	}
}

func serverError(w http.ResponseWriter, r *http.Request, err error) {
	log.Printf("%s: %v", r.URL.Path, err)
	http.Error(w, "Chyba serveru.", http.StatusInternalServerError)
}

func (a *app) index(w http.ResponseWriter, r *http.Request) {
	render(w, r, views.Index(a.dir.Featured(), a.dir.Random(8)))
}

// search: HTMX fragment of obce matching ?q=; mode decides what a result does.
func (a *app) search(w http.ResponseWriter, r *http.Request) {
	mode := r.PathValue("mode")
	if mode != "pick" && mode != "add" && mode != "member" {
		http.NotFound(w, r)
		return
	}
	q := r.URL.Query().Get("q")
	render(w, r, views.SearchResults(mode, q, a.dir.Search(q, 20)))
}

// obec reads the {id} of the path; false = a response was written.
func (a *app) obec(w http.ResponseWriter, r *http.Request) (*obce.Info, bool) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil {
		http.NotFound(w, r)
		return nil, false
	}
	info, err := obce.GetInfo(r.Context(), a.db, a.dir, id)
	if err != nil {
		serverError(w, r, err)
		return nil, false
	}
	if info == nil {
		http.Error(w, "Obec nenalezena.", http.StatusNotFound)
		return nil, false
	}
	return info, true
}

// vyber: the comparison picker. ?c=<selection> pre-fills it ("změnit" on the menu);
// otherwise the obec's kraj and SO ORP are selected.
func (a *app) vyber(w http.ResponseWriter, r *http.Request) {
	info, ok := a.obec(w, r)
	if !ok {
		return
	}
	type item struct {
		Token string `json:"token"`
		Label string `json:"label"`
	}
	items := []item{}
	if c := r.URL.Query().Get("c"); c != "" {
		if sel, err := obce.ParseSelection(c); err == nil {
			if res, err := obce.Resolve(r.Context(), a.db, a.dir, sel); err == nil {
				for _, x := range res {
					items = append(items, item{x.Token(), x.Label})
				}
			}
		}
	}
	if len(items) == 0 {
		for _, typ := range []string{"kraj", "SO ORP"} {
			if g, ok := info.Group(typ); ok {
				items = append(items, item{"g." + g.ID, g.Label()})
			}
		}
	}
	initial, _ := json.Marshal(items)
	render(w, r, views.Vyber(info, string(initial), obce.MaxItems))
}

// saveSet stores a named custom group: JSON {name, obce: [ids]} -> {token, label}.
func (a *app) saveSet(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name string `json:"name"`
		Obce []int  `json:"obce"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&in); err != nil {
		http.Error(w, "Neplatný požadavek.", http.StatusBadRequest)
		return
	}
	in.Name = strings.TrimSpace(in.Name)
	switch {
	case in.Name == "" || len([]rune(in.Name)) > 80:
		http.Error(w, "Skupina potřebuje název (nejvýše 80 znaků).", http.StatusBadRequest)
		return
	case len(in.Obce) == 0 || len(in.Obce) > 1000:
		http.Error(w, "Skupina potřebuje 1 až 1000 obcí.", http.StatusBadRequest)
		return
	}
	id, err := obce.SaveSet(r.Context(), a.db, in.Name, in.Obce)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "22023" { // invalid_parameter_value
		http.Error(w, pgErr.Message, http.StatusBadRequest)
		return
	} else if err != nil {
		serverError(w, r, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"token": "s." + id, "label": in.Name})
}

// selection reads {id} and {sel}; false = a response was written.
func (a *app) selection(w http.ResponseWriter, r *http.Request) (*obce.Info, []obce.Resolved, bool) {
	info, ok := a.obec(w, r)
	if !ok {
		return nil, nil, false
	}
	items, err := obce.ParseSelection(r.PathValue("sel"))
	if err == nil {
		var res []obce.Resolved
		res, err = obce.Resolve(r.Context(), a.db, a.dir, items)
		if err == nil {
			return info, res, true
		}
	}
	if errors.Is(err, obce.ErrSelection) {
		http.Error(w, "Neplatné srovnání: "+err.Error(), http.StatusNotFound)
	} else {
		serverError(w, r, err)
	}
	return nil, nil, false
}

func (a *app) menu(w http.ResponseWriter, r *http.Request) {
	info, sel, ok := a.selection(w, r)
	if !ok {
		return
	}
	render(w, r, views.Menu(info, sel))
}

func (a *app) dashboard(w http.ResponseWriter, r *http.Request) {
	info, sel, ok := a.selection(w, r)
	if !ok {
		return
	}
	d, err := a.demografie(r.Context(), info, sel)
	if err != nil {
		serverError(w, r, err)
		return
	}
	render(w, r, views.Dashboard(info, sel, d.pairs))
}

// chartData serves {chart}.json and {chart}.csv for Charta.
func (a *app) chartData(w http.ResponseWriter, r *http.Request) {
	info, sel, ok := a.selection(w, r)
	if !ok {
		return
	}
	file := r.PathValue("file")
	ext := path.Ext(file)
	d, err := a.demografie(r.Context(), info, sel)
	if err != nil {
		serverError(w, r, err)
		return
	}
	c := d.charts[strings.TrimSuffix(file, ext)]
	if c == nil {
		http.NotFound(w, r)
		return
	}
	var body []byte
	switch ext {
	case ".json":
		body, err = c.JSON()
		w.Header().Set("Content-Type", "application/json")
	case ".csv":
		body, err = c.CSV()
		w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	default:
		http.NotFound(w, r)
		return
	}
	if err != nil {
		serverError(w, r, err)
		return
	}
	w.Write(body)
}

// ---------------------------------------------------------------- charts

type dashData struct {
	charts map[string]*dash.Chart
	pairs  []dash.Pair
	at     time.Time
}

// chartCache keeps computed dashboards for a while, so the page and its
// twenty JSON/CSV requests compute them once.
type chartCache struct {
	mu  sync.Mutex
	ttl time.Duration
	m   map[string]*dashData
}

func newChartCache(ttl time.Duration) *chartCache {
	return &chartCache{ttl: ttl, m: map[string]*dashData{}}
}

func (c *chartCache) get(key string) *dashData {
	c.mu.Lock()
	defer c.mu.Unlock()
	if d := c.m[key]; d != nil && time.Since(d.at) < c.ttl {
		return d
	}
	return nil
}

func (c *chartCache) put(key string, d *dashData) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for k, v := range c.m {
		if time.Since(v.at) >= c.ttl {
			delete(c.m, k)
		}
	}
	c.m[key] = d
}

// territoryLabels: the obec, then the selection items; labels tell lines and
// columns apart, so repeated ones get a number.
func territoryLabels(info *obce.Info, sel []obce.Resolved) []string {
	out := []string{info.Name}
	used := map[string]bool{info.Name: true}
	for _, s := range sel {
		label := s.Label
		for n := 2; used[label]; n++ {
			label = fmt.Sprintf("%s (%d)", s.Label, n)
		}
		used[label] = true
		out = append(out, label)
	}
	return out
}

func (a *app) demografie(ctx context.Context, info *obce.Info, sel []obce.Resolved) (*dashData, error) {
	items := make([]obce.Item, len(sel))
	for i, s := range sel {
		items[i] = s.Item
	}
	key := fmt.Sprintf("demografie/%d/%s", info.ID, obce.FormatSelection(items))
	if d := a.cache.get(key); d != nil {
		return d, nil
	}

	rows, err := obce.ObecRows(ctx, a.db, info.ID, dash.Indicators)
	if err != nil {
		return nil, err
	}
	in := dash.Input{Obec: dash.NewTerritory(info.Name, true, rows)}
	labels := territoryLabels(info, sel)
	var parts []string
	for i, s := range sel {
		rows, err := obce.ItemRows(ctx, a.db, s.Item, dash.Indicators)
		if err != nil {
			return nil, err
		}
		label := labels[i+1]
		in.Peers = append(in.Peers, dash.NewTerritory(label, false, rows))
		if s.Kind == 'o' {
			parts = append(parts, label)
		} else {
			parts = append(parts, fmt.Sprintf("%s (%s)", label, obce.ObceCount(s.Members)))
		}
	}
	in.CmpNote = "Srovnání: " + strings.Join(parts, "; ") + ". Hodnoty skupin jsou součty za jejich obce; " +
		"obce bez údaje se nezapočítávají."
	if in.Sources, err = obce.Sources(ctx, a.db, dash.Datasets); err != nil {
		return nil, err
	}
	charts, pairs := dash.Demografie(in)
	d := &dashData{charts: charts, pairs: pairs, at: time.Now()}
	a.cache.put(key, d)
	return d, nil
}

// members: HTMX fragment listing the obce of a group or custom set.
func (a *app) members(w http.ResponseWriter, r *http.Request) {
	items, err := obce.ParseSelection(r.PathValue("item"))
	if err != nil || len(items) != 1 || items[0].Kind == 'o' {
		http.NotFound(w, r)
		return
	}
	list, err := obce.Members(r.Context(), a.db, a.dir, items[0])
	if err != nil {
		serverError(w, r, err)
		return
	}
	render(w, r, views.Members(list))
}

// xlsx: all indicators for the obec and its comparison, one sheet per indicator.
func (a *app) xlsx(w http.ResponseWriter, r *http.Request) {
	info, sel, ok := a.selection(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	labels := territoryLabels(info, sel)
	in := export.Input{Title: info.Name, Facts: views.FactLines(info)}
	vals, err := obce.ObecValues(ctx, a.db, info.ID)
	if err != nil {
		serverError(w, r, err)
		return
	}
	in.Territories = append(in.Territories, export.Territory{Label: labels[0],
		Kind: fmt.Sprintf("vybraná obec (kód %d)", info.ID), Values: vals})
	for i, s := range sel {
		vals, err := obce.ItemValues(ctx, a.db, s.Item)
		if err != nil {
			serverError(w, r, err)
			return
		}
		kind := map[byte]string{'g': "skupina", 'o': "obec", 's': "vlastní skupina"}[s.Kind]
		if s.Kind != 'o' {
			kind += " (" + obce.ObceCount(s.Members) + ")"
		}
		in.Territories = append(in.Territories, export.Territory{Label: labels[i+1], Kind: kind, Values: vals})
	}
	if in.Indicators, err = obce.Indicators(ctx, a.db, info.ID); err != nil {
		serverError(w, r, err)
		return
	}
	if in.Series, err = obce.AllSeries(ctx, a.db); err != nil {
		serverError(w, r, err)
		return
	}
	f, err := export.Build(in)
	if err != nil {
		serverError(w, r, err)
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="obcevkostce_%d.xlsx"`, info.ID))
	if err := f.Write(w); err != nil {
		log.Printf("%s: %v", r.URL.Path, err)
	}
}
