// Charta on the dashboards: chart data comes from data/ next to the page
// (/obec/{id}/{selection}/data/{chart}.json and .csv).
import { configure, renderAll } from "/lib/charta/charts.js";

configure({
  dataDir: "data/",
  font: 'ui-monospace, "DejaVu Sans Mono", Menlo, monospace',
  colors: { ink: "#000", ink2: "#222", ink3: "#666", bg: "#fff" },
});
renderAll();
