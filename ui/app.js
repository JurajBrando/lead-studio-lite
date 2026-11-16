/* Lead Studio Lite - single-page UI. Vanilla JS, no build step, no dependencies. */
(function () {
  'use strict';

  var TOKEN = (document.querySelector('meta[name="ls-token"]') || {}).content || '';
  var state = {
    data: null,
    view: 'dashboard',
    leads: { items: [], total: 0, page: 1, pages: 1, q: '', status: '',
             filters: { sort: 'newest', country: '', title: '', industry: '', must_industry: '',
                        company_size: '', location: '', technologies: '', source: '' },
             facets: null, showFilters: false },
    selection: new Set(),
    inboxFilter: 'all',
    signalKind: '',
    offers: [],
    offerCat: '',
    chatHistory: [],
    llmProviders: null,
    llmPresets: [],
    social: null,
    domains: { items: [], total: 0, page: 1, pages: 1, q: '', source: '', stats: null, sources: [] },
    busy: false
  };

  var ICONS = {
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3.2"/><path d="M2.8 20c.6-3.4 3.1-5.2 6.2-5.2s5.6 1.8 6.2 5.2"/><path d="M16.5 5.6a3 3 0 0 1 0 5.6M18 14.6c2 .6 3.2 2.3 3.4 5"/></svg>',
    flow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="7" height="5" rx="1.5"/><rect x="14" y="15" width="7" height="5" rx="1.5"/><path d="M10 6.5h4a3 3 0 0 1 3 3v5.5"/></svg>',
    mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7 8.5 6 8.5-6"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5 21 3Z"/></svg>',
    cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9A1.7 1.7 0 0 0 10 3.1V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>',
    search: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2 4.5 13.5H11l-1 8.5L19.5 10H13l0-8Z"/></svg>',
    radar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12 18 6"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></svg>',
    tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.6 13.4 12 22l-9-9 8.6-8.6a2 2 0 0 1 1.4-.6H19a2 2 0 0 1 2 2v5.2a2 2 0 0 1-.4 1.4Z"/><circle cx="16.5" cy="7.5" r="1.3"/></svg>',
    target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/></svg>',
    pulse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12h4l2.5-6 4 12 2.5-6h5"/></svg>',
    video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="5" width="14" height="14" rx="2.5"/><path d="m16.5 10.5 5-3v9l-5-3v-3Z"/></svg>',
    globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 2.6 15.4 0 18M12 3c-2.6 2.6-2.6 15.4 0 18"/></svg>',
    sparkles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3.5 13.7 9l5.3 1.7-5.3 1.8L12 18l-1.7-5.5L5 10.7 10.3 9 12 3.5Z"/><path d="M18.5 16.5 19.2 19l2.3.7-2.3.8-.7 2.5-.8-2.5-2.2-.8 2.2-.7.8-2.5Z"/></svg>'
  };

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function h(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function num(n) { return (n === null || n === undefined) ? '0' : Number(n).toLocaleString(); }
  function pct(a, b) { return b ? Math.round((a / b) * 100) + '%' : '0%'; }
  function ts(v) { return v ? new Date(v * 1000) : null; }
  function fmtDate(v) {
    var d = ts(v);
    if (!d) return '—';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function fmtEta(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0));
    if (seconds < 60) return seconds + 's';
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    if (minutes < 60) return minutes + 'm' + (rest ? ' ' + rest + 's' : '');
    var hours = Math.floor(minutes / 60);
    return hours + 'h ' + (minutes % 60) + 'm';
  }

  function fmtWhen(v) {
    var d = ts(v);
    if (!d) return '—';
    var diff = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diff < 0) {
      var ahead = -diff;
      if (ahead < 60) return 'in <1m';
      if (ahead < 3600) return 'in ' + Math.round(ahead / 60) + 'm';
      if (ahead < 86400) return 'in ' + Math.round(ahead / 3600) + 'h';
      return 'in ' + Math.round(ahead / 86400) + 'd';
    }
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
    return fmtDate(v);
  }

  // ------------------------------------------------------------------ api
  function call(path, body) {
    var opts = { method: body === undefined ? 'GET' : 'POST', headers: { 'X-LS-Token': TOKEN } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Server returned HTTP ' + r.status }; });
    }).catch(function () {
      // The app can die or be restarted while the page is open. Without this, every caller
      // that disables a button before awaiting it would leave the button stuck forever.
      return { ok: false, error: 'The app is not responding. Is Lead Studio still running?' };
    });
  }

  // ------------------------------------------------------------------ ui bits
  function toast(msg, kind) {
    var box = $('#toasts');
    var el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === 'err' ? 6500 : 3800);
  }

  function openModal(opts) {
    var root = $('#modal-root');
    root.innerHTML = '<div class="overlay"><div class="modal ' + (opts.wide ? 'wide' : '') + '">' +
      '<div class="modal-head"><div><h2>' + h(opts.title) + '</h2>' +
      (opts.subtitle ? '<div class="card-sub">' + h(opts.subtitle) + '</div>' : '') + '</div>' +
      '<div class="spacer"></div><button class="x" data-close>&times;</button></div>' +
      '<div class="modal-body">' + opts.body + '</div>' +
      (opts.footer ? '<div class="modal-foot">' + opts.footer + '</div>' : '') +
      '</div></div>';
    $$('[data-close]', root).forEach(function (b) { b.onclick = closeModal; });
    root.querySelector('.overlay').addEventListener('mousedown', function (e) {
      if (e.target.classList.contains('overlay')) closeModal();
    });
    if (opts.onMount) opts.onMount(root);
    return root;
  }
  function closeModal() { $('#modal-root').innerHTML = ''; }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });

  function statusBadge(s) {
    var map = { new: 'blue', contacted: 'violet', replied: 'green', interested: 'green',
      won: 'green', lost: 'grey', bounced: 'red', unsubscribed: 'red' };
    return '<span class="badge ' + (map[s] || 'grey') + '">' + h(s || 'new') + '</span>';
  }
  function verifyBadge(s) {
    var map = { valid: 'green', risky: 'amber', invalid: 'red', unverified: 'grey' };
    return '<span class="badge ' + (map[s] || 'grey') + '">' + h(s || 'unverified') + '</span>';
  }

  // ------------------------------------------------------------------ data
  function refresh() {
    return call('/api/state').then(function (res) {
      if (!res.ok) { toast(res.error || 'Could not load state', 'err'); return; }
      state.data = res;
      paintChrome();
      render();
    }).catch(function () {
      $('#view').innerHTML = '<div class="empty"><div class="big">The app is not responding</div>' +
        '<p>Make sure the black command window (or LeadStudio.exe) is still open, then reload.</p>' +
        '<button class="btn" data-act="reload">Reload</button></div>';
    });
  }

  var LEAD_FILTER_KEYS = ['country', 'title', 'industry', 'must_industry', 'company_size',
                          'location', 'technologies', 'source'];
  var LEAD_SORTS = [
    { key: 'newest', label: 'Newest first' },
    { key: 'company', label: 'Company A-Z' },
    { key: 'country', label: 'Country A-Z' },
    { key: 'title', label: 'Job title A-Z' },
    { key: 'size', label: 'Company size' }
  ];

  function leadFilterBadge() {
    var button = $('#lead-filter-toggle');
    if (!button) return;
    var active = LEAD_FILTER_KEYS.filter(function (key) { return state.leads.filters[key]; }).length;
    button.textContent = 'Filters' + (active ? ' (' + active + ')' : '');
    button.classList.toggle('primary', active > 0);
  }

  function loadLeads() {
    var q = state.leads;
    var f = q.filters || {};
    var qs = '?page=' + q.page + '&per=50&q=' + encodeURIComponent(q.q) +
      '&status=' + encodeURIComponent(q.status) + '&sort=' + encodeURIComponent(f.sort || 'newest');
    LEAD_FILTER_KEYS.forEach(function (key) {
      if (f[key]) qs += '&' + key + '=' + encodeURIComponent(f[key]);
    });
    return call('/api/leads' + qs).then(function (res) {
      if (res.ok) {
        state.leads.items = res.leads;
        state.leads.total = res.total;
        state.leads.pages = res.pages;
        state.leads.facets = res.facets || state.leads.facets;
        var totalEl = $('#lead-total');
        if (totalEl) totalEl.textContent = num(res.total) + ' contacts in this workspace.';
        leadFilterBadge();
      }
      if (state.view !== 'leads') return;
      var box = $('#lead-table');
      if (box) {
        box.innerHTML = leadsTableHtml();
        wireLeadTable();
      } else {
        render();
      }
    });
  }

  function paintChrome() {
    var d = state.data;
    if (!d) return;
    $('#brand-ver').textContent = 'v' + d.version + ' · local-first SDR';
    $('#nav-leads').textContent = num(d.stats.leads_total);
    $('#nav-seqs').textContent = num(d.stats.sequences);
    $('#nav-inbox').textContent = num(d.stats.replies_total);
    var auto = d.automation || {};
    if ($('#nav-jobs')) $('#nav-jobs').textContent = num(auto.due || 0);
    if ($('#nav-signals')) $('#nav-signals').textContent = num(auto.signals_new || 0);
    if ($('#nav-brands')) $('#nav-brands').textContent = num(auto.brands || 0);
    if ($('#nav-offers')) $('#nav-offers').textContent = num(auto.offers_active || 0);
    var w = d.worker || {};
    var running = w.running;
    var last = w.last_tick_result || {};
    var text;
    if (!running) text = 'queue stopped';
    else if (last.reason) text = 'queue idle';
    else if (last.action === 'sent') text = 'sending...';
    else text = 'queue active';
    var sending = d.settings.sending || {};
    $('#worker-text').textContent = text + ' · ' + (sending.enabled ? 'sending on' : 'sending off');
    $('#worker-text').title = (w.last_tick_result && w.last_tick_result.reason) || '';
    var dot = $('#worker-dot');
    dot.className = 'dot ' + (running ? 'on' : 'off');
    $$('.nav-item').forEach(function (n) {
      var nav = n.dataset.nav;
      var on = nav === 'daily' ? DAILY.indexOf(state.view) >= 0 : nav === state.view;
      n.classList.toggle('active', on);
    });
    $$('[data-ico]').forEach(function (n) { if (!n.innerHTML) n.innerHTML = ICONS[n.dataset.ico] || ''; });
  }

  // ------------------------------------------------------------------ views
  var DAILY = ['signals', 'automation', 'brands', 'match'];

  function dailyTabs(active) {
    var labels = { automation: 'Automation', signals: 'Signals', brands: 'Brands', match: 'Match' };
    return '<div class="toolbar" style="margin-bottom:14px">' +
      DAILY.map(function (v) {
        var on = v === active;
        return '<button class="btn' + (on ? ' primary' : '') + ' sm" data-nav="' + v + '">' +
          labels[v] + '</button>';
      }).join('') + '</div>';
  }

  function render() {
    var d = state.data;
    if (!d) return;
    var view = $('#view');
    var html = '';
    if (state.view === 'dashboard') html = viewDashboard(d);
    else if (state.view === 'leads') html = viewLeads(d);
    else if (state.view === 'domains') html = viewDomains(d);
    else if (state.view === 'osint') html = viewOsint(d);
    else if (state.view === 'sequences') html = viewSequences(d);
    else if (state.view === 'inbox') html = viewInbox(d);
    else if (state.view === 'automation') html = viewAutomation(d);
    else if (state.view === 'signals') html = viewSignals(d);
    else if (state.view === 'brands') html = viewBrands(d);
    else if (state.view === 'offers') html = viewOffers(d);
    else if (state.view === 'match') html = viewMatch(d);
    else if (state.view === 'mailboxes') html = viewMailboxes(d);
    else if (state.view === 'ads') html = viewAds(d);
    else if (state.view === 'agent') html = viewAgent(d);
    else if (state.view === 'settings') html = viewSettings(d);
    else if (state.view === 'social') {
      if (!state.social) {
        loadSocial();
        view.innerHTML = '<div class="empty"><div class="big">Opening the workshop...</div></div>';
        return;
      }
      if (SOCIAL.rendered) return;      // keep whatever the user has typed in the form
      view.innerHTML = viewSocial();
      wireSocial();
      SOCIAL.rendered = true;
      return;
    }
    if (DAILY.indexOf(state.view) >= 0) html = dailyTabs(state.view) + html;
    view.innerHTML = html;
    if (state.view === 'leads') wireLeads();
    if (state.view === 'domains') wireDomains();
    if (state.view === 'leads') wireLeads();
    if (state.view === 'settings') wireSettings();
    if (state.view === 'mailboxes') wireMailboxes();
    if (state.view === 'inbox') wireInbox();
    if (state.view === 'sequences') wireSequences();
    if (state.view === 'ads') wireAds();
    if (state.view === 'agent') wireAgent();
  }

  function pageHead(title, sub, actions) {
    return '<div class="topbar"><div><h1>' + h(title) + '</h1>' +
      (sub ? '<div class="sub">' + sub + '</div>' : '') + '</div><div class="spacer"></div>' +
      (actions || '') + '</div>';
  }

  // ---- dashboard
  function viewDashboard(d) {
    var s = d.stats;
    var replyRate = pct(s.replies_total, s.sends_total);
    var onboarded = d.settings.onboarded;
    var checklist = [
      { done: d.mailboxes.length > 0, title: 'Connect a mailbox', hint: 'SMTP for sending, IMAP to catch replies.', go: 'mailboxes' },
      { done: s.leads_total > 10, title: 'Import your leads', hint: 'CSV, TSV or Excel with an email column.', go: 'leads' },
      { done: s.sequences > 0, title: 'Set up a sequence', hint: 'Two or three short emails beat one long one.', go: 'sequences' },
      { done: s.sends_total > 0, title: 'Send your first batch', hint: 'Enroll leads, then let the queue pace them.', go: 'sequences' }
    ];
    var doneCount = checklist.filter(function (c) { return c.done; }).length;

    var html = pageHead('Dashboard', 'Your outreach at a glance.',
      '<button class="btn" data-act="demo">' + (s.leads_total ? 'Reset demo data' : 'Try the demo') + '</button>' +
      '<button class="btn" data-act="import">Import leads</button>' +
      '<button class="btn primary" data-act="tick">Send next now</button>');

    html += chatCard(d);

    if (!onboarded || doneCount < 4) {
      html += '<div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Get set up</h2>' +
        '<div class="spacer"></div><span class="badge ' + (doneCount === 4 ? 'green' : 'blue') + '">' +
        doneCount + ' of 4</span></div><div class="checklist">' +
        checklist.map(function (c, i) {
          return '<div class="ci"><div class="mark ' + (c.done ? 'done' : '') + '">' + (c.done ? '✓' : (i + 1)) + '</div>' +
            '<div class="grow"><strong>' + h(c.title) + '</strong><div class="hint">' + h(c.hint) + '</div></div>' +
            (c.done ? '' : '<button class="btn sm" data-go="' + c.go + '">Start</button>') + '</div>';
        }).join('') + '</div></div>';
    }

    html += '<div class="grid cols-4" style="margin-bottom:14px">' +
      kpi('Leads', num(s.leads_total), num(s.leads_new) + ' never contacted', '') +
      kpi('Sent today', num(s.sends_today), num(s.sends_week) + ' in the last 7 days', 'accent') +
      kpi('Replies', num(s.replies_total), replyRate + ' of everything sent', 'green') +
      kpi('In sequence', num(s.running), num(s.due_now) + ' due right now', 'amber') +
      '</div>';

    var stages = ['new', 'contacted', 'replied', 'interested', 'won'];
    var maxStage = Math.max.apply(null, stages.map(function (k) { return s.by_status[k] || 0; }).concat([1]));
    html += '<div class="grid cols-2">';
    html += '<div class="card"><div class="card-head"><h2>Pipeline</h2><div class="spacer"></div>' +
      '<span class="badge grey">' + num(s.leads_total) + ' leads</span></div><div class="bars">' +
      stages.map(function (k) {
        var v = s.by_status[k] || 0;
        return '<div class="bar-row"><div>' + h(k) + '</div><div class="bar-track">' +
          '<div class="bar-fill" style="width:' + Math.round((v / maxStage) * 100) + '%"></div></div>' +
          '<div class="n">' + num(v) + '</div></div>';
      }).join('') +
      '<div class="bar-row"><div>lost / bounced</div><div class="bar-track"><div class="bar-fill" ' +
      'style="width:' + Math.round((((s.by_status.lost || 0) + (s.by_status.bounced || 0)) / maxStage) * 100) +
      '%;background:var(--red)"></div></div><div class="n">' + num((s.by_status.lost || 0) + (s.by_status.bounced || 0)) +
      '</div></div></div></div>';

    html += '<div class="card"><div class="card-head"><h2>Sending capacity</h2><div class="spacer"></div>' +
      '<span class="badge ' + (s.due_now ? 'blue' : 'grey') + '">' + num(s.due_now) + ' due</span></div>';
    if (!d.mailboxes.length) {
      html += '<div class="empty" style="padding:22px"><div class="big">No mailbox yet</div>' +
        '<p class="small">Add your SMTP and IMAP details and Lead Studio sends from your own inbox.</p>' +
        '<button class="btn primary sm" data-go="mailboxes">Add a mailbox</button></div>';
    } else {
      html += d.mailboxes.map(function (b) {
        var used = Math.min(100, Math.round((b.sent_today / Math.max(1, b.daily_limit)) * 100));
        return '<div class="usage"><div style="min-width:96px" class="small">' + h(b.name) + '</div>' +
          '<div class="track"><div class="fill" style="width:' + used + '%;background:' +
          (used > 85 ? 'var(--amber)' : 'var(--green)') + '"></div></div>' +
          '<div class="small dim nowrap">' + b.sent_today + '/' + b.daily_limit + '</div></div>';
      }).join('');
      html += '<div class="sep"></div><div class="small muted">The queue sends one email at a time, ' +
        'respects each mailbox daily cap and waits between sends.</div>';
    }
    html += '</div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Recent activity</h2><div class="spacer"></div>' +
      '<button class="btn sm ghost" data-act="events">Refresh</button></div><div class="list" id="events-list">' +
      '<div class="small dim">Loading...</div></div></div>';

    setTimeout(loadEvents, 30);
    setTimeout(function () {
      renderChat();
      var send = $('#chat-send');
      if (send) send.onclick = sendChat;
      var input = $('#chat-input');
      if (input) input.onkeydown = function (e) { if (e.key === 'Enter') sendChat(); };
    }, 30);
    setTimeout(wireDashboard, 0);
    return html;
  }

  function kpi(label, value, sub, cls) {
    return '<div class="card kpi"><div class="label">' + h(label) + '</div>' +
      '<div class="value ' + (cls || '') + '">' + value + '</div>' +
      '<div class="delta">' + h(sub) + '</div></div>';
  }

  function loadEvents() {
    call('/api/events?limit=12').then(function (res) {
      var box = $('#events-list');
      if (!box) return;
      if (!res.ok || !res.events.length) {
        box.innerHTML = '<div class="small dim">Nothing yet. Import leads or add a mailbox to get started.</div>';
        return;
      }
      box.innerHTML = res.events.map(function (e) {
        return '<div class="list-item"><div class="avatar">' + h((e.kind || '?').slice(0, 2)) + '</div>' +
          '<div class="grow"><div class="small">' + h(e.detail || e.kind) + '</div>' +
          '<div class="t-sub">' + h(e.kind) + ' · ' + fmtWhen(e.ts) + '</div></div></div>';
      }).join('');
    });
  }

  function wireDashboard() {
    $$('[data-go]').forEach(function (b) {
      b.onclick = function () { state.view = b.dataset.go; render(); };
    });
  }

  // ---- leads
  function leadFilterOptions(items, current, anyLabel) {
    return '<option value="">' + h(anyLabel) + '</option>' + (items || []).map(function (value) {
      return '<option value="' + h(value) + '"' + (value === current ? ' selected' : '') + '>' +
        h(value) + '</option>';
    }).join('');
  }

  function leadFilterField(label, id, value, placeholder) {
    return '<div class="field"><label>' + h(label) + '</label>' +
      '<input type="text" id="' + id + '" value="' + h(value || '') + '" placeholder="' +
      h(placeholder || '') + '"></div>';
  }

  function leadFilterPanel(facets, f) {
    var sizes = (facets.sizes || []).map(function (value) {
      return '<option value="' + h(value) + '"' + (f.company_size === value ? ' selected' : '') +
        '>' + h(value) + '</option>';
    }).join('');
    return '<div class="sep"></div><div class="grid cols-4" style="gap:12px">' +
      '<div class="field"><label>Country</label><select id="f-country">' +
      leadFilterOptions(facets.countries, f.country, 'Any country') + '</select></div>' +
      '<div class="field"><label>Source</label><select id="f-source">' +
      leadFilterOptions(facets.sources, f.source, 'Any source') + '</select></div>' +
      '<div class="field"><label>Company size</label><select id="f-size">' +
      '<option value="">Any size</option>' + sizes +
      '<option value="unknown"' + (f.company_size === 'unknown' ? ' selected' : '') +
      '>unknown</option></select></div>' +
      leadFilterField('Location (city or country)', 'f-location', f.location,
                      'e.g. Bratislava') +
      leadFilterField('Job title', 'f-title', f.title, 'e.g. marketing') +
      leadFilterField('Industries - any of', 'f-industry', f.industry,
                      'e.g. saas, manufacturing') +
      leadFilterField('Must have industries - all of', 'f-must', f.must_industry,
                      'e.g. manufacturing') +
      leadFilterField('Technologies - any of', 'f-tech', f.technologies,
                      'e.g. shopify, wordpress') +
      '</div>' +
      '<div class="toolbar" style="margin:12px 0 0"><span class="small dim">Filters apply as you ' +
      'type. A comma separates several values.</span><div class="spacer" style="flex:1"></div>' +
      '<button class="btn sm ghost" data-act="lead-clear">Clear filters</button></div>';
  }

  function wireLeads() {
    var view = $('#view');
    if (!view || view.dataset.dropWired) return;
    view.dataset.dropWired = '1';
    view.ondragover = function (e) { e.preventDefault(); };
    view.ondrop = function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      importModal(e.dataTransfer.files[0]);
    };
  }

  function viewLeads(d) {
    var q = state.leads;
    var f = q.filters || {};
    var facets = q.facets || { countries: [], sources: [], sizes: [] };
    var active = LEAD_FILTER_KEYS.filter(function (key) { return f[key]; }).length;
    var html = pageHead('Leads',
      '<span id="lead-total">' + num(q.total) + ' contacts in this workspace.</span>',
      '<button class="btn" data-act="export">CSV</button>' +
      '<button class="btn" data-act="export-parquet">Parquet</button>' +
      '<button class="btn" data-act="export-duckdb">DuckDB</button>' +
      '<button class="btn" data-act="bulk-data">Bulk data</button>' +
      '<button class="btn primary" data-act="add-lead">Add lead</button>');

    html += '<div class="card" id="contacts-import" style="margin-bottom:12px">' +
      '<div class="card-head"><h2>Import contacts</h2><div class="spacer"></div>' +
      '<span class="small dim">CSV · XLSX · JSON · Parquet · DuckDB · SQLite</span></div>' +
      '<div class="row" style="align-items:center;gap:12px">' +
      '<div class="grow small dim">Drop a file anywhere on this page, or press Import contacts. ' +
      'Every row in the file is read; a run that stops continues from where it stopped.</div>' +
      '<button class="btn primary" data-act="import">Import contacts</button></div></div>';

    html += '<div class="card tight"><div class="toolbar" style="margin:0">' +
      '<div class="search grow"><span class="s-ico">' + ICONS.search + '</span>' +
      '<input type="search" id="lead-search" placeholder="Search name, company, email or description" value="' +
      h(q.q) + '"></div>' +
      '<select id="lead-status" style="max-width:150px">' +
      ['', 'new', 'contacted', 'replied', 'interested', 'won', 'lost', 'bounced', 'unsubscribed']
        .map(function (s) {
          return '<option value="' + s + '"' + (q.status === s ? ' selected' : '') + '>' +
            (s ? s[0].toUpperCase() + s.slice(1) : 'All statuses') + '</option>';
        }).join('') + '</select>' +
      '<select id="lead-sort" style="max-width:160px">' + LEAD_SORTS.map(function (sort) {
        return '<option value="' + sort.key + '"' +
          ((f.sort || 'newest') === sort.key ? ' selected' : '') + '>' + h(sort.label) +
          '</option>';
      }).join('') + '</select>' +
      '<button class="btn sm' + (active ? ' primary' : '') + '" id="lead-filter-toggle">Filters' +
      (active ? ' (' + active + ')' : '') + '</button>' +
      '<button class="btn sm" data-act="verify">Verify emails</button>' +
      '</div>' + (q.showFilters ? leadFilterPanel(facets, f) : '') + '</div>';

    html += '<div id="lead-table" class="mt"></div>';
    return html;
  }

  function leadsTableHtml() {
    var q = state.leads;
    if (!q.items.length) {
      return '<div class="card"><div class="empty"><div class="big">No leads match</div>' +
        '<p>Nothing under these filters. Clear them, or import a file - an email is welcome but ' +
        'not required.</p><button class="btn primary" data-act="import">Import a file</button> ' +
        '<button class="btn" data-act="lead-clear">Clear filters</button></div></div>';
    }
    var html = '<div class="card tight" style="padding:0">' +
      '<div class="toolbar" style="padding:10px 12px;margin:0;border-bottom:1px solid var(--line)">' +
      '<label class="check"><input type="checkbox" id="sel-all"> Select page</label>' +
      '<span class="small dim" id="sel-count">' + state.selection.size + ' selected</span>' +
      '<div class="spacer" style="flex:1"></div>' +
      '<button class="btn sm" data-act="enroll-sel">Enroll selected</button>' +
      '<button class="btn sm danger" data-act="delete-sel">Delete</button>' +
      '</div><div class="table-wrap"><table><thead><tr>' +
      '<th style="width:30px"></th><th>Lead</th><th>Company</th><th>Industry</th><th>Size</th>' +
      '<th>Status</th><th>Verified</th><th>Last contact</th><th></th></tr></thead><tbody>' +
      q.items.map(function (l) {
        return '<tr data-lead="' + l.id + '">' +
          '<td><input type="checkbox" class="row-sel" data-id="' + l.id + '"' +
          (state.selection.has(l.id) ? ' checked' : '') + '></td>' +
          '<td><div class="t-email">' + h(l.email || l.company || '—') +
          (l.email ? '' : ' <span class="badge grey">no email</span>') + '</div>' +
          '<div class="t-sub">' + h([l.first_name, l.last_name].filter(Boolean).join(' ') ||
            (l.email ? '—' : (l.title || ''))) + '</div></td>' +
          '<td>' + (l.email
            ? '<div>' + h(l.company || '—') + '</div><div class="t-sub">' +
              h([l.title, l.city].filter(Boolean).join(' · ') || l.domain || '') + '</div>'
            : '<div class="small">' + h((l.description || '—').slice(0, 80)) + '</div>' +
              '<div class="t-sub">' + h(l.domain || '') + '</div>') + '</td>' +
          '<td class="small">' + h((l.industry || '—').slice(0, 70)) + '</td>' +
          '<td class="small dim nowrap">' + h(l.company_size || '—') + '</td>' +
          '<td>' + statusBadge(l.status) + '</td>' +
          '<td>' + verifyBadge(l.verify_status) + '</td>' +
          '<td class="small dim nowrap">' + fmtDate(l.last_contact_ts) + '</td>' +
          '<td class="right nowrap"><button class="btn sm ghost" data-edit="' + l.id +
          '">Edit</button></td></tr>';
      }).join('') + '</tbody></table></div></div>';

    html += '<div class="toolbar mt"><span class="small dim">Page ' + q.page + ' of ' + q.pages +
      ' · ' + num(q.total) + ' matching</span>' +
      '<div class="spacer" style="flex:1"></div>' +
      '<button class="btn sm" data-page="prev"' + (q.page <= 1 ? ' disabled' : '') +
      '>Previous</button>' +
      '<button class="btn sm" data-page="next"' + (q.page >= q.pages ? ' disabled' : '') +
      '>Next</button></div>';
    return html;
  }

  function wireLeadTable() {
    var all = $('#sel-all');
    if (all) all.onchange = function () {
      state.leads.items.forEach(function (l) {
        if (all.checked) state.selection.add(l.id); else state.selection.delete(l.id);
      });
      render();
    };
    $$('.row-sel').forEach(function (cb) {
      cb.onchange = function () {
        var id = Number(cb.dataset.id);
        if (cb.checked) state.selection.add(id); else state.selection.delete(id);
        var counter = $('#sel-count');
        if (counter) counter.textContent = state.selection.size + ' selected';
      };
    });
    $$('[data-edit]').forEach(function (button) {
      button.onclick = function () { editLead(Number(button.dataset.edit)); };
    });
    $$('[data-page]').forEach(function (button) {
      button.onclick = function () {
        state.leads.page += button.dataset.page === 'next' ? 1 : -1;
        state.selection.clear();
        loadLeads();
      };
    });
  }

  function wireLeads() {
    var q = state.leads;
    var search = $('#lead-search');
    if (search) {
      var timer;
      search.oninput = function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          q.q = search.value;
          q.page = 1;
          state.selection.clear();   // ticks on rows that are no longer listed must not act on them
          loadLeads();
        }, 260);
      };
    }
    var status = $('#lead-status');
    if (status) status.onchange = function () {
      q.status = status.value;
      q.page = 1;
      state.selection.clear();
      loadLeads();
    };
    var sort = $('#lead-sort');
    if (sort) sort.onchange = function () {
      q.filters.sort = sort.value;
      q.page = 1;
      loadLeads();
    };
    var toggle = $('#lead-filter-toggle');
    if (toggle) toggle.onclick = function () {
      q.showFilters = !q.showFilters;
      render();
    };
    var fields = {
      'f-country': 'country', 'f-source': 'source', 'f-size': 'company_size',
      'f-location': 'location', 'f-title': 'title', 'f-industry': 'industry',
      'f-must': 'must_industry', 'f-tech': 'technologies'
    };
    Object.keys(fields).forEach(function (id) {
      var el = $('#' + id);
      if (!el) return;
      var key = fields[id];
      var apply = function () {
        q.filters[key] = el.value;
        q.page = 1;
        loadLeads();
      };
      if (el.tagName === 'SELECT') {
        el.onchange = apply;
      } else {
        var timer;
        el.oninput = function () {
          clearTimeout(timer);
          timer = setTimeout(apply, 400);
        };
      }
    });
    wireLeadTable();
  }

  // ---- domains
  function loadDomains() {
    var q = state.domains;
    return call('/api/domain/list?page=' + q.page + '&per=50&q=' + encodeURIComponent(q.q) +
                '&source=' + encodeURIComponent(q.source)).then(function (res) {
      if (res.ok) {
        q.items = res.domains || [];
        q.total = res.total;
        q.pages = res.pages;
        q.stats = res.stats;
        q.sources = res.sources || [];
        var nav = $('#nav-domains');
        if (nav) nav.textContent = num(res.stats.total);
      }
      if (state.view === 'domains') render();
    });
  }

  function ccDate(stamp) {
    var value = String(stamp || '');
    if (value.length < 8) return '—';
    return value.slice(0, 4) + '-' + value.slice(4, 6) + '-' + value.slice(6, 8);
  }

  function osmSummary(r) {
    if (!r || !r.ok) return '';
    return '<div class="small"><b>' + num(r.total) + '</b> businesses · <b>' +
      num(r.with_email) + '</b> with an email · <b>' + num(r.website_only) +
      '</b> website only · <b>' + num(r.phone_only) + '</b> phone only' +
      (r.cached ? ' <span class="badge grey">cached</span>' : '') + '</div>' +
      '<div class="small dim">' + h(r.place || '') + ' · ' + h((r.tags || []).join(', ')) + '</div>';
  }

  var OSINT_JOBS = ['phishing', 'scam_radar', 'brand_watch'];

  function loadOsintJobs() {
    var box = $('#osint-jobs');
    if (!box) return;
    call('/api/jobs').then(function (res) {
      if (!res.ok) { box.innerHTML = '<div class="small dim">' + h(res.error) + '</div>'; return; }
      var jobs = (res.jobs || []).filter(function (job) { return OSINT_JOBS.indexOf(job.key) >= 0; });
      if (!jobs.length) {
        box.innerHTML = '<div class="empty"><div class="big">No scam jobs registered</div></div>';
        return;
      }
      box.innerHTML = jobs.map(function (j) {
        var last = j.last_run || {};
        var badge = j.running ? '<span class="badge blue">running</span>'
          : (j.enabled ? '<span class="badge green">scheduled</span>' : '<span class="badge grey">off</span>');
        return '<div class="card tight" style="margin-bottom:10px">' +
          '<div class="card-head" style="margin-bottom:6px"><div><h3>' + h(j.name) + '</h3>' +
          '<div class="card-sub">' + h(j.description) + '</div></div><div class="spacer"></div>' + badge + '</div>' +
          '<div class="toolbar" style="margin:0">' +
          '<input type="text" class="job-sched" data-key="' + h(j.key) + '" value="' + h(j.schedule || '') +
          '" placeholder="daily 06:30" style="max-width:170px">' +
          '<label class="check"><input type="checkbox" class="job-en" data-key="' + h(j.key) + '"' +
          (j.enabled ? ' checked' : '') + '> enabled</label>' +
          '<button class="btn sm" data-run="' + h(j.key) + '"' + (j.running ? ' disabled' : '') + '>Run now</button>' +
          '<button class="btn sm ghost" data-log="' + h(j.key) + '">Last log</button>' +
          '<div class="spacer" style="flex:1"></div>' +
          '<span class="small dim nowrap">' + (last.started_ts ?
            (last.status === 'ok' ? 'last ok ' : 'last failed ') + fmtWhen(last.started_ts) : 'never run') +
          (j.next_run_ts ? ' · next ' + fmtWhen(j.next_run_ts) : ' · manual') + '</span></div></div>';
      }).join('');
      wireAutomation();
    });
  }

  function viewOsint(d) {
    var o = (d && d.osint) || {};
    var rp = o.red_points || {};
    var html = pageHead('OSINT - fake shops & scams',
      'The Red Points side of the business: which domains are faking the brands you protect, ' +
      'what the watchers found, and what happens next.',
      '<button class="btn" data-act="osint-refresh">Refresh</button>');
    html += '<div class="grid cols-4" style="margin-bottom:14px">' +
      kpi('Fake shops', num(o.fake_shops), 'matched against your brands', 'red') +
      kpi('Candidates', num(o.candidates), 'dorks + certificates', 'accent') +
      kpi('Lookalikes', num(o.lookalikes), 'from the brand watch', 'amber') +
      kpi('Domains profiled', num(o.profiled), 'identified from the site', '') + '</div>';

    html += '<div class="card mt" style="margin-bottom:14px">' +
      '<div class="card-head"><h2>Red Points - brand protection</h2>' +
      '<div class="spacer"></div><span class="small dim">affiliate</span></div>' +
      '<div class="small dim">' + h(rp.name || 'Red Points - brand protection') + '</div>' +
      '<div class="toolbar mt"><a class="btn primary" href="' + h(rp.affiliate_url || 'https://www.redpoints.com/request-demo-affiliate/?referredid=255256383275') + '" target="_blank" rel="noopener sponsored">Open Red Points</a>' +
      '<span class="small dim">Affiliate link - we may earn a commission.</span></div></div>';

    html += '<div class="nav-label" style="padding-left:2px">Scam jobs</div>' +
      '<div class="small dim" style="margin:-4px 0 10px 2px">The watchers run from here; what ' +
      'they find lands in this tab and in the Daily feed.</div>' +
      '<div id="osint-jobs"><div class="small dim">Loading the jobs...</div></div>';
    setTimeout(loadOsintJobs, 20);

    html += '<div class="card"><div class="card-head"><h2>Fake-shop radar</h2><div class="spacer"></div>' +
      '<span class="small dim">domains-monitor feed x the brands you protect</span></div>' +
      '<div class="field"><label>Domains feed (zip, txt or URL)</label>' +
      '<input id="osint-source" placeholder="C:\\path\\dailyupdate.zip  or  https://...zip"></div>' +
      '<div class="field mt"><label>Brands to protect (one per line, optional)</label>' +
      '<textarea id="osint-brands" rows="3" placeholder="type a few brand names, or leave empty to use the brands saved on the server"></textarea></div>' +
      '<div class="toolbar mt"><button class="btn primary" data-act="osint-scan">Run the radar</button>' +
      '<span class="small dim">' + num(o.brands) + ' brands saved</span></div>' +
      '<div id="osint-result" class="small dim mt"></div></div>';

    var keys = o.keys || {};
    html += '<div class="card mt"><div class="card-head"><h2>Dork sweep (Serper)</h2>' +
      '<div class="spacer"></div>' + (keys.serper ? '<span class="badge green">Serper key set</span>'
        : '<span class="badge grey">no Serper key</span>') + '</div>' +
      '<div class="grid cols-2"><div class="field"><label>Brands</label>' +
      '<textarea id="osint-dork-brands" rows="2" placeholder="Nike' + '\n' + 'Adidas"></textarea></div>' +
      '<div class="field"><label>Their own domains (dropped from the results)</label>' +
      '<input id="osint-dork-own" placeholder="nike.com, adidas.com"></div></div>' +
      '<div class="toolbar mt"><button class="btn primary" data-act="osint-dorks">Run the sweep</button>' +
      '<span class="small dim">official store · shop now · outlet · site:myshopify.com</span></div>' +
      '<div id="osint-dorks-result" class="small dim mt"></div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Certificate discovery (crt.sh)</h2>' +
      '<div class="spacer"></div><span class="small dim">free, no key</span></div>' +
      '<div class="field"><label>Brands</label>' +
      '<textarea id="osint-crt-brands" rows="2" placeholder="Nike"></textarea></div>' +
      '<div class="toolbar mt"><button class="btn primary" data-act="osint-discover">Find domains in certificates</button>' +
      '<span class="small dim">finds fakes before any scam feed does</span></div>' +
      '<div id="osint-crt-result" class="small dim mt"></div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Treg tools</h2>' +
      '<div class="spacer"></div><span class="small dim">one token, pay per call</span></div>' +
      '<div class="grid cols-3"><div class="field"><label>Verify emails</label>' +
      '<input id="treg-emails" placeholder="a@b.com, c@d.com"></div>' +
      '<div class="field"><label>Google Maps businesses</label>' +
      '<input id="treg-maps" placeholder="coffee Bratislava"></div>' +
      '<div class="field"><label>&nbsp;</label><div class="toolbar" style="margin:0">' +
      '<button class="btn primary" data-act="treg-verify">Verify</button>' +
      '<button class="btn" data-act="treg-maps-run">Find places</button></div></div></div>' +
      '<div id="treg-result" class="small dim mt">Email verification is usually free on a public route, otherwise about $0.001; ' +
      'Maps costs a fraction of a cent per call.</div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Recent fake shops</h2></div>' +
      ((o.recent_fake_shops || []).length
        ? '<div class="table-wrap"><table><thead><tr><th>Domain</th><th>Brand</th><th>Why</th></tr></thead><tbody>' +
          o.recent_fake_shops.map(function (row) {
            return '<tr><td class="mono">' + h(row.domain) + '</td><td>' + h(row.company) +
              '</td><td class="small dim">' + h(row.title) + '</td></tr>';
          }).join('') + '</tbody></table></div>'
        : '<div class="small dim">Nothing yet - run the radar on a feed.</div>') + '</div>';
    return html;
  }

  function viewDomains(d) {
    var q = state.domains;
    var s = q.stats || {};
    var html = pageHead('Domains', num(q.total) +
      ' domains in the catalogue - pulled from open data, enriched with Common Crawl.',
      '<button class="btn" data-act="domain-sync">Import lead domains</button>' +
      '<button class="btn" data-act="domain-enrich">Check Common Crawl</button>' +
      '<button class="btn" data-act="domain-refresh">Refresh</button>');

    html += '<div class="grid cols-4" style="margin-bottom:14px">' +
      kpi('Domains', num(s.total), 'in the catalogue', 'accent') +
      kpi('Crawled', num(s.crawled), 'seen by Common Crawl', 'green') +
      kpi('With MX', num(s.with_mx), 'can receive mail', '') +
      kpi('New this week', num(s.new_week), num(s.leads) + ' domains have leads', 'amber') +
      '</div>';

    html += '<div class="card"><div class="card-head"><h2>Pull from open data</h2>' +
      '<div class="spacer"></div><span class="small dim">streamed from Hugging Face - only new domains are stored</span></div>' +
      '<div class="grid cols-3">' + (q.sources || []).map(function (src) {
        return '<div class="card sv-ad-card" style="background:var(--panel-2)">' +
          '<div class="card-head"><h3>' + h(src.label) + '</h3><div class="spacer"></div>' +
          '<span class="badge grey">' + num(src.rows) + '</span></div>' +
          '<div class="small dim">' + h(src.note) + '</div>' +
          '<div class="small dim mt">' + h(src.license) + ' · <span class="mono">' +
          h(src.dataset) + '</span></div>' +
          '<div class="toolbar" style="margin:12px 0 0">' +
          '<input type="number" id="pull-' + h(src.key) + '" value="2000" min="1" max="200000" style="max-width:96px">' +
          '<button class="btn sm primary" data-act="domain-pull" data-source="' + h(src.key) + '">Pull</button>' +
          '<a class="btn sm ghost" href="' + h(src.url) + '" target="_blank" rel="noopener">Open</a>' +
          '</div></div>';
      }).join('') + '</div></div>';

    html += '<div class="card mt" id="osm-card"><div class="card-head"><h2>Find local businesses ' +
      '(OpenStreetMap)</h2><div class="spacer"></div>' +
      '<span class="small dim">free · no key · ODbL</span></div>' +
      '<div class="grid cols-3">' +
      '<div class="field"><label>What to look for</label>' +
      '<input id="osm-text" placeholder="roofers in Slovakia · dentists, Bratislava · yoga studio"></div>' +
      '<div class="field"><label>Where (optional)</label>' +
      '<input id="osm-area" placeholder="country, region or city"></div>' +
      '<div class="field"><label>How many</label>' +
      '<input type="number" id="osm-limit" value="200" min="5" max="1000"></div>' +
      '</div>' +
      '<div class="toolbar" style="margin-top:10px">' +
      '<button class="btn primary" data-act="osm-search">Find businesses</button>' +
      '<button class="btn" data-act="osm-import">Import the ones with a contact</button></div>' +
      '<div id="osm-result" class="small dim mt">Any trade or business type works - an unknown term ' +
      'is matched against the name/craft/shop tags, and the answer is cached.</div>' +
      '<div class="small dim mt">Data &copy; OpenStreetMap contributors, ODbL. Emailing people ' +
      'found this way is your GDPR call.</div></div>';

    html += '<div class="card mt" id="profile-card"><div class="card-head"><h2>Identify a bare ' +
      'domain</h2><div class="spacer"></div><span class="small dim">title, description, emails, ' +
      'phones</span></div>' +
      '<div class="toolbar"><input id="osint-domain" placeholder="example.sk" style="max-width:260px">' +
      '<button class="btn primary" data-act="osint-profile">Profile it</button>' +
      '<button class="btn" data-act="osint-profile-new">Profile 20 leads with no email</button></div>' +
      '<div id="osint-profile-result" class="small dim mt">Turns a domain with no email into ' +
      'something the enrichment and the offer fit can use.</div></div>';

    html += '<div class="card tight mt" style="padding:0">' +
      '<div class="toolbar" style="padding:10px 12px;margin:0;border-bottom:1px solid var(--line)">' +
      '<div class="search grow"><span class="s-ico">' + ICONS.search + '</span>' +
      '<input type="search" id="domain-search" placeholder="Search a domain" value="' + h(q.q) + '"></div>' +
      '<select id="domain-source" style="max-width:220px">' +
      '<option value="">All sources</option>' +
      (q.sources || []).map(function (src) {
        return '<option value="' + h(src.key) + '"' + (q.source === src.key ? ' selected' : '') +
          '>' + h(src.label) + '</option>';
      }).join('') +
      '<option value="commoncrawl-hf"' + (q.source === 'commoncrawl-hf' ? ' selected' : '') +
      '>Common Crawl host index</option></select>' +
      '</div>' +
      (q.items.length
        ? '<div class="table-wrap"><table><thead><tr><th>Domain</th><th>Source</th>' +
          '<th class="num">CC pages</th><th>Last crawl</th><th>MX</th><th class="num">Leads</th>' +
          '<th></th></tr></thead><tbody>' + q.items.map(function (dom) {
            return '<tr><td><div class="t-email">' + h(dom.domain) + '</div>' +
              '<div class="t-sub">' + h(dom.name || (dom.data && dom.data.title) || '') + '</div></td>' +
              '<td>' + (dom.source ? '<span class="badge grey">' + h(dom.source) + '</span>' : '—') + '</td>' +
              '<td class="num">' + (dom.cc_pages ? num(dom.cc_pages) : '—') + '</td>' +
              '<td class="small dim nowrap">' + (dom.cc_last_crawl ? ccDate(dom.cc_last_crawl) : '—') + '</td>' +
              '<td>' + (dom.mx_status
                ? '<span class="badge ' + (dom.mx_status === 'ok' ? 'green'
                  : dom.mx_status === 'none' ? 'red' : 'grey') + '">' + h(dom.mx_status) + '</span>'
                : '—') + '</td>' +
              '<td class="num">' + (dom.leads ? num(dom.leads) : '—') + '</td>' +
              '<td class="right nowrap">' +
              '<button class="btn sm ghost" data-act="domain-enrich" data-domain="' + h(dom.domain) + '">Check</button>' +
              '<button class="btn sm" data-act="domain-scrape" data-domain="' + h(dom.domain) + '">Scrape</button>' +
              '</td></tr>';
          }).join('') + '</tbody></table></div>'
        : '<div class="empty"><div class="big">No domains yet</div><p>Pull a slice from one of the ' +
          'sources above, press <strong>Import lead domains</strong> to take the domain of every ' +
          'lead you already have (with an email or not), or let the weekly Common Crawl job add them.</p></div>') +
      '</div>';

    html += '<div class="toolbar mt"><span class="small dim">Page ' + q.page + ' of ' + q.pages + '</span>' +
      '<div class="spacer" style="flex:1"></div>' +
      '<button class="btn sm" data-dpage="prev"' + (q.page <= 1 ? ' disabled' : '') + '>Previous</button>' +
      '<button class="btn sm" data-dpage="next"' + (q.page >= q.pages ? ' disabled' : '') + '>Next</button></div>';
    return html;
  }

  function wireDomains() {
    var search = $('#domain-search');
    if (search) {
      var timer;
      search.oninput = function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          state.domains.q = search.value;
          state.domains.page = 1;
          loadDomains();
        }, 260);
      };
    }
    var select = $('#domain-source');
    if (select) select.onchange = function () {
      state.domains.source = select.value;
      state.domains.page = 1;
      loadDomains();
    };
    $$('[data-dpage]').forEach(function (button) {
      button.onclick = function () {
        state.domains.page += button.dataset.dpage === 'next' ? 1 : -1;
        loadDomains();
      };
    });
  }

  function scrapeModal(result) {
    var emails = result.emails || [];
    openModal({
      title: 'Scrape ' + (result.domain || ''),
      subtitle: result.ok ? 'Read from the live site, robots.txt respected.'
        : 'Could not read the site.',
      body: (result.error ? '<div class="callout" style="border-left-color:var(--red)">' +
          h(result.error) + '</div>' : '') +
        (result.title ? '<div class="small"><strong>' + h(result.title) + '</strong></div>' : '') +
        (result.description ? '<div class="small dim">' + h(result.description) + '</div>' : '') +
        '<div class="list mt">' + (emails.length ? emails.map(function (email) {
          return '<div class="file-row"><span class="grow mono small">' + h(email) + '</span>' +
            '<button class="btn sm" data-act="add-scraped" data-email="' + h(email) +
            '" data-domain="' + h(result.domain) + '">Add as lead</button></div>';
        }).join('') : '<div class="small dim">No addresses found on the pages read.</div>') +
        '</div>' +
        ((result.phones || []).length ? '<div class="small dim mt">Phones: ' +
          h(result.phones.join(', ')) + '</div>' : '') +
        ((result.pages || []).length ? '<div class="small dim mt">Read: ' +
          h(result.pages.join(', ')) + '</div>' : '') +
        (result.blocked_by_robots ? '<div class="small dim mt">robots.txt disallows crawling ' +
          'this site, so it was not read.</div>' : ''),
      footer: '<button class="btn" data-close>Close</button>'
    });
  }

  // ---- sequences
  // ---- sequences: the campaign table
  function seqFolder(name) {
    var clean = String(name || '').replace(/^\[[A-Z]+\]\s*/, '');
    return clean.split('|')[0].trim() || 'Other';
  }

  function seqTag(name) {
    var clean = String(name || '').replace(/^\[[A-Z]+\]\s*/, '');
    var parts = clean.split('|').map(function (p) { return p.trim(); });
    return parts.length > 1 ? parts[1] : 'Other';
  }

  function seqVisible(d) {
    var query = (state.seqQuery || '').toLowerCase();
    return (d.sequences || []).filter(function (s) {
      if (state.seqFolder && seqFolder(s.name) !== state.seqFolder) return false;
      if (state.seqTag && seqTag(s.name) !== state.seqTag) return false;
      if (state.seqStatus && s.status !== state.seqStatus) return false;
      if (query && (s.name || '').toLowerCase().indexOf(query) < 0) return false;
      return true;
    });
  }

  function seqNumber(value, pctOf, dashWhenZero) {
    var number = Number(value || 0);
    if (!number && dashWhenZero) return '<div class="dim">-</div>';
    var text = num(number);
    if (pctOf && number) {
      return '<div>' + text + '</div><div class="small dim">' +
        Math.round((number / pctOf) * 100) + '%</div>';
    }
    return '<div>' + text + '</div>';
  }

  function viewSequences(d) {
    var sequences = d.sequences || [];
    var folders = [], tags = [];
    sequences.forEach(function (s) {
      var folder = seqFolder(s.name), tag = seqTag(s.name);
      if (folders.indexOf(folder) < 0) folders.push(folder);
      if (tags.indexOf(tag) < 0) tags.push(tag);
    });
    folders.sort(); tags.sort();
    var rows = seqVisible(d);

    var html = pageHead('Campaigns', 'Campaigns, their prospects and what came back. ' +
      num(sequences.length) + ' in this workspace.',
      '<button class="btn primary" data-act="new-sequence">Create campaign</button>');

    html += '<div class="card tight" style="margin-bottom:12px"><div class="toolbar" style="margin:0">' +
      '<select id="seq-folder" style="max-width:150px"><option value="">All Folders</option>' +
      folders.map(function (f) {
        return '<option value="' + h(f) + '"' + (state.seqFolder === f ? ' selected' : '') + '>' +
          h(f) + '</option>';
      }).join('') + '</select>' +
      '<select id="seq-tag" style="max-width:150px"><option value="">All Tags</option>' +
      tags.map(function (t) {
        return '<option value="' + h(t) + '"' + (state.seqTag === t ? ' selected' : '') + '>' +
          h(t) + '</option>';
      }).join('') + '</select>' +
      '<select id="seq-status" style="max-width:150px"><option value="">All Statuses</option>' +
      ['draft', 'running', 'paused', 'finished'].map(function (status) {
        return '<option value="' + status + '"' + (state.seqStatus === status ? ' selected' : '') +
          '>' + status.charAt(0).toUpperCase() + status.slice(1) + '</option>';
      }).join('') + '</select>' +
      '<input type="search" id="seq-search" placeholder="Search campaigns" style="max-width:220px" value="' +
      h(state.seqQuery || '') + '">' +
      '<div class="spacer" style="flex:1"></div>' +
      '<a class="btn sm ghost" href="#settings">Settings</a></div></div>';

    if (!sequences.length) {
      return html + '<div class="card"><div class="empty"><div class="big">No campaigns yet</div>' +
        '<p>A campaign is a series of emails with delays: day 0 intro, day 3 follow-up, day 7 close.</p>' +
        '<button class="btn primary" data-act="new-sequence">Create the first one</button></div></div>';
    }

    var totals = { prospects: 0, reached: 0, opened: 0, clicked: 0, replied: 0, interested: 0, won: 0, last24: 0 };
    rows.forEach(function (s) {
      var m = s.metrics || {};
      Object.keys(totals).forEach(function (key) { totals[key] += Number(m[key] || 0); });
    });

    html += '<div class="card" style="padding:0"><div class="table-wrap"><table class="campaigns">' +
      '<thead><tr><th>Campaign</th><th class="num">prospects</th><th class="num">Reached</th>' +
      '<th class="num">opened</th><th class="num">clicked</th><th class="num">resp.</th>' +
      '<th class="num">inter.</th><th class="num">opp.</th><th class="num">24h</th>' +
      '<th class="num">Actions</th></tr></thead><tbody>' +
      rows.map(function (s) {
        var m = s.metrics || {};
        var when = s.created_ts ? fmtDate(s.created_ts) : '';
        return '<tr><td><div><strong>' + h(s.name) + '</strong> ' +
          '<span class="badge ' + (s.status === 'running' ? 'green' :
            s.status === 'paused' ? 'amber' : 'grey') + '">' + h(s.status) + '</span>' +
          (s.source ? ' <span class="badge grey">' + h(s.source) + '</span>' : '') + '</div>' +
          '<div class="small dim">' + h(when) + ' · ' + num(m.senders || 0) + ' senders · ' +
          num((s.steps || []).length) + ' steps' +
          (s.stats_at ? ' · stats ' + fmtWhen(s.stats_at) : '') + '</div></td>' +
          '<td class="num">' + seqNumber(m.prospects) + '</td>' +
          '<td class="num">' + seqNumber(m.reached) + '</td>' +
          '<td class="num">' + seqNumber(m.opened, m.reached, s.source) + '</td>' +
          '<td class="num">' + seqNumber(m.clicked, m.reached, s.source) + '</td>' +
          '<td class="num">' + seqNumber(m.replied, m.reached, s.source) + '</td>' +
          '<td class="num">' + seqNumber(m.interested, 0, s.source) + '</td>' +
          '<td class="num">' + seqNumber(m.won, m.prospects, s.source) + '</td>' +
          '<td class="num">' + seqNumber(m.last24, 0, s.source) + '</td>' +
          '<td class="num"><div class="nowrap">' +
          '<button class="btn sm ghost" data-enroll="' + s.id + '">Enroll</button> ' +
          '<button class="btn sm" data-edit-seq="' + s.id + '">Open</button> ' +
          '<button class="btn sm danger" data-del-seq="' + s.id + '">Delete</button>' +
          '</div></td></tr>';
      }).join('') + '</tbody><tfoot><tr>' +
      '<td><strong>Showing ' + num(rows.length) + ' of ' + num(sequences.length) + ' campaigns</strong></td>' +
      '<td class="num"><strong>' + num(totals.prospects) + '</strong></td>' +
      '<td class="num"><strong>' + num(totals.reached) + '</strong></td>' +
      '<td class="num"><strong>' + num(totals.opened) +
        (totals.reached ? '<div class="small dim">' + Math.round((totals.opened / totals.reached) * 100) + '%</div>' : '') +
        '</strong></td>' +
      '<td class="num"><strong>' + num(totals.clicked) + '</strong></td>' +
      '<td class="num"><strong>' + num(totals.replied) + '</strong></td>' +
      '<td class="num"><strong>' + num(totals.interested) + '</strong></td>' +
      '<td class="num"><strong>' + num(totals.won) +
        (totals.prospects ? '<div class="small dim">' + Math.round((totals.won / totals.prospects) * 100) + '%</div>' : '') +
        '</strong></td>' +
      '<td class="num"><strong>' + num(totals.last24) + '</strong></td><td></td></tr></tfoot></table></div></div>';

    html += '<div class="small dim mt">Opened, clicked and replied are percentages of Reached; ' +
      'opp. is won over prospects. 24h counts the sends of the last day. A campaign imported ' +
      'from a sending platform shows that platform\'s numbers and the moment they were pulled.</div>';
    return html;
  }

  function wireSequences() {
    var folder = $('#seq-folder');
    if (folder) folder.onchange = function () { state.seqFolder = folder.value; render(); };
    var tag = $('#seq-tag');
    if (tag) tag.onchange = function () { state.seqTag = tag.value; render(); };
    var status = $('#seq-status');
    if (status) status.onchange = function () { state.seqStatus = status.value; render(); };
    var search = $('#seq-search');
    if (search) {
      search.oninput = function () {
        state.seqQuery = search.value;
        render();
        var again = $('#seq-search');
        if (again && document.activeElement !== again) {
          again.focus();
          again.setSelectionRange(again.value.length, again.value.length);
        }
      };
    }

    $$('[data-edit-seq]').forEach(function (b) {
      b.onclick = function () { sequenceEditor(Number(b.dataset.editSeq)); };
    });
    $$('[data-del-seq]').forEach(function (b) {
      b.onclick = function () {
        if (!confirm('Delete this sequence? Leads enrolled in it stop receiving it.')) return;
        call('/api/sequence/delete', { id: Number(b.dataset.delSeq) }).then(function (r) {
          if (r.ok) { toast('Sequence deleted'); refresh(); } else toast(r.error, 'err');
        });
      };
    });
    $$('[data-enroll]').forEach(function (b) {
      b.onclick = function () { enrollModal(Number(b.dataset.enroll)); };
    });
  }

  // ---- inbox
  function viewInbox(d) {
    var boxes = d.mailboxes || [];
    var imap = boxes.filter(function (b) { return b.imap_host; }).length;
    var html = pageHead('Inbox', 'One inbox for every mailbox. Replies and bounces are collected from ' +
      'all of them, matched to the lead, and the sequence stops.',
      '<button class="btn" data-act="sync">Check all mailboxes</button>');
    if (!boxes.length) {
      html += '<div class="callout"><strong>No mailbox yet.</strong> Add one in <em>Mailboxes</em> — ' +
        'every mailbox you add feeds this same list, and sending rotates across them.</div>';
    } else if (!imap) {
      html += '<div class="callout"><strong>Replies need IMAP.</strong> Add an IMAP server to a mailbox in ' +
        'Mailboxes so Lead Studio can see who answered, and stop their sequence automatically.</div>';
    }
    html += '<div class="card" id="inbox-card"><div class="small dim">Loading replies...</div></div>';
    setTimeout(function () { loadInbox(0); }, 20);
    return html;
  }

  function loadInbox(mailboxId) {
    call('/api/inbox?limit=200' + (mailboxId ? '&mailbox_id=' + mailboxId : '')).then(function (res) {
      var card = $('#inbox-card');
      if (!card) return;
      if (!res.ok) { card.innerHTML = '<div class="small dim">' + h(res.error) + '</div>'; return; }
      var boxes = res.mailboxes || [];
      var head = '<div class="card-head"><h2>Unified inbox</h2><div class="spacer"></div>' +
        '<span class="badge ' + (res.unread ? 'blue' : 'grey') + '">' + num(res.unread) + ' unread</span> ' +
        '<select id="inbox-box" style="max-width:270px"><option value="0">All mailboxes · ' +
        num(boxes.length) + '</option>' + boxes.map(function (b) {
          return '<option value="' + b.id + '"' + (Number(mailboxId) === b.id ? ' selected' : '') +
            '>' + h(b.name) + (b.unread ? ' · ' + b.unread + ' new' : '') +
            ' (' + num(b.total) + ')</option>';
        }).join('') + '</select></div>' +
        (res.orphans ? '<div class="small dim">' + num(res.orphans) +
          ' replies came from a mailbox that has since been removed.</div>' : '');
      if (!res.replies.length) {
        card.innerHTML = head + '<div class="empty"><div class="big">No replies yet</div>' +
          '<p>When someone answers one of your emails it lands here, their sequence stops, ' +
          'and their status flips to replied.</p></div>';
      } else {
        card.innerHTML = head + '<div class="list">' + res.replies.map(function (r) {
          return '<div class="list-item"><div class="avatar">' +
            h((r.from_email || '?').slice(0, 2).toUpperCase()) + '</div>' +
            '<div class="grow"><div><strong>' + h(r.subject || '(no subject)') + '</strong> ' +
            (r.seen ? '' : '<span class="badge blue">new</span> ') +
            (r.is_bounce ? '<span class="badge red">bounce</span>' : '<span class="badge green">reply</span>') +
            (r.mailbox_name ? ' <span class="badge grey">' + h(r.mailbox_name) + '</span>' : '') +
            '</div><div class="t-sub">' + h(r.from_email) + (r.company ? ' · ' + h(r.company) : '') +
            ' · ' + fmtWhen(r.received_ts) + '</div>' +
            (r.snippet ? '<div class="small muted" style="margin-top:4px">' + h(r.snippet) + '</div>' : '') +
            '</div><div class="nowrap">' +
            (r.seen ? '' : '<button class="btn sm ghost" data-seen="' + r.id + '">Mark read</button>') +
            '</div></div>';
        }).join('') + '</div>';
        $$('[data-seen]').forEach(function (b) {
          b.onclick = function () {
            call('/api/inbox/seen', { id: Number(b.dataset.seen) }).then(function () {
              loadInbox(mailboxId); refresh();
            });
          };
        });
      }
      var select = $('#inbox-box');
      if (select) {
        select.onchange = function () { loadInbox(Number(select.value) || 0); };
      }
    });
  }

  function wireInbox() {

  }

  // ---- automation
  function viewAutomation(d) {
    var auto = d.automation || {};
    var html = pageHead('Automation', 'Daily and weekly enrichment jobs. They run while the app is open.',
      '<button class="btn" data-act="run-due">Run due now</button>' +
      '<button class="btn primary" data-act="run-all">Run everything</button>');
    html += '<div class="callout"><strong>' + num(auto.enabled || 0) + ' jobs enabled</strong> · ' +
      num(auto.due || 0) + ' due now · ' + num(auto.signals || 0) + ' signals collected · ' +
      num(auto.domains || 0) + ' domains enriched. Schedules use this computer\'s local time; ' +
      'the app must be open for them to fire.</div>';
    html += '<div id="jobs-box"><div class="small dim">Loading jobs...</div></div>';
    setTimeout(loadJobs, 20);
    return html;
  }

  function loadJobs() {
    call('/api/jobs').then(function (res) {
      var box = $('#jobs-box');
      if (!box) return;
      if (!res.ok) { box.innerHTML = '<div class="small dim">' + h(res.error) + '</div>'; return; }
      var groups = {};
      res.jobs.forEach(function (j) { (groups[j.grp] = groups[j.grp] || []).push(j); });
      var order = ['Domains', 'Signals', 'Data', 'AI'];
      var notes = {
        Domains: 'First. New domains land, their mail server is checked, Common Crawl says whether ' +
          'the site is real, and the contact finder looks for an address.',
        Signals: 'Then. Bankruptcy, fraud, brand and hiring signals, read off the domains that just ' +
          'came in.',
        Data: 'Then. Whatever CSV or Excel files were dropped in data/imports are folded in.',
        AI: 'Last, once the data is there. The AI reads each lead and writes the segment and the ' +
          'cold-email angle. Optional: it needs a provider.'
      };
      var names = Object.keys(groups).sort(function (a, b) {
        var left = order.indexOf(a), right = order.indexOf(b);
        return (left < 0 ? 99 : left) - (right < 0 ? 99 : right);
      });
      var html = '';
      names.forEach(function (grp, index) {
        var rank = order.indexOf(grp);
        html += '<div class="nav-label" style="padding-left:2px">' +
          (rank >= 0 ? (rank + 1) + ' · ' : '') + h(grp) + '</div>' +
          (notes[grp] ? '<div class="small dim" style="margin:-4px 0 10px 2px">' + h(notes[grp]) +
            '</div>' : '');
        groups[grp].forEach(function (j) {
          var last = j.last_run || {};
          var badge = j.running ? '<span class="badge blue">running</span>'
            : (j.enabled ? '<span class="badge green">scheduled</span>' : '<span class="badge grey">off</span>');
          html += '<div class="card tight" style="margin-bottom:10px">' +
            '<div class="card-head" style="margin-bottom:6px"><div><h3>' + h(j.name) + '</h3>' +
            '<div class="card-sub">' + h(j.description) + '</div></div><div class="spacer"></div>' + badge + '</div>' +
            '<div class="toolbar" style="margin:0">' +
            '<input type="text" class="job-sched" data-key="' + h(j.key) + '" value="' + h(j.schedule || '') +
            '" placeholder="daily 06:30" style="max-width:170px">' +
            '<label class="check"><input type="checkbox" class="job-en" data-key="' + h(j.key) + '"' +
            (j.enabled ? ' checked' : '') + '> enabled</label>' +
            '<button class="btn sm" data-run="' + h(j.key) + '"' + (j.running ? ' disabled' : '') + '>Run now</button>' +
            '<button class="btn sm ghost" data-log="' + h(j.key) + '">Last log</button>' +
            '<div class="spacer" style="flex:1"></div>' +
            '<span class="small dim nowrap">' + (last.started_ts ?
              (last.status === 'ok' ? 'last ok ' : 'last failed ') + fmtWhen(last.started_ts) : 'never run') +
            (j.next_run_ts ? ' · next ' + fmtWhen(j.next_run_ts) : ' · manual') + '</span></div></div>';
        });
      });
      box.innerHTML = html || '<div class="empty"><div class="big">No jobs registered</div></div>';
      wireAutomation();
    });
  }

  function wireAutomation() {
    $$('[data-run]').forEach(function (b) {
      b.onclick = function () {
        b.disabled = true; b.innerHTML = '<span class="spin"></span> running';
        call('/api/jobs/run', { key: b.dataset.run }).then(function (r) {
          if (r.ok) toast('Done: ' + JSON.stringify(r.summary || {}).slice(0, 160));
          else toast((r.error || r.status || 'Failed'), 'err');
          loadJobs(); refresh();
        });
      };
    });
    $$('.job-en').forEach(function (cb) {
      cb.onchange = function () {
        call('/api/jobs/update', { key: cb.dataset.key, enabled: cb.checked }).then(function (r) {
          if (!r.ok) toast(r.error, 'err');
          loadJobs();
        });
      };
    });
    $$('.job-sched').forEach(function (input) {
      input.onchange = function () {
        call('/api/jobs/update', { key: input.dataset.key, schedule: input.value }).then(function (r) {
          if (!r.ok) return toast(r.error, 'err');
          toast('Schedule saved'); loadJobs();
        });
      };
    });
    $$('[data-log]').forEach(function (b) {
      b.onclick = function () {
        call('/api/jobs/runs?limit=1&key=' + encodeURIComponent(b.dataset.log)).then(function (r) {
          var run = (r.runs || [])[0];
          openModal({
            wide: true, title: 'Last run: ' + b.dataset.log,
            body: run ? ('<div class="small dim" style="margin-bottom:8px">' + h(run.status) + ' · ' +
              fmtWhen(run.started_ts) + ' · ' + h(run.summary || '') + '</div>' +
              '<pre class="mono small" style="white-space:pre-wrap;max-height:50vh;overflow:auto">' +
              h(run.log || '(no log)') + '</pre>')
              : '<div class="small dim">This job has not run yet.</div>',
            footer: '<button class="btn" data-close>Close</button>'
          });
        });
      };
    });
  }

  // ---- signals
  // ---- daily: what the jobs found, aimed at what you sell
  function viewSignals(d) {
    var html = pageHead('Daily',
      'Everything the jobs found, scored against the offers you sell - then turned into a lead, ' +
      'an email or a campaign.',
      '<button class="btn" data-act="signals-seen">Mark all seen</button>' +
      '<button class="btn" data-act="run-due">Run due jobs</button>');
    html += '<div class="card tight" id="daily-target" style="margin-bottom:12px">' +
      '<div class="small dim">Loading...</div></div>';
    html += '<div class="card mt" id="hot-card"><div class="card-head"><h2>TODAY HOT LIST</h2>' +
      '<div class="spacer"></div><span class="small dim">heat · offer · first email question</span></div>' +
      '<div class="toolbar"><button class="btn primary" data-act="hot-build">Build today\'s hot list</button>' +
      '<span class="small dim" id="hot-note">Turns the findings below into the list worth a first email.</span></div>' +
      '<div id="hot-result" class="small dim mt"></div></div>';
    html += '<div class="card tight" id="signal-filter" style="margin-bottom:12px">' +
      '<div class="small dim">Loading...</div></div>';
    html += '<div class="card" id="signals-box" style="padding:0">' +
      '<div class="small dim" style="padding:16px">Loading findings...</div></div>';
    setTimeout(loadSignals, 20);
    return html;
  }

  function loadSignals() {
    var url = '/api/signals?limit=300' +
      (state.signalKind ? '&kind=' + encodeURIComponent(state.signalKind) : '') +
      (state.dailyOffer ? '&offer_id=' + state.dailyOffer : '') +
      (state.dailyOnlyMatch ? '&min_fit=1' : '');
    call(url).then(function (res) {
      var target = $('#daily-target'), filter = $('#signal-filter'), box = $('#signals-box');
      if (!target || !filter || !box) return;
      if (!res.ok) { box.innerHTML = '<div class="small dim" style="padding:16px">' + h(res.error) + '</div>'; return; }
      var offers = res.offers || [];
      target.innerHTML = '<div class="row" style="align-items:flex-end;gap:12px">' +
        '<div class="field" style="max-width:320px"><label>What you sell</label>' +
        '<select id="daily-offer"><option value="0">Score against every offer</option>' +
        offers.map(function (offer) {
          return '<option value="' + offer.id + '"' +
            (Number(state.dailyOffer) === offer.id ? ' selected' : '') + '>' + h(offer.name) + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field" style="max-width:230px"><label>&nbsp;</label>' +
        '<label class="check small"><input type="checkbox" id="daily-only"' +
        (state.dailyOnlyMatch ? ' checked' : '') + '> Only findings that match</label></div>' +
        '<div class="grow small dim">' + num(res.matching) + ' of ' + num(res.total) +
        ' findings fit ' + (offers.length ? 'your offers' : 'nothing yet') + '.' +
        (offers.length ? '' : ' Add keywords to an offer in Toolbox so findings can be scored.') + '</div></div>';
      var kinds = res.kinds || [];
      filter.innerHTML = '<div class="toolbar" style="margin:0"><span class="small dim">' +
        num(res.total) + ' findings · ' + num(res.unseen) + ' new</span>' +
        '<div class="spacer" style="flex:1"></div><span class="chip' +
        (state.signalKind ? '' : ' on') + '" data-kind="">All</span>' +
        kinds.map(function (k) {
          return '<span class="chip' + (state.signalKind === k.kind ? ' on' : '') +
            '" data-kind="' + h(k.kind) + '">' + h(k.kind) + ' ' + num(k.n) + '</span>';
        }).join('') + '</div>';
      if (!res.signals.length) {
        box.innerHTML = '<div class="empty" style="padding:36px"><div class="big">Nothing to work with</div>' +
          '<p>Open <strong>Automation</strong> and run a job, or clear the filters above. ' +
          'Each job writes what it finds here.</p></div>';
      } else {
        box.innerHTML = '<div class="list">' + res.signals.map(function (s) {
          var fit = s.fit ? '<span class="badge ' + (s.fit >= 50 ? 'green' : 'amber') + '">' +
            s.fit + '% fit</span>' : '<span class="badge grey">no match</span>';
          return '<div class="list-item"><div class="avatar">' +
            h((s.kind || '?').slice(0, 2).toUpperCase()) + '</div>' +
            '<div class="grow"><div>' + (s.seen ? '' : '<span class="badge blue">new</span> ') +
            '<span class="badge grey">' + h(s.kind) + '</span> ' + fit + ' <strong>' +
            h(s.company || s.domain || s.title || '') + '</strong></div>' +
            '<div class="t-sub">' + h([s.domain, s.title, s.offer].filter(Boolean).join(' · ')) +
            ' · ' + fmtWhen(s.ts) + '</div>' +
            (s.matched && s.matched.length ? '<div class="small dim">matches: ' +
              h(s.matched.join(', ')) + '</div>' : '') +
            (s.source_url ? '<div class="small"><a href="' + h(s.source_url) +
              '" target="_blank" rel="noopener">' + h(s.source || 'source') + '</a></div>' : '') +
            '</div><div class="nowrap">' +
            '<button class="btn sm" data-daily-email="' + s.id + '">Email</button> ' +
            '<button class="btn sm" data-daily-campaign="' + s.id + '">Campaign</button> ' +
            '<button class="btn sm ghost" data-promote="' + s.id + '">To lead</button>' +
            '</div></div>';
        }).join('') + '</div>';
        $$('[data-daily-email]').forEach(function (b) {
          b.onclick = function () { dailyEmail(Number(b.dataset.dailyEmail)); };
        });
        $$('[data-daily-campaign]').forEach(function (b) {
          b.onclick = function () { dailyCampaign(Number(b.dataset.dailyCampaign)); };
        });
        $$('[data-promote]').forEach(function (b) {
          b.onclick = function () { promoteSignal(Number(b.dataset.promote)); };
        });
      }
      $$('[data-kind]').forEach(function (chip) {
        chip.onclick = function () {
          state.signalKind = chip.dataset.kind || '';
          loadSignals();
        };
      });
      var offerSelect = $('#daily-offer');
      if (offerSelect) {
        offerSelect.onchange = function () {
          state.dailyOffer = Number(offerSelect.value) || 0;
          loadSignals();
        };
      }
      var only = $('#daily-only');
      if (only) {
        only.onchange = function () { state.dailyOnlyMatch = only.checked; loadSignals(); };
      }
    });
  }

  function dailyMailboxes() {
    var boxes = (state.data && state.data.mailboxes) || [];
    return boxes.filter(function (b) { return b.active !== 0 && b.smtp_host; });
  }

  function dailyEmail(id) {
    call('/api/signals/draft', { id: id, offer_id: state.dailyOffer || 0 }).then(function (r) {
      if (!r.ok) return toast(r.error || 'Could not write that email', 'err');
      var boxes = dailyMailboxes();
      openModal({
        title: 'Email from this finding',
        body: '<div class="row"><div class="field"><label>To</label>' +
          '<input type="email" id="de-to" value="' + h(r.to || '') + '" placeholder="info@company.com"></div>' +
          '<div class="field"><label>From mailbox</label><select id="de-box">' +
          (boxes.length ? boxes.map(function (b) {
            return '<option value="' + b.id + '">' + h(b.name) + ' — ' +
              b.sent_today + '/' + b.daily_limit + ' today</option>';
          }).join('') : '<option value="0">no mailbox yet</option>') + '</select></div></div>' +
          '<div class="field"><label>Subject</label>' +
          '<input type="text" id="de-subject" value="' + h(r.subject) + '"></div>' +
          '<div class="field"><label>Body</label>' +
          '<textarea id="de-body" rows="10">' + h(r.body) + '</textarea></div>' +
          '<div class="small dim">Written from <strong>' + h(r.offer) + '</strong>' +
          (r.used_ai ? ' with the AI provider' : ' using the offer pitch') +
          '. Merge fields such as {{first_name}} are filled from the lead when it sends.</div>',
        footer: '<button class="btn" data-close>Cancel</button>' +
          '<button class="btn" id="de-save">Save as lead only</button>' +
          '<button class="btn primary" id="de-send">Send now</button>',
        onMount: function (root) {
          $('#de-save', root).onclick = function () {
            call('/api/signals/promote', { id: id, email: $('#de-to', root).value }).then(function (res) {
              if (!res.ok) return toast(res.error, 'err');
              toast('Lead saved');
              closeModal(); refresh();
            });
          };
          $('#de-send', root).onclick = function () {
            call('/api/signals/contact', {
              id: id, email: $('#de-to', root).value,
              subject: $('#de-subject', root).value, body: $('#de-body', root).value,
              mailbox_id: Number($('#de-box', root).value || 0)
            }).then(function (res) {
              if (!res.ok) return toast(res.error || 'The email did not go out', 'err');
              toast('Sent from ' + res.mailbox + ' to ' + res.to);
              closeModal(); loadSignals(); refresh();
            });
          };
        }
      });
    });
  }

  function dailyCampaign(id) {
    var sequences = (state.data && state.data.sequences) || [];
    openModal({
      title: 'Put this finding in a campaign',
      body: (sequences.length
        ? '<div class="field"><label>Sequence</label><select id="dc-seq">' +
          sequences.map(function (s) {
            return '<option value="' + s.id + '">' + h(s.name) + ' · ' +
              num((s.steps || []).length) + ' steps</option>';
          }).join('') + '</select></div>' +
          '<div class="field"><label>Email address <span class="dim">(optional)</span></label>' +
          '<input type="email" id="dc-email" placeholder="info@company.com - empty uses info@domain">' +
          '<div class="hint">The finding becomes a lead, is attached as a note, and enters the ' +
          'sequence at step one.</div></div>'
        : '<div class="callout"><strong>No sequence yet.</strong> Create one in Sequences, then come back.</div>'),
      footer: '<button class="btn" data-close>Cancel</button>' +
        (sequences.length ? '<button class="btn primary" id="dc-go">Add to sequence</button>' : ''),
      onMount: function (root) {
        if (!sequences.length) return;
        $('#dc-go', root).onclick = function () {
          call('/api/signals/campaign', {
            id: id, sequence_id: Number($('#dc-seq', root).value),
            email: $('#dc-email', root).value, offer_id: state.dailyOffer || 0
          }).then(function (r) {
            if (!r.ok) return toast(r.error || 'Could not enrol that lead', 'err');
            toast('In "' + r.sequence + '" as ' + r.email);
            closeModal(); loadSignals(); refresh();
          });
        };
      }
    });
  }

  function promoteSignal(id) {
    openModal({
      title: 'Turn this finding into a lead',
      body: '<div class="field"><label>Email address</label>' +
        '<input type="email" id="pr-email" placeholder="info@company.com">' +
        '<div class="hint">The finding only knows the company or domain. Add the address you want to ' +
        'contact; the finding is attached to the lead as a note.</div></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="pr-go">Create lead</button>',
      onMount: function (root) {
        $('#pr-go', root).onclick = function () {
          call('/api/signals/promote', { id: id, email: $('#pr-email', root).value }).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast(r.existing ? 'Added to the existing lead' : 'Lead created');
            closeModal(); refresh();
          });
        };
      }
    });
  }

  // ---- ads: one page, one tab per platform
  var ADS_TABS = [
    { key: 'metricool', label: 'Metricool Ads' },
    { key: 'hyros', label: 'HYROS Ad Tracking' },
    { key: 'plai', label: 'PLAI' },
    { key: 'datafast', label: 'DataFast' }
  ];

  function viewAds(d) {
    var tab = state.adsTab || 'metricool';
    var html = pageHead('Ads',
      'The advertising stack in one place: Metricool for the ad accounts, then the tracking and ' +
      'management tools on their own sub-tab.');
    html += '<div class="card mb"><div class="card-head"><h2>Meta Ad Library - HYROS ICP</h2>' +
      '<div class="spacer"></div><span class="small dim">free, no key</span></div>' +
      '<div class="grid cols-3"><div class="field"><label>Keywords</label>' +
      '<input id="osint-meta-keywords" placeholder="shopify store, skincare, supplements"></div>' +
      '<div class="field"><label>Country code</label><input id="osint-meta-country" value="SK"></div>' +
      '<div class="field"><label>&nbsp;</label>' +
      '<button class="btn primary" data-act="osint-meta">Find advertisers</button></div></div>' +
      '<div id="osint-meta-result" class="small dim mt">Stores running Meta ads are the people ' +
      'who need attribution most - the Ad Library answers without a key.</div></div>';

    html += '<div class="toolbar" style="margin-bottom:12px">' + ADS_TABS.map(function (t) {
      return '<button class="btn sm ' + (tab === t.key ? 'primary' : 'ghost') +
        '" data-act="ads-tab" data-tab="' + t.key + '">' + h(t.label) + '</button>';
    }).join('') + '</div>';
    if (tab === 'metricool') {
      html += state.social
        ? '<div id="ads-metricool">' + svBodyAds() + '</div>'
        : '<div class="callout"><strong>Metricool loads with the Social tab.</strong> ' +
          '<button class="btn sm" data-act="ads-social">Load the connection here</button></div>';
    } else {
      var platform = (d.ads || []).filter(function (p) { return p.name === tab; })[0];
      html += platform ? adsPlatformCard(platform)
        : '<div class="small dim">That platform is not defined.</div>';
    }
    html += '<div id="ads-out" class="mt"></div>';
    return html;
  }

  function adsPlatformCard(p) {
    return '<div class="card"><div class="card-head"><h2>' + h(p.label) + '</h2>' +
      '<div class="spacer"></div>' +
      '<span class="badge ' + (p.has_key ? 'green' : 'grey') + '">' +
      (p.has_key ? 'key saved' : 'no key') + '</span>' +
      (p.last_test ? ' <span class="badge ' + (p.last_ok ? 'green' : 'red') + '">' +
        (p.last_ok ? 'last test ok' : 'last test failed') + '</span>' : '') +
      '</div><div class="small muted">' + h(p.note) + ' <a href="' + h(p.docs) +
      '" target="_blank" rel="noopener">Docs</a></div>' +
      '<div class="row"><div class="field"><label>API key</label>' +
      '<input type="password" id="ads-key" placeholder="' +
      (p.has_key ? 'a key is saved - paste a new one to replace it' : 'paste the key') + '"></div>' +
      '<div class="field" style="max-width:180px"><label>Auth header</label>' +
      '<input type="text" value="' + h(p.auth) + '" disabled></div></div>' +
      '<div class="row"><div class="field"><label>Base URL</label>' +
      '<input type="text" id="ads-base" value="' + h(p.base_url) + '" placeholder="https://..."></div>' +
      '<div class="field"><label>Path <span class="dim">(optional)</span></label>' +
      '<input type="text" id="ads-path" value="' + h(p.path) + '" placeholder="/leads"></div></div>' +
      '<button class="btn primary" data-act="ads-save" data-name="' + p.name + '">Save</button> ' +
      '<button class="btn" data-act="ads-test" data-name="' + p.name + '">Test</button> ' +
      '<button class="btn" data-act="ads-pull" data-name="' + p.name + '">Pull</button>' +
      '<div class="hint small dim">Nothing is guessed: the call goes to the base URL and path you ' +
      'give, with ' + h(p.auth) + ' auth, and the raw JSON comes back below.</div>' +
      (p.name === 'hyros'
        ? '<div class="sep"></div><div class="toolbar">' +
          '<button class="btn sm" data-act="hyros-accounts">Ad accounts</button>' +
          '<button class="btn sm" data-act="hyros-attribution">Attribution · 30 days</button>' +
          '<button class="btn sm ghost" data-act="hyros-mcp">Add the Hyros MCP server</button></div>' +
          '<div class="hint small dim">The first two use the published paths ' +
          '(<span class="mono">GET /api/v1.0/ad-accounts</span>, ' +
          '<span class="mono">GET /api/v1.0/attribution</span>) from ' +
          '<a href="https://api-docs.hyros.com/" target="_blank" rel="noopener">api-docs.hyros.com</a>. ' +
          'The MCP server at <span class="mono">mcp.hyros.com/mcp</span> is registered with the same ' +
          'key as a token connection.</div>'
        : '') + '</div>';
  }

  function adsRowsHtml(title, rows) {
    if (!rows || !rows.length) return '';
    var keys = Object.keys(rows[0]).slice(0, 7);
    return '<div class="card" style="background:var(--panel-2)"><div class="card-head"><h3>' +
      h(title) + '</h3><div class="spacer"></div><span class="badge grey">' + num(rows.length) +
      '</span></div><div class="table-wrap"><table><thead><tr>' +
      keys.map(function (k) { return '<th>' + h(k) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.slice(0, 100).map(function (row) {
        return '<tr>' + keys.map(function (k) {
          var value = row[k];
          return '<td class="small">' + h(typeof value === 'object' && value !== null
            ? JSON.stringify(value).slice(0, 80) : value) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div></div>';
  }

  function wireAds() { }

  // ---- HYROS AIR: a paid Hyros service, this page is its client
  function loadHyros() {
    return call('/api/hyros', {}).then(function (r) {
      if (!r.ok) return;
      state.hyros = r;
      if (state.view === 'agent') render();
    });
  }

  function viewAgent(d) {
    var info = state.hyros;
    var html = pageHead('HYROS AIR Sales Agent',
      'HYROS AIR is a paid Hyros service - subscribe to it and manage it at Hyros. This page is ' +
      'only the client: it reads what the agent did in your account with your HYROS API key.',
      '<a class="btn" href="' + h((info && info.links && info.links.product) ||
        'https://hyros.com/enterprise') + '" target="_blank"' +
      ((info && info.links && info.links.affiliate) ? ' rel="noopener sponsored"' : ' rel="noopener"') +
      '>Hyros Enterprise</a>' +
      '<a class="btn" href="' + h((info && info.links && info.links.api) ||
        'https://api-docs.hyros.com/') + '" target="_blank" rel="noopener">API docs</a>');
    if (!info) {
      setTimeout(loadHyros, 20);
      return html + '<div class="card"><div class="small dim">Loading the HYROS connection...</div></div>';
    }
    html += '<div class="callout"><strong>What this is.</strong> HYROS AIR is an AI sales agent sold ' +
      'by Hyros, not something this app runs. Everything it produces lives in your Hyros account, ' +
      'and the HYROS API exposes it with the same key the Ad Tracking tab stores. Nothing on this ' +
      'page is generated locally.' +
      ((info.links && info.links.affiliate)
        ? ' The Hyros Enterprise button opens the link from your Toolbox, which is an affiliate ' +
          'link: we may earn a commission at no extra cost to you.' : '') + '</div>';
    html += '<div class="card"><div class="card-head"><h2>Connection</h2><div class="spacer"></div>' +
      '<span class="badge ' + (info.has_key ? 'green' : 'amber') + '">' +
      (info.has_key ? 'HYROS API key saved' : 'no HYROS API key') + '</span></div>' +
      '<div class="small muted">' + h(info.note) + '</div>' +
      '<div class="small dim mono mt">' + h(info.base_url) + '</div>' +
      '<div class="small dim">Hyros also runs an official MCP server at ' +
      '<span class="mono">mcp.hyros.com/mcp</span> - add it from Ads → HYROS Ad Tracking.</div>' +
      (info.has_key
        ? '<div class="toolbar mt">' +
          ['leads', 'sales', 'calls'].map(function (kind) {
            return '<button class="btn sm" data-act="hyros-pull" data-kind="' + kind + '">Pull ' +
              h(kind) + (info.last_count ? '' : '') + '</button>';
          }).join(' ') + '</div>' +
          (info.last_pull ? '<div class="small dim">Last pull: ' + num(info.last_count) +
            ' rows · ' + fmtWhen(info.last_pull) + '</div>' : '')
        : '<div class="mt"><button class="btn primary" data-act="hyros-key">Add the HYROS API key</button>' +
          '<div class="hint small dim">It is the same key as Ads → HYROS Ad Tracking.</div></div>') +
      '</div>';
    html += '<div id="hyros-out" class="mt"></div>';
    html += '<details class="card mt"><summary class="small dim">Endpoint paths (from the Hyros ' +
      'API, editable if they change)</summary><div class="row mt">' +
      Object.keys(info.paths || {}).map(function (kind) {
        return '<div class="field"><label>' + h(kind) + '</label>' +
          '<input type="text" id="hyros-path-' + h(kind) + '" value="' + h(info.paths[kind]) + '"></div>';
      }).join('') + '</div><button class="btn sm" data-act="hyros-paths">Save paths</button>' +
      '</details>';
    return html;
  }

  function wireAgent() { }

  // ---- offers (the money layer)
  function viewOffers(d) {
    var auto = d.automation || {};
    var locked = (d.settings || {}).offers_locked !== false;
    var html = pageHead('Toolbox', 'The tools this app is built around - with the links we use.',
      (locked ? '' : '<button class="btn" data-act="offer-import">Import JSON</button>' +
        '<button class="btn" data-act="offer-pack">Load starter pack</button>' +
        '<button class="btn primary" data-act="new-offer">New offer</button>'));
    html += '<div class="callout"><strong>' + num(auto.offers_active || 0) +
      ' recommended tools</strong> · pick the ones you need, sign up directly with the provider. ' +
      'Some links are affiliate links: we may earn a commission at no extra cost to you. ' +
      'Lead Studio works without any of them.</div>';
    html += '<div id="offer-tabs" class="toolbar" style="margin-bottom:10px"></div>';
    html += '<div id="offers-box"><div class="small dim">Loading offers...</div></div>';
    setTimeout(loadOffers, 20);
    return html;
  }

  function loadOffers() {
    call('/api/offers').then(function (res) {
      var box = $('#offers-box');
      if (!box) return;
      if (!res.ok) { box.innerHTML = '<div class="small dim">' + h(res.error) + '</div>'; return; }
      state.offers = res.offers;
      if (!res.offers.length) {
        box.innerHTML = '<div class="card"><div class="empty"><div class="big">No offers yet</div>' +
          '<p>Add the tools you resell - automated accounting, ads tracking, social media, ' +
          'referrals - with your affiliate link and the keywords that identify a good fit. ' +
          'The app then matches leads to them and drafts the email.</p>' +
          '<button class="btn primary" data-act="new-offer">Add the first offer</button></div></div>';
        return;
      }
      var locked = (state.data && state.data.settings && state.data.settings.offers_locked !== false);
      box.innerHTML = '<div class="grid cols-2">' + res.offers.map(function (o) {
        return '<div class="card" data-cat="' + h(o.category || 'other') + '"><div class="card-head"><div><h2>' + h(o.name) + '</h2>' +
          '<div class="card-sub">' + h(o.category || 'other') +
          (o.connections ? ' · ' + num(o.connections) + ' connection(s)' : '') + '</div></div>' +
          '<div class="spacer"></div><span class="badge ' + (o.active ? 'green' : 'grey') + '">' +
          (o.active ? 'active' : 'off') + '</span></div>' +
          '<div class="small muted" style="min-height:34px">' + h((o.description || '').slice(0, 140)) + '</div>' +
          '<div style="margin:10px 0 12px">' + (o.affiliate_url ?
            '<a class="btn primary big" href="' + h(o.affiliate_url) + '" target="_blank" rel="noopener sponsored">Visit ' +
            h(((o.affiliate_url.replace(/^https?:\/\//, '').split('/')[0]) || 'site').replace(/^www\./, '')) +
            ' &rarr;</a>' :
            '<span class="btn big" style="opacity:.45;cursor:default">No link yet</span>') + '</div>' +
          '<div class="merge-chips">' + (o.keyword_list || []).slice(0, 6).map(function (k) {
            return '<span class="chip">' + h(k) + '</span>';
          }).join('') + '</div>' +
          '<div class="toolbar mt" style="margin-bottom:0">' +
          '<button class="btn sm" data-open-offer="' + o.id + '">Open</button>' +
          '<button class="btn sm ghost" data-conns="' + o.id + '">Connect</button>' +
          '<a class="btn sm ghost" href="/offer/' + o.id + '" target="_blank" rel="noopener">Details</a>' +
          (locked ? '' :
            '<button class="btn sm ghost" data-edit-offer="' + o.id + '">Edit</button>' +
            '<button class="btn sm danger" data-del-offer="' + o.id + '">Delete</button>') +
          '</div></div>';
      }).join('') + '</div>';

      drawOfferTabs();
      $$('[data-edit-offer]').forEach(function (b) {
        b.onclick = function () { offerEditor(Number(b.dataset.editOffer)); };
      });
      $$('[data-del-offer]').forEach(function (b) {
        b.onclick = function () {
          if (!confirm('Delete this offer and its connections?')) return;
          call('/api/offer/delete', { id: Number(b.dataset.delOffer) }).then(function (r) {
            if (r.ok) { toast('Offer deleted'); loadOffers(); refresh(); } else toast(r.error, 'err');
          });
        };
      });
      $$('[data-open-offer]').forEach(function (b) {
        b.onclick = function () { offerWorkspace(Number(b.dataset.openOffer)); };
      });
      $$('[data-conns]').forEach(function (b) {
        b.onclick = function () { offerConnections(Number(b.dataset.conns)); };
      });
    });
  }

  function offerEditor(id) {
    var o = (state.offers || []).filter(function (x) { return x.id === id; })[0] || {};
    openModal({
      wide: true,
      title: id ? 'Edit offer' : 'New offer',
      subtitle: 'The pitch can use {{first_name}}, {{company}}, {{offer_link}}, {{description}}.',
      body: '<div class="row"><div class="field"><label>Offer name</label>' +
        '<input type="text" id="of-name" value="' + h(o.name || '') + '" placeholder="Automated accounting"></div>' +
        '<div class="field"><label>Category</label>' +
        '<input type="text" id="of-cat" value="' + h(o.category || '') + '" placeholder="accounting"></div></div>' +
        '<div class="row"><div class="field"><label>Affiliate link</label>' +
        '<input type="text" id="of-link" value="' + h(o.affiliate_url || '') + '" placeholder="https://partner.example.com/?ref=you"></div>' +
        '<div class="field"><label>Commission</label>' +
        '<input type="text" id="of-commission" value="' + h(o.commission || '') + '" placeholder="20% recurring"></div></div>' +
        '<div class="field"><label>Who it fits (comma separated keywords)</label>' +
        '<input type="text" id="of-keywords" value="' + h(o.keywords || '') + '" placeholder="accounting, bookkeeping, payroll, finance">' +
        '<div class="hint">Matched against each lead\'s industry, tags, company, title and notes.</div></div>' +
        '<div class="field"><label>Description shown to the lead</label>' +
        '<textarea id="of-desc" rows="2" placeholder="Automated bookkeeping and monthly reporting for small firms.">' + h(o.description || '') + '</textarea></div>' +
        '<div class="row"><div class="field"><label>Pitch subject</label>' +
        '<input type="text" id="of-subject" value="' + h(o.pitch_subject || '') + '" placeholder="{{first_name|Hi}} - a thought for {{company}}"></div></div>' +
        '<div class="field"><label>Pitch body</label>' +
        '<textarea id="of-body" rows="7" placeholder="Hi {{first_name|there}}, ...">' + h(o.pitch_body || '') + '</textarea>' +
        '<div class="hint">Leave empty to use the default template. The link is appended if you forget it, ' +
        'and the affiliate disclosure is always appended below it.</div></div>' +
        '<div class="field"><label>Affiliate disclosure (shown at the bottom of the offer page and every pitch)</label>' +
        '<textarea id="of-disclosure" rows="4" placeholder="Leave empty for the standard FTC/EU wording">' +
        h(o.disclosure || '') + '</textarea>' +
        '<div class="hint">Use <span class="mono">{company}</span> for your company and ' +
        '<span class="mono">{partner}</span> for the offer name. The default states that the commission is ' +
        'at no additional cost to the customer.</div></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="of-save">Save offer</button>',
      onMount: function (root) {
        $('#of-save', root).onclick = function () {
          var body = {
            id: id || null,
            name: $('#of-name', root).value, category: $('#of-cat', root).value,
            affiliate_url: $('#of-link', root).value, commission: $('#of-commission', root).value,
            keywords: $('#of-keywords', root).value, description: $('#of-desc', root).value,
            pitch_subject: $('#of-subject', root).value, pitch_body: $('#of-body', root).value,
            disclosure: $('#of-disclosure', root).value
          };
          call('/api/offer/save', body).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Offer saved'); closeModal(); loadOffers(); refresh();
          });
        };
      }
    });
  }

  function offerMatches(id) {
    call('/api/offer/matches?limit=40&offer_id=' + id).then(function (res) {
      if (!res.ok) return toast(res.error, 'err');
      var rows = res.matches || [];
      var body = '<div class="small dim" style="margin-bottom:10px">' + rows.length +
        ' lead(s) match <strong>' + h(res.offer.name) + '</strong>, best first.</div>';
      if (!rows.length) {
        body += '<div class="empty" style="padding:24px"><div class="big">No matches yet</div>' +
          '<p class="small">Add keywords that describe who needs this offer, or import more leads ' +
          'with an industry column.</p></div>';
      } else {
        body += '<div class="list">' + rows.map(function (m) {
          return '<div class="list-item"><div class="avatar">' + h((m.lead.email || '?').slice(0, 2).toUpperCase()) +
            '</div><div class="grow"><div><strong>' + h(m.lead.company || m.lead.email) + '</strong>' +
            ' <span class="badge blue">score ' + m.score + '</span></div>' +
            '<div class="t-sub">' + h(m.lead.email) + ' · ' + h([m.lead.industry, m.lead.city].filter(Boolean).join(' · ')) +
            '</div></div><div class="nowrap"><button class="btn sm" data-pitch="' + m.lead.id + '">Pitch</button></div></div>';
        }).join('') + '</div>';
      }
      openModal({
        wide: true, title: 'Matched leads: ' + res.offer.name, body: body,
        footer: '<button class="btn" data-close>Close</button>',
        onMount: function (root) {
          $$('[data-pitch]', root).forEach(function (b) {
            b.onclick = function () { pitchModal(res.offer.id, Number(b.dataset.pitch)); };
          });
        }
      });
    });
  }

  function pitchModal(offerId, leadId) {
    function render(useAi) {
      call('/api/offer/pitch', { offer_id: offerId, lead_id: leadId, ai: useAi }).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        var body = (r.ai_error ? '<div class="callout"><strong>AI unavailable.</strong> ' +
          h(r.ai_error) + ' Using the template instead.</div>' : '') +
          '<div class="field"><label>Subject</label><input type="text" id="pitch-subject" value="' + h(r.subject) + '"></div>' +
          '<div class="field"><label>Body</label><textarea id="pitch-body" rows="11">' + h(r.body) + '</textarea></div>' +
          (r.link ? '<div class="small dim">Affiliate link: ' + h(r.link) + '</div>' : '');
        openModal({
          wide: true,
          title: 'Pitch: ' + r.offer,
          subtitle: r.used_ai ? 'Drafted by your default LLM provider - edit before sending.' :
            'From your template - edit before sending.',
          body: body,
          footer: '<button class="btn" data-close>Close</button>' +
            (r.used_ai ? '' : '<button class="btn" id="pitch-ai">Draft with AI</button>') +
            '<button class="btn primary" id="pitch-copy">Copy</button>',
          onMount: function (root) {
            var aiBtn = $('#pitch-ai', root);
            if (aiBtn) aiBtn.onclick = function () { render(true); };
            $('#pitch-copy', root).onclick = function () {
              var text = $('#pitch-subject', root).value + '\n\n' + $('#pitch-body', root).value;
              if (navigator.clipboard) navigator.clipboard.writeText(text);
              toast('Copied to clipboard');
            };
          }
        });
      });
    }
    render(false);
  }

  function offerConnections(id) {
    call('/api/offer/connections?offer_id=' + id).then(function (res) {
      if (!res.ok) return toast(res.error, 'err');
      var rows = res.connections || [];
      var body = '<div class="small dim" style="margin-bottom:10px">How a customer gets connected to ' +
        '<strong>' + h(res.offer.name) + '</strong> once they say yes.</div>';
      body += rows.length ? '<div class="list">' + rows.map(function (c) {
        var conf = c.config_masked || {};
        var detail = Object.keys(conf).map(function (k) { return k + ': ' + conf[k]; }).join(' · ');
        return '<div class="list-item"><div class="avatar">' + h(c.kind.slice(0, 2).toUpperCase()) + '</div>' +
          '<div class="grow"><div><strong>' + h(c.label) + '</strong> <span class="badge grey">' +
          h(c.kind) + '</span></div><div class="t-sub">' + h(detail) + '</div></div>' +
          '<div class="nowrap"><button class="btn sm danger" data-del-conn="' + c.id + '">Remove</button></div></div>';
      }).join('') + '</div>' : '<div class="small dim">No connections yet.</div>';
      body += '<div class="sep"></div><div class="row"><div class="field" style="max-width:150px">' +
        '<label>Type</label><select id="conn-kind"><option value="link">link</option>' +
        '<option value="api_key">api_key</option><option value="mcp">mcp</option>' +
        '<option value="webhook">webhook</option></select></div>' +
        '<div class="field"><label>Label</label><input type="text" id="conn-label" placeholder="Customer portal"></div></div>' +
        '<div class="field"><label>Value (URL, key or MCP command)</label>' +
        '<input type="text" id="conn-value" placeholder="https://app.example.com/connect"></div>' +
        '<button class="btn sm" id="conn-add">Add connection</button>';
      openModal({
        wide: true, title: 'Connections: ' + res.offer.name, body: body,
        footer: '<button class="btn" data-close>Close</button>',
        onMount: function (root) {
          $$('[data-del-conn]', root).forEach(function (b) {
            b.onclick = function () {
              call('/api/offer/connection/delete', { id: Number(b.dataset.delConn) }).then(function (r) {
                if (r.ok) { toast('Removed'); offerConnections(id); } else toast(r.error, 'err');
              });
            };
          });
          $('#conn-add', root).onclick = function () {
            var payload = { offer_id: id, kind: $('#conn-kind', root).value,
              label: $('#conn-label', root).value, value: $('#conn-value', root).value };
            call('/api/offer/connection/add', payload).then(function (r) {
              if (!r.ok) return toast(r.error, 'err');
              toast('Connection saved'); offerConnections(id); loadOffers();
            });
          };
        }
      });
    });
  }

  function llmCard(d) {
    var s = d.llm || {};
    var providers = state.llmProviders || [];
    return '<div class="card mt" id="llm-card"><div class="card-head"><h2>LLM providers</h2>' +
      '<div class="spacer"></div>' + (s.configured
        ? '<span class="badge green">' + h(s.default) + ' · ' + h(s.model) + '</span>'
        : '<span class="badge amber">none configured</span>') + '</div>' +
      '<div class="small muted" style="margin-bottom:10px">Bring your own model: OpenAI, Anthropic ' +
      '(Claude), DeepSeek, Ollama (local; the cloud is pay-as-you-go since Nov 2026) or any ' +
      'OpenAI-compatible endpoint. The default one ' +
      'powers AI enrichment and pitch drafting. Providers that share a base URL act as one pool: a ' +
      'call rotates through the keys and moves on when one is out of quota.</div>' +
      (s.pooled ? '<div class="small dim" style="margin-bottom:10px">Pool: ' + s.pooled +
        ' keys on ' + h(s.pool_base || '') + '</div>' : '') +
      (providers.length ? '<div class="list">' + providers.map(function (p) {
        return '<div class="list-item"><div class="avatar">' + h(p.name.slice(0, 2).toUpperCase()) + '</div>' +
          '<div class="grow"><div><strong>' + h(p.name) + '</strong> ' +
          (p.is_default ? '<span class="badge green">default</span>' : '') +
          (p.has_key ? ' <span class="badge grey">key saved</span>' : ' <span class="badge amber">no key</span>') +
          '</div><div class="t-sub">' + h(p.model) + ' · ' + h(p.base_url) + '</div></div>' +
          '<div class="nowrap">' +
          (p.is_default ? '' : '<button class="btn sm ghost" data-llm-default="' + p.id + '">Make default</button> ') +
          '<button class="btn sm" data-llm-test="' + p.id + '">Test</button> ' +
          '<button class="btn sm danger" data-llm-del="' + p.id + '">Remove</button></div></div>';
      }).join('') + '</div>' : '<div class="small dim">No provider added yet.</div>') +
      '<div class="sep"></div><div class="row">' +
      '<div class="field" style="max-width:190px"><label>Preset</label><select id="llm-preset">' +
      (state.llmPresets || []).map(function (p) {
        return '<option value="' + h(p.id) + '">' + h(p.label) + '</option>';
      }).join('') + '</select></div>' +
      '<div class="field"><label>Name</label><input type="text" id="llm-name" placeholder="My OpenAI"></div>' +
      '<div class="field"><label>Model</label><input type="text" id="llm-model" placeholder="gpt-4o-mini"></div></div>' +
      '<div class="row"><div class="field"><label>Base URL</label>' +
      '<input type="text" id="llm-base" placeholder="https://api.openai.com/v1"></div>' +
      '<div class="field"><label>API key</label><input type="password" id="llm-key" placeholder="sk-..."></div></div>' +
      '<button class="btn primary" data-act="llm-add">Add provider</button></div>';
  }

  function wireLlm() {
    var preset = $('#llm-preset');
    if (preset) {
      preset.onchange = function () {
        var p = (state.llmPresets || []).filter(function (x) { return x.id === preset.value; })[0];
        if (!p) return;
        $('#llm-base').value = p.base_url || '';
        $('#llm-model').value = p.model || '';
        $('#llm-name').value = p.label || '';
      };
    }
    $$('[data-llm-default]').forEach(function (b) {
      b.onclick = function () {
        call('/api/llm/default', { id: Number(b.dataset.llmDefault) }).then(function (r) {
          if (!r.ok) return toast(r.error, 'err');
          toast('Default provider updated'); loadLlm(); refresh();
        });
      };
    });
    $$('[data-llm-test]').forEach(function (b) {
      b.onclick = function () {
        b.disabled = true; b.textContent = 'Testing...';
        call('/api/llm/test', { id: Number(b.dataset.llmTest) }).then(function (r) {
          b.disabled = false; b.textContent = 'Test';
          if (r.ok) toast(r.provider + ' answered: "' + r.reply + '"');
          else toast(r.error || 'Test failed', 'err');
        });
      };
    });
    $$('[data-llm-del]').forEach(function (b) {
      b.onclick = function () {
        if (!confirm('Remove this provider?')) return;
        call('/api/llm/delete', { id: Number(b.dataset.llmDel) }).then(function (r) {
          if (r.ok) { toast('Removed'); loadLlm(); refresh(); } else toast(r.error, 'err');
        });
      };
    });
  }

  // ---- MCP connections (another server the app calls, not the app as a server)
  function mcpCard(d) {
    var list = d.mcp || [];
    var mr = d.manyreach || { counts: {} };
    return '<div class="card mt" id="mcp-card"><div class="card-head"><h2>MCP connections</h2>' +
      '<div class="spacer"></div><span class="small dim">remote MCP servers this app calls</span></div>' +
      '<div class="small muted" style="margin-bottom:10px">Manyreach runs over its own MCP server ' +
      '(Streamable HTTP + OAuth). Connecting opens the browser once; the tokens stay in ' +
      '<span class="mono">data/settings.json</span> on this computer and never in the repository.</div>' +
      (list.length ? '<div class="list">' + list.map(function (c) {
        return '<div class="list-item"><div class="avatar">' +
          h((c.label || c.name).slice(0, 2).toUpperCase()) + '</div>' +
          '<div class="grow"><div><strong>' + h(c.label) + '</strong> ' +
          (c.connected ? '<span class="badge green">connected</span>'
            : '<span class="badge grey">not connected</span>') +
          (c.expired ? ' <span class="badge amber">expired</span>' : '') + '</div>' +
          '<div class="t-sub">' + h(c.note) + '</div>' +
          '<div class="t-sub mono">' + h(c.url) + '</div></div>' +
          '<div class="nowrap">' +
          (c.connected
            ? '<button class="btn sm" data-mcp-pull="' + h(c.name) + '">Download</button> ' +
              '<button class="btn sm" data-mcp-tools="' + h(c.name) + '">Tools</button> ' +
              '<button class="btn sm danger" data-mcp-off="' + h(c.name) + '">Disconnect</button>'
            : '<button class="btn sm primary" data-mcp-on="' + h(c.name) + '">Connect</button>') +
          '</div></div>';
      }).join('') + '</div>' : '<div class="small dim">No MCP connection defined.</div>') +
      '<div class="sep"></div>' +
      '<div class="small muted">Or skip OAuth: paste the Manyreach REST API key ' +
      '(<span class="mono">X-API-Key</span>). It paginates 1000 rows a page and fills the same lists.</div>' +
      '<div class="row"><div class="field"><label>Manyreach API key</label>' +
      '<input type="password" id="mr-key" placeholder="' +
      (mr.has_key ? 'a key is saved - paste a new one to replace it' : 'paste the API key') +
      '"></div><div class="field"><label>&nbsp;</label>' +
      '<button class="btn" data-act="mr-save">Save key</button> ' +
      '<button class="btn primary" data-act="mr-sync"' + (mr.has_key ? '' : ' disabled') +
      '>Sync campaigns &amp; senders</button></div></div>' +
      '<div class="small dim">' + (mr.has_key
        ? (num((mr.counts || {}).campaigns || 0) + ' campaigns and ' +
           num((mr.counts || {}).senders || 0) + ' senders stored' +
           (mr.last_sync ? ' · last sync ' + fmtWhen(mr.last_sync) : ''))
        : 'No API key yet.') + '</div>' +
      '<div class="small dim" style="margin-top:6px">' +
      ((mr.in_app && (mr.in_app.mailboxes || mr.in_app.sequences))
        ? 'In the app: <strong>' + num(mr.in_app.mailboxes) + ' mailboxes</strong> and <strong>' +
          num(mr.in_app.sequences) + ' sequences</strong> came from Manyreach. '
        : '') +
      (((mr.counts || {}).senders || (mr.counts || {}).campaigns)
        ? '<button class="btn sm" data-act="mr-import">Put senders in Mailboxes, campaigns in Sequences</button>'
        : '') + '</div>' +
      '<div class="sep"></div><div class="row">' +
      '<div class="field"><label>Add another MCP server</label>' +
      '<input type="text" id="mcp-name" placeholder="Instantly"></div>' +
      '<div class="field"><label>MCP URL</label>' +
      '<input type="text" id="mcp-url" placeholder="https://mcp.instantly.ai/mcp/your-key"></div>' +
      '<div class="field"><label>Token <span class="dim">(optional)</span></label>' +
      '<input type="password" id="mcp-token" placeholder="for servers without OAuth"></div></div>' +
      '<button class="btn" data-act="mcp-add">Add server</button>' +
      '<div class="hint small dim mt">Known remote servers: Instantly ' +
      '(<span class="mono">https://mcp.instantly.ai/mcp/&lt;key&gt;</span>), lemlist and Apify ' +
      '(both OAuth). Smartlead publishes SSE only, which this client does not speak.</div>' +
      '<div id="mcp-out" class="mt"></div></div>';
  }

  function renderMcpItems(name, items) {
    var box = $('#mcp-out');
    if (!box) return;
    function block(title, rows, withStatus) {
      return '<div class="card" style="background:var(--panel-2)">' +
        '<div class="card-head"><h3>' + h(title) + '</h3><div class="spacer"></div>' +
        '<span class="badge grey">' + rows.length + '</span></div>' +
        (rows.length
          ? '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Status</th></tr></thead>' +
            '<tbody>' + rows.map(function (row) {
              return '<tr><td class="small">' + h(row.name) + '</td>' +
                '<td><span class="badge ' + (row.status === 'warmup' ? 'amber' : 'grey') + '">' +
                h(row.status || '-') + '</span></td></tr>';
            }).join('') + '</tbody></table></div>'
          : '<div class="small dim">nothing stored yet</div>') + '</div>';
    }
    var campaigns = (items || []).filter(function (item) { return item.kind === 'campaigns'; });
    var senders = (items || []).filter(function (item) { return item.kind === 'senders'; });
    box.innerHTML = '<div class="small dim" style="margin-bottom:8px">Stored from ' + h(name) +
      ', refreshed on demand.</div><div class="grid cols-2">' +
      block('Campaigns', campaigns) + block('Senders', senders) + '</div>';
  }

  function renderMcpTools(name, tools) {
    var box = $('#mcp-out');
    if (!box) return;
    if (!tools || !tools.length) {
      box.innerHTML = '<div class="small dim">That server exposes no tools.</div>';
      return;
    }
    box.innerHTML = '<div class="card" style="background:var(--panel-2)">' +
      '<div class="card-head"><h3>' + h(name) + ' tools</h3><div class="spacer"></div>' +
      '<span class="badge grey">' + tools.length + '</span></div>' +
      '<div class="field"><label>Tool</label><select id="mcp-tool">' + tools.map(function (tool) {
        return '<option value="' + h(tool.name) + '">' + h(tool.name) + '</option>';
      }).join('') + '</select></div>' +
      '<div class="hint small dim" id="mcp-tool-desc">' +
      h(tools[0].description || '') + '</div>' +
      '<div class="field"><label>Arguments <span class="dim">(JSON)</span></label>' +
      '<textarea id="mcp-args" rows="3"></textarea></div>' +
      '<button class="btn primary" id="mcp-run">Run</button>' +
      '<div id="mcp-answer" class="mt"></div></div>';

    var select = $('#mcp-tool');
    var args = $('#mcp-args');
    var describe = function () {
      var tool = tools.filter(function (x) { return x.name === select.value; })[0] || tools[0];
      $('#mcp-tool-desc').textContent = tool.description || '';
      var skeleton = {};
      Object.keys((tool.schema && tool.schema.properties) || {}).forEach(function (key) {
        var type = (tool.schema.properties[key] || {}).type;
        skeleton[key] = type === 'number' || type === 'integer' ? 0
          : (type === 'array' ? [] : '');
      });
      args.value = JSON.stringify(skeleton, null, 1);
    };
    select.onchange = describe;
    describe();

    $('#mcp-run').onclick = function () {
      var answer = $('#mcp-answer');
      answer.innerHTML = '<div class="small dim">Calling...</div>';
      call('/api/mcp/call', { name: name, tool: select.value, arguments: args.value })
        .then(function (r) {
          if (!r.ok) {
            answer.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
              h(r.error || 'The tool failed') + '</div>';
            return;
          }
          answer.innerHTML = '<pre class="log">' +
            h(r.text || JSON.stringify(r.result, null, 1)) + '</pre>';
        });
    };
  }

  function wireMcp() {
    $$('[data-mcp-on]').forEach(function (button) {
      button.onclick = function () {
        var name = button.dataset.mcpOn;
        button.disabled = true;
        call('/api/mcp/start', { name: name }).then(function (r) {
          button.disabled = false;
          if (!r.ok) return toast(r.error || 'Could not start the sign-in', 'err');
          window.open(r.url, '_blank');
          toast('Approve the sign-in in the browser tab');
          var tries = 0;
          var timer = setInterval(function () {
            tries += 1;
            if (tries > 24) { clearInterval(timer); return; }
            call('/api/mcp/status', {}).then(function (state_res) {
              var row = (state_res.connections || []).filter(function (c) {
                return c.name === name;
              })[0];
              if (state_res.ok && row && row.connected) {
                clearInterval(timer);
                toast('Connected', 'ok');
                refresh();
              }
            });
          }, 5000);
        });
      };
    });
    $$('[data-mcp-pull]').forEach(function (button) {
      button.onclick = function () {
        var name = button.dataset.mcpPull;
        var box = $('#mcp-out');
        button.disabled = true;
        if (box) box.innerHTML = '<div class="small dim">Downloading campaigns and senders...</div>';
        call('/api/mcp/pull', { name: name }).then(function (r) {
          button.disabled = false;
          if (!r.ok) {
            var reason = (r.results || []).filter(function (row) { return !row.ok; })
              .map(function (row) { return row.error; }).join(' ');
            if (box) box.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
              h(r.error || reason || 'The download failed') + '</div>';
            return;
          }
          renderMcpItems(name, r.items || []);
          toast((r.items || []).length + ' records stored');
        });
      };
    });
    $$('[data-mcp-off]').forEach(function (button) {
      button.onclick = function () {
        call('/api/mcp/disconnect', { name: button.dataset.mcpOff }).then(function (r) {
          if (!r.ok) return toast(r.error, 'err');
          toast('Disconnected');
          refresh();
        });
      };
    });
    $$('[data-mcp-tools]').forEach(function (button) {
      button.onclick = function () {
        var name = button.dataset.mcpTools;
        var box = $('#mcp-out');
        if (box) box.innerHTML = '<div class="small dim">Asking ' + h(name) + '...</div>';
        call('/api/mcp/tools', { name: name }).then(function (r) {
          if (!r.ok) {
            if (box) box.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
              h(r.error) + '</div>';
            return;
          }
          renderMcpTools(name, r.tools || []);
        });
      };
    });
  }

  function loadLlm() {
    return call('/api/llm/providers').then(function (res) {
      if (!res.ok) return;
      state.llmProviders = res.providers || [];
      state.llmPresets = res.presets || [];
      if (state.view === 'settings') render();
    });
  }

  // ---- offers: category tabs + per-offer workspace
  function drawOfferTabs() {
    var box = $('#offer-tabs');
    if (!box) return;
    var offers = state.offers || [];
    var cats = [];
    offers.forEach(function (o) {
      var c = o.category || 'other';
      if (cats.indexOf(c) < 0) cats.push(c);
    });
    cats.sort();
    var html = '<span class="chip" data-tab="">all ' + offers.length + '</span>';
    cats.forEach(function (c) {
      var n = offers.filter(function (o) { return (o.category || 'other') === c; }).length;
      html += '<span class="chip" data-tab="' + h(c) + '">' + h(c) + ' ' + n + '</span>';
    });
    box.innerHTML = html;
    $$('[data-tab]', box).forEach(function (chip) {
      var on = (chip.dataset.tab || '') === (state.offerCat || '');
      chip.style.borderColor = on ? 'var(--accent)' : '';
      chip.style.color = on ? '#cfe0ff' : '';
      chip.onclick = function () {
        state.offerCat = chip.dataset.tab || '';
        drawOfferTabs();
      };
    });
    $$('[data-cat]').forEach(function (card) {
      card.style.display = (!state.offerCat || card.dataset.cat === state.offerCat) ? '' : 'none';
    });
  }

  function offerWorkspace(id) {
    var o = (state.offers || []).filter(function (x) { return x.id === id; })[0];
    if (!o) return;
    var locked = (state.data && state.data.settings && state.data.settings.offers_locked !== false);
    var body = '<div class="callout"><strong>' + h(o.name) + '</strong> · ' +
      h(o.category || 'other') + (o.commission ? ' · ' + h(o.commission) : '') + '</div>' +
      '<p class="small">' + h(o.description || 'No description yet.') + '</p>' +
      '<div class="field"><label>Where to sign up</label>' +
      (o.affiliate_url ? '<a href="' + h(o.affiliate_url) + '" target="_blank" rel="noopener">' +
        h(o.affiliate_url) + '</a>' : '<span class="dim">not set</span>') + '</div>' +
      '<div class="merge-chips">' + (o.keyword_list || []).map(function (k) {
        return '<span class="chip">' + h(k) + '</span>';
      }).join('') + '</div>' +
      '<div class="sep"></div><div class="small muted">' + h(o.disclosure ||
        'Affiliate disclosure is shown on the details page and next to the link.') + '</div>';
    openModal({
      wide: true, title: 'Recommended tool', body: body,
      footer: '<button class="btn" data-close>Close</button>' +
        '<button class="btn" id="ow-conns">How to connect</button>' +
        '<a class="btn" href="/offer/' + o.id + '" target="_blank" rel="noopener">Details</a>' +
        (locked ? '' : '<button class="btn" id="ow-edit">Edit</button>') +
        '<a class="btn primary" style="font-size:15px;padding:11px 22px;font-weight:700" href="' + h(o.affiliate_url || '#') + '" target="_blank" rel="noopener sponsored">Visit site &rarr;</a>',
      onMount: function (root) {
        $('#ow-conns', root).onclick = function () { offerConnections(o.id); };
        if ($('#ow-edit', root)) $('#ow-edit', root).onclick = function () { offerEditor(o.id); };
      }
    });
  }

  // ---- match: describe your business -> leads that fit
  function viewMatch(d) {
    var llmOk = (d.llm || {}).configured;
    var html = pageHead('Match', 'Describe what you sell, or paste your website - the app finds the leads that fit.');
    html += '<div class="callout' + (llmOk ? ' green' : '') + '">' +
      (llmOk ? 'Using <strong>' + h(d.llm.default) + '</strong> to read your description.'
             : 'No LLM provider: matching falls back to keyword extraction. Add a provider in Settings for better results.') +
      '</div>';
    html += '<div class="card"><div class="field"><label>What do you sell, and to whom?</label>' +
      '<textarea id="match-text" rows="5" placeholder="We provide automated bookkeeping and monthly reporting for small accounting firms and ecommerce stores in the EU..."></textarea></div>' +
      '<div class="row"><div class="field"><label>...or a website to read</label>' +
      '<input type="text" id="match-url" placeholder="acme.com"></div>' +
      '<div class="field" style="max-width:120px"><label>Leads</label>' +
      '<input type="number" id="match-limit" value="25" min="5" max="200"></div></div>' +
      '<button class="btn primary" data-act="run-match">Find matching leads</button></div>' +
      '<div id="match-results"></div>';
    return html;
  }

  function runMatch() {
    var text = ($('#match-text') || {}).value || '';
    var url = ($('#match-url') || {}).value || '';
    if (!text.trim() && !url.trim()) return toast('Describe your business or paste a website', 'err');
    var box = $('#match-results');
    box.innerHTML = '<div class="card"><div class="small dim">Reading and scoring leads...</div></div>';
    call('/api/match', { text: text, url: url, limit: Number(($('#match-limit') || {}).value || 25) })
      .then(function (r) {
        if (!r.ok) { box.innerHTML = '<div class="callout">' + h(r.error) + '</div>'; return; }
        var p = r.profile || {};
        var head = '<div class="small dim" style="margin:12px 0">' + num(r.found) + ' of ' + num(r.scanned) +
          ' leads match · keywords: ' + h((p.keywords || []).slice(0, 8).join(', ')) +
          (p.used_ai ? ' · read by AI' : '') + (p.ai_error ? ' · ' + h(p.ai_error) : '') + '</div>';
        if (!r.matches.length) {
          box.innerHTML = head + '<div class="card"><div class="empty" style="padding:24px">' +
            '<div class="big">Nothing matched</div><p class="small">Try more concrete words ' +
            '(the industry and the job title of the person you want), or import more leads.</p></div></div>';
          return;
        }
        var offers = '';
        box.innerHTML = head + '<div class="card tight" style="padding:0"><div class="toolbar" style="padding:10px 12px;margin:0;border-bottom:1px solid var(--line)">' +
          '<span class="small dim">These are your own leads, scored against what you described.</span></div><div class="list">' +
          r.matches.map(function (m) {
            return '<div class="list-item"><div class="avatar">' + h((m.lead.email || '?').slice(0, 2).toUpperCase()) +
              '</div><div class="grow"><div><strong>' + h(m.lead.company || m.lead.email) + '</strong> ' +
              '<span class="badge blue">score ' + m.score + '</span></div>' +
              '<div class="t-sub">' + h(m.lead.email) + ' · ' + h([m.lead.industry, m.lead.title, m.lead.city].filter(Boolean).join(' · ')) +
              '</div><div class="small dim">' + h(m.reason) + '</div></div>' +
              '<div class="nowrap"><button class="btn sm ghost" data-copy-email="' + h(m.lead.email) + '">Copy email</button></div></div>';
          }).join('') + '</div></div>';
        $$('[data-copy-email]').forEach(function (b) {
          b.onclick = function () {
            if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copyEmail);
            toast('Copied ' + b.dataset.copyEmail);
          };
        });
      });
  }

  // ---- dashboard chat (drives the app through tools)
  function chatCard(d) {
    var llmState = d.llm || {};
    return '<div class="card" id="chat-card" style="margin-bottom:14px">' +
      '<div class="card-head"><h2>Assistant</h2><div class="spacer"></div>' +
      (llmState.configured ? '<span class="badge green">' + h(llmState.default) + '</span>'
        : '<span class="badge amber">no LLM provider</span>') + '</div>' +
      '<div id="chat-log" style="max-height:250px;overflow:auto;display:flex;flex-direction:column;gap:8px;margin-bottom:10px"></div>' +
      '<div class="row"><input type="text" id="chat-input" placeholder="e.g. find dental leads and show the best five">' +
      '<button class="btn primary" id="chat-send" style="max-width:96px">Send</button></div></div>';
  }

  function renderChat() {
    var log = $('#chat-log');
    if (!log) return;
    if (!state.chatHistory.length) {
      log.innerHTML = '<div class="small dim">Ask it anything about this workspace. It can search leads, ' +
        'run jobs, add offers and read signals - it cannot delete or send email.</div>';
      return;
    }
    log.innerHTML = state.chatHistory.map(function (m) {
      var mine = m.role === 'user';
      var body = m.tool ? '<div class="small dim">used tool: ' + h(m.tool) + '</div>' : '';
      return '<div style="align-self:' + (mine ? 'flex-end' : 'flex-start') + ';max-width:88%;' +
        'background:' + (mine ? 'var(--accent-soft)' : 'var(--panel-2)') + ';border:1px solid var(--line);' +
        'border-radius:10px;padding:8px 11px"><div class="small">' + h(m.content).replace(/\n/g, '<br>') + '</div>' + body + '</div>';
    }).join('');
    log.scrollTop = log.scrollHeight;
  }

  function sendChat() {
    var input = $('#chat-input');
    if (!input) return;
    var message = input.value.trim();
    if (!message) return;
    input.value = '';
    state.chatHistory.push({ role: 'user', content: message });
    renderChat();
    var btn = $('#chat-send');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>'; }
    call('/api/chat', { message: message, history: state.chatHistory.slice(-8) }).then(function (r) {
      if (btn) { btn.disabled = false; btn.textContent = 'Send'; }
      if (!r.ok) {
        state.chatHistory.push({ role: 'assistant', content: r.error || 'Failed.' });
      } else {
        (r.steps || []).forEach(function (s) {
          state.chatHistory.push({ role: 'assistant', content: 'Running ' + s.tool + '...', tool: s.tool });
        });
        state.chatHistory.push({ role: 'assistant', content: r.answer || '(no answer)' });
      }
      renderChat();
    });
  }

  function connectMailbox(id, provider) {
    call('/api/oauth/start', { mailbox_id: id, provider: provider }).then(function (r) {
      if (!r.ok) return toast(r.error, 'err');
      window.open(r.url, '_blank');
      toast('Finish the sign-in in the new tab, then reload');
    });
  }

  // ---- brands (lookalike & scam watch)
  function viewBrands(d) {
    var auto = d.automation || {};
    var html = pageHead('Brands', 'Watch any number of businesses for copies, lookalikes and scam clones.',
      '<button class="btn" data-act="import-brands">Import list</button>');
    html += '<div class="callout"><strong>' + num(auto.brands || 0) + ' brands watched</strong> · ' +
      num(auto.brand_findings || 0) + ' findings so far. The scan builds the names a fake shop would ' +
      'use, checks which are registered and accepting mail, and cross-checks the new-domain and ' +
      'phishing feeds. It rotates through the list, so a large list is covered over several runs.</div>';

    html += '<div class="card tight" style="margin-bottom:12px"><div class="toolbar" style="margin:0">' +
      '<input type="text" id="brand-name" placeholder="Brand name (optional)" style="max-width:220px">' +
      '<input type="text" id="brand-domain" placeholder="acme.com" style="max-width:220px">' +
      '<button class="btn primary" data-act="add-brand">Add brand</button>' +
      '<div class="spacer" style="flex:1"></div>' +
      '<span class="small dim">Scan a brand now to see its case file immediately.</span></div></div>';

    html += '<div id="brands-box"><div class="small dim">Loading brands...</div></div>';
    setTimeout(loadBrands, 20);
    return html;
  }

  function loadBrands() {
    call('/api/brands').then(function (res) {
      var box = $('#brands-box');
      if (!box) return;
      if (!res.ok) { box.innerHTML = '<div class="small dim">' + h(res.error) + '</div>'; return; }
      if (!res.brands.length) {
        box.innerHTML = '<div class="card"><div class="empty"><div class="big">No brands monitored yet</div>' +
          '<p>Add the domain of a brand you want to protect - your own, or a client\'s. ' +
          'Brand watch then looks for lookalike registrations, fake shops and phishing listed ' +
          'against that name.</p></div></div>';
        return;
      }
      box.innerHTML = '<div class="card tight" style="padding:0"><div class="table-wrap"><table><thead><tr>' +
        '<th>Brand</th><th class="num">Live lookalikes</th><th class="num">New registrations</th>' +
        '<th class="num">Listed</th><th>Last scan</th><th></th></tr></thead><tbody>' +
        res.brands.map(function (b) {
          return '<tr><td><div class="t-email">' + h(b.name) + '</div>' +
            '<div class="t-sub">' + h(b.domain) + '</div></td>' +
            '<td class="num">' + (b.live ? '<span class="badge red">' + num(b.live) + '</span>' : '0') + '</td>' +
            '<td class="num">' + num(b.nrd) + '</td>' +
            '<td class="num">' + num(b.listed) + '</td>' +
            '<td class="small dim nowrap">' + (b.last_scan_ts ? fmtWhen(b.last_scan_ts) : 'never') + '</td>' +
            '<td class="right nowrap">' +
            '<button class="btn sm" data-scan="' + b.id + '">Scan now</button> ' +
            '<button class="btn sm ghost" data-cases="' + b.id + '">Cases</button> ' +
            '<button class="btn sm danger" data-del-brand="' + b.id + '">Remove</button></td></tr>';
        }).join('') + '</tbody></table></div></div>';
      $$('[data-scan]').forEach(function (btn) {
        btn.onclick = function () {
          btn.disabled = true; btn.innerHTML = '<span class="spin"></span> scanning';
          call('/api/brand/scan', { id: Number(btn.dataset.scan) }).then(function (r) {
            btn.disabled = false; btn.textContent = 'Scan now';
            if (!r.ok) return toast(r.error, 'err');
            toast(r.count ? r.count + ' finding(s) for ' + r.brand : 'No new findings for ' + r.brand);
            loadBrands(); refresh();
            if (r.count) brandCases(Number(btn.dataset.scan));
          });
        };
      });
      $$('[data-cases]').forEach(function (btn) {
        btn.onclick = function () { brandCases(Number(btn.dataset.cases)); };
      });
      $$('[data-del-brand]').forEach(function (btn) {
        btn.onclick = function () {
          if (!confirm('Stop monitoring this brand? Its findings stay in the Signals feed.')) return;
          call('/api/brand/delete', { id: Number(btn.dataset.delBrand) }).then(function (r) {
            if (r.ok) { toast('Removed'); loadBrands(); refresh(); } else toast(r.error, 'err');
          });
        };
      });
    });
  }

  function brandCases(id) {
    call('/api/brand/cases?brand_id=' + id).then(function (res) {
      if (!res.ok) return toast(res.error, 'err');
      var b = res.brand;
      var body = '<div class="toolbar" style="margin:0 0 10px"><span class="small dim">' +
        num(res.cases.length) + ' findings for ' + h(b.domain) + '</span>' +
        '<div class="spacer" style="flex:1"></div>' +
        '<a class="btn sm" href="/brand/cases.csv?brand_id=' + id + '">Export CSV</a></div>';
      if (!res.cases.length) {
        body += '<div class="empty" style="padding:24px"><div class="big">No copies found</div>' +
          '<p class="small">Nothing registered looks like this brand right now. Run the scan again ' +
          'after the next new-domain feed arrives.</p></div>';
      } else {
        body += '<div class="list">' + res.cases.map(function (c) {
          var payload = {};
          try { payload = JSON.parse(c.payload || '{}'); } catch (e) {}
          var cls = c.kind === 'lookalike_live' ? (payload.has_mail ? 'red' : 'amber')
            : (c.kind === 'lookalike_listed' ? 'violet' : 'blue');
          return '<div class="list-item"><div class="avatar">' + h(c.domain.slice(0, 2).toUpperCase()) +
            '</div><div class="grow"><div><span class="badge ' + cls + '">' + h(c.kind.replace('lookalike_', '')) +
            '</span> <strong>' + h(c.domain) + '</strong></div>' +
            '<div class="t-sub">' + h(c.title || '') + (payload.has_mail ? ' · accepts mail' : '') +
            (payload.score ? ' · score ' + payload.score : '') + ' · ' + fmtWhen(c.ts) + '</div></div>' +
            '<div class="nowrap"><a class="btn sm ghost" href="https://' + h(c.domain) +
            '" target="_blank" rel="noopener">Open</a></div></div>';
        }).join('') + '</div>';
      }
      openModal({
        wide: true, title: 'Case file: ' + b.name, subtitle: b.domain + ' - nothing here is proof of ' +
          'fraud, only that the name exists. Verify before you accuse anyone.',
        body: body, footer: '<button class="btn" data-close>Close</button>'
      });
    });
  }

  function importBrands() {
    openModal({
      title: 'Import brands to watch',
      subtitle: 'One per line: "Brand name, domain.com" or just the domain.',
      body: '<div class="field"><textarea id="bi-text" rows="10" placeholder="Acme Widgets, acme.com&#10;other-brand.com"></textarea>' +
        '<div class="hint">Up to a few thousand lines. Duplicates are ignored, and a URL is reduced ' +
        'to its domain.</div></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="bi-go">Import</button>',
      onMount: function (root) {
        $('#bi-go', root).onclick = function () {
          call('/api/brand/import', { text: $('#bi-text', root).value }).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Imported ' + r.added + ' brand(s)'); closeModal(); loadBrands(); refresh();
          });
        };
      }
    });
  }

  // ---- mailboxes
  function viewMailboxes(d) {
    var html = pageHead('Mailboxes', 'Send from your own inbox - no third-party sender in the middle.',
      '<button class="btn primary" data-act="new-box">Add mailbox</button>');
    if (!d.mailboxes.length) {
      html += '<div class="card"><div class="empty"><div class="big">No mailbox connected</div>' +
        '<p>You need an SMTP server to send and an IMAP server to read replies. Gmail, Outlook and most ' +
        'providers need an <strong>app password</strong> - your normal password will be refused.</p>' +
        '<button class="btn primary" data-act="new-box">Add your first mailbox</button>' +
        '<div class="small dim" style="margin-top:14px">Common values: smtp.gmail.com:587 (STARTTLS), ' +
        'smtp-mail.outlook.com:587, smtp.zoho.eu:465 (SSL).</div></div></div>';
      return html;
    }
    html += '<div class="grid cols-2">';
    d.mailboxes.forEach(function (b) {
      var used = Math.min(100, Math.round((b.sent_today / Math.max(1, b.daily_limit)) * 100));
      html += '<div class="card"><div class="card-head"><div><h2>' + h(b.name) + '</h2>' +
        '<div class="card-sub">' + h(b.from_email) + '</div></div><div class="spacer"></div>' +
        '<span class="badge ' + (b.active ? 'green' : 'grey') + '">' + (b.active ? 'active' : 'paused') + '</span></div>' +
        '<div class="small muted">SMTP ' + h(b.smtp_host || '—') + ':' + h(b.smtp_port) + ' · ' + h(b.smtp_security) +
        '<br>IMAP ' + h(b.imap_host || 'not set') + (b.imap_host ? ':' + h(b.imap_port) : '') + '</div>' +
        '<div class="usage mt"><div class="small">today</div><div class="track"><div class="fill" ' +
        'style="width:' + used + '%"></div></div><div class="small dim">' + b.sent_today + '/' + b.daily_limit + '</div></div>' +
        '<div class="toolbar" style="margin:0"><button class="btn sm" data-test-box="' + b.id + '">Test connection</button>' +
        '<button class="btn sm" data-send-test="' + b.id + '">Send test email</button>' +
        '<button class="btn sm" data-oauth="google:' + b.id + '">Connect Google</button>' +
        '<button class="btn sm" data-oauth="microsoft:' + b.id + '">Connect Microsoft</button>' +
        '<button class="btn sm" data-edit-box="' + b.id + '">Edit</button>' +
        '<button class="btn sm danger" data-del-box="' + b.id + '">Remove</button></div></div>';
    });
    html += '</div>';
    return html;
  }

  function wireMailboxes() {

    $$('[data-edit-box]').forEach(function (b) {
      b.onclick = function () { mailboxForm(Number(b.dataset.editBox)); };
    });
    $$('[data-del-box]').forEach(function (b) {
      b.onclick = function () {
        if (!confirm('Remove this mailbox? Sent emails stay in the history.')) return;
        call('/api/mailbox/delete', { id: Number(b.dataset.delBox) }).then(function (r) {
          if (r.ok) { toast('Mailbox removed'); refresh(); } else toast(r.error, 'err');
        });
      };
    });
    $$('[data-test-box]').forEach(function (b) {
      b.onclick = function () {
        b.disabled = true; b.textContent = 'Testing...';
        call('/api/mailbox/test', { id: Number(b.dataset.testBox) }).then(function (r) {
          b.disabled = false; b.textContent = 'Test connection';
          if (r.ok) toast('SMTP works' + (r.imap === 'ok' ? ' and IMAP works' : '') + '. Ready to send.');
          else toast((r.smtp_detail || 'SMTP failed') + (r.imap === 'failed' ? ' | IMAP: ' + r.imap_detail : ''), 'err');
        });
      };
    });
    $$('[data-send-test]').forEach(function (b) {
      b.onclick = function () { sendTest(Number(b.dataset.sendTest)); };
    });
    $$('[data-oauth]').forEach(function (b) {
      b.onclick = function () {
        var parts = b.dataset.oauth.split(':');
        connectMailbox(Number(parts[1]), parts[0]);
      };
    });
  }

  // ---- settings
  function viewSettings(d) {
    var s = d.settings, send = s.sending || {}, tr = s.tracking || {}, vf = s.verify || {};
    var html = pageHead('Settings', 'Sending rules, tracking and your own details.');
    html += '<div class="grid cols-2">';

    html += '<div class="card"><div class="card-head"><h2>Sending rules</h2></div>' +
      '<label class="check" style="margin-bottom:12px"><input type="checkbox" id="set-enabled"' +
      (send.enabled ? ' checked' : '') + '> Sending enabled</label>' +
      '<div class="row"><div class="field"><label>Start hour</label>' +
      '<input type="number" id="set-ws" min="0" max="23" value="' + h(send.window_start) + '"></div>' +
      '<div class="field"><label>End hour</label>' +
      '<input type="number" id="set-we" min="0" max="23" value="' + h(send.window_end) + '"></div></div>' +
      '<div class="row"><div class="field"><label>Gap between sends (seconds)</label>' +
      '<input type="number" id="set-gap" min="5" max="3600" value="' + h(send.gap_seconds) + '"></div>' +
      '<div class="field"><label>Random jitter (seconds)</label>' +
      '<input type="number" id="set-jitter" min="0" max="600" value="' + h(send.jitter_seconds) + '"></div></div>' +
      '<label class="check"><input type="checkbox" id="set-weekdays"' + (send.weekdays_only ? ' checked' : '') +
      '> Only send on weekdays</label>' +
      '<div class="hint small dim" style="margin-top:8px">A realistic gap is 40-120 seconds. ' +
      'The daily cap per mailbox is set on each mailbox.</div></div>';

    html += '<div class="card"><div class="card-head"><h2>Your details</h2></div>' +
      '<div class="field"><label>Your name (used as {{sender_name}})</label>' +
      '<input type="text" id="set-sender" value="' + h(s.sender_name) + '" placeholder="e.g. Alex"></div>' +
      '<div class="field"><label>Company</label>' +
      '<input type="text" id="set-company" value="' + h(s.company_name) + '" placeholder="e.g. Acme Ltd"></div>' +
      '<div class="field"><label>Contact email (sent in API user agents)</label>' +
      '<input type="text" id="set-contact" value="' + h(s.contact_email || '') + '" placeholder="you@company.com">' +
      '<div class="hint">Some public APIs ask automated clients to identify themselves. This is used only in the user agent.</div></div>' +
      '<label class="check" style="margin-top:4px"><input type="checkbox" id="set-offers-locked"' +
      (s.offers_locked !== false ? ' checked' : '') + '> Keep the Toolbox read-only</label>' +
      '<div class="hint small dim" style="margin-top:4px">Leave this on for the build you hand out: the ' +
      'recommended tools and their links stay exactly as shipped. Turn it off to edit your own copy.</div>' +
      '<div class="field"><label>Public address for tracking links</label>' +
      '<input type="text" id="set-track-base" value="' + h(tr.base_url) + '" placeholder="http://127.0.0.1:' + d.port + '">' +
      '<div class="hint">Leave as the local address for open tracking on this machine only. ' +
      'For tracking from a public URL, put a tunnel address here.</div></div>' +
      '<label class="check"><input type="checkbox" id="set-track"' + (tr.enabled ? ' checked' : '') +
      '> Track opens and clicks</label>' +
      '<div class="sep"></div><label class="check"><input type="checkbox" id="set-mx"' + (vf.mx ? ' checked' : '') +
      '> Check MX records when verifying</label>' +
      '<label class="check" style="margin-top:8px"><input type="checkbox" id="set-smtp"' + (vf.smtp_probe ? ' checked' : '') +
      '> Also probe the mail server (slower, sometimes blocked)</label>' +
      '<div class="toolbar mt"><button class="btn primary" data-act="save-settings">Save settings</button></div></div>';

    html += '</div>';

    html += '<div class="card mt"><div class="card-head"><h2>Mailbox sign-in (Google / Microsoft)</h2></div>' +
      '<div class="small muted" style="margin-bottom:10px">Google Workspace and Microsoft 365 no longer accept plain ' +
      'passwords for IMAP/SMTP. Register an OAuth client once, paste the ids here, then use ' +
      '<strong>Connect Google / Connect Microsoft</strong> on a mailbox card.</div>' +
      '<div class="small dim" style="margin-bottom:10px">Redirect URI to register: <span class="mono">' +
      h(((d.oauth || {}).redirect_uri) || '') + '</span></div>' +
      '<div class="row"><div class="field"><label>Google client id</label>' +
      '<input type="text" id="set-gcid" value="' + h(((s.oauth || {}).google_client_id) || '') + '"></div>' +
      '<div class="field"><label>Google client secret ' +
      (((s.oauth || {}).has_google_client_secret) ? '<span class="badge green">saved</span>' : '') + '</label>' +
      '<input type="password" id="set-gsecret" placeholder="leave blank to keep"></div></div>' +
      '<div class="row"><div class="field"><label>Microsoft client id</label>' +
      '<input type="text" id="set-mcid" value="' + h(((s.oauth || {}).microsoft_client_id) || '') + '"></div>' +
      '<div class="field"><label>Microsoft client secret ' +
      (((s.oauth || {}).has_microsoft_client_secret) ? '<span class="badge green">saved</span>' : '') + '</label>' +
      '<input type="password" id="set-msecret" placeholder="leave blank to keep"></div></div></div>';

    html += llmCard(d);

    html += mcpCard(d);

    html += '<div class="card mt"><div class="card-head"><h2>Connectors (optional)</h2>' +
      '<div class="spacer"></div>' +
      ['hunter', 'ai', 'serper'].map(function (k) {
        var on = (s.connector_status || {})[k];
        return '<span class="badge ' + (on ? 'green' : 'grey') + '" style="margin-left:4px">' +
          k + (on ? ' on' : ' off') + '</span>';
      }).join('') + '</div>' +
      '<div class="small muted" style="margin-bottom:12px">Every connector is optional and off until ' +
      'you add a key. Keys are stored in <span class="mono">data/settings.json</span> on this computer ' +
      'and are never sent anywhere except to the service they belong to.</div>' +
      '<div class="row"><div class="field"><label>Hunter.io API key ' +
      (s.connectors && s.connectors.has_hunter ? '<span class="badge green">saved</span>' : '') + '</label>' +
      '<input type="password" id="set-hunter" placeholder="' +
      (s.connectors && s.connectors.has_hunter ? 'leave blank to keep' : 'find addresses behind a domain') + '"></div>' +
      '<div class="field"><label>Serper API key ' +
      (s.connectors && s.connectors.has_serper ? '<span class="badge green">saved</span>' : '') + '</label>' +
      '<input type="password" id="set-serper" placeholder="' +
      (s.connectors && s.connectors.has_serper ? 'leave blank to keep' : 'company name -> website') + '"></div></div>' +
      '<div class="row"><div class="field"><label>AI base URL</label>' +
      '<input type="text" id="set-aibase" value="' + h((s.connectors && s.connectors.ai_base) || '') +
      '" placeholder="https://api.openai.com/v1"></div>' +
      '<div class="field"><label>AI model</label>' +
      '<input type="text" id="set-aimodel" value="' + h((s.connectors && s.connectors.ai_model) || '') +
      '" placeholder="gpt-4o-mini"></div>' +
      '<div class="field"><label>AI API key ' +
      (s.connectors && s.connectors.has_ai ? '<span class="badge green">saved</span>' : '') + '</label>' +
      '<input type="password" id="set-aikey" placeholder="' +
      (s.connectors && s.connectors.has_ai ? 'leave blank to keep' : 'optional') + '"></div></div>' +
      '<div class="hint small dim">Any OpenAI-compatible endpoint works: OpenAI, DeepSeek ' +
      '(<span class="mono">https://api.deepseek.com/v1</span>), Ollama Cloud, or a local server. ' +
      'Used only by the AI enrichment job.</div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Data and privacy</h2></div>' +
      '<div class="small muted">Everything is stored in <span class="mono">' + h(d.data_dir || 'data/') +
      '</span> on this computer. No account, no cloud, no telemetry.</div>' +
      '<div class="toolbar mt"><button class="btn" data-act="export">Export all leads as CSV</button>' +
      '<button class="btn" data-act="demo">Load demo data</button>' +
      '<button class="btn danger" data-act="wipe">Delete all data</button></div></div>';

    html += '<div class="card mt"><div class="card-head"><h2>Diagnostics</h2><div class="spacer"></div>' +
      '<button class="btn sm" data-act="diag">Run check</button></div><div id="diag-box" class="small dim">' +
      'Version ' + h(d.version) + ' · ' + h(d.python) + '</div></div>';

    var legal = d.legal || {};
    html += '<div class="card mt"><div class="card-head"><h2>Legal notice</h2></div>' +
      '<div class="small muted">' + h(legal.not_affiliated ||
        'This is an independent project, not affiliated with Metricool.') + '</div>' +
      '<div class="small muted mt">' + h(legal.affiliate_note ||
        'The Metricool links in this app are affiliate links.') + '</div>' +
      '<div class="small dim mt">Metricool&rsquo;s own terms and privacy policy apply to your ' +
      'account. This app is provided as is, under the ' + h(legal.licence || 'MIT') + ' licence, ' +
      'without warranty. You are responsible for the messages you send and for the data you ' +
      'import (GDPR, CAN-SPAM, PECR and local equivalents).</div>' +
      '<div class="toolbar mt"><a class="btn sm" href="' +
      h(legal.signup_url || 'https://my.mtr.cool/NEMGNE') +
      '" target="_blank" rel="noopener sponsored">Metricool (affiliate link)</a></div></div>';
    return html;
  }

  function wireSettings() {
    wireLlm();
    wireMcp();
  }

  // ------------------------------------------------------------------ actions
  var actions = {
    reload: function () { window.location.reload(); },
    'social-refresh': function () { svCaptureForm(); loadSocial(); },
    'social-make': function () {
      svCaptureForm();
      var spec = svSpec();
      if (!spec.media) return toast('Pick a picture or a video', 'err');
      if (svKind(spec.media) === 'image' && !spec.audio) {
        return toast('A picture needs an audio track: the audio sets the length', 'err');
      }
      var btn = $('[data-act="social-make"]');
      if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }
      call('/api/social/make', { spec: spec }).then(function (r) {
        if (btn) { btn.disabled = false; btn.textContent = 'Make the video'; }
        if (!r.ok) return toast(r.error, 'err');
        toast('Rendering - the log below updates live');
        svTaskBadge('running');
        pollTask(r.task);
      });
    },
    'social-cancel': function () {
      if (!SOCIAL.task) return;
      call('/api/social/cancel', { id: SOCIAL.task }).then(function (r) {
        toast(r.ok ? 'Cancelled' : 'Could not cancel that render', r.ok ? 'ok' : 'err');
      });
    },
    'sv-tab': function (el) {
      if (SOCIAL.tab === 'create') svCaptureForm();
      SOCIAL.tab = el.dataset.tab || 'create';
      SOCIAL.rendered = false;
      render();
    },
    'sv-month': function (el) {
      SOCIAL.month += Number(el.dataset.step) || 0;
      SOCIAL.plan = null;
      SOCIAL.rendered = false;
      render();
      svLoadSection('planner', true);
    },
    'sv-thread': function (el) {
      SOCIAL.thread = el.dataset.id;
      SOCIAL.rendered = false;
      render();
    },
    'sv-demo': function () {
      svCaptureForm();
      SOCIAL.demo = !SOCIAL.demo;
      SOCIAL.rendered = false;
      render();
      if (!SOCIAL.demo) svAutoLoad();
    },
    'sv-section': function (el) {
      var name = el.dataset.section;
      if (SV_SECTION_SLOT[name]) SOCIAL[SV_SECTION_SLOT[name]] = null;
      toast('Asking Metricool...');
      svLoadSection(name, true);
    },
    'sv-legal': function () {
      var legal = svLegal();
      openModal({
        title: 'Legal notice',
        body: '<div class="small muted">' + h(legal.not_affiliated ||
          'This is an independent project, not affiliated with Metricool.') + '</div>' +
          '<div class="small muted mt">' + h(legal.affiliate_note ||
            'The Metricool links in this app are affiliate links.') + '</div>' +
          '<div class="small dim mt">Metricool&rsquo;s own terms and privacy policy apply to your ' +
          'account. This app is provided as is, under the MIT licence, without warranty. You are ' +
          'responsible for the messages you send and for the data you import (GDPR, CAN-SPAM, ' +
          'PECR and local equivalents).</div>' +
          '<div class="toolbar mt"><a class="btn sm" href="' +
          h(legal.signup_url || 'https://my.mtr.cool/NEMGNE') +
          '" target="_blank" rel="noopener sponsored">Metricool (affiliate link)</a>' +
          '<a class="btn sm ghost" href="#settings" data-close>Settings</a></div>',
        footer: '<button class="btn" data-close>Close</button>'
      });
    },
    'sv-reply': function () {
      var text = svVal('sv-reply');
      if (!text.trim()) return toast('Write the reply first', 'err');
      if (SOCIAL.demo) return toast('Sample data: nothing was sent to anyone');
      call('/api/social/reply', { conversation: SOCIAL.thread, text: text }).then(function (r) {
        if (!r.ok) return toast(r.error || 'Metricool refused the reply', 'err');
        toast('Reply sent', 'ok');
        SOCIAL.rendered = false;
        svLoadSection('inbox', true);
      });
    },
    'sv-note': function (el) {
      var id = el.dataset.id || SOCIAL.thread;
      openModal({
        title: 'Internal note',
        subtitle: 'Kept in this app. A note is never sent to the network.',
        body: '<div class="field"><label>Note</label>' +
          '<textarea id="sv-note-text" rows="4" placeholder="What the team should know"></textarea></div>',
        footer: '<button class="btn" data-close>Cancel</button>' +
          '<button class="btn primary" id="sv-note-save">Save note</button>',
        onMount: function (root) {
          $('#sv-note-save', root).onclick = function () {
            var text = $('#sv-note-text', root).value.trim();
            if (!text) return toast('Write the note first', 'err');
            SOCIAL.notes[id] = (SOCIAL.notes[id] || []).concat([text]);
            closeModal();
            SOCIAL.rendered = false;
            render();
          };
        }
      });
    },
    'sv-assign': function (el) {
      var id = el.dataset.id || SOCIAL.thread;
      openModal({
        title: 'Assign this conversation',
        subtitle: 'Kept in this app as the owner of the thread.',
        body: '<div class="field"><label>Teammate</label>' +
          '<input type="text" id="sv-assign-name" value="' + h(SOCIAL.assign[id] || '') + '" ' +
          'placeholder="a name, or leave empty to unassign"></div>',
        footer: '<button class="btn" data-close>Cancel</button>' +
          '<button class="btn primary" id="sv-assign-save">Save</button>',
        onMount: function (root) {
          $('#sv-assign-save', root).onclick = function () {
            var name = $('#sv-assign-name', root).value.trim();
            if (name) SOCIAL.assign[id] = name;
            else delete SOCIAL.assign[id];
            closeModal();
            SOCIAL.rendered = false;
            render();
          };
        }
      });
    },
    'mc-on': function () {
      call('/api/social/metricool/start', {}).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        window.open(r.url, '_blank');
        toast('Approve the access in the browser tab');
        var tries = 0;
        var timer = setInterval(function () {
          tries += 1;
          if (tries > 24) { clearInterval(timer); return; }
          call('/api/social/state').then(function (st) {
            if (st.ok && st.metricool.connected) {
              clearInterval(timer);
              toast('Metricool connected', 'ok');
              SOCIAL.workspace = null;
              SOCIAL.workspaceTried = false;
              SOCIAL.inbox = SOCIAL.plan = SOCIAL.ads = SOCIAL.reports = null;
              loadSocial();
            }
          });
        }, 5000);
      });
    },
    'mc-off': function () {
      call('/api/social/metricool/disconnect', {}).then(function (r) {
        toast(r.ok ? 'Disconnected' : 'Could not disconnect', r.ok ? 'ok' : 'err');
        SOCIAL.workspace = null;
        SOCIAL.workspaceTried = false;
        SOCIAL.inbox = SOCIAL.plan = SOCIAL.ads = SOCIAL.reports = null;
        loadSocial();
      });
    },
    'mc-tools': function () {
      toast('Asking Metricool for its tools...');
      call('/api/social/workspace?refresh=1').then(function (r) {
        if (!r.ok) return toast(r.error || 'Could not read the tools', 'err');
        SOCIAL.workspace = r;
        SOCIAL.workspaceTried = true;
        SOCIAL.rendered = false;
        render();
        toast(r.count + ' tools');
      });
    },
    'mc-schedule': function () {
      svCaptureForm();
      var nets = $$('[data-sv-net]').filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
      var caption = svVal('sv-caption').trim();
      if (!caption) return toast('Write the caption first', 'err');
      call('/api/social/metricool/schedule', {
        caption: caption, when: svVal('sv-when'), networks: nets,
        media_url: svVal('sv-media-url'), video: svVal('sv-video')
      }).then(function (r) {
        if (!r.ok) { toast(r.error || 'Metricool refused it', 'err'); resultBox('sv-mc-post', r); return; }
        toast('Sent to Metricool');
        resultBox('sv-mc-post', r);
        SOCIAL.plan = null;
      });
    },
    import: function () { importModal(); },
    'hyros-accounts': function () {
      var out = $('#ads-out');
      if (out) out.innerHTML = '<div class="small dim">Reading ad accounts from Hyros...</div>';
      call('/api/ads/hyros/accounts', {}).then(function (r) {
        if (!out) return;
        if (!r.ok) {
          out.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
            h(r.error) + '</div>';
          return;
        }
        out.innerHTML = adsRowsHtml('Ad accounts', r.rows) ||
          '<pre class="log">' + h(JSON.stringify(r.raw, null, 1).slice(0, 4000)) + '</pre>';
      });
    },
    'hyros-attribution': function () {
      var out = $('#ads-out');
      if (out) out.innerHTML = '<div class="small dim">Reading attribution for the last 30 days...</div>';
      call('/api/ads/hyros/attribution', { days: 30 }).then(function (r) {
        if (!out) return;
        if (!r.ok) {
          out.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
            h(r.error) + '</div>';
          return;
        }
        out.innerHTML = '<div class="small dim">' + h((r.window || []).join(' to ')) + '</div>' +
          (adsRowsHtml('Attribution', r.rows) ||
            '<pre class="log">' + h(JSON.stringify(r.raw, null, 1).slice(0, 4000)) + '</pre>');
      });
    },
    'hyros-mcp': function () {
      call('/api/ads/hyros/mcp', {}).then(function (r) {
        if (!r.ok) return toast(r.error || 'Could not add the MCP server', 'err');
        toast('Hyros MCP server added - see MCP connections in Settings');
        refresh();
      });
    },
    'ads-tab': function (button) {
      state.adsTab = button.dataset.tab;
      render();
    },
    'ads-social': function () {
      loadSocial().then(function () { render(); });
    },
    'ads-save': function (button) {
      var name = button.dataset.name;
      call('/api/ads/save', {
        name: name, api_key: svVal('ads-key'), base_url: svVal('ads-base'), path: svVal('ads-path')
      }).then(function (r) {
        if (!r.ok) return toast(r.error || 'Could not save that', 'err');
        toast('Saved');
        refresh();
      });
    },
    'ads-test': function (button) {
      call('/api/ads/test', { name: button.dataset.name }).then(function (r) {
        var out = $('#ads-out');
        if (!r.ok) {
          if (out) out.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' + h(r.error) + '</div>';
          return toast(r.error || 'The test failed', 'err');
        }
        toast('Answered HTTP ' + r.status);
        if (out) out.innerHTML = '<pre class="log">' + h(JSON.stringify(r.data, null, 1).slice(0, 4000)) + '</pre>';
        refresh();
      });
    },
    'ads-pull': function (button) {
      var out = $('#ads-out');
      if (out) out.innerHTML = '<div class="small dim">Calling ' + h(button.dataset.name) + '...</div>';
      call('/api/ads/pull', { name: button.dataset.name, path: svVal('ads-path') }).then(function (r) {
        if (!out) return;
        if (!r.ok) {
          out.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' + h(r.error) + '</div>';
          return;
        }
        out.innerHTML = '<div class="small dim">' + h(r.url) + ' · HTTP ' + r.status + '</div>' +
          '<pre class="log">' + h(JSON.stringify(r.data, null, 1).slice(0, 6000)) + '</pre>';
      });
    },
    'hyros-key': function () {
      state.adsTab = 'hyros';
      go('ads');
    },
    'hyros-pull': function (button) {
      var out = $('#hyros-out');
      if (out) out.innerHTML = '<div class="small dim">Reading ' + h(button.dataset.kind) +
        ' from Hyros...</div>';
      call('/api/hyros/pull', { kind: button.dataset.kind, limit: 100 }).then(function (r) {
        if (!r.ok) {
          if (out) out.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
            h(r.error) + '</div>';
          return;
        }
        var rows = r.rows || [];
        if (!out) return;
        if (!rows.length) {
          out.innerHTML = '<div class="small dim">Nothing came back. The raw answer:</div>' +
            '<pre class="log">' + h(JSON.stringify(r.raw, null, 1).slice(0, 4000)) + '</pre>';
          return;
        }
        var keys = Object.keys(rows[0]).slice(0, 6);
        out.innerHTML = '<div class="card" style="background:var(--panel-2)"><div class="card-head">' +
          '<h3>' + h(r.kind) + '</h3><div class="spacer"></div><span class="badge grey">' +
          num(r.count) + '</span></div><div class="table-wrap"><table><thead><tr>' +
          keys.map(function (k) { return '<th>' + h(k) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          rows.slice(0, 100).map(function (row) {
            return '<tr>' + keys.map(function (k) {
              var value = row[k];
              return '<td class="small">' + h(typeof value === 'object' && value !== null
                ? JSON.stringify(value).slice(0, 80) : value) + '</td>';
            }).join('') + '</tr>';
          }).join('') + '</tbody></table></div></div>';
        refresh();
      });
    },
    'hyros-paths': function () {
      var paths = {};
      ['leads', 'sales', 'calls'].forEach(function (kind) {
        var el = $('#hyros-path-' + kind);
        if (el) paths[kind] = el.value;
      });
      call('/api/hyros/paths', { paths: paths }).then(function (r) {
        if (!r.ok) return toast(r.error || 'Could not save', 'err');
        toast('Saved');
        loadHyros();
      });
    },
    'mr-save': function () {
      var key = svVal('mr-key');
      if (!key) return toast('Paste the API key first', 'err');
      call('/api/manyreach/key', { key: key }).then(function (r) {
        if (!r.ok) return toast(r.error || 'That key was refused', 'err');
        toast('Connected to ' + ((r.account && r.account.title) || 'Manyreach'));
        refresh();
      });
    },
    'mr-sync': function () {
      toast('Downloading campaigns and senders from the API...');
      call('/api/manyreach/sync', {}).then(function (r) {
        if (!r.ok) return toast(r.error || 'The sync failed', 'err');
        var counts = r.counts || {};
        var imported = r.imported || {};
        toast(num(counts.campaigns || 0) + ' campaigns, ' + num(counts.senders || 0) +
          ' senders · put ' + num(imported.mailboxes || 0) + ' in Mailboxes and ' +
          num(imported.sequences || 0) + ' in Sequences');
        refresh().then(function () { renderMcpItems('manyreach', r.items || []); });
      });
    },
    'mr-import': function () {
      call('/api/manyreach/import', {}).then(function (r) {
        if (!r.ok) return toast(r.error || 'The import failed', 'err');
        toast(num(r.mailboxes) + ' mailboxes, ' + num(r.sequences) + ' sequences added' +
          (r.mailboxes_without_password
            ? ' · ' + num(r.mailboxes_without_password) + ' need the SMTP password' : ''));
        refresh();
      });
    },
    'mcp-add': function () {
      var name = svVal('mcp-name'), url = svVal('mcp-url'), token = svVal('mcp-token');
      if (!name || !url) return toast('A name and an MCP URL are required', 'err');
      call('/api/mcp/custom', { name: name, url: url, token: token }).then(function (r) {
        if (!r.ok) return toast(r.error || 'Could not add that server', 'err');
        toast('Server added');
        refresh();
      });
    },
    'bulk-data': function () { bulkDataModal(); },
    'lead-apply': function () { state.leads.page = 1; loadLeads(); },
    'lead-clear': function () {
      var q = state.leads;
      LEAD_FILTER_KEYS.forEach(function (key) { q.filters[key] = ''; });
      q.filters.sort = 'newest';
      q.q = '';
      q.status = '';
      q.page = 1;
      state.selection.clear();
      render();
      loadLeads();
    },
    'osint-refresh': function () { refresh(); },
    'osint-scan': function (el) {
      var box = $('#osint-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Scanning the feed...';
      call('/api/osint/scan', { source: ($('#osint-source') || {}).value || '',
        brands: ($('#osint-brands') || {}).value || '', limit: 5000 }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + num(r.scanned) + '</b> domains scanned against <b>' +
          num(r.brands) + '</b> brands: <b>' + num(r.hit_count) + '</b> fake shops</div>' +
          (r.hits || []).slice(0, 30).map(function (hit) {
            return '<div class="small mono">' + h(hit.domain) + ' ~ ' + h(hit.brand) + ' <span class="dim">(' + h(hit.why) + ')</span></div>';
          }).join('');
        toast(num(r.hit_count) + ' fake shops found');
        refresh();
      });
    },
    'treg-verify': function (el) {
      var box = $('#treg-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Verifying...';
      call('/api/treg/verify', { emails: ($('#treg-emails') || {}).value || '' }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = r.results.map(function (row) {
          return '<div>' + h(row.email || '') + ' - <b>' + h(row.status || 'error') + '</b>' +
            (row.provider ? ' <span class="dim">via ' + h(row.provider) + (row.free ? ' · free' : '') + '</span>' : '') +
            (row.error ? ' <span style="color:var(--red)">' + h(row.error) + '</span>' : '') + '</div>';
        }).join('');
      });
    },
    'treg-maps-run': function (el) {
      var box = $('#treg-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Asking Google Maps through treg...';
      call('/api/treg/maps', { query: ($('#treg-maps') || {}).value || '' }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + num(r.count) + '</b> places · cost $' +
          ((r.charged_micro || 0) / 1e6).toFixed(5) + '</div>' + (r.places || []).slice(0, 20).map(function (p) {
            return '<div class="small">' + h(p.name) + ' <span class="dim">' + h(p.address) +
              (p.phone ? ' · ' + h(p.phone) : '') + '</span></div>';
          }).join('');
      });
    },
    'osint-dorks': function (el) {
      var box = $('#osint-dorks-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Running the dorks...';
      call('/api/osint/dorks', { brands: ($('#osint-dork-brands') || {}).value || '',
        own: ($('#osint-dork-own') || {}).value || '', per_brand: 5 }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + num(r.candidates.length) + '</b> candidates from <b>' +
          num(r.brands) + '</b> brands</div>' + r.candidates.slice(0, 30).map(function (row) {
            return '<div class="small">' + h(row.domain) + ' <span class="dim">~ ' + h(row.brand) +
              ' (' + h(row.query) + ')</span></div>';
          }).join('');
        toast(num(r.candidates.length) + ' fake-shop candidates');
        refresh();
      });
    },
    'osint-discover': function (el) {
      var box = $('#osint-crt-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Asking certificate transparency...';
      call('/api/osint/discover', { brands: ($('#osint-crt-brands') || {}).value || '' }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + num(r.count) + '</b> domains in certificates</div>' +
          (r.candidates || []).slice(0, 40).map(function (row) {
            return '<div class="small">' + h(row.domain) + ' <span class="dim">~ ' + h(row.brand) + '</span></div>';
          }).join('');
        toast(num(r.count) + ' certificate candidates');
        refresh();
      });
    },
    'osint-meta': function (el) {
      var box = $('#osint-meta-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Asking the Ad Library...';
      call('/api/osint/meta-ads', { keywords: ($('#osint-meta-keywords') || {}).value || '',
        country: ($('#osint-meta-country') || {}).value || '' }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + num(r.found) + '</b> advertisers</div>' +
          (r.advertisers || []).slice(0, 40).map(function (row) {
            return '<div class="small">' + h(row.page) + ' <span class="dim">' + h(row.domain || row.page_url || '') + '</span></div>';
          }).join('');
        refresh();
      });
    },
    'osint-profile': function (el) {
      var box = $('#osint-profile-result');
      if (el) el.disabled = true;
      call('/api/osint/profile', { domain: ($('#osint-domain') || {}).value || '' }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) { if (box) box.innerHTML = '<span style="color:var(--red)">' + h(r.error) + '</span>'; return; }
        if (box) box.innerHTML = '<div><b>' + h(r.domain) + '</b> - ' + h(r.business || 'unknown') +
          '</div><div class="small">' + h(r.title) + '</div><div class="small dim">' + h(r.description) +
          '</div><div class="small">Emails: ' + h(r.emails || 'none') + ' | Phones: ' + h(r.phones || 'none') + '</div>';
        refresh();
      });
    },
    'osint-profile-new': function (el) {
      var box = $('#osint-profile-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Visiting 20 domains...';
      call('/api/osint/profile', { limit: 20 }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) return toast(r.error || 'Could not profile the domains', 'err');
        if (box) box.innerHTML = '<div><b>' + num(r.profiled) + '</b> domains profiled</div>' +
          (r.domains || []).map(function (row) {
            return '<div class="small">' + h(row.domain) + ' - ' + h(row.title || '') + '</div>';
          }).join('');
        toast(num(r.profiled) + ' domains profiled');
        refresh();
      });
    },
    'domain-refresh': function () { loadDomains(); },
    'hot-build': function (el) {
      var box = $('#hot-result');
      if (el) el.disabled = true;
      if (box) box.innerHTML = 'Building...';
      call('/api/hot/today', { limit: 300, table: true }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) {
          if (box) box.innerHTML = '<div style="color:var(--red)">' + h(r.error) + '</div>';
          return;
        }
        var c = r.counts || {};
        var rows = r.rows || [];
        if (!rows.length) {
          if (box) box.innerHTML = 'No signals in the last two days yet - run the Daily jobs first.';
          return;
        }
        if (box) {
          box.innerHTML = '<div class="small"><b>' + num(c.total) + '</b> rows · <b>' + num(c.HOT) +
            '</b> hot · <b>' + num(c.WARM) + '</b> warm · <b>' + num(c.with_email) +
            '</b> with an email</div><div class="table-wrap mt"><table><thead><tr>' +
            '<th>Heat</th><th>Offer</th><th>Signal</th><th>Reason</th><th>First email question</th>' +
            '<th>Email</th><th>Company</th><th>Confidence</th></tr></thead><tbody>' +
            rows.map(function (row) {
              return '<tr><td><span class="badge ' + (row.heat === 'HOT' ? 'red' : 'amber') + '">' +
                h(row.heat) + '</span></td><td>' + h(row.offer || '—') + '</td><td class="small">' +
                h(row.signal) + '</td><td class="small">' + h(row.reason) + '</td>' +
                '<td class="small" style="max-width:360px">' + h(row['first email question']) + '</td>' +
                '<td class="small">' + h(row.email || '—') + '</td><td>' + h(row.company || '') +
                '</td><td class="small dim">' + h(row['email confidence']) + '</td></tr>';
            }).join('') + '</tbody></table></div>' +
            '<div class="small dim mt">CSV: <span class="mono">' + h(r.path || '') + '</span></div>';
        }
        if (r.path) toast('Hot list written to ' + r.path.split('\\').pop());
      });
    },
    'osm-search': function (el) {
      var text = ($('#osm-text') || {}).value || '';
      var area = ($('#osm-area') || {}).value || '';
      var limit = Number(($('#osm-limit') || {}).value) || 200;
      var box = $('#osm-result');
      if (!text.trim()) return toast('Type what to look for, e.g. roofers in Slovakia', 'err');
      if (el) el.disabled = true;
      if (box) box.innerHTML = '<div class="small dim">Searching OpenStreetMap - a whole country ' +
        'can take half a minute...</div>';
      call('/api/osm/search', { text: text, area: area, limit: limit }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) {
          if (box) box.innerHTML = '<div class="small" style="color:var(--red)">' + h(r.error) + '</div>';
          return;
        }
        state.osm = r;
        if (box) box.innerHTML = osmSummary(r);
      });
    },
    'osm-import': function (el) {
      var found = state.osm || {};
      var box = $('#osm-result');
      if (el) el.disabled = true;
      call('/api/osm/import', {
        category: found.category || '', area: found.area || '', limit: 5000
      }).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) return toast(r.error || 'Could not import the OpenStreetMap rows', 'err');
        toast(num(r.imported) + ' contacts imported from OpenStreetMap');
        if (box) box.innerHTML = osmSummary(state.osm || {}) +
          '<div class="small mt">Imported <b>' + num(r.imported) + '</b> of ' + num(r.examined) +
          ' rows with a contact.</div>';
        loadDomains();
        loadLeads();
      });
    },
    'domain-sync': function (el) {
      if (el) el.disabled = true;
      call('/api/domain/sync', {}).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) return toast(r.error || 'Could not read the leads', 'err');
        toast(r.added ? (num(r.added) + ' domains added from leads')
          : 'Every lead domain is already in the catalogue');
        loadDomains();
      });
    },
    'domain-pull': function (el) {
      var source = el.dataset.source;
      var input = $('#pull-' + source);
      var limit = Number(input ? input.value : 2000) || 2000;
      var label = el.textContent;
      el.disabled = true;
      el.textContent = 'Pulling...';
      call('/api/domain/pull', { source: source, limit: limit }).then(function (r) {
        el.disabled = false;
        el.textContent = label;
        if (!r.ok) return toast(r.error || 'Could not pull that source', 'err');
        toast(num(r.added) + ' new domains, ' + num(r.known) + ' already known');
        state.domains.page = 1;
        loadDomains();
      });
    },
    'domain-enrich': function (el) {
      var body = (el && el.dataset && el.dataset.domain)
        ? { domains: [el.dataset.domain] } : { limit: 3 };
      if (el) el.disabled = true;
      toast('Asking Common Crawl - it can take half a minute...');
      call('/api/domain/enrich', body).then(function (r) {
        if (el) el.disabled = false;
        if (!r.ok) return toast(r.error || 'Common Crawl is not reachable', 'err');
        if (!r.checked) {
          toast(r.note || ('No answer from the index' + (r.errors && r.errors.length
            ? ': ' + r.errors[0] : '')), 'err');
        } else {
          toast(r.hits + ' of ' + r.checked + ' have captures' +
            (r.errors && r.errors.length ? ' · ' + r.errors.length + ' timed out' : ''));
        }
        loadDomains();
      });
    },
    'domain-scrape': function (el) {
      var domain = el.dataset.domain;
      var label = el.textContent;
      el.disabled = true;
      el.textContent = 'Reading...';
      call('/api/domain/scrape', { domain: domain }).then(function (r) {
        el.disabled = false;
        el.textContent = label;
        if (!r.ok) return toast(r.error || 'Could not read that site', 'err');
        scrapeModal(r);
        loadDomains();
      });
    },
    'add-scraped': function (el) {
      call('/api/lead/save', {
        email: el.dataset.email, domain: el.dataset.domain, source: 'crawl'
      }).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        el.disabled = true;
        el.textContent = 'Added';
        toast('Lead added');
      });
    },
    export: function () { window.location.href = '/export.csv'; },
    'export-parquet': function () { window.location.href = '/export.parquet'; },
    'export-duckdb': function () { window.location.href = '/export.duckdb'; },
    'add-lead': function () { leadForm(null); },
    'new-sequence': function () { sequenceEditor(null); },
    'new-box': function () { mailboxForm(null); },
    'enroll-sel': function () {
      if (!state.selection.size) return toast('Select some leads first');
      enrollModal(null, Array.from(state.selection));
    },
    'delete-sel': function () {
      if (!state.selection.size) return toast('Select some leads first');
      if (!confirm('Delete ' + state.selection.size + ' lead(s)? This cannot be undone.')) return;
      call('/api/lead/delete', { ids: Array.from(state.selection) }).then(function (r) {
        if (r.ok) { toast('Deleted ' + r.deleted); state.selection.clear(); refresh(); loadLeads(); }
        else toast(r.error, 'err');
      });
    },
    verify: function () {
      toast('Checking addresses...');
      call('/api/lead/verify', { limit: state.selection.size ? 500 : 200, ids: Array.from(state.selection) })
        .then(function (r) {
          if (!r.ok) return toast(r.error, 'err');
          toast('Checked ' + r.checked + ': ' + (r.results.valid || 0) + ' valid, ' +
            (r.results.risky || 0) + ' risky, ' + (r.results.invalid || 0) + ' invalid');
          refresh(); loadLeads();
        });
    },
    tick: function () {
      toast('Running the queue...');
      call('/api/send/tick', { force: false }).then(function (r) {
        if (r.ok && r.action === 'sent') toast('Sent to ' + r.to);
        else toast(r.reason || r.error || 'Nothing due');
        refresh();
      });
    },
    sync: function () {
      toast('Checking the inbox...');
      call('/api/inbox/sync', {}).then(function (r) {
        if (!r.ok) return toast(r.error || 'Sync failed', 'err');
        var msg = r.new_replies + ' new repl' + (r.new_replies === 1 ? 'y' : 'ies') + ', ' +
          r.new_bounces + ' bounce(s)';
        toast((r.errors && r.errors.length) ? msg + ' · ' + r.errors[0] : msg,
          (r.errors && r.errors.length) ? 'err' : 'ok');
        loadInbox(); refresh();
      });
    },
    events: loadEvents,
    'run-due': function () {
      call('/api/jobs').then(function (r) {
        var due = (r.jobs || []).filter(function (j) { return j.enabled && j.next_run_ts &&
          j.next_run_ts <= Math.floor(Date.now() / 1000); });
        if (!due.length) return toast('Nothing is due right now.');
        toast('Running ' + due.length + ' due job(s)...');
        var chain = Promise.resolve();
        due.forEach(function (j) {
          chain = chain.then(function () { return call('/api/jobs/run', { key: j.key }); });
        });
        chain.then(function () { toast('Due jobs finished'); refresh(); });
      });
    },
    'run-all': function () {
      if (!confirm('Run every enabled job now? Some download tens of megabytes.')) return;
      call('/api/jobs').then(function (r) {
        var list = (r.jobs || []).filter(function (j) { return j.enabled && j.available; });
        toast('Running ' + list.length + ' jobs, this can take a while...');
        var chain = Promise.resolve();
        list.forEach(function (j) {
          chain = chain.then(function () { return call('/api/jobs/run', { key: j.key }); });
        });
        chain.then(function () { toast('All jobs finished'); refresh(); loadJobs(); });
      });
    },
    'signals-seen': function () {
      call('/api/signals/seen', { all: true }).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        toast('Marked as seen'); loadSignals(); refresh();
      });
    },
    'add-brand': function () {
      var domain = ($('#brand-domain') || {}).value || '';
      var name = ($('#brand-name') || {}).value || '';
      if (!domain.trim()) return toast('Enter a domain first', 'err');
      call('/api/brand/save', { name: name.trim(), domain: domain.trim() }).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        toast('Brand added');
        $('#brand-domain').value = '';
        $('#brand-name').value = '';
        loadBrands(); refresh();
      });
    },
    'import-brands': importBrands,
    'new-offer': function () { offerEditor(null); },
    'run-match': runMatch,
    'offer-pack': function () {
      call('/api/offer/import', { pack: true }).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        toast('Starter pack: ' + r.added + ' added, ' + r.skipped + ' already there');
        loadOffers(); refresh();
      });
    },
    'offer-import': function () {
      openModal({
        title: 'Import offers (JSON)',
        subtitle: 'Paste an array of offers, or a {"offers": [...]} object.',
        body: '<div class="field"><textarea id="oi-text" rows="12" placeholder=\'[{"name":"...","affiliate_url":"https://...","keywords":"accounting, payroll"}]\'></textarea>' +
          '<div class="hint">Required: name. Everything else is optional. Duplicate names or links are skipped.</div></div>',
        footer: '<button class="btn" data-close>Cancel</button>' +
          '<button class="btn primary" id="oi-go">Import</button>',
        onMount: function (root) {
          $('#oi-go', root).onclick = function () {
            call('/api/offer/import', { text: $('#oi-text', root).value }).then(function (r) {
              if (!r.ok) return toast(r.error, 'err');
              toast(r.added + ' added, ' + r.skipped + ' skipped');
              closeModal(); loadOffers(); refresh();
            });
          };
        }
      });
    },
    'llm-add': function () {
      var payload = {
        name: ($('#llm-name') || {}).value,
        model: ($('#llm-model') || {}).value,
        base_url: ($('#llm-base') || {}).value,
        api_key: ($('#llm-key') || {}).value
      };
      if (!payload.name || !payload.model || !payload.base_url) {
        return toast('Name, model and base URL are required', 'err');
      }
      call('/api/llm/save', payload).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        toast('Provider added');
        ['#llm-name', '#llm-model', '#llm-base', '#llm-key'].forEach(function (sel) {
          if ($(sel)) $(sel).value = '';
        });
        loadLlm().then(function () { render(); });
        refresh();
      });
    },
    demo: function () {
      var has = state.data.stats.leads_total > 0;
      call(has ? '/api/demo/remove' : '/api/demo/seed', {}).then(function (r) {
        if (!r.ok) return toast(r.error, 'err');
        toast(has ? 'Demo data removed' : 'Demo data loaded');
        refresh();
      });
    },
    'save-settings': function () {
      var conn = {};
      [['set-hunter', 'hunter_key'], ['set-aikey', 'ai_key'], ['set-serper', 'serper_key'], ['set-treg', 'treg_key']]
        .forEach(function (pair) {
          var el = $('#' + pair[0]);
          if (el && el.value.trim()) conn[pair[1]] = el.value.trim();
        });
      var aiBase = $('#set-aibase'), aiModel = $('#set-aimodel');
      if (aiBase) conn.ai_base = aiBase.value.trim();
      if (aiModel) conn.ai_model = aiModel.value.trim();
      var oauthCfg = {
        google_client_id: ($('#set-gcid') || {}).value || '',
        microsoft_client_id: ($('#set-mcid') || {}).value || ''
      };
      if (($('#set-gsecret') || {}).value) oauthCfg.google_client_secret = $('#set-gsecret').value;
      if (($('#set-msecret') || {}).value) oauthCfg.microsoft_client_secret = $('#set-msecret').value;
      var payload = {
        sender_name: $('#set-sender').value.trim(),
        company_name: $('#set-company').value.trim(),
        contact_email: ($('#set-contact') || {}).value ? $('#set-contact').value.trim() : '',
        offers_locked: $('#set-offers-locked') ? $('#set-offers-locked').checked : true,
        sending: {
          enabled: $('#set-enabled').checked,
          window_start: Number($('#set-ws').value || 8),
          window_end: Number($('#set-we').value || 18),
          gap_seconds: Number($('#set-gap').value || 45),
          jitter_seconds: Number($('#set-jitter').value || 30),
          weekdays_only: $('#set-weekdays').checked
        },
        tracking: { enabled: $('#set-track').checked, base_url: $('#set-track-base').value.trim() },
        verify: { mx: $('#set-mx').checked, smtp_probe: $('#set-smtp').checked },
        connectors: conn,
        oauth: oauthCfg,
        onboarded: true
      };
      call('/api/settings/save', { settings: payload }).then(function (r) {
        if (r.ok) { toast('Settings saved'); refresh(); } else toast(r.error, 'err');
      });
    },
    wipe: function () {
      if (!confirm('Delete every lead, sequence, email and reply on this computer? There is no undo.')) return;
      if (!confirm('Really delete everything? Export a CSV first if you are unsure.')) return;
      call('/api/wipe', {}).then(function (r) {
        if (r.ok) { toast('All data deleted'); refresh(); } else toast(r.error || 'Failed', 'err');
      });
    },
    diag: function () {
      call('/api/diagnose').then(function (r) {
        var box = $('#diag-box');
        if (!box) return;
        box.innerHTML = '<div class="mono small">' + h(JSON.stringify(r, null, 2)) + '</div>';
      });
    }
  };

  // ---- lead form
  function leadForm(id) {
    var lead = null;
    if (id) {
      lead = state.leads.items.filter(function (x) { return x.id === id; })[0] || null;
    }
    var f = lead || {};
    openModal({
      title: lead ? 'Edit lead' : 'Add a lead',
      body: '<div class="row"><div class="field"><label>Email <span class="dim">(optional)</span></label>' +
        '<input type="email" id="lf-email" value="' + h(f.email || '') + '" ' +
        'placeholder="name@company.com"></div>' +
        '<div class="field"><label>Status</label><select id="lf-status">' +
        ['new', 'contacted', 'replied', 'interested', 'won', 'lost', 'bounced', 'unsubscribed'].map(function (s) {
          return '<option value="' + s + '"' + (f.status === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="row"><div class="field"><label>First name</label>' +
        '<input type="text" id="lf-first" value="' + h(f.first_name || '') + '"></div>' +
        '<div class="field"><label>Last name</label>' +
        '<input type="text" id="lf-last" value="' + h(f.last_name || '') + '"></div></div>' +
        '<div class="row"><div class="field"><label>Company</label>' +
        '<input type="text" id="lf-company" value="' + h(f.company || '') + '"></div>' +
        '<div class="field"><label>Job title</label>' +
        '<input type="text" id="lf-title" value="' + h(f.title || '') + '"></div></div>' +
        '<div class="row"><div class="field"><label>Domain</label>' +
        '<input type="text" id="lf-domain" value="' + h(f.domain || '') + '"></div>' +
        '<div class="field"><label>Website</label>' +
        '<input type="text" id="lf-website" value="' + h(f.website || '') + '" placeholder="https://..."></div></div>' +
        '<div class="row"><div class="field"><label>City</label>' +
        '<input type="text" id="lf-city" value="' + h(f.city || '') + '"></div>' +
        '<div class="field"><label>Country</label>' +
        '<input type="text" id="lf-country" value="' + h(f.country || '') + '"></div></div>' +
        '<div class="row"><div class="field"><label>Phone</label>' +
        '<input type="text" id="lf-phone" value="' + h(f.phone || '') + '"></div>' +
        '<div class="field"><label>Industry</label>' +
        '<input type="text" id="lf-industry" value="' + h(f.industry || '') + '" placeholder="accounting"></div></div>' +
        '<div class="row"><div class="field"><label>Company size</label><select id="lf-size">' +
        ['', '1-10', '11-50', '51-200', '201-500', '501-1000', '1000+'].map(function (size) {
          return '<option value="' + size + '"' + (f.company_size === size ? ' selected' : '') +
            '>' + (size || '- unknown -') + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><label>Technologies <span class="dim">(comma separated)</span></label>' +
        '<input type="text" id="lf-tech" value="' + h(f.technologies || '') +
        '" placeholder="shopify, klaviyo"></div></div>' +
        '<div class="row"><div class="field"><label>LinkedIn</label>' +
        '<input type="text" id="lf-linkedin" value="' + h(f.linkedin || '') + '"></div>' +
        '<div class="field"><label>Tags</label>' +
        '<input type="text" id="lf-tags" value="' + h(f.tags || '') + '" placeholder="comma, separated"></div></div>' +
        '<div class="field"><label>Description <span class="dim">(what the company does)</span></label>' +
        '<textarea id="lf-description" rows="3">' + h(f.description || '') + '</textarea></div>' +
        '<div class="field"><label>Notes</label><textarea id="lf-notes" rows="2">' + h(f.notes || '') + '</textarea></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="lf-save">' + (lead ? 'Save changes' : 'Add lead') + '</button>',
      onMount: function (root) {
        $('#lf-save', root).onclick = function () {
          // Every field the form shows is sent back: the API only writes what it receives.
          var body = {
            id: lead ? lead.id : null,
            email: $('#lf-email', root).value,
            status: $('#lf-status', root).value,
            first_name: $('#lf-first', root).value,
            last_name: $('#lf-last', root).value,
            company: $('#lf-company', root).value,
            title: $('#lf-title', root).value,
            domain: $('#lf-domain', root).value,
            website: $('#lf-website', root).value,
            city: $('#lf-city', root).value,
            country: $('#lf-country', root).value,
            phone: $('#lf-phone', root).value,
            industry: $('#lf-industry', root).value,
            company_size: $('#lf-size', root).value,
            technologies: $('#lf-tech', root).value,
            linkedin: $('#lf-linkedin', root).value,
            tags: $('#lf-tags', root).value,
            description: $('#lf-description', root).value,
            notes: $('#lf-notes', root).value
          };
          call('/api/lead/save', body).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Saved'); closeModal(); refresh(); loadLeads();
          });
        };
      }
    });
  }
  function editLead(id) { leadForm(id); }

  // ---- import
  function impCallout(r) {
    if (!r.ok) {
      return '<div class="callout" style="border-left-color:var(--red)"><strong>Import failed.</strong> ' +
        h(r.error || 'Unknown error') + '</div>';
    }
    return '<div class="callout green"><strong>' + num(r.imported) + ' new contacts imported.</strong> ' +
      (r.no_email ? num(r.no_email) + ' of them have no email yet. ' : '') +
      (r.existing_email ? num(r.existing_email) + ' already existed. ' : '') +
      (r.duplicates ? num(r.duplicates) + ' duplicate rows in the file. ' : '') +
      (r.invalid ? num(r.invalid) + ' invalid addresses. ' : '') +
      (r.updated ? num(r.updated) + ' existing leads updated. ' : '') +
      (r.skipped_blank ? num(r.skipped_blank) + ' rows had nothing to identify them. ' : '') +
      (r.lead_domains ? num(r.lead_domains) + ' of their domains added to the catalogue. ' : '') +
      (r.stopped_at_limit ? 'Stopped at the row limit. ' : '') +
      '<div class="small dim" style="margin-top:6px">Mapped: ' +
      h(Object.keys(r.mapped || {}).join(', ') || 'nothing') +
      (r.table ? ' &middot; table: ' + h(r.table) : '') +
      (r.examined ? ' &middot; ' + num(r.examined) + ' rows read' : '') + '</div></div>';
  }

  function importModal(dropFile, startOnPath) {
    openModal({
      title: 'Import contacts',
      subtitle: 'CSV, TSV, JSON, Excel, Parquet, DuckDB or SQLite. An email is welcome but no longer ' +
        'required: a company with a description is a contact too.',
      body: '<div class="sv-tabs" style="margin-bottom:14px">' +
        '<button class="sv-tab on" data-imp-mode="upload">Upload a file</button>' +
        '<button class="sv-tab" data-imp-mode="path">File on this computer</button>' +
        '</div>' +
        '<div id="imp-upload">' +
        '<div class="drop" id="drop"><div class="big">Drop a file here</div>' +
        '<div class="small">or click to choose - CSV, TSV, JSON, Excel, Parquet, DuckDB, SQLite</div>' +
        '<div class="progress hidden" id="imp-progress"><div class="fill" id="imp-fill"></div></div></div>' +
      '<div id="imp-upload-result" class="mt"></div>' +
        '</div>' +
        '<div id="imp-path" class="hidden">' +
        '<div class="field"><label>Path on this computer</label>' +
        '<div class="row"><div class="grow"><input type="text" id="imp-path-input" ' +
        'placeholder="C:\\Users\\...\\leads.parquet"></div>' +
        '<button class="btn" id="imp-browse" style="flex:0 0 auto" ' +
        'title="Find the file in a window">Browse...</button></div>' +
        '<div class="hint">The window opens on this computer. A big file is read where it is, ' +
        'nothing is copied.</div></div>' +
        '<div class="toolbar"><button class="btn" id="imp-check">Check the file</button>' +
        '<span class="small dim" id="imp-info"></span></div>' +
        '<div id="imp-tables"></div>' +
        '<div class="row"><div class="field"><label>Filter <span class="dim">(SQL WHERE, DuckDB ' +
        'or SQLite only)</span></label><input type="text" id="imp-where" ' +
        'placeholder="best_email IS NOT NULL"></div>' +
        '<label class="check"><input type="checkbox" id="imp-only-email"> ' +
        'Only rows that have an email</label>' +
        '<div class="toolbar" style="margin-top:12px">' +
        '<button class="btn primary" id="imp-run">Import from this file</button></div>' +
        '</div>' +
        '<div id="imp-result" class="mt"></div>' +
        '<div class="sep"></div><div class="small dim">Recognised columns: email, first name, ' +
        'last name, company, title, domain, website, description (the company description), ' +
        'phone, city, country, industry, linkedin, notes, tags, source. Duplicates are skipped, ' +
        'and every row needs an email, a company, a domain or a name to identify it.</div>' +
        '<label class="check mt"><input type="checkbox" id="imp-update"> ' +
        'Fill empty fields on existing leads</label>',
      footer: '<button class="btn" data-close>Done</button>',
      onMount: function (root) {
        var modeButtons = $$('[data-imp-mode]', root);
        modeButtons.forEach(function (button) {
          button.onclick = function () {
            modeButtons.forEach(function (x) { x.classList.toggle('on', x === button); });
            $('#imp-upload', root).classList.toggle('hidden', button.dataset.impMode !== 'upload');
            $('#imp-path', root).classList.toggle('hidden', button.dataset.impMode !== 'path');
          };
        });

        if (startOnPath) {
          modeButtons.forEach(function (button) {
            if (button.dataset.impMode === 'path' && button.onclick) button.onclick();
          });
        }

        // ---- upload
        var drop = $('#drop', root);
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = '.csv,.tsv,.txt,.json,.xlsx,.xlsm,.parquet,.duckdb,.ddb,.db,.sqlite,.sqlite3';
        input.style.display = 'none';
        root.appendChild(input);
        drop.onclick = function () { input.click(); };
        drop.ondragover = function (e) { e.preventDefault(); drop.classList.add('over'); };
        drop.ondragleave = function () { drop.classList.remove('over'); };
        drop.ondrop = function (e) {
          e.preventDefault(); drop.classList.remove('over');
          if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        };
        input.onchange = function () { if (input.files.length) handleFile(input.files[0]); };

        function handleFile(file) {
          var prog = $('#imp-progress', root), fill = $('#imp-fill', root);
          var box = $('#imp-upload-result', root);
          if (prog) prog.classList.remove('hidden');
          if (fill) fill.style.width = '20%';
          if (box) {
            box.innerHTML = '<div class="small dim">Reading <b>' + h(file.name) + '</b> (' +
              Math.round(file.size / 1024) + ' KB)...</div>';
          }
          var reader = new FileReader();
          reader.onload = function () {
            var bytes = new Uint8Array(reader.result);
            var b64 = '';
            for (var i = 0; i < bytes.length; i += 0x8000) {
              b64 += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            }
            b64 = btoa(b64);
            if (fill) fill.style.width = '60%';
            startImport({
              name: file.name, b64: b64,
              update_existing: $('#imp-update', root).checked,
              only_email: $('#imp-only-email', root).checked
            }, root, 'imp-upload-result');
          };
          reader.readAsArrayBuffer(file);
        }

        // ---- a file on this computer (the LEAD_INDEX files are gigabytes)
        $('#imp-browse', root).onclick = function () {
          var button = this;
          var label = button.textContent;
          button.disabled = true;
          button.textContent = 'Opening...';
          call('/api/dialog/pick', {
            title: 'Choose a file to import',
            extensions: ['csv', 'tsv', 'txt', 'xlsx', 'xlsm', 'parquet', 'duckdb', 'ddb',
                         'db', 'sqlite', 'sqlite3']
          }).then(function (r) {
            button.disabled = false;
            button.textContent = label;
            if (!r.ok) return toast(r.error || 'Could not open the file dialog', 'err');
            if (r.cancelled || !r.path) return;
            $('#imp-path-input', root).value = r.path;
            check();
          });
        };
        $('#imp-check', root).onclick = function () { check(); };
        $('#imp-run', root).onclick = function () { run(); };

        function check() {
          var path = $('#imp-path-input', root).value.trim();
          if (!path) return toast('Paste the path to a file first', 'err');
          var box = $('#imp-tables', root);
          box.innerHTML = '<div class="small dim">Reading the file...</div>';
          call('/api/lead/inspect', { path: path }).then(function (r) {
            if (!r.ok) {
              box.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
                h(r.error) + '</div>';
              return;
            }
            showInfo(r);
          });
        }

        function showInfo(info) {
          var box = $('#imp-tables', root);
          var head = '<div class="small dim">' + h(info.name) + ' &middot; ' + h(info.kind) +
            ' &middot; ' + info.size_mb + ' MB' +
            (info.rows ? ' &middot; ' + num(info.rows) + ' rows' : '') + '</div>';
          var tables = (info.tables && info.tables.length > 1)
            ? '<div class="field mt"><label>Table</label><select id="imp-table">' +
              info.tables.map(function (t) {
                return '<option value="' + h(t.name) + '"' +
                  (t.name === info.table ? ' selected' : '') + '>' + h(t.name) +
                  (t.rows ? '  (' + num(t.rows) + ')' : '') + '</option>';
              }).join('') + '</select></div>'
            : '';
          var columns = (info.columns && info.columns.length)
            ? '<div class="small dim mt">Columns: ' +
              h(info.columns.slice(0, 20).join(', ')) +
              (info.columns.length > 20 ? ', ...' : '') + '</div>'
            : '';
          box.innerHTML = head + tables + columns;
          var select = $('#imp-table', root);
          if (select) {
            select.onchange = function () {
              call('/api/lead/inspect', { path: info.path, table: select.value })
                .then(function (next) { if (next.ok) showInfo(next); });
            };
          }
        }

        if (dropFile) setTimeout(function () { handleFile(dropFile); }, 60);

        function run() {
          var path = $('#imp-path-input', root).value.trim();
          if (!path) return toast('Paste the path to a file first', 'err');
          var body = {
            path: path,
            limit: 0,
            where: $('#imp-where', root).value,
            restart: $('#imp-restart', root).checked,
            update_existing: $('#imp-update', root).checked,
            only_email: $('#imp-only-email', root).checked
          };
          var select = $('#imp-table', root);
          if (select) body.table = select.value;
          startImport(body, root);
        }

        function startImport(body, scope, boxId) {
          var box = $('#' + (boxId || 'imp-result'), scope), bar = $('#imp-progress', scope), fill = $('#imp-fill', scope);
          if (bar) bar.classList.remove('hidden');
          if (fill) fill.style.width = '2%';
          if (box) box.innerHTML = '<div class="small dim">Starting the import...</div>';
          call('/api/lead/import' + (body.path ? '/path' : ''), body).then(function (r) {
            if (!r.ok || !r.import_id) {
              if (box) {
                box.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
                  h(r.error || 'The import did not start') + '</div>';
              }
              return;
            }
            watchImport(r.import_id, scope, boxId);
          });
        }

        function watchImport(importId, scope, boxId) {
          var box = $('#' + (boxId || 'imp-result'), scope), bar = $('#imp-progress', scope), fill = $('#imp-fill', scope);
          if (bar) bar.classList.remove('hidden');
          var timer = setInterval(function () {
            call('/api/import/progress', { id: importId }).then(function (r) {
              if (!r.ok) {
                clearInterval(timer);
                if (box) box.innerHTML = '<div class="callout">' + h(r.error) + '</div>';
                return;
              }
              var percent = r.percent || 0;
              if (fill) fill.style.width = Math.max(2, percent) + '%';
              if (r.finished) {
                clearInterval(timer);
                if (fill) fill.style.width = '100%';
                if (r.error) {
                  box.innerHTML = '<div class="callout" style="border-left-color:var(--red)">' +
                    h(r.error) + '</div>';
                  return;
                }
                var result = r.result || {};
                box.innerHTML = impCallout(result);
                if (result.ok) {
                  toast(num(result.imported || 0) + ' contacts imported');
                  refresh();
                  loadLeads();
                }
                return;
              }
              var parts = [r.phase || 'working'];
              if (percent) parts.push(percent + '%');
              if (r.rows) parts.push(num(r.rows) + ' rows');
              if (r.total > 1) {
                parts.push(num(Math.round(r.done)) + ' / ' + num(Math.round(r.total)));
              }
              if (r.eta > 0) parts.push(fmtEta(r.eta) + ' left');
              else if (r.rate) parts.push(num(Math.round(r.rate)) + ' a second');
              if (box) box.innerHTML = '<div class="small dim">' + h(parts.join(' · ')) + '</div>';
            });
          }, 500);
        }
      }
    });
  }

  // ---- bulk data (the files too big for the folder, published on Hugging Face)
  function bulkDataModal() {
    var hf = (state.data && state.data.hf) || {};
    var files = hf.files || [];
    openModal({
      title: 'Bulk data on Hugging Face',
      subtitle: 'The two files too big for this folder: the scraped contact table and the domain list.',
      body: '<div class="small muted">Published as a public dataset at ' +
        '<a href="' + h(hf.url || '#') + '" target="_blank" rel="noopener">' +
        h(hf.dataset || 'huggingface.co') + '</a>. The Common Crawl job reads the host index ' +
        'from the Hub as well, so new domains keep arriving.</div>' +
        '<div class="list mt">' + files.map(function (f) {
          return '<div class="list-item"><div class="avatar">' +
            h((f.name || '?').slice(0, 2).toUpperCase()) + '</div>' +
            '<div class="grow"><div><strong>' + h(f.label || f.name) + '</strong> ' +
            '<span class="mono small dim">' + h(f.name) + '</span></div>' +
            '<div class="small dim">' + h(f.note || '') + '</div></div>' +
            '<a class="btn sm" href="' + h(f.url || '#') + '" target="_blank" rel="noopener">' +
            'Download</a></div>';
        }).join('') + '</div>' +
        '<div class="small dim mt">Downloads land in your browser folder. To use one, ' +
        'Leads &rarr; Import &rarr; File on this computer, and point it at the file.</div>',
      footer: '<button class="btn" data-close>Close</button>'
    });
  }

  // ---- sequence editor
  function sequenceEditor(id) {
    var seq = id ? (state.data.sequences.filter(function (s) { return s.id === id; })[0]) : null;
    var steps = seq ? seq.steps.map(function (s) {
      return { delay_days: s.delay_days, subject: s.subject, body: s.body };
    }) : [{ delay_days: 0, subject: '', body: '' }];

    function stepsHtml() {
      return steps.map(function (st, i) {
        return '<div class="step-card" data-step="' + i + '"><div class="step-head">' +
          '<span class="step-no">' + (i + 1) + '</span>' +
          '<div class="step-delay">' + (i === 0 ? 'sends right away' : 'wait') + '</div>' +
          (i > 0 ? '<input type="number" min="0" max="90" style="width:70px" data-delay="' + i + '" value="' + st.delay_days + '">' : '') +
          (i > 0 ? '<span class="step-delay">days after the previous one</span>' : '') +
          '<div class="step-actions"><button class="btn sm ghost" data-add-step="' + i + '">+ step below</button>' +
          (steps.length > 1 ? '<button class="btn sm danger" data-del-step="' + i + '">Remove</button>' : '') +
          '</div></div>' +
          '<div class="field"><input type="text" data-subject="' + i + '" placeholder="Subject" value="' + h(st.subject) + '"></div>' +
          '<div class="field" style="margin:0"><textarea data-body="' + i + '" rows="6" placeholder="Body">' + h(st.body) + '</textarea>' +
          '<div class="merge-chips">' + ['first_name|there', 'company', 'title', 'city', 'sender_name']
            .map(function (m) {
              return '<span class="chip" data-chip="' + i + '">{{' + m + '}}</span>';
            }).join('') + '</div></div></div>';
      }).join('');
    }

    openModal({
      wide: true,
      title: seq ? 'Edit sequence' : 'New sequence',
      subtitle: 'Each step sends after the delay you set. Replies stop the sequence automatically.',
      body: '<div class="row"><div class="field"><label>Sequence name</label>' +
        '<input type="text" id="sq-name" value="' + h(seq ? seq.name : '') + '" placeholder="Cold outreach - 3 touches"></div>' +
        '<div class="field"><label>Description</label>' +
        '<input type="text" id="sq-desc" value="' + h(seq ? seq.description : '') + '" placeholder="Optional"></div></div>' +
        '<div id="sq-steps" class="steps">' + stepsHtml() + '</div>' +
        '<div class="toolbar mt"><button class="btn sm" id="sq-add">+ Add step</button></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="sq-save">Save sequence</button>',
      onMount: function (root) {
        var box = $('#sq-steps', root);
        function syncFromDom() {
          $$('[data-subject]', box).forEach(function (el) {
            steps[Number(el.dataset.subject)].subject = el.value;
          });
          $$('[data-body]', box).forEach(function (el) {
            steps[Number(el.dataset.body)].body = el.value;
          });
          $$('[data-delay]', box).forEach(function (el) {
            steps[Number(el.dataset.delay)].delay_days = Math.max(0, Number(el.value) || 0);
          });
        }
        function repaint() { box.innerHTML = stepsHtml(); }
        box.addEventListener('click', function (e) {
          var t = e.target;
          if (t.dataset.addStep !== undefined) {
            syncFromDom();
            var at = Number(t.dataset.addStep);
            steps.splice(at + 1, 0, { delay_days: 3, subject: '', body: '' });
            repaint();
          } else if (t.dataset.delStep !== undefined) {
            syncFromDom();
            steps.splice(Number(t.dataset.delStep), 1);
            repaint();
          } else if (t.dataset.chip !== undefined) {
            var i = Number(t.dataset.chip);
            var ta = box.querySelector('[data-body="' + i + '"]');
            var chip = t.textContent;
            var pos = ta.selectionStart || 0;
            ta.value = ta.value.slice(0, pos) + chip + ta.value.slice(ta.selectionEnd || pos);
            steps[i].body = ta.value;
            ta.focus();
          }
        });
        $('#sq-add', root).onclick = function () {
          syncFromDom();
          var last = steps[steps.length - 1];
          steps.push({ delay_days: Math.max(3, (last.delay_days || 0) + 3), subject: '', body: '' });
          repaint();
        };
        $('#sq-save', root).onclick = function () {
          syncFromDom();
          var body = {
            id: seq ? seq.id : null,
            name: $('#sq-name', root).value.trim(),
            description: $('#sq-desc', root).value.trim(),
            steps: steps.filter(function (s) { return (s.subject || '').trim() || (s.body || '').trim(); })
          };
          if (!body.name) return toast('Give the sequence a name', 'err');
          if (!body.steps.length) return toast('Add at least one step with a subject or body', 'err');
          call('/api/sequence/save', body).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Sequence saved'); closeModal(); refresh();
          });
        };
      }
    });
  }

  // ---- enroll
  function enrollModal(sequenceId, preselected) {
    var d = state.data;
    if (!d.sequences.length) return toast('Create a sequence first', 'err');
    var seqs = d.sequences;
    var boxes = d.mailboxes.filter(function (b) { return b.active; });
    var selected = preselected || [];
    var newCount = d.stats.leads_new;
    openModal({
      title: 'Enroll leads',
      body: '<div class="field"><label>Sequence</label><select id="en-seq">' +
        seqs.map(function (s) {
          return '<option value="' + s.id + '"' + (sequenceId === s.id ? ' selected' : '') + '>' +
            h(s.name) + ' (' + s.steps.length + ' steps)</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><label>Who</label>' +
        (selected.length ? '<label class="check"><input type="radio" name="who" value="sel" checked> ' +
          selected.length + ' selected lead(s)</label>' : '') +
        '<label class="check"><input type="radio" name="who" value="new"' + (selected.length ? '' : ' checked') + '> ' +
        'All ' + newCount + ' leads with status "new"</label></div>' +
        '<div class="field"><label>Send from</label><select id="en-box">' +
        (boxes.length ? boxes.map(function (b) {
          return '<option value="' + b.id + '">' + h(b.name) + ' — ' + b.sent_today + '/' + b.daily_limit + ' today</option>';
        }).join('') : '<option value="0">No mailbox configured yet</option>') + '</select>' +
        '<div class="hint">Only one mailbox sends per enrollment. Add more later for rotation.</div></div>' +
        (boxes.length ? '' : '<div class="callout"><strong>No mailbox yet.</strong> You can enroll leads now, but ' +
          'nothing sends until you add SMTP details in Mailboxes.</div>'),
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn primary" id="en-go">Enroll</button>',
      onMount: function (root) {
        $('#en-go', root).onclick = function () {
          var who = (root.querySelector('input[name=who]:checked') || {}).value || 'new';
          var body = {
            sequence_id: Number($('#en-seq', root).value),
            mailbox_id: Number($('#en-box', root).value || 0)
          };
          if (who === 'sel') body.leads = selected;
          else body.filter = 'new';
          call('/api/enroll', body).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Added ' + r.added + ' lead(s), skipped ' + r.skipped);
            closeModal(); refresh();
          });
        };
      }
    });
  }

  // ---- mailbox form
  function mailboxForm(id) {
    var b = id ? state.data.mailboxes.filter(function (x) { return x.id === id; })[0] || {} : {};
    openModal({
      wide: true,
      title: id ? 'Edit mailbox' : 'Add a mailbox',
      subtitle: 'Use an app password, not your normal login.',
      body: '<div class="row"><div class="field"><label>Name</label>' +
        '<input type="text" id="mb-name" value="' + h(b.name || '') + '" placeholder="Sales inbox"></div>' +
        '<div class="field"><label>From email</label>' +
        '<input type="email" id="mb-email" value="' + h(b.from_email || '') + '" placeholder="you@company.com"></div></div>' +
        '<div class="row"><div class="field"><label>From name</label>' +
        '<input type="text" id="mb-fromname" value="' + h(b.from_name || '') + '" placeholder="Your name"></div>' +
        '<div class="field"><label>Reply-to (optional)</label>' +
        '<input type="text" id="mb-replyto" value="' + h(b.reply_to || '') + '"></div></div>' +
        '<h3 style="margin:6px 0 10px">SMTP - sending</h3>' +
        '<div class="row"><div class="field"><label>Host</label>' +
        '<input type="text" id="mb-smtp" value="' + h(b.smtp_host || '') + '" placeholder="smtp.gmail.com"></div>' +
        '<div class="field" style="max-width:110px"><label>Port</label>' +
        '<input type="number" id="mb-smtpport" value="' + h(b.smtp_port || 587) + '"></div>' +
        '<div class="field" style="max-width:150px"><label>Security</label><select id="mb-smtpsec">' +
        ['starttls', 'ssl', 'none'].map(function (s) {
          return '<option value="' + s + '"' + (b.smtp_security === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="row"><div class="field"><label>Username</label>' +
        '<input type="text" id="mb-smtpuser" value="' + h(b.smtp_user || '') + '"></div>' +
        '<div class="field"><label>Password ' + (b.has_smtp_pass ? '<span class="badge green">saved</span>' : '') + '</label>' +
        '<input type="password" id="mb-smtppass" placeholder="' + (b.has_smtp_pass ? 'leave blank to keep' : 'app password') + '"></div></div>' +
        '<h3 style="margin:6px 0 10px">IMAP - reading replies</h3>' +
        '<div class="row"><div class="field"><label>Host</label>' +
        '<input type="text" id="mb-imap" value="' + h(b.imap_host || '') + '" placeholder="imap.gmail.com"></div>' +
        '<div class="field" style="max-width:110px"><label>Port</label>' +
        '<input type="number" id="mb-imapport" value="' + h(b.imap_port || 993) + '"></div></div>' +
        '<div class="row"><div class="field"><label>Username</label>' +
        '<input type="text" id="mb-imapuser" value="' + h(b.imap_user || '') + '"></div>' +
        '<div class="field"><label>Password ' + (b.has_imap_pass ? '<span class="badge green">saved</span>' : '') + '</label>' +
        '<input type="password" id="mb-imappass" placeholder="' + (b.has_imap_pass ? 'leave blank to keep' : 'app password') + '"></div></div>' +
        '<div class="row"><div class="field"><label>Daily send limit</label>' +
        '<input type="number" id="mb-limit" min="1" max="500" value="' + h(b.daily_limit || 30) + '">' +
        '<div class="hint">Start low. Raise it over a few weeks.</div></div>' +
        '<div class="field"><label>Signature</label>' +
        '<textarea id="mb-sig" rows="3" placeholder="Appended to every email">' + h(b.signature || '') + '</textarea></div></div>',
      footer: '<button class="btn" data-close>Cancel</button>' +
        '<button class="btn" id="mb-test">Test connection</button>' +
        '<button class="btn primary" id="mb-save">Save mailbox</button>',
      onMount: function (root) {
        function payload() {
          return {
            id: b.id || null, name: $('#mb-name', root).value, from_email: $('#mb-email', root).value,
            from_name: $('#mb-fromname', root).value, reply_to: $('#mb-replyto', root).value,
            smtp_host: $('#mb-smtp', root).value, smtp_port: Number($('#mb-smtpport', root).value || 587),
            smtp_security: $('#mb-smtpsec', root).value, smtp_user: $('#mb-smtpuser', root).value,
            smtp_pass: $('#mb-smtppass', root).value, imap_host: $('#mb-imap', root).value,
            imap_port: Number($('#mb-imapport', root).value || 993), imap_user: $('#mb-imapuser', root).value,
            imap_pass: $('#mb-imappass', root).value, daily_limit: Number($('#mb-limit', root).value || 30),
            signature: $('#mb-sig', root).value, active: true
          };
        }
        $('#mb-save', root).onclick = function () {
          call('/api/mailbox/save', payload()).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            toast('Mailbox saved'); closeModal(); refresh();
          });
        };
        $('#mb-test', root).onclick = function () {
          var btn = this;
          call('/api/mailbox/save', payload()).then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            btn.disabled = true; btn.textContent = 'Testing...';
            call('/api/mailbox/test', { id: r.id }).then(function (t) {
              btn.disabled = false; btn.textContent = 'Test connection';
              if (t.ok) toast('SMTP ok' + (t.imap === 'ok' ? ', IMAP ok' : '') + '. You are ready to send.');
              else toast((t.smtp_detail || 'SMTP failed') + (t.imap === 'failed' ? ' | IMAP: ' + t.imap_detail : ''), 'err');
              refresh();
            });
          });
        };
      }
    });
  }

  function sendTest(boxId) {
    openModal({
      title: 'Send a test email',
      body: '<div class="field"><label>Send to</label>' +
        '<input type="email" id="ts-to" placeholder="you@company.com"></div>' +
        '<div class="small dim">This sends one real email through the mailbox, so you can confirm it lands ' +
        'in the inbox and not in spam.</div>',
      footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ts-go">Send test</button>',
      onMount: function (root) {
        $('#ts-go', root).onclick = function () {
          var btn = this;
          btn.disabled = true; btn.textContent = 'Sending...';
          call('/api/send/test', { mailbox_id: boxId, to: $('#ts-to', root).value }).then(function (r) {
            btn.disabled = false; btn.textContent = 'Send test';
            if (r.ok) { toast('Test email sent to ' + r.to); closeModal(); }
            else toast(r.error || 'Failed', 'err');
          });
        };
      }
    });
  }

