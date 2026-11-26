// Charts of the demo pages: Charta (lib/charta) with the demo's colours and font.
// Serve the repository root so that ../lib/charta is reachable.
import { configure, renderAll } from "../../lib/charta/js/charts.js";

configure({
  root: new URL("..", import.meta.url), // chart_data/ lives next to the pages
  palette: ["#2a78d6", "#e34948", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#eb6834", "#8a5a2b", "#5c6670"],
  muted: "#c9c8c2",
  colors: { ink: "#1f1f1e", ink2: "#52514e", ink3: "#77756f", bg: "#ffffff" },
  font: '"DM Sans", "Helvetica Neue", Helvetica, Arial, sans-serif',
});
renderAll();
