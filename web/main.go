// The Obce v kostce web app: pick an obec and a comparison, then browse its dashboards.
//
//	cd web && go tool templ generate && go run .
package main

import (
	"context"
	"embed"
	"flag"
	"io/fs"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"gopkg.in/yaml.v3"

	"obcevkostce/web/obce"
)

//go:embed static
var staticFS embed.FS

func main() {
	addr := flag.String("addr", "localhost:8080", "listen address")
	cfgPath := flag.String("config", "../config.yaml", "config with web_connection (OVK_WEB_CONNECTION overrides it)")
	chartaDir := flag.String("charta", "../lib/charta/js", "Charta's js/ folder, served at /lib/charta/")
	flag.Parse()

	conn := os.Getenv("OVK_WEB_CONNECTION")
	if conn == "" {
		raw, err := os.ReadFile(*cfgPath)
		if err != nil {
			log.Fatal(err)
		}
		var cfg struct {
			WebConnection string `yaml:"web_connection"`
		}
		if err := yaml.Unmarshal(raw, &cfg); err != nil {
			log.Fatal(err)
		}
		conn = cfg.WebConnection
	}
	if conn == "" {
		log.Fatal("no database connection: set web_connection in the config or OVK_WEB_CONNECTION")
	}

	ctx := context.Background()
	db, err := pgxpool.New(ctx, conn)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	dir, err := obce.LoadDirectory(ctx, db)
	if err != nil {
		log.Fatal(err)
	}

	a := &app{db: db, dir: dir, cache: newChartCache(10 * time.Minute)}
	static, _ := fs.Sub(staticFS, "static")

	mux := http.NewServeMux()
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServerFS(static)))
	mux.Handle("GET /lib/charta/", http.StripPrefix("/lib/charta/", http.FileServer(http.Dir(*chartaDir))))
	mux.HandleFunc("GET /{$}", a.index)
	mux.HandleFunc("GET /hledat/{mode}", a.search)
	mux.HandleFunc("POST /skupina", a.saveSet)
	mux.HandleFunc("GET /obec/{id}/vyber", a.vyber)
	mux.HandleFunc("GET /obec/{id}/{sel}/{$}", a.menu)
	mux.HandleFunc("GET /obec/{id}/{sel}/demografie", a.dashboard)
	mux.HandleFunc("GET /obec/{id}/{sel}/data/{file}", a.chartData)
	mux.HandleFunc("GET /obec/{id}/{sel}/data.xlsx", a.xlsx)
	mux.HandleFunc("GET /clenove/{item}", a.members)

	log.Printf("listening on http://%s (%d obcí)", *addr, len(dir.All()))
	log.Fatal(http.ListenAndServe(*addr, logRequests(mux)))
}

func logRequests(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		h.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.RequestURI(), time.Since(start).Round(time.Millisecond))
	})
}