﻿  // ------------------------------------------------------------------ social
  // One workspace, five panes: the composer, the Metricool inbox, the planner, the ad
  // panels and the reporting cards. Everything that needs an account is read through
  // Metricool's MCP server; everything that does not (the renderer) works on its own.
  var SOCIAL = {
    timer: null, task: null, tab: 'create', rendered: false, workspace: null,
    workspaceTried: false, inbox: null, plan: null, ads: null, reports: null,
    demo: false, thread: null, month: 0, form: null, duration: null,
    notes: {}, assign: {}, captionTouched: false
  };

  var SV_TABS = [
    { key: 'create', label: 'Create' },
    { key: 'inbox', label: 'Inbox' },
    { key: 'planner', label: 'Planner' },
    { key: 'ads', label: 'Ads' },
    { key: 'reports', label: 'Reporting' }
  ];

  var SV_NETWORKS = ['instagram', 'facebook', 'tiktok', 'youtube', 'linkedin', 'x'];
  var SV_SECTION_SLOT = { inbox: 'inbox', planner: 'plan', ads: 'ads', reports: 'reports' };

  function loadSocial(rerender) {
    return call('/api/social/state').then(function (r) {
      if (!r.ok) { toast(r.error || 'Could not load the Social tab', 'err'); return; }
      state.social = r;
      if (rerender !== false) { SOCIAL.rendered = false; render(); }
    });
  }

  // ------------------------------------------------------------------ small helpers
  function svVal(id) { var el = $('#' + id); return el ? el.value : ''; }
  function svChecked(id) { var el = $('#' + id); return !!(el && el.checked); }
  function svSet(id, value) { var el = $('#' + id); if (el) el.value = value; }

  function svKind(name) {
    var lower = String(name || '').toLowerCase();
    if (/\.(mp3|wav|m4a|aac|flac|ogg|opus|wma)$/.test(lower)) return 'audio';
    if (/\.(jpg|jpeg|png|webp|bmp)$/.test(lower)) return 'image';
    if (/\.(mp4|mov|webm|mkv)$/.test(lower)) return 'video';
    return '';
  }

  function svFileOptions(items, placeholder, emptyText, selected) {
    var body = items.length ? items.map(function (f) {
      return '<option value="' + h(f.name) + '"' + (f.name === selected ? ' selected' : '') + '>' +
        h(f.name) + '  (' + f.mb + ' MB)</option>';
    }).join('') : '';
    return '<option value="">' + h(items.length ? placeholder : (emptyText || placeholder)) + '</option>' + body;
  }

  function svMediaOptions(v, selected) {
    var html = '<option value="">- none -</option>';
    if (v.images.length) {
      html += '<optgroup label="Pictures">' + v.images.map(function (f) {
        return '<option value="' + h(f.name) + '"' + (f.name === selected ? ' selected' : '') + '>' +
          h(f.name) + '  (' + f.mb + ' MB)</option>';
      }).join('') + '</optgroup>';
    }
    if (v.videos.length) {
      html += '<optgroup label="Videos">' + v.videos.map(function (f) {
        return '<option value="' + h(f.name) + '"' + (f.name === selected ? ' selected' : '') + '>' +
          h(f.name) + '  (' + f.mb + ' MB)</option>';
      }).join('') + '</optgroup>';
    }
    return html;
  }

  function svDraft(key, fallback) {
    if (!SOCIAL.form || SOCIAL.form[key] === undefined) return fallback;
    return SOCIAL.form[key];
  }

  function svCaptureForm() {
    if (!$('#sv-title')) return;
    SOCIAL.form = {
      title: svVal('sv-title'), description: svVal('sv-desc'), caption: svVal('sv-caption'),
      when: svVal('sv-when'), audio: svVal('sv-audio'), media: svVal('sv-media'),
      format: svVal('sv-format'), size: svVal('sv-size'),
      networks: $$('[data-sv-net]').filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; })
    };
  }

  function svParse(text) {
    try { return JSON.parse(text); } catch (e) { return null; }
  }

  // The MCP answer is whatever the tool felt like sending: text, a JSON object, or a
  // list buried somewhere in it. Find the first non-empty array of objects.
  function svFirstList(value, depth) {
    if (depth > 4 || value === null || value === undefined) return null;
    if (Array.isArray(value)) {
      if (value.length && typeof value[0] === 'object' && value[0] !== null) return value;
      for (var i = 0; i < value.length; i += 1) {
        var inner = svFirstList(value[i], depth + 1);
        if (inner) return inner;
      }
      return null;
    }
    if (typeof value !== 'object') return null;
    var keys = Object.keys(value);
    for (var k = 0; k < keys.length; k += 1) {
      var found = svFirstList(value[keys[k]], depth + 1);
      if (found) return found;
    }
    return null;
  }

  function svField(row, keys, fallback) {
    for (var i = 0; i < keys.length; i += 1) {
      var value = row[keys[i]];
      if (value !== undefined && value !== null && value !== '') return value;
    }
    return fallback;
  }

  function svInitials(name) {
    var parts = String(name || '?').trim().split(/\s+/).slice(0, 2);
    return parts.map(function (p) { return p.charAt(0).toUpperCase(); }).join('') || '?';
  }

  function svAgo(minutes) {
    if (minutes === null || minutes === undefined || isNaN(minutes)) return '';
    var m = Math.max(0, Math.round(Number(minutes)));
    if (m < 1) return 'now';
    if (m < 60) return m + 'm';
    if (m < 1440) return Math.round(m / 60) + 'h';
    return Math.round(m / 1440) + 'd';
  }

  function svNetwork(name) {
    var key = String(name || '').toLowerCase();
    return '<span class="sv-net">' + h(key || 'network') + '</span>';
  }

  // Every link that leaves for Metricool goes through the affiliate link, and every one of
  // them is marked rel="sponsored". The disclosure sits right under the connection bar.
  function svMetricoolUrl() {
    var m = (state.social && state.social.metricool) || {};
    return m.signup_url || m.app_url || 'https://app.metricool.com';
  }

  function svLegal() {
    return (state.data && state.data.legal) || {};
  }

  // ------------------------------------------------------------------ sample data
  // Only shown when the operator asks for it, always labelled. It exists so the layout
  // can be read without a connected account, not to look like real numbers.
  function svSampleInbox() {
    return {
      metrics: { first: 42, open: 12, satisfaction: 96, volume: 128 },
      conversations: [
        { id: 'c1', name: 'Petra Kováčová', network: 'instagram', channel: 'DM',
          snippet: 'Hi! How much is the Pro plan for two brands?', unread: 2, minutes: 6,
          mine: true, messages: [
            { from: 'them', text: 'Hi! We run two small brands and post every day.', at: '09:12' },
            { from: 'them', text: 'How much is the Pro plan for two brands?', at: '09:13' },
            { from: 'me', text: 'Hi Petra - the Pro plan covers two brands and unlimited scheduling.', at: '09:31' }
          ] },
        { id: 'c2', name: 'Marek z Trnavy', network: 'facebook', channel: 'Comment',
          snippet: 'Does this work with a Slovak interface?', unread: 1, minutes: 34,
          mine: false, messages: [
            { from: 'them', text: 'Does this work with a Slovak interface?', at: '08:40' }
          ] },
        { id: 'c3', name: '@lucia.builds', network: 'x', channel: 'Mention',
          snippet: 'Great thread on carousels, saved it.', unread: 0, minutes: 128,
          mine: false, messages: [
            { from: 'them', text: 'Great thread on carousels, saved it.', at: '06:55' }
          ] },
        { id: 'c4', name: 'Dana Horváth', network: 'linkedin', channel: 'DM',
          snippet: 'Can we book a walkthrough next week?', unread: 1, minutes: 210,
          mine: true, messages: [
            { from: 'them', text: 'Can we book a walkthrough next week?', at: '05:10' }
          ] }
      ]
    };
  }

  function svSamplePlan() {
    var base = new Date();
    base.setDate(1);
    var day = function (n) {
      return svDayKey(new Date(base.getFullYear(), base.getMonth(), n));
    };
    return {
      posts: [
        { date: day(3), time: '09:00', text: 'Behind the scenes', networks: ['instagram', 'tiktok'] },
        { date: day(4), time: '18:30', text: 'Carousel: 5 hooks', networks: ['instagram'] },
        { date: day(9), time: '12:00', text: 'Client result', networks: ['linkedin', 'facebook'] },
        { date: day(11), time: '08:15', text: 'Short: one tip', networks: ['tiktok', 'youtube'] },
        { date: day(17), time: '17:00', text: 'Poll: what next?', networks: ['x', 'facebook'] },
        { date: day(19), time: '10:00', text: 'Newsletter teaser', networks: ['linkedin'] },
        { date: day(24), time: '19:45', text: 'Reel: before / after', networks: ['instagram', 'tiktok'] }
      ],
      best: [
        [0, 0, 1, 2, 3, 1], [0, 1, 2, 3, 2, 1], [1, 1, 2, 2, 1, 0],
        [0, 1, 2, 3, 3, 2], [1, 2, 3, 3, 2, 1], [0, 0, 1, 1, 1, 0], [1, 2, 2, 1, 1, 0]
      ]
    };
  }

  function svSampleAds() {
    return {
      platforms: { meta: true, google: true, tiktok: false },
      campaigns: [
        { name: 'Spring retargeting', platform: 'Meta', spend: 1240, results: 86, cpa: 14.4, status: 'active' },
        { name: 'Brand search', platform: 'Google', spend: 860, results: 41, cpa: 21.0, status: 'active' },
        { name: 'Lookalike 1%', platform: 'Meta', spend: 640, results: 19, cpa: 33.7, status: 'learning' },
        { name: 'Broad match test', platform: 'Google', spend: 310, results: 7, cpa: 44.3, status: 'paused' }
      ]
    };
  }

  // ------------------------------------------------------------------ answer → rows
  function svConversationsOf(answer) {
    var list = svFirstList(answer && (answer.result || answer.data || answer), 0);
    if (!list) return null;
    return list.slice(0, 40).map(function (row, index) {
      var snippet = String(svField(row, ['text', 'message', 'snippet', 'lastMessage', 'content', 'body'], ''));
      return {
        id: String(svField(row, ['id', 'conversationId', 'conversation_id', 'threadId', 'thread_id'], 'c' + index)),
        name: String(svField(row, ['name', 'author', 'user', 'username', 'from', 'sender'], 'Conversation ' + (index + 1))),
        network: String(svField(row, ['network', 'platform', 'channel', 'source'], '')),
        channel: String(svField(row, ['channel', 'type', 'kind'], 'Message')),
        snippet: snippet,
        unread: Number(svField(row, ['unread', 'unreadCount', 'unread_count', 'pending'], 0)) || 0,
        minutes: Number(svField(row, ['waitingMinutes', 'minutes', 'ageMinutes', 'age'], 0)) || 0,
        messages: []
      };
    });
  }

  function svPostsOf(answer) {
    var list = svFirstList(answer && (answer.result || answer.data || answer), 0);
    if (!list) return null;
    return list.slice(0, 120).map(function (row, index) {
      var when = String(svField(row, ['date', 'when', 'scheduledAt', 'scheduled_at', 'publishedAt', 'time'], ''));
      return {
        id: 'p' + index,
        date: when.slice(0, 10),
        time: when.length > 10 ? when.slice(11, 16) : '',
        text: String(svField(row, ['text', 'caption', 'content', 'title', 'message'], 'Post ' + (index + 1))),
        networks: [].concat(svField(row, ['networks', 'network', 'platform', 'platforms'], []) || []).map(String),
        status: String(svField(row, ['status', 'state'], 'scheduled'))
      };
    }).filter(function (post) { return /^\d{4}-\d{2}-\d{2}$/.test(post.date); });
  }

  function svCampaignsOf(answer) {
    var list = svFirstList(answer && (answer.result || answer.data || answer), 0);
    if (!list) return null;
    return list.slice(0, 40).map(function (row, index) {
      return {
        name: String(svField(row, ['name', 'campaign', 'campaignName', 'title'], 'Campaign ' + (index + 1))),
        platform: String(svField(row, ['platform', 'network', 'source', 'publisher'], '')),
        spend: Number(svField(row, ['spend', 'cost', 'amount', 'budget'], 0)) || 0,
        results: Number(svField(row, ['results', 'conversions', 'leads', 'clicks'], 0)) || 0,
        cpa: Number(svField(row, ['cpa', 'costPerResult', 'cost_per_lead'], 0)) || 0,
        status: String(svField(row, ['status', 'state'], ''))
      };
    });
  }

  // ------------------------------------------------------------------ shell
  function viewSocial() {
    var s = state.social;
    var m = s.metricool;
    var v = s.video;
    var html = pageHead('Social',
      'Make the video, then plan, publish and measure it with Metricool.',
      '<button class="btn" data-act="social-refresh">Refresh</button>' +
      (SOCIAL.tab === 'create'
        ? '<button class="btn primary" data-act="social-make">Make the video</button>' : ''));

    html += svMetricoolBar(m);
    if (SOCIAL.demo) {
      html += '<div class="callout"><strong>Sample data.</strong> These rows are made up so the ' +
        'layout can be read without an account. Nothing here comes from Metricool.</div>';
    }
    html += svTabBar();
    if (SOCIAL.tab === 'inbox') html += svBodyInbox();
    else if (SOCIAL.tab === 'planner') html += svBodyPlanner();
    else if (SOCIAL.tab === 'ads') html += svBodyAds();
    else if (SOCIAL.tab === 'reports') html += svBodyReports();
    else html += svBodyCreate(v);
    return html;
  }

  function svMetricoolBar(m) {
    var connected = m.connected;
    var coverage = '';
    if (SOCIAL.workspace) {
      var keys = Object.keys(SOCIAL.workspace.capabilities || {}).filter(function (key) {
        return SOCIAL.workspace.capabilities[key];
      });
      coverage = SOCIAL.workspace.count + ' tools · ' + (keys.length ? 'covers ' + keys.join(', ') : 'no matching section');
    }
    return '<div class="sv-mcbar' + (connected ? ' on' : '') + '">' +
      '<span class="dot ' + (connected ? 'on' : 'off') + '"></span>' +
      '<div class="grow"><strong>' + (connected ? 'Metricool connected' : 'Metricool not connected') + '</strong>' +
      '<div class="small dim">' + (connected
        ? h(coverage || m.note)
        : 'Connect once: the inbox, the planner, the ad panels and the reports all read from it.') +
      '</div></div>' +
      '<div class="sv-mcbar-actions">' +
      (connected
        ? '<button class="btn sm" data-act="mc-tools">Load tools</button>' +
          '<button class="btn sm danger" data-act="mc-off">Disconnect</button>'
        : '<a class="btn sm primary" href="' + h(svMetricoolUrl()) +
          '" target="_blank" rel="noopener sponsored">Get Metricool</a>' +
          '<button class="btn sm" data-act="mc-on">I have an account - connect</button>') +
      '<button class="btn sm ghost" data-act="sv-demo">' +
      (SOCIAL.demo ? 'Hide sample data' : 'Show sample data') + '</button>' +
      '</div></div>' +
      '<div class="sv-disclosure">Metricool links here are affiliate links: if you open an account ' +
      'through one, this project may earn a commission at no extra cost to you. It is not ' +
      'affiliated with Metricool. <button class="linkish" data-act="sv-legal">Legal notice</button></div>';
  }

  function svTabBar() {
    return '<div class="sv-tabs">' + SV_TABS.map(function (tab) {
      var on = tab.key === SOCIAL.tab;
      return '<button class="sv-tab' + (on ? ' on' : '') + '" data-act="sv-tab" data-tab="' +
        tab.key + '">' + h(tab.label) + '</button>';
    }).join('') + '</div>';
  }

  function svIntro(title, sub, bullets) {
    return '<div class="card sv-intro"><h2>' + h(title) + '</h2>' +
      '<p class="small muted">' + h(sub) + '</p>' +
      (bullets && bullets.length ? '<div class="sv-features">' + bullets.map(function (b) {
        return '<div class="sv-feature"><strong>' + h(b[0]) + '</strong>' +
          '<div class="small dim">' + h(b[1]) + '</div></div>';
      }).join('') + '</div>' : '') +
      '</div>';
  }

  function svEmpty(title, text, action) {
    return '<div class="empty sv-empty"><div class="big">' + h(title) + '</div>' +
      '<p class="small">' + h(text) + '</p>' + (action || '') + '</div>';
  }

  function svAnswerBlock(slot, answer) {
    if (!answer) return '';
    var text = answer.text || '';
    return '<div class="mt"><div class="sv-answer-head"><span class="small dim">' +
      (answer.tool ? 'tool: <span class="mono">' + h(answer.tool) + '</span>' : 'answer') +
      '</span>' + (answer.arguments && Object.keys(answer.arguments).length
        ? '<span class="small dim mono">' + h(JSON.stringify(answer.arguments)) + '</span>' : '') +
      '</div><pre class="log">' + h(text || JSON.stringify(answer.result || answer, null, 1)) + '</pre></div>';
  }

  // ------------------------------------------------------------------ create
  function svDepsLine(deps) {
    if (deps && deps.ready) return '<span class="badge green">renderer ready</span>';
    var missing = [];
    if (!deps || !deps.ffmpeg) missing.push('ffmpeg');
    if (!deps || !deps.pillow) missing.push('Pillow');
    return '<span class="badge amber">needs ' + h(missing.join(' + ')) + '</span>';
  }

  function svBodyCreate(v) {
    var title = svDraft('title', '');
    var description = svDraft('description', '');
    var caption = svDraft('caption', '');
    var when = svDraft('when', '');
    var networks = svDraft('networks', []);
    var fmt = svDraft('format', '9:16');
    var size = svDraft('size', '1080p');
    var media = svDraft('media', '');
    var audio = svDraft('audio', '');

    var html = '<div class="grid cols-2">';

    // ---- the form
    html += '<div class="card"><div class="card-head"><h2>New video</h2><div class="spacer"></div>' +
      svDepsLine(v.deps) + '</div>' +
      '<div class="drop" id="sv-drop"><div class="big">Drop audio, a picture or a video</div>' +
      '<div class="small">or click to choose - copied into ' + h(v.dirs.inputs) + '</div>' +
      '<input type="file" id="sv-file" multiple class="hidden" ' +
      'accept="audio/*,image/*,video/*"></div>' +
      '<div class="field mt"><label>Title</label>' +
      '<input type="text" id="sv-title" maxlength="120" value="' + h(title) + '" ' +
      'placeholder="What the video is about"></div>' +
      '<div class="row"><div class="field"><label>Audio <span class="dim">(sets the length)</span></label>' +
      '<select id="sv-audio">' + svFileOptions(v.audio, '- none -', 'drop an audio file first', audio) +
      '</select></div>' +
      '<div class="field"><label>Picture or video</label>' +
      '<select id="sv-media">' + svMediaOptions(v, media) + '</select></div></div>' +
      '<div class="field"><label>Description <span class="dim">(drawn on a picture, used as the caption)</span></label>' +
      '<textarea id="sv-desc" rows="4" placeholder="The caption for the post">' + h(description) + '</textarea></div>' +
      '<div class="row"><div class="field"><label>Format</label><select id="sv-format">' +
      v.formats.map(function (f) {
        return '<option value="' + h(f.key) + '"' + (f.key === fmt ? ' selected' : '') + '>' +
          h(f.label) + '</option>';
      }).join('') + '</select></div>' +
      '<div class="field"><label>Size</label><select id="sv-size">' +
      v.sizes.map(function (s) {
        return '<option value="' + h(s.key) + '"' + (s.key === size ? ' selected' : '') + '>' +
          h(s.label) + '</option>';
      }).join('') + '</select></div></div>' +
      '<div class="sv-summary" id="sv-summary">MP4 - H.264 + AAC</div>' +
      '<div class="toolbar" style="margin-top:12px;margin-bottom:0">' +
      '<button class="btn primary" data-act="social-make">Make the video</button>' +
      '<button class="btn sm ghost hidden" id="sv-cancel" data-act="social-cancel">Cancel</button>' +
      '</div></div>';

    // ---- publish
    html += '<div class="card"><div class="card-head"><h2>Publish through Metricool</h2>' +
      '<div class="spacer"></div><span class="badge ' +
      (state.social.metricool.connected ? 'green' : 'grey') + '">' +
      (state.social.metricool.connected ? 'connected' : 'not connected') + '</span></div>' +
      '<div class="field"><label>Video</label><select id="sv-video">' +
      svFileOptions(v.outputs, '- newest -', 'nothing rendered yet') + '</select></div>' +
      '<div class="field"><label>Caption</label>' +
      '<textarea id="sv-caption" rows="3" placeholder="The text that goes with the post">' +
      h(caption) + '</textarea></div>' +
      '<div class="row"><div class="field"><label>When</label>' +
      '<input type="text" id="sv-when" value="' + h(when) + '" placeholder="2026-11-20 18:00"></div>' +
      '<div class="field"><label>Media URL</label>' +
      '<input type="text" id="sv-media-url" placeholder="https://.../video.mp4"></div></div>' +
      '<div class="field"><label>Networks</label><div class="sv-nets">' +
      SV_NETWORKS.map(function (name) {
        return '<label class="check"><input type="checkbox" data-sv-net value="' + name + '"' +
          (networks.indexOf(name) >= 0 ? ' checked' : '') + '>' + h(name) + '</label>';
      }).join('') + '</div></div>' +
      '<button class="btn primary" data-act="mc-schedule">Schedule in Metricool</button>' +
      '<div class="hint small dim" style="margin-top:8px">Metricool fetches the file itself, so it ' +
      'needs a public URL: the domain or tunnel already pointing at this app, or a file you ' +
      'uploaded in Metricool.</div>' +
      '<div id="sv-mc-post" class="mt"></div></div>';

    html += '</div>';  // end grid

    // ---- log + finished videos
    html += '<div class="grid cols-2 mt">';
    html += '<div class="card"><div class="card-head"><h2>Render</h2><div class="spacer"></div>' +
      '<span id="sv-task-badge" class="badge grey">idle</span></div>' +
      '<pre class="log" id="sv-log">Nothing rendered in this session.</pre></div>';
    html += '<div class="card"><div class="card-head"><h2>Finished videos</h2><div class="spacer"></div>' +
      '<span class="small dim sv-path" title="' + h(v.dirs.output) + '">' + h(v.dirs.output) + '</span></div>' +
      '<div id="sv-preview"></div><div id="sv-outputs">' + svOutputRows(v.outputs) + '</div></div>';
    html += '</div>';
    return html;
  }

  function svOutputRows(items) {
    if (!items.length) return '<div class="small dim">No videos yet.</div>';
    return items.map(function (f) {
      var url = '/api/social/file?kind=output&name=' + encodeURIComponent(f.name);
      return '<div class="file-row"><span class="grow">' + h(f.name) + '</span>' +
        '<span class="small dim nowrap">' + f.mb + ' MB</span>' +
        '<a class="btn sm" href="' + url + '" download>Download</a>' +
        '<button class="btn sm" data-sv-preview="' + h(f.name) + '">Preview</button>' +
        '<button class="btn sm ghost" data-sv-del="output" data-sv-name="' + h(f.name) + '" ' +
        'title="Remove">&times;</button></div>';
    }).join('');
  }

  function svSummary() {
    var box = $('#sv-summary');
    if (!box) return;
    var fmt = svVal('sv-format') || '16:9';
    var size = svVal('sv-size') || '1080p';
    var table = {
      '16:9': { '1080p': [1920, 1080], '720p': [1280, 720], '480p': [854, 480] },
      '9:16': { '1080p': [1080, 1920], '720p': [720, 1280], '480p': [480, 854] },
      '1:1': { '1080p': [1080, 1080], '720p': [720, 720], '480p': [480, 480] },
      '4:5': { '1080p': [1080, 1350], '720p': [720, 900], '480p': [480, 600] }
    };
    var dims = (table[fmt] || table['16:9'])[size] || [1920, 1080];
    var media = svKind(svVal('sv-media'));
    var what = media === 'video' ? 'source video scaled and cropped'
      : media === 'image' ? 'still cover held for the audio' : 'add a picture or a video';
    var length = SOCIAL.duration
      ? ' - audio ' + Math.floor(SOCIAL.duration / 60) + ':' +
        ('0' + Math.round(SOCIAL.duration % 60)).slice(-2) : '';
    box.innerHTML = '<span class="mono">' + dims[0] + ' x ' + dims[1] + '</span> - MP4, H.264 + AAC 30 fps - ' +
      '<span class="dim">' + h(what) + length + '</span>';
  }

  function svProbeAudio() {
    SOCIAL.duration = null;
    svSummary();
    var name = svVal('sv-audio');
    if (!name || svKind(name) !== 'audio') return;
    var probe = new Audio('/api/social/file?kind=input&name=' + encodeURIComponent(name));
    probe.addEventListener('loadedmetadata', function () {
      if (!isFinite(probe.duration)) return;
      SOCIAL.duration = probe.duration;
      svSummary();
    });
    probe.addEventListener('error', function () { });
    probe.load();
  }

  function svSpec() {
    return {
      title: svVal('sv-title'),
      description: svVal('sv-desc'),
      audio: svVal('sv-audio'),
      media: svVal('sv-media'),
      format: svVal('sv-format'),
      size: svVal('sv-size')
    };
  }

  // ------------------------------------------------------------------ inbox
  function svBodyInbox() {
    var answer = SOCIAL.demo ? null : SOCIAL.inbox;
    var sample = SOCIAL.demo ? svSampleInbox() : null;
    var conversations = sample ? sample.conversations
      : (answer ? (svConversationsOf(answer) || []) : []);
    var metrics = sample ? sample.metrics : null;
    if (!sample && conversations.length) {
      metrics = {
        first: Math.round(conversations.reduce(function (sum, c) { return sum + (c.minutes || 0); }, 0) /
          Math.max(1, conversations.length)),
        open: conversations.length,
        satisfaction: null,
        volume: conversations.length
      };
    }

    var html = svIntro('Manage all your conversations in one place',
      'Reply to messages from Instagram, Facebook, X and more without switching apps. ' +
      'Your team and your clients in a single inbox.',
      [['Reply from a unified inbox', 'Centralize direct messages, comments and mentions from all your networks in one place.'],
       ['Collaborate with your team in real time', 'Assign conversations, leave internal notes and respond faster with shared workflows.'],
       ['Measure your response time', 'Customer service metrics: average response time, volume and satisfaction.']]);

    if (metrics) {
      html += '<div class="grid cols-4 mt">' +
        kpi('Avg first response', metrics.first === null ? '-' : svAgo(metrics.first), 'first reply', 'green') +
        kpi('Open', num(metrics.open), 'waiting for an answer', 'amber') +
        kpi('Satisfaction', metrics.satisfaction === null ? '-' : metrics.satisfaction + '%', 'from replies', '') +
        kpi('Volume', num(metrics.volume), 'last 7 days', 'accent') + '</div>';
    }

    if (!conversations.length) {
      html += '<div class="card mt">' + svEmpty(
        state.social.metricool.connected ? 'No conversations came back' : 'No network connected yet',
        state.social.metricool.connected
          ? 'Metricool answered, but this account exposes no conversation list. The raw answer is below when there is one.'
          : 'Connect Metricool, or show the sample data to see how the inbox is laid out.',
        (state.social.metricool.connected
          ? '<button class="btn" data-act="sv-section" data-section="inbox">Ask Metricool again</button>'
          : '<button class="btn primary" data-act="mc-on">Connect Metricool</button> ' +
            '<button class="btn" data-act="sv-demo">Show sample data</button>')) +
        '</div>';
      if (answer) html += '<div class="card mt">' + svAnswerBlock('sv-inbox', answer) + '</div>';
      return html;
    }

    if (!SOCIAL.thread || !conversations.some(function (c) { return c.id === SOCIAL.thread; })) {
      SOCIAL.thread = conversations[0].id;
    }
    var current = conversations.filter(function (c) { return c.id === SOCIAL.thread; })[0] || conversations[0];

    html += '<div class="sv-inbox mt"><div class="card sv-list"><div class="toolbar">' +
      '<span class="grow"><strong>' + num(conversations.length) + '</strong> conversations</span>' +
      (state.social.metricool.connected && !SOCIAL.demo
        ? '<button class="btn sm" data-act="sv-section" data-section="inbox">Refresh</button>' : '') +
      '</div><div class="sv-convs">' + conversations.map(function (c) {
        return '<div class="sv-conv' + (c.id === current.id ? ' on' : '') +
          '" data-act="sv-thread" data-id="' + h(c.id) + '">' +
          '<div class="avatar">' + h(svInitials(c.name)) + '</div>' +
          '<div class="grow"><div class="sv-conv-top"><strong>' + h(c.name) + '</strong>' +
          '<span class="small dim">' + h(svAgo(c.minutes)) + '</span></div>' +
          '<div class="small dim sv-clip">' + h(c.snippet) + '</div>' +
          '<div class="sv-conv-meta">' + svNetwork(c.network) +
          (c.channel ? '<span class="small dim">' + h(c.channel) + '</span>' : '') +
          (c.unread ? '<span class="badge blue">' + c.unread + ' new</span>' : '') +
          '</div></div></div>';
      }).join('') + '</div></div>';

    html += '<div class="card sv-thread"><div class="card-head"><div class="avatar">' +
      h(svInitials(current.name)) + '</div><div><h2>' + h(current.name) + '</h2>' +
      '<div class="small dim">' + h(current.network || 'network') + ' - ' + h(current.channel || 'message') +
      '</div></div><div class="spacer"></div>' +
      (SOCIAL.assign[current.id]
        ? '<span class="badge violet">' + h(SOCIAL.assign[current.id]) + '</span>' : '') +
      '<button class="btn sm" data-act="sv-note" data-id="' + h(current.id) + '">Internal note</button>' +
      '<button class="btn sm" data-act="sv-assign" data-id="' + h(current.id) + '">Assign</button></div>' +
      '<div class="sv-messages">' + svMessages(current) + svNotes(current.id) + '</div>' +
      '<div class="sv-reply"><textarea id="sv-reply" rows="3" ' +
      'placeholder="Write the reply..."></textarea><div class="toolbar" style="margin:8px 0 0">' +
      '<span class="grow small dim" id="sv-note-hint">Answering ' + h(current.name) + '</span>' +
      '<button class="btn primary" data-act="sv-reply">Send reply</button></div></div>' +
      '</div></div>';
    return html;
  }

  function svMessages(conversation) {
    if (conversation.messages && conversation.messages.length) {
      return conversation.messages.map(function (m) {
        return '<div class="sv-msg ' + (m.from === 'me' ? 'me' : 'them') + '">' +
          '<div class="sv-bubble">' + h(m.text) + '</div>' +
          '<div class="small dim">' + h(m.at || '') + '</div></div>';
      }).join('');
    }
    return '<div class="sv-msg them"><div class="sv-bubble">' + h(conversation.snippet || 'No message body.') +
      '</div><div class="small dim">' + h(svAgo(conversation.minutes)) + '</div></div>' +
      '<div class="small dim mt">Thread contents come from Metricool once a reply tool is available.</div>';
  }

  function svNotes(threadId) {
    var notes = SOCIAL.notes[threadId] || [];
    return notes.map(function (note) {
      return '<div class="sv-note"><span class="badge amber">note</span>' +
        '<div class="small">' + h(note) + '</div></div>';
    }).join('');
  }

  // ------------------------------------------------------------------ planner
  function svMonthBase() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth() + SOCIAL.month, 1);
  }

  function svDayKey(date) {
    return date.getFullYear() + '-' + ('0' + (date.getMonth() + 1)).slice(-2) + '-' +
      ('0' + date.getDate()).slice(-2);
  }

  function todayOffset(days) {
    var date = new Date();
    date.setDate(date.getDate() + days);
    return svDayKey(date);
  }

  function svMonthCells() {
    var base = svMonthBase();
    var start = (base.getDay() + 6) % 7;          // Monday first
    var days = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate();
    var cells = [];
    for (var i = 0; i < start; i += 1) cells.push(null);
    for (var d = 1; d <= days; d += 1) cells.push(new Date(base.getFullYear(), base.getMonth(), d));
    while (cells.length % 7) cells.push(null);
    return cells;
  }

  function svBodyPlanner() {
    var answer = SOCIAL.demo ? null : SOCIAL.plan;
    var sample = SOCIAL.demo ? svSamplePlan() : null;
    var posts = (sample && { posts: sample.posts }) || (answer ? { posts: svPostsOf(answer) } : null);
    posts = (posts && posts.posts) || [];
    var best = (sample && sample.best) || null;
    var today = svDayKey(new Date());
    var byDay = {};
    posts.forEach(function (post) {
      byDay[post.date] = (byDay[post.date] || []).concat([post]);
    });

    var html = svIntro('Plan, schedule and publish your content across all your networks',
      'Organize your editorial calendar, schedule posts and keep your brand consistent.',
      [['Visual content calendar', 'See all your planned content in a drag and drop calendar and reorganize it with one click.'],
       ['Schedule across all networks at once', 'Publish to Instagram, Facebook, TikTok, X and LinkedIn from a single place.'],
       ['Find your best time to publish', 'Analyze when your audience is most active and schedule during peak hours.']]);

    html += '<div class="card mt"><div class="card-head">' +
      '<button class="btn sm" data-act="sv-month" data-step="-1">&larr;</button>' +
      '<h2>' + h(svMonthBase().toLocaleDateString(undefined, { month: 'long', year: 'numeric' })) + '</h2>' +
      '<button class="btn sm" data-act="sv-month" data-step="1">&rarr;</button>' +
      '<div class="spacer"></div>' +
      '<span class="badge ' + (posts.length ? 'blue' : 'grey') + '">' +
      num(posts.length) + ' planned</span>' +
      (state.social.metricool.connected && !SOCIAL.demo
        ? '<button class="btn sm" data-act="sv-section" data-section="planner">Refresh</button>' : '') +
      '</div><div class="sv-cal">' +
      ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(function (day) {
        return '<div class="sv-cal-head">' + day + '</div>';
      }).join('') +
      svMonthCells().map(function (date) {
        if (!date) return '<div class="sv-cell empty"></div>';
        var key = svDayKey(date);
        var items = byDay[key] || [];
        return '<div class="sv-cell' + (key === today ? ' today' : '') + '">' +
          '<div class="sv-day">' + date.getDate() + '</div>' +
          items.slice(0, 3).map(function (post) {
            return '<div class="sv-post" title="' + h(post.text) + '">' +
              (post.time ? '<span class="sv-time">' + h(post.time) + '</span>' : '') +
              h(post.text) + '</div>';
          }).join('') +
          (items.length > 3 ? '<div class="small dim">+' + (items.length - 3) + ' more</div>' : '') +
          '</div>';
      }).join('') +
      '</div></div>';

    html += '<div class="grid cols-2 mt">';
    html += '<div class="card"><div class="card-head"><h2>Best time to publish</h2><div class="spacer"></div>' +
      (best ? '<span class="badge grey">sample</span>' : '') + '</div>' +
      (best ? svHeat(best) :
        '<div class="small muted">Connect Metricool to see when your audience is active. ' +
        'Until then this stays empty on purpose.</div>') +
      '</div>';
    html += '<div class="card"><div class="card-head"><h2>Connect social networks</h2></div>' +
      '<div class="small muted" style="margin-bottom:10px">Connect your accounts to unlock analytics, ' +
      'scheduling and content management. Accounts are connected in Metricool itself; this app ' +
      'reads and writes through its MCP server.</div>' +
      '<div class="sv-networks">' + SV_NETWORKS.map(function (name) {
        var live = (sample && /instagram|tiktok/.test(name)) || false;
        return '<div class="sv-network"><span class="sv-dot"></span><span class="grow">' + h(name) + '</span>' +
          (live ? '<span class="badge green">connected</span>'
            : '<a class="btn sm" href="' + h(svMetricoolUrl()) +
              '" target="_blank" rel="noopener sponsored">Connect</a>') + '</div>';
      }).join('') + '</div></div>';
    html += '</div>';
    return html;
  }

  function svHeat(rows) {
    var days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    var hours = ['00', '04', '08', '12', '16', '20'];
    var html = '<div class="sv-heat"><div></div>' + hours.map(function (hour) {
      return '<div class="sv-heat-hour">' + hour + '</div>';
    }).join('');
    rows.forEach(function (row, index) {
      html += '<div class="sv-heat-day">' + days[index] + '</div>' + row.map(function (value) {
        return '<div class="sv-heat-cell l' + value + '"></div>';
      }).join('');
    });
    html += '</div><div class="small dim mt">Darker is busier. The sample pattern is fixed; a ' +
      'connected account fills it from your own analytics.</div>';
    return html;
  }

  // ------------------------------------------------------------------ ads
  function svBodyAds() {
    var answer = SOCIAL.demo ? null : SOCIAL.ads;
    var sample = SOCIAL.demo ? svSampleAds() : null;
    var campaigns = sample ? sample.campaigns : (answer ? svCampaignsOf(answer) : null) || [];
    var live = (sample && sample.platforms) || {};

    var html = svIntro('Manage your advertising campaigns',
      'Centralize the performance of your ads across all platforms and make data-driven decisions.',
      [['Meta Ads', 'Manage your Facebook and Instagram ads from a unified panel.'],
       ['Google Ads', 'Analyze search, display, keywords and recommendation campaigns.'],
       ['TikTok Ads', 'Centralize the performance of your TikTok campaigns.']]);

    var totalSpend = campaigns.reduce(function (sum, c) { return sum + (c.spend || 0); }, 0);
    var totalResults = campaigns.reduce(function (sum, c) { return sum + (c.results || 0); }, 0);
    html += '<div class="grid cols-3 mt">' + [['meta', 'Meta Ads', 'Facebook and Instagram'],
      ['google', 'Google Ads', 'Search, display, keywords'], ['tiktok', 'TikTok Ads', 'TikTok campaigns']]
      .map(function (row) {
        var on = !!live[row[0]];
        return '<div class="card sv-ad-card"><div class="card-head"><h2>' + h(row[1]) + '</h2>' +
          '<div class="spacer"></div><span class="badge ' + (on ? 'green' : 'grey') + '">' +
          (on ? 'connected' : 'not connected') + '</span></div>' +
          '<div class="small dim">' + h(row[2]) + '</div>' +
          '<div class="toolbar" style="margin:12px 0 0">' +
          '<a class="btn sm" href="' + h(svMetricoolUrl()) +
          '" target="_blank" rel="noopener sponsored">' + (on ? 'Open' : 'Connect') + '</a>' +
          (state.social.metricool.connected && !SOCIAL.demo
            ? '<button class="btn sm" data-act="sv-section" data-section="ads">Load</button>' : '') +
          '</div></div>';
      }).join('') + '</div>';

    html += '<div class="card mt"><div class="card-head"><h2>Campaigns</h2><div class="spacer"></div>' +
      '<span class="small dim">' + (campaigns.length
        ? num(Math.round(totalSpend)) + ' spend - ' + num(totalResults) + ' results'
        : 'nothing loaded') + '</span></div>';
    if (!campaigns.length) {
      html += svEmpty('No ad accounts connected',
        'Connect Meta, Google or TikTok in Metricool and their campaigns show up here.',
        '<a class="btn primary" href="' + h(svMetricoolUrl()) +
        '" target="_blank" rel="noopener sponsored">Open Metricool</a>');
    } else {
      html += '<div class="table-wrap"><table><thead><tr><th>Campaign</th><th>Platform</th>' +
        '<th class="num">Spend</th><th class="num">Results</th><th class="num">Cost / result</th>' +
        '<th>Status</th></tr></thead><tbody>' + campaigns.map(function (c) {
          return '<tr><td>' + h(c.name) + '</td><td>' + svNetwork(c.platform) + '</td>' +
            '<td class="num">' + num(Math.round(c.spend)) + '</td>' +
            '<td class="num">' + num(c.results) + '</td>' +
            '<td class="num">' + (c.cpa ? c.cpa.toFixed(1) : '-') + '</td>' +
            '<td><span class="badge ' + (c.status === 'paused' ? 'grey'
              : c.status === 'learning' ? 'amber' : 'green') + '">' + h(c.status || '-') +
            '</span></td></tr>';
        }).join('') + '</tbody></table></div>';
      html += '<div class="small dim mt">Planning for these campaigns lives in the ' +
        '<button class="btn sm ghost" data-act="sv-tab" data-tab="planner">Planner</button> tab.</div>';
    }
    html += '</div>';
    if (answer) html += '<div class="card mt">' + svAnswerBlock('sv-ads', answer) + '</div>';
    return html;
  }

  // ------------------------------------------------------------------ reporting
  function svBodyReports() {
    var answer = SOCIAL.demo ? null : SOCIAL.reports;
    var cards = [
      ['Reports', 'Generate pre-designed reports with data from the social networks you choose for a ' +
        'specific period. Customize them with your logo and colors, and export them as PDF or PPT.'],
      ['Campaign dashboards', 'Group content by campaign or topic, manually or automatically. Analyze ' +
        'their combined performance to measure the real impact of each action.'],
      ['Metricool Studio', 'Create custom views without technical skills. Choose which charts, metrics ' +
        'and insights to display for one brand or multiple at once.'],
      ['Looker Studio', 'Connect your data to Looker Studio and build dashboards with complete freedom ' +
        'in design, structure and visualization.'],
      ['Hashtag Tracker', 'Monitor and analyze the use of a hashtag on X or Instagram and get data on ' +
        'its performance.']
    ];
    var connected = state.social.metricool.connected;
    var html = svIntro('Turn your data into reports or dashboards',
      'Ready to analyze, share, or present to your clients.',
      [['Reports', 'Pre-designed reports, your logo and colors, exported as PDF or PPT.'],
       ['Campaign dashboards', 'Content grouped by campaign or topic, with combined performance.'],
       ['Metricool Studio', 'Custom views built without technical skills.']]);

    if (!connected && !SOCIAL.demo) {
      html += '<div class="card mt"><div class="sv-empty-brand">' +
        '<div><strong>Your brand has no connected networks</strong>' +
        '<div class="small muted">Connect social networks or switch brands to start analyzing your data.</div></div>' +
        '<div class="spacer"></div><button class="btn primary" data-act="mc-on">Connect Metricool</button>' +
        '<button class="btn" data-act="sv-demo">Show sample data</button></div></div>';
    }

    html += '<div class="grid cols-3 mt">' + cards.map(function (card, index) {
      return '<div class="card sv-report"><div class="card-head"><h2>' + h(card[0]) + '</h2>' +
        '<div class="spacer"></div>' +
        (index === 4 ? '<span class="badge amber">25&euro;/day</span>' : '') + '</div>' +
        '<div class="small muted">' + h(card[1]) + '</div>' +
        (index === 4
          ? '<div class="toolbar" style="margin:12px 0 0"><a class="btn sm" ' +
            'href="https://metricool.com/hashtag-tracker/" target="_blank" rel="noopener">More information</a></div>'
          : '') +
        '</div>';
    }).join('') + '</div>';

    html += '<div class="card mt"><div class="card-head"><h2>Your data</h2><div class="spacer"></div>' +
      (connected && !SOCIAL.demo
        ? '<button class="btn sm" data-act="sv-section" data-section="reports">Load from Metricool</button>'
        : '') + '</div>' +
      (answer ? '' : '<div class="small muted">' +
        (SOCIAL.demo ? 'Sample numbers are on so the cards can be read. Connect Metricool to load your own.'
          : connected ? 'Pick a period and load the numbers your account exposes.'
            : 'Nothing is loaded yet: connect Metricool, or show the sample data to see the layout.') +
        '</div>') +
      (answer ? svAnswerBlock('sv-reports', answer) : '') + '</div>';
    return html;
  }

  // ------------------------------------------------------------------ wiring
  function svAutoLoad() {
    if (!state.social || !state.social.metricool.connected || SOCIAL.demo) return;
    if (SOCIAL.tab === 'inbox') svLoadSection('inbox');
    else if (SOCIAL.tab === 'planner') svLoadSection('planner');
    else if (SOCIAL.tab === 'ads') svLoadSection('ads');
    else if (SOCIAL.tab === 'reports') svLoadSection('reports');
  }

  function svLoadSection(name, force) {
    var slot = SV_SECTION_SLOT[name];
    if (!slot) return Promise.resolve();
    if (SOCIAL[slot] && !force) return Promise.resolve();
    if (!state.social || !state.social.metricool.connected) return Promise.resolve();
    var query = '/api/social/section?name=' + encodeURIComponent(name);
    if (name === 'planner') {
      query += '&from=' + svDayKey(svMonthBase()) + '&to=' + todayOffset(0);
    } else if (name === 'reports') {
      query += '&from=' + todayOffset(-30) + '&to=' + todayOffset(0);
    }
    return call(query).then(function (r) {
      SOCIAL[slot] = r;
      if (SOCIAL.tab === name) { SOCIAL.rendered = false; render(); }
    });
  }

  function svLoadWorkspace(force) {
    if (!state.social || !state.social.metricool.connected) return Promise.resolve();
    if (SOCIAL.workspace && !force) return Promise.resolve();
    if (SOCIAL.workspaceTried && !force) return Promise.resolve();
    SOCIAL.workspaceTried = true;
    return call('/api/social/workspace' + (force ? '?refresh=1' : '')).then(function (r) {
      if (r.ok) SOCIAL.workspace = r;
      SOCIAL.rendered = false;
      render();
    });
  }

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    var picked = {};
    var chain = Promise.resolve();
    list.forEach(function (file) {
      chain = chain.then(function () {
        return new Promise(function (resolve) {
          if (file.size > 400 * 1024 * 1024) {
            toast(file.name + ' is larger than 400 MB', 'err');
            return resolve();
          }
          var reader = new FileReader();
          reader.onload = function () {
            var b64 = String(reader.result || '').split(',')[1] || '';
            call('/api/social/upload', { name: file.name, b64: b64 }).then(function (r) {
              if (!r.ok) toast(file.name + ': ' + r.error, 'err');
              else if (r.kind) picked[r.kind] = r.name;
              resolve();
            });
          };
          reader.onerror = function () { toast('Could not read ' + file.name, 'err'); resolve(); };
          reader.readAsDataURL(file);
        });
      });
    });
    chain.then(function () {
      toast('Files copied in');
      svCaptureForm();
      if (SOCIAL.form) {
        if (picked.audio && !SOCIAL.form.audio) SOCIAL.form.audio = picked.audio;
        var media = picked.video || picked.image;
        if (media && !SOCIAL.form.media) SOCIAL.form.media = media;
      }
      SOCIAL.rendered = false;
      loadSocial();
    });
  }

  function wireSocial() {
    var drop = $('#sv-drop'), input = $('#sv-file');
    if (drop && input) {
      drop.onclick = function () { input.click(); };
      input.onchange = function () { uploadFiles(input.files); input.value = ''; };
      ['dragenter', 'dragover'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); });
      });
      drop.addEventListener('drop', function (e) { uploadFiles(e.dataTransfer.files); });
    }

    var desc = $('#sv-desc'), caption = $('#sv-caption');
    if (desc && caption) {
      desc.addEventListener('input', function () {
        if (!SOCIAL.captionTouched) caption.value = desc.value;
      });
      caption.addEventListener('input', function () { SOCIAL.captionTouched = true; });
    }
    if (desc && !caption) desc.addEventListener('input', function () { });

    ['sv-format', 'sv-size', 'sv-media'].forEach(function (id) {
      var el = $('#' + id);
      if (el) el.addEventListener('change', function () { svSummary(); svFillMediaUrl(); });
    });
    var audioSel = $('#sv-audio');
    if (audioSel) audioSel.addEventListener('change', svProbeAudio);
    svSummary();
    svProbeAudio();

    $$('[data-sv-del]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (!confirm('Remove ' + btn.dataset.svName + '?')) return;
        call('/api/social/remove', { kind: btn.dataset.svDel, name: btn.dataset.svName })
          .then(function (r) {
            if (!r.ok) return toast(r.error, 'err');
            svCaptureForm();
            loadSocial();
          });
      };
    });
    $$('[data-sv-preview]').forEach(function (btn) {
      btn.onclick = function () {
        var box = $('#sv-preview');
        if (!box) return;
        var url = '/api/social/file?kind=output&name=' + encodeURIComponent(btn.dataset.svPreview);
        box.innerHTML = '<video class="video-preview" controls src="' + url + '"></video>' +
          '<div class="small dim mt">' + h(btn.dataset.svPreview) + '</div>';
      };
    });
    var outputs = state.social && state.social.video.outputs;
    if (outputs && outputs.length && $('#sv-preview') && !$('#sv-preview').innerHTML) {
      var newest = outputs[0];
      $('#sv-preview').innerHTML = '<video class="video-preview" controls src="/api/social/file?kind=output&name=' +
        encodeURIComponent(newest.name) + '"></video>';
    }
    svFillMediaUrl();

    var running = (state.social.video.running || [])[0];
    var last = (state.social.video.tasks || [])[0];
    if (running) pollTask(running.id);
    else if (last) { svTaskBadge(last.status); svLog((last.log || []).join('\n')); }

    svLoadWorkspace(false);
    svAutoLoad();
  }

  function svFillMediaUrl() {
    var select = $('#sv-video'), field = $('#sv-media-url');
    if (!select || !field) return;
    var base = ((state.data && state.data.settings && state.data.settings.tracking) || {}).base_url || '';
    var outputs = (state.social && state.social.video.outputs) || [];
    var name = select.value || (outputs.length ? outputs[0].name : '');
    if (base && name && !field.value) {
      field.value = base.replace(/\/+$/, '') + '/t/v/' + encodeURIComponent(name);
    }
    select.onchange = function () {
      var picked = select.value || (outputs.length ? outputs[0].name : '');
      field.value = (base && picked)
        ? base.replace(/\/+$/, '') + '/t/v/' + encodeURIComponent(picked) : field.value;
    };
  }

  function svLog(text) {
    var el = $('#sv-log');
    if (el) { el.textContent = text; el.scrollTop = el.scrollHeight; }
  }

  function svTaskBadge(status) {
    var el = $('#sv-task-badge');
    if (!el) return;
    var map = { running: 'blue', ok: 'green', failed: 'red', cancelled: 'amber' };
    el.className = 'badge ' + (map[status] || 'grey');
    el.textContent = status;
    var cancel = $('#sv-cancel');
    if (cancel) cancel.classList.toggle('hidden', status !== 'running');
  }

  function pollTask(id) {
    SOCIAL.task = id;
    if (SOCIAL.timer) clearInterval(SOCIAL.timer);
    SOCIAL.timer = setInterval(function () {
      call('/api/social/task?id=' + encodeURIComponent(id)).then(function (r) {
        if (!r.ok || !r.task) { clearInterval(SOCIAL.timer); return; }
        var t = r.task;
        svTaskBadge(t.status);
        svLog((t.log || []).join('\n') || 'Waiting for the first line...');
        if (t.status !== 'running') {
          clearInterval(SOCIAL.timer);
          SOCIAL.timer = null;
          toast(t.status === 'ok' ? 'Video ready' : ('Render ' + t.status + ': ' + (t.error || '')),
            t.status === 'ok' ? 'ok' : 'err');
          loadSocial();
        }
      });
    }, 1500);
  }

  function resultBox(slot, result) {
    var box = $('#' + slot);
    if (!box) return;
    var text = result.text || '';
    box.innerHTML = '<pre class="log">' + h(text || JSON.stringify(result.result || result, null, 1)) + '</pre>' +
      (result.tool ? '<div class="small dim mt">tool: ' + h(result.tool) + '</div>' : '');
  }


  var VIEWS = ['dashboard', 'leads', 'osint', 'domains', 'sequences', 'inbox', 'automation', 'signals', 'brands', 'offers', 'match', 'social', 'ads', 'agent', 'mailboxes', 'settings'];

  function go(view) {
    if (view === 'daily') view = 'signals';
    if (VIEWS.indexOf(view) < 0) view = 'dashboard';
    state.view = view;
    SOCIAL.rendered = false;
    if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
    if (view === 'leads') loadLeads().then(function () { render(); });
    if (view === 'domains') loadDomains().then(function () { render(); });
    if (view === 'settings' && !state.llmProviders) loadLlm();
    render();
    paintChrome();
  }

  // ---- design: dark by default, light on request, remembered on this computer
  function themeNow() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function paintTheme() {
    var button = $('#theme-toggle');
    if (button) button.textContent = themeNow() === 'light' ? 'Dark design' : 'Light design';
  }

  function setTheme(value) {
    if (value === 'light') document.documentElement.setAttribute('data-theme', 'light');
    else document.documentElement.removeAttribute('data-theme');
    try { localStorage.setItem('ls-theme', value); } catch (e) { /* private mode */ }
    paintTheme();
  }

  (function bootTheme() {
    try {
      if (localStorage.getItem('ls-theme') === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
      }
    } catch (e) { /* private mode */ }
  })();

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-nav]');
    if (t) { go(t.dataset.nav); return; }
    if (e.target.closest('#theme-toggle')) {
      setTheme(themeNow() === 'light' ? 'dark' : 'light');
      return;
    }
    var a = e.target.closest('[data-act]');
    if (a && actions[a.dataset.act]) { actions[a.dataset.act](a, e); }
  });

  window.addEventListener('hashchange', function () {
    var v = (location.hash || '').replace('#', '');
    if (v !== state.view) go(v);
  });

  var initial = (location.hash || '').replace('#', '');
  if (VIEWS.indexOf(initial) >= 0) state.view = initial;
  paintTheme();
  refresh();
  if (state.view === 'leads') loadLeads();
  if (state.view === 'domains') loadDomains();
  setInterval(function () { if ($('#view') && state.view === 'dashboard') refresh(); }, 30000);
})();
