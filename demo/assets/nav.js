// Side navigation: lists the sections (h2, h3) of the open article and marks the one being read.
const list = document.querySelector("[data-sections]");
const heads = [...document.querySelectorAll("main h2[id], main h3[id]")];

if (list && heads.length) {
  const links = heads.map((head) => {
    const a = document.createElement("a");
    a.href = "#" + head.id;
    a.textContent = head.textContent;
    const li = document.createElement("li");
    if (head.tagName === "H3") li.className = "sub";
    li.append(a);
    list.append(li);
    return a;
  });

  // the section being read = the last heading that has passed the top of the window
  let waiting = false;
  const mark = () => {
    waiting = false;
    let on = -1;
    heads.forEach((head, i) => { if (head.getBoundingClientRect().top < 120) on = i; });
    links.forEach((a, i) => a.classList.toggle("reading", i === on));
  };
  addEventListener("scroll", () => {
    if (!waiting) { waiting = true; requestAnimationFrame(mark); }
  }, { passive: true });
  mark();
}
