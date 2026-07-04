document.addEventListener('alpine:init', () => {
  const yaml = window.jsyaml;
  const dump = (o) => yaml.dump(o, { indent: 2, lineWidth: -1, sortKeys: false });

  Alpine.data('inkwell', (base) => ({
    base,
    view: 'list',
    displays: [],
    err: null,
    listT: Date.now(),

    // reference data for the form
    hardware: [], renderers: [], fonts: [],

    // editor state
    name: null,
    config: null,
    tab: 'form',
    yamlText: '',
    yamlErr: null,
    fontRows: [],
    busy: false,
    msg: null,
    dirty: false,
    previewT: Date.now(),
    previewSrc: '',
    previewErr: null,
    _pvTimer: null,
    _pvUrl: null,

    // entity picker
    pickerOpen: false,
    entities: [],
    entitiesLoaded: false,
    entityQuery: '',
    _pickCb: null,

    // helpers panel
    missingHelpers: [], helpersChecked: false,

    async init() {
      await this.loadList();
      setInterval(() => { if (this.view === 'list') this.tick(); }, 5000);
      window.addEventListener('beforeunload', (e) => {
        if (this.dirty) { e.preventDefault(); e.returnValue = ''; }
      });
    },

    // any user edit: mark unsaved and refresh the live preview
    edited() { this.dirty = true; this.schedulePreview(); },

    // ---------------------------------------------------------- list
    async loadList() {
      try {
        const r = await fetch(this.base + '/api/displays');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        this.displays = (await r.json()).displays || [];
        this.err = null;
      } catch (e) { this.err = 'Failed to load displays: ' + e; }
    },
    tick() { this.listT = Date.now(); this.loadList(); },
    listImg(name) { return `${this.base}/api/displays/${name}.png?t=${this.listT}`; },
    fmt(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString() : '—'; },

    // ---------------------------------------------------------- editor
    async loadRefs() {
      const grab = async (path, key) => {
        try { const r = await fetch(this.base + path); return (await r.json())[key] || []; }
        catch { return []; }
      };
      this.hardware = await grab('/api/hardware', 'hardware');
      this.renderers = await grab('/api/renderers', 'renderers');
      this.fonts = await grab('/api/fonts', 'fonts');
    },

    async openEditor(name) {
      this.msg = null; this.yamlErr = null;
      await this.loadRefs();
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(name));
        if (!r.ok) throw new Error('HTTP ' + r.status);
        this.name = name;
        this.config = (await r.json()).config || {};
        if (this.config.renderer === 'menu' && this.config.groups) {
          this.config.title = this.config.title || { text: '' };
          this.config.layout = this.config.layout || {};
          this.config.option_sets = this.config.option_sets || {};
        }
        this.buildFontRows();
        this.tab = 'form';
        this.yamlText = dump(this.config);
        this.previewErr = null;
        this.dirty = false;
        this.missingHelpers = []; this.helpersChecked = false;
        this.view = 'edit';
        this.livePreview();
        this.loadMissing();
      } catch (e) { this.err = 'Open failed: ' + e; }
    },
    backToList() {
      if (this.dirty && !confirm('Discard unsaved changes?')) return;
      this.dirty = false;
      this.view = 'list'; this.name = null; this.config = null; this.loadList();
    },

    buildFontRows() {
      const f = (this.config && this.config.fonts) || {};
      this.fontRows = Object.entries(f).map(([name, v]) => ({
        name, file: (v && v.file) || '', size: (v && v.size) || 12,
      }));
    },
    addFontRow() { this.fontRows.push({ name: '', file: this.fonts[0] || '', size: 24 }); },
    removeFontRow(i) { this.fontRows.splice(i, 1); },
    applyForm() {
      const fonts = {};
      for (const r of this.fontRows) {
        if (!r.name) continue;
        fonts[r.name] = { file: r.file, size: Number(r.size) || 0 };
      }
      this.config.fonts = fonts;
    },

    setTab(t) {
      if (t === this.tab) return;
      if (t === 'yaml') {
        this.applyForm();
        this.yamlText = dump(this.config);
        this.yamlErr = null;
      } else {
        if (!this.syncYaml()) return;  // stay on YAML tab if it doesn't parse
        this.buildFontRows();
      }
      this.tab = t;
      this.schedulePreview();
    },

    // ---- live preview of the current (unsaved) config ----
    schedulePreview() {
      clearTimeout(this._pvTimer);
      this._pvTimer = setTimeout(() => this.livePreview(), 500);
    },
    async livePreview() {
      let cfg;
      if (this.tab === 'yaml') {
        try {
          const p = yaml.load(this.yamlText);
          if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error('top level must be a mapping');
          cfg = p; this.yamlErr = null;
        } catch (e) { this.yamlErr = 'YAML error: ' + (e.message || e); return; }
      } else {
        this.applyForm(); cfg = this.config;
      }
      try {
        const r = await fetch(this.base + '/api/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.name, config: cfg }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          this.previewErr = d.error || ('HTTP ' + r.status);
          return;
        }
        const d = await r.json();
        this.previewErr = null;
        // same-origin GET URL (ingress allows this; blob:/data: are CSP-blocked)
        this.previewSrc = `${this.base}/api/preview/${encodeURIComponent(d.name || this.name)}.png?t=${Date.now()}`;
      } catch (e) { this.previewErr = '' + e; }
    },
    syncYaml() {
      try {
        const parsed = yaml.load(this.yamlText);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('top level must be a mapping');
        }
        this.config = parsed;
        this.yamlErr = null;
        return true;
      } catch (e) { this.yamlErr = 'YAML error: ' + (e.message || e); return false; }
    },

    isMenu() { return this.config && this.config.renderer === 'menu'; },
    isDashboard() { return this.config && this.config.renderer === 'dashboard'; },
    ensureTitle() { if (this.config && !this.config.title) this.config.title = { text: '' }; return this.config.title; },

    // legacy config that predates the groups schema (still has `sections`)
    isLegacyMenu() { return this.isMenu() && !this.config.groups && Array.isArray(this.config.sections); },

    // one-shot converter: old `sections` -> new `groups` (review, then Save)
    async convertLegacy() {
      if (!confirm('Convert this menu to the new builder format? Review, then Save.')) return;
      // options aren't in the old config — recover them from the live input_select entities
      if (!this.entitiesLoaded) {
        try { const r = await fetch(this.base + '/api/entities'); this.entities = (await r.json()).entities || []; this.entitiesLoaded = true; } catch (e) {}
      }
      const optsOf = (eid) => { const e = this.entities.find(x => x.entity_id === eid); return (e && e.options) ? e.options.slice() : []; };
      const groups = [];
      const bottleItems = [];
      for (const s of this.config.sections || []) {
        if (s.type === 'bottles') {
          const items = (s.items || []).map(i => {
            let text = i.prefix || '', sep = '：';
            const m = text.match(/^(.+?)([：:])\s*$/);   // split a trailing colon into the separator
            if (m) { text = m[1]; sep = m[2]; }
            const it = { text, control: 'choice', entity: i.entity, managed: true, separator: sep, options: optsOf(i.entity) };
            bottleItems.push(it); return it;
          });
          groups.push({ items });
        } else if (s.type === 'static') {
          groups.push({ header: s.header || '', items: (s.items || []).map(i => ({ text: i.text || '', when: [] })) });
        } else if (s.type === 'conditional') {
          const cond = s.condition || {};
          const g = { header: s.header || '', hide_if_empty: !!cond.require_any_item_active, items: [] };
          const base = (cond.require_all || []).map(e => ({ entity_on: e }));
          // convert a show_when to a `when` list, or null if not a simple AND
          const swToWhen = (sw) => {
            if (!sw || !Object.keys(sw).length) return [];
            if (sw.entity_on) return [{ entity_on: sw.entity_on }];
            if (sw.all_on) return sw.all_on.map(e => ({ entity_on: e }));
            if (sw.any_select_equals) return [{ select_equals: { any_of: sw.any_select_equals.entities, value: sw.any_select_equals.value } }];
            return null;
          };
          for (const i of (s.items || [])) {
            const itemWhen = swToWhen(i.show_when || {});
            const vs = i.variants;
            if (vs) {
              // split a simple 2-variant (toggle on / else) into two Lines
              if (itemWhen !== null && vs.length === 2 && vs[0].when && vs[0].when.entity_on && (!vs[1].when || !Object.keys(vs[1].when).length)) {
                const x = vs[0].when.entity_on;
                g.items.push({ text: vs[0].text || '', when: [...base, ...itemWhen, { entity_on: x }] });
                g.items.push({ text: vs[1].text || '', when: [...base, ...itemWhen, { entity_off: x }] });
                continue;
              }
              g.items.push({ text: i.text || '', control: 'advanced', show_when: i.show_when || {}, variants: vs });
              continue;
            }
            if (itemWhen === null) { g.items.push({ text: i.text || '', control: 'advanced', show_when: i.show_when }); continue; }
            g.items.push({ text: i.text || '', when: [...base, ...itemWhen] });
          }
          groups.push(g);
        }
      }
      const optionSets = { ...(this.config.option_sets || {}) };
      // if the bottles share one non-empty option list, hoist it into a shared set
      if (bottleItems.length > 1) {
        const first = JSON.stringify(bottleItems[0].options);
        if (bottleItems[0].options.length && bottleItems.every(it => JSON.stringify(it.options) === first)) {
          optionSets.bottles = bottleItems[0].options.slice();
          bottleItems.forEach(it => { it.options = 'bottles'; });
        }
      }
      this.config.groups = groups;
      this.config.option_sets = optionSets;
      this.config.title = this.config.title || { text: '' };
      this.config.layout = this.config.layout || {};
      delete this.config.sections;
      this.edited();
      this.msg = { ok: true, text: 'Converted — review and Save.' };
    },

    // Choice item options: named set (string) vs inline list
    setChoiceOptions(it, val) {
      if (val === '__inline__') it.options = Array.isArray(it.options) ? it.options : [];
      else it.options = val;
      this.edited();
    },
    addInlineOption(it, ev) {
      const v = (ev.target.value || '').trim(); if (!v) return;
      if (!Array.isArray(it.options)) it.options = [];
      it.options.push(v); ev.target.value = ''; this.edited();
    },
    removeInlineOption(it, i) { it.options.splice(i, 1); this.edited(); },

    // ---- option sets ----
    optionSetNames() { return Object.keys(this.config.option_sets || {}); },
    addOptionSet() {
      if (!this.config.option_sets) this.config.option_sets = {};
      let n = 'set', i = 1;
      while (this.config.option_sets[n]) n = 'set' + (++i);
      this.config.option_sets[n] = [];
      this.edited();
    },
    renameOptionSet(oldName, ev) {
      const nn = (ev.target.value || '').trim();
      if (!nn || nn === oldName || this.config.option_sets[nn]) { ev.target.value = oldName; return; }
      const rebuilt = {};
      for (const [k, v] of Object.entries(this.config.option_sets)) rebuilt[k === oldName ? nn : k] = v;
      this.config.option_sets = rebuilt;
      for (const g of this.config.groups || []) for (const it of g.items || []) if (it.options === oldName) it.options = nn;
      this.edited();
    },
    removeOptionSet(name) { delete this.config.option_sets[name]; this.edited(); },
    addOption(setName, ev) {
      const v = (ev.target.value || '').trim(); if (!v) return;
      this.config.option_sets[setName].push(v); ev.target.value = ''; this.edited();
    },
    removeOption(setName, i) { this.config.option_sets[setName].splice(i, 1); this.edited(); },
    optionSetUsage(name) {
      let n = 0;
      for (const g of this.config.groups || []) for (const it of g.items || []) if (it.options === name) n++;
      return n;
    },

    ensureLayout() { if (!this.config.layout) this.config.layout = {}; return this.config.layout; },

    // ---- groups ----
    groupsOf() { if (!this.config.groups) this.config.groups = []; return this.config.groups; },
    addGroup() { this.groupsOf().push({ header: '', hide_if_empty: true, items: [] }); this.edited(); },
    removeGroup(i) { this.config.groups.splice(i, 1); this.edited(); },
    moveGroup(i, dir) { const g = this.config.groups, j = i + dir; if (j < 0 || j >= g.length) return; [g[i], g[j]] = [g[j], g[i]]; this.edited(); },
    hasHeader(g) { return g.header !== undefined && g.header !== null; },
    toggleHeader(g) { if (this.hasHeader(g)) delete g.header; else g.header = ''; this.edited(); },

    // ---- items ----
    addItem(g, kind) {
      if (!g.items) g.items = [];
      if (kind === 'choice') g.items.push({ text: '', control: 'choice', entity: '', managed: true, separator: '：', options: this.optionSetNames()[0] || [] });
      else if (kind === 'advanced') g.items.push({ text: '', control: 'advanced', show_when: {} });
      else g.items.push({ text: '', when: [] });
      this.edited();
    },
    removeItem(g, i) { g.items.splice(i, 1); this.edited(); },
    moveItem(g, i, dir) { const it = g.items, j = i + dir; if (j < 0 || j >= it.length) return; [it[i], it[j]] = [it[j], it[i]]; this.edited(); },
    itemKind(it) { return it.control === 'choice' ? 'choice' : it.control === 'advanced' ? 'advanced' : 'line'; },

    // ---- conditions (when) ----
    whenOf(it) { if (!it.when) it.when = []; return it.when; },
    addCond(it, type) {
      const w = this.whenOf(it);
      if (type === 'entity_on') this.openPickerFor(id => w.push({ entity_on: id }));
      else if (type === 'entity_off') this.openPickerFor(id => w.push({ entity_off: id }));
      else { w.push({ select_equals: { any_of: [], value: '' } }); this.edited(); }
    },
    removeCond(it, i) { it.when.splice(i, 1); this.edited(); },
    condType(c) { return 'entity_on' in c ? 'entity_on' : 'entity_off' in c ? 'entity_off' : 'select_equals' in c ? 'select_equals' : '?'; },
    condEntity(c) { return c.entity_on || c.entity_off || ''; },
    short(e) { return (e && e.includes('.')) ? e.slice(e.indexOf('.') + 1) : (e || ''); },
    advancedSummary(it) {
      if (Array.isArray(it.variants) && it.variants.length) return it.variants.map(v => v.text).filter(Boolean).join(' / ');
      return it.text || '(condition)';
    },
    seEnts(c) { if (!c.select_equals.any_of) c.select_equals.any_of = []; return c.select_equals.any_of; },
    seAddEnt(c) { this.openPickerFor(id => this.seEnts(c).push(id)); },
    seRemoveEnt(c, i) { c.select_equals.any_of.splice(i, 1); this.edited(); },

    // ---- choice options (named set vs inline) ----
    choiceUsesSet(it) { return typeof it.options === 'string'; },

    // ---- helpers panel ----
    async loadMissing() {
      if (!this.name) return;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name) + '/missing-helpers');
        this.missingHelpers = (await r.json()).missing || [];
      } catch (e) { this.missingHelpers = []; }
      this.helpersChecked = true;
    },
    async createMissing() {
      this.busy = true; this.msg = null;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name) + '/create-helpers', { method: 'POST' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        const errs = (d.errors || []).length;
        this.msg = { ok: errs === 0, text: `Created ${d.created.length} helper(s)` + (errs ? `; ${errs} failed` : '') };
        await this.loadMissing();
        this.schedulePreview();
      } catch (e) { this.msg = { ok: false, text: 'Create failed: ' + e.message }; }
      finally { this.busy = false; }
    },

    openPickerFor(cb) { this._pickCb = cb; this.openPicker(); },

    async save() {
      if (this.tab === 'yaml') { if (!this.syncYaml()) return; }
      else this.applyForm();
      this.busy = true; this.msg = null;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: this.config }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        this.dirty = false;
        this.previewT = Date.now();
        this.msg = d.rendered
          ? { ok: true, text: 'Saved and rendered.' }
          : { ok: false, text: 'Saved, but render failed: ' + (d.error || '') };
        if (this.tab === 'form') this.yamlText = dump(this.config);
        else this.buildFontRows();
        this.loadMissing();
      } catch (e) { this.msg = { ok: false, text: 'Save failed: ' + e.message }; }
      finally { this.busy = false; }
    },

    async renderNow() {
      this.busy = true; this.msg = null;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name) + '/render',
          { method: 'POST' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        this.previewT = Date.now();
        this.msg = { ok: true, text: d.changed ? 'Rendered.' : 'No change.' };
      } catch (e) { this.msg = { ok: false, text: 'Render failed: ' + e.message }; }
      finally { this.busy = false; }
    },
    previewUrl() { return `${this.base}/api/displays/${this.name}.png?t=${this.previewT}`; },

    // ---------------------------------------------------------- CRUD
    async createDisplay() {
      const name = prompt('New display name (a-z, 0-9, _, -):');
      if (!name) return;
      const template = prompt(
        'Template: blank_menu, blank_dashboard, or an existing display name to copy:',
        'blank_menu') || 'blank_menu';
      try {
        const r = await fetch(this.base + '/api/displays', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), template: template.trim() }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        await this.openEditor(d.name);
      } catch (e) { alert('Create failed: ' + e.message); }
    },
    async renameDisplay() {
      const nn = prompt('Rename display to:', this.name);
      if (!nn || nn.trim() === this.name) return;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name) + '/rename', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_name: nn.trim() }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        this.name = d.name; this.previewT = Date.now();
        this.msg = { ok: true, text: 'Renamed.' };
      } catch (e) { alert('Rename failed: ' + e.message); }
    },
    async deleteDisplay() {
      if (!confirm(`Delete display "${this.name}"? This cannot be undone.`)) return;
      try {
        const r = await fetch(this.base + '/api/displays/' + encodeURIComponent(this.name), { method: 'DELETE' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        this.backToList();
      } catch (e) { alert('Delete failed: ' + e.message); }
    },

    // ---------------------------------------------------------- font upload
    async uploadFont(ev) {
      const file = ev.target.files[0]; if (!file) return;
      const fd = new FormData(); fd.append('file', file);
      this.busy = true; this.msg = null;
      try {
        const r = await fetch(this.base + '/api/fonts', { method: 'POST', body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        this.fonts = d.fonts || this.fonts;
        this.msg = { ok: true, text: 'Uploaded ' + d.font };
      } catch (e) { this.msg = { ok: false, text: 'Upload failed: ' + e.message }; }
      finally { this.busy = false; ev.target.value = ''; }
    },

    // ---------------------------------------------------------- entity picker
    async openPicker() {
      this.pickerOpen = true; this.entityQuery = '';
      if (!this.entitiesLoaded) {
        try {
          const r = await fetch(this.base + '/api/entities');
          this.entities = (await r.json()).entities || [];
          this.entitiesLoaded = true;
        } catch (e) { /* leave empty */ }
      }
    },
    filteredEntities() {
      const q = this.entityQuery.toLowerCase().trim();
      const list = q
        ? this.entities.filter(e =>
            e.entity_id.toLowerCase().includes(q) || (e.name || '').toLowerCase().includes(q))
        : this.entities;
      return list.slice(0, 200);
    },
    pickEntity(id) {
      if (this._pickCb) {
        this._pickCb(id);
        this._pickCb = null;
        this.pickerOpen = false;
        this.edited();
        return;
      }
      const ta = this.$refs.yaml;
      if (this.tab === 'yaml' && ta) {
        const s = ta.selectionStart ?? this.yamlText.length;
        const e = ta.selectionEnd ?? s;
        this.yamlText = this.yamlText.slice(0, s) + id + this.yamlText.slice(e);
        this.$nextTick(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = s + id.length; });
        this.edited();
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(id);
        this.msg = { ok: true, text: 'Copied ' + id };
      }
      this.pickerOpen = false;
    },
  }));
});
