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
        this.buildFontRows();
        this.tab = 'form';
        this.yamlText = dump(this.config);
        this.previewErr = null;
        this.dirty = false;
        this.view = 'edit';
        this.livePreview();
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
