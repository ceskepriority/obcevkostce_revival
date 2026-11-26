// Alpine state of the comparison picker (/obec/{id}/vyber).
function picker(obec, items, max) {
  return {
    obec, items, max,
    msg: "",
    group: { name: "", members: [] },
    has(token) { return this.items.some((i) => i.token === token); },
    add({ token, label }) {
      this.msg = "";
      if (this.has(token)) return;
      if (this.items.length >= this.max) { this.msg = `Nejvýše ${this.max} položek.`; return; }
      this.items.push({ token, label });
    },
    remove(i) { this.items.splice(i, 1); },
    toggle(el) {
      if (!el.checked) { this.items = this.items.filter((i) => i.token !== el.dataset.token); return; }
      this.add(el.dataset);
      el.checked = this.has(el.dataset.token);
    },
    addMember({ id, label }) {
      if (!this.group.members.some((m) => m.id === +id)) this.group.members.push({ id: +id, label });
    },
    async saveGroup() {
      this.msg = "";
      const r = await fetch("/skupina", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: this.group.name, obce: this.group.members.map((m) => m.id) }),
      });
      if (!r.ok) { this.msg = await r.text(); return; }
      this.add(await r.json());
      this.group = { name: "", members: [] };
    },
    next() { return `/obec/${this.obec}/${this.items.map((i) => i.token).join(",")}/`; },
  };
}
