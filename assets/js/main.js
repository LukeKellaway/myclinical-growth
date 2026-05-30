/* MyClinical Growth — rendering engine. Reads window.GROWTH_DATA (assets/js/data.js). */
(function () {
  "use strict";
  var D = window.GROWTH_DATA || {};

  /* ---------- portal guides (how-to-apply per source) ---------- */
  // Build two indexes from D.portalGuides:
  //   PORTAL_BY_SLUG: slug -> guide object
  //   ALIAS_TO_SLUG: lowercased alias OR name -> slug
  // Then portalSlugForSource(name) returns the best slug match for a given
  // opportunity's source string. Tries exact alias first, then a substring
  // pass (longer aliases preferred so "Atamis - Health Family" beats "Atamis"
  // only if both match the input).
  var PORTAL_BY_SLUG = {};
  var ALIAS_TO_SLUG = {};
  (function buildPortalIndex() {
    var pg = (D.portalGuides && D.portalGuides.portals) || {};
    Object.keys(pg).forEach(function (slug) {
      var p = pg[slug] || {};
      p.slug = slug;
      PORTAL_BY_SLUG[slug] = p;
      var aliases = (p.aliases || []).slice();
      if (p.name) aliases.push(p.name);
      aliases.forEach(function (a) {
        if (a) ALIAS_TO_SLUG[String(a).toLowerCase().trim()] = slug;
      });
    });
  })();

  function portalSlugForSource(source) {
    if (!source) return null;
    var s = String(source).toLowerCase().trim();
    if (ALIAS_TO_SLUG[s]) return ALIAS_TO_SLUG[s];
    // Substring match. Prefer the longest alias that fits to avoid
    // "NHS England" eating "NHS England Digital" or similar.
    var best = null, bestLen = 0;
    Object.keys(PORTAL_BY_SLUG).forEach(function (slug) {
      var p = PORTAL_BY_SLUG[slug];
      var aliases = (p.aliases || []).slice();
      if (p.name) aliases.push(p.name);
      aliases.forEach(function (a) {
        if (!a) return;
        var al = String(a).toLowerCase();
        if (s.indexOf(al) > -1 && al.length > bestLen) {
          best = slug; bestLen = al.length;
        }
      });
    });
    return best;
  }

  function portalGuideBlockHtml(slug) {
    var p = PORTAL_BY_SLUG[slug];
    if (!p) return "";
    var hoops = (p.hoops || []).map(function (h) { return "<li>" + esc(h) + "</li>"; }).join("");
    var steps = (p.steps || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    var gotchas = p.gotchas
      ? '<div class="guide-gotchas"><strong>Watch out for: </strong>' + esc(p.gotchas) + "</div>"
      : "";
    return (
      '<details class="portal-guide">' +
        '<summary class="guide-summary">How to apply on this portal</summary>' +
        '<div class="guide-body">' +
          (p.intro ? '<p class="guide-intro">' + esc(p.intro) + "</p>" : "") +
          (hoops ? '<div class="guide-block"><div class="guide-block-label">Hoops to qualify</div><ul>' + hoops + "</ul></div>" : "") +
          (steps ? '<div class="guide-block"><div class="guide-block-label">How to apply, step by step</div><ol>' + steps + "</ol></div>" : "") +
          gotchas +
        "</div>" +
      "</details>"
    );
  }

  /* ---------- helpers ---------- */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function daysUntil(d) {
    if (!d) return null;
    var t = new Date(d);
    if (isNaN(t)) return null;
    return Math.ceil((t - new Date()) / 86400000);
  }
  function fmtDate(d) {
    if (!d) return "";
    var t = new Date(d);
    if (isNaN(t)) return esc(d);
    return t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }
  function tagClass(t) {
    var x = t.toLowerCase();
    if (x.indexOf("soon") > -1 || x.indexOf("urgent") > -1) return "tag tag-urgent";
    if (x === "grant" || x === "non-dilutive" || x === "international") return "tag tag-grant";
    if (x === "framework" || x === "tender" || x === "ai") return "tag tag-proc";
    return "tag tag-neutral";
  }
  function tagsHtml(tags) {
    return (tags || []).map(function (t) {
      return '<span class="' + tagClass(t) + '">' + esc(t) + "</span>";
    }).join("");
  }
  function allOpportunities() {
    var standing = (D.opportunities && D.opportunities.opportunities) || [];
    var live = (D.opportunitiesLive && D.opportunitiesLive.opportunities) || [];
    return standing.concat(live);
  }
  function sortByDeadline(list) {
    return list.slice().sort(function (a, b) {
      var da = daysUntil(a.deadline), db = daysUntil(b.deadline);
      if (da == null && db == null) return 0;
      if (da == null) return 1;
      if (db == null) return -1;
      return da - db;
    });
  }

  /* ---------- card builders ---------- */
  // True when an item is past its deadline OR explicitly marked closed.
  function isClosed(o) {
    if (/closed|completed|awarded/i.test(o.status || "")) return true;
    var d = daysUntil(o.deadline);
    return d != null && d < 0;
  }

  function oppCard(o) {
    var d = daysUntil(o.deadline);
    var closed = isClosed(o);
    var deadlineHtml;
    if (closed) {
      var closedLabel = o.deadline
        ? "Closed " + fmtDate(o.deadline)
        : (o.status || "Closed");
      deadlineHtml = '<span class="deadline closed">' + esc(closedLabel) + "</span>";
    } else if (o.deadline && d != null) {
      var cls = d <= 21 ? "deadline" : "deadline soft";
      deadlineHtml = '<span class="' + cls + '">Closes ' + fmtDate(o.deadline) + "</span>";
    } else {
      deadlineHtml = '<span class="deadline soft">' + esc(o.status || "Ongoing") + "</span>";
    }
    var meansHtml = o.means
      ? '<p class="means">' + esc(o.means) + "</p>"
      : '<p class="means">' + esc(o.summary || "") + "</p>";
    // Tile click now routes to our editorial detail page, not the source.
    // That keeps the user on-site and surfaces the "what it means" first.
    var detailLink = "/opportunity#" + encodeURIComponent(o.id || "");
    var cardClass = "opp-card" + (closed ? " opp-card-closed" : "");
    return (
      '<div class="' + cardClass + '">' +
        '<div class="tags">' + tagsHtml(o.tags) + "</div>" +
        '<h3><a href="' + esc(detailLink) + '" class="card-title-link">' + esc(o.title) + "</a></h3>" +
        '<div class="src">' + esc(o.source) +
          (o.value ? " · " + esc(o.value) : "") + "</div>" +
        meansHtml +
        '<div class="foot">' + deadlineHtml +
          '<a class="arrow" href="' + esc(detailLink) + '">More &rarr;</a>' +
        "</div>" +
      "</div>"
    );
  }

  function feedBadge(val) {
    var v = (val || "").toLowerCase();
    if (v.indexOf("api") > -1) return '<span class="feed feed-api">API</span>';
    if (v.indexOf("alert") > -1) return '<span class="feed feed-alert">Alerts</span>';
    return '<span class="feed feed-no">Tracked</span>';
  }
  // Fallback slug from a source name, used when there is no matching
  // portal guide. Keeps directory anchors stable across renders.
  function slugify(s) {
    return String(s || "").toLowerCase()
      .replace(/&/g, "and")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60);
  }

  function dirItem(r) {
    // Only resolve to a portal guide slug for procurement-group entries.
    // Grant-group entries share the namespace but must not collide with
    // procurement portals via the fuzzy alias match (e.g. "Health Innovation
    // Networks - regional funds" must not pick up the HIN portal slug).
    var isProc = r._group === "Procurement";
    var portalSlug = isProc ? portalSlugForSource(r["Source"]) : null;
    var slug = portalSlug || (isProc ? slugify(r["Source"]) : "grant-" + slugify(r["Source"]));
    var guide = portalSlug ? portalGuideBlockHtml(portalSlug) : "";
    return (
      '<div class="dir-item" id="portal-' + esc(slug) + '">' +
        '<div class="dir-item-top">' +
          "<div><h3>" + esc(r["Source"]) + "</h3>" +
            '<div class="kind">' + esc(r["Type"]) + " &middot; " + esc(r["Geographic scope"]) + "</div></div>" +
          feedBadge(r["Feed / API"] || r["Feed / alerts"]) +
        "</div>" +
        '<div class="desc">' + esc(r["What it covers"] || r["What it funds"] || "") + "</div>" +
        (r["Notes & relevance to digital health"]
          ? '<div class="note">' + esc(r["Notes & relevance to digital health"]) + "</div>" : "") +
        '<div class="row-meta">' +
          '<span class="tag tag-neutral">' + esc(r["Update cadence"] || r["Cadence"] || "") + "</span>" +
          (r["URL"] && r["URL"].indexOf("http") === 0
            ? '<a class="visit" href="' + esc(r["URL"]) + '" target="_blank" rel="noopener">Visit &rarr;</a>'
            : '<span class="kind">' + esc(r["URL"] || "") + "</span>") +
        "</div>" +
        (guide || "") +
      "</div>"
    );
  }

  /* ---------- page renderers ---------- */
  function renderHome() {
    var box = document.getElementById("home-opps");
    if (box) {
      var feat = sortByDeadline(allOpportunities()).slice(0, 3);
      box.innerHTML = feat.map(oppCard).join("");
    }
    var dirStats = document.getElementById("home-dir-stats");
    if (dirStats && D.directory) {
      dirStats.innerHTML =
        '<div class="stat"><div class="n">' + (D.directory.procurement_count || 0) +
          '</div><div class="l">Procurement sources</div></div>' +
        '<div class="stat"><div class="n">' + (D.directory.grant_count || 0) +
          '</div><div class="l">Grant &amp; funding sources</div></div>';
    }
    var gbox = document.getElementById("home-grants");
    if (gbox) {
      var g = sortByDeadline((D.grants && D.grants.grants) || []).slice(0, 3);
      gbox.innerHTML = g.map(oppCard).join("");
    }
  }

  function buildFilter(container, cats, onChange) {
    var current = "All";
    function paint() {
      container.querySelectorAll(".filter-chip").forEach(function (c) {
        c.classList.toggle("active", c.dataset.cat === current);
      });
    }
    container.innerHTML = ["All"].concat(cats).map(function (c) {
      return '<span class="filter-chip" data-cat="' + esc(c) + '">' + esc(c) + "</span>";
    }).join("");
    container.addEventListener("click", function (e) {
      var chip = e.target.closest(".filter-chip");
      if (!chip) return;
      current = chip.dataset.cat;
      paint();
      onChange(current);
    });
    paint();
  }

  function renderListPage(opts) {
    var listEl = document.getElementById(opts.listId);
    var searchEl = document.getElementById(opts.searchId);
    var filterEl = document.getElementById(opts.filterId);
    var countEl = document.getElementById(opts.countId);
    var closedToggleEl = document.getElementById(opts.closedToggleId || "");
    if (!listEl) return;
    var data = opts.data;
    var cats = [];
    data.forEach(function (r) {
      var c = opts.catOf(r);
      if (c && cats.indexOf(c) === -1) cats.push(c);
    });
    cats.sort();
    // Default: closed items hidden. Toggle reveals them.
    var state = { cat: "All", q: "", showClosed: false };
    var closedCount = opts.canClose
      ? data.filter(function (r) { return isClosed(r); }).length
      : 0;
    function apply() {
      var rows = data.filter(function (r) {
        var catOk = state.cat === "All" || opts.catOf(r) === state.cat;
        var qOk = !state.q || opts.textOf(r).toLowerCase().indexOf(state.q) > -1;
        var closedOk = state.showClosed || !isClosed(r);
        return catOk && qOk && closedOk;
      });
      listEl.innerHTML = rows.length
        ? rows.map(opts.render).join("")
        : '<div class="empty">Nothing matches that filter yet.</div>';
      if (countEl) {
        var totalForCount = state.showClosed
          ? data.length
          : data.filter(function (r) { return !isClosed(r); }).length;
        countEl.textContent = rows.length + " of " + totalForCount + " shown";
      }
    }
    if (filterEl) buildFilter(filterEl, cats, function (c) { state.cat = c; apply(); });
    if (searchEl) searchEl.addEventListener("input", function () {
      state.q = searchEl.value.trim().toLowerCase(); apply();
    });
    if (closedToggleEl && opts.canClose) {
      // Render the toggle inline. Count is visible in the label so the option
      // feels worthwhile even when collapsed.
      closedToggleEl.innerHTML = '<label class="closed-toggle">' +
        '<input type="checkbox" id="closed-toggle-input" /> ' +
        '<span>Show closed (' + closedCount + ')</span></label>';
      var cb = document.getElementById("closed-toggle-input");
      cb.addEventListener("change", function () {
        state.showClosed = cb.checked; apply();
      });
    }
    apply();
  }

  function renderOpportunities() {
    renderListPage({
      listId: "opp-list", searchId: "opp-search", filterId: "opp-filter",
      countId: "opp-count", closedToggleId: "opp-closed-toggle",
      canClose: true,
      data: sortByDeadline(allOpportunities()),
      catOf: function (o) { return o.category; },
      textOf: function (o) { return [o.title, o.source, o.summary, o.means, o.buyer].join(" "); },
      render: oppCard,
    });
  }
  function renderGrants() {
    renderListPage({
      listId: "grant-list", searchId: "grant-search", filterId: "grant-filter",
      countId: "grant-count", closedToggleId: "grant-closed-toggle",
      canClose: true,
      data: sortByDeadline((D.grants && D.grants.grants) || []),
      catOf: function (g) { return g.category; },
      textOf: function (g) { return [g.title, g.source, g.summary, g.means].join(" "); },
      render: oppCard,
    });
  }
  /* ---------- opportunity / grant detail page ---------- */
  function findItemById(id) {
    if (!id) return null;
    var all = allOpportunities();
    var grants = (D.grants && D.grants.grants) || [];
    var pool = all.concat(grants);
    for (var i = 0; i < pool.length; i++) {
      if (String(pool[i].id) === String(id)) return pool[i];
    }
    return null;
  }
  function relatedItems(item) {
    if (!item) return [];
    var pool = allOpportunities().concat((D.grants && D.grants.grants) || []);
    return pool
      .filter(function (o) { return o.id !== item.id && o.category === item.category; })
      .slice(0, 3);
  }
  function isGrant(item) {
    var grants = (D.grants && D.grants.grants) || [];
    for (var i = 0; i < grants.length; i++) {
      if (grants[i].id === item.id) return true;
    }
    return false;
  }
  function renderOpportunityDetail() {
    var root = document.getElementById("opp-detail");
    if (!root) return;
    var id = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    var item = findItemById(id);
    if (!item) {
      root.innerHTML = '<div class="not-found">' +
        "<h2>We can't find that opportunity.</h2>" +
        "<p>It may have been renamed or removed. Try the full list:</p>" +
        '<p><a class="btn btn-primary" href="/opportunities">Browse procurement</a> ' +
        '&nbsp;<a class="btn btn-ghost" href="/grants">Browse grants</a></p>' +
        "</div>";
      return;
    }
    var grant = isGrant(item);
    var trackLabel = grant ? "Grant" : "Procurement";
    var backLink = grant ? "/grants" : "/opportunities";
    var backLabel = grant ? "All grants" : "All procurement";
    var closed = isClosed(item);
    var d = daysUntil(item.deadline);
    var deadlineBadge;
    if (closed) {
      var closedLabel = item.deadline
        ? "Closed " + fmtDate(item.deadline)
        : (item.status || "Closed");
      deadlineBadge = '<span class="detail-pill closed">' + esc(closedLabel) + "</span>";
    } else if (item.deadline && d != null) {
      var urg = d <= 7 ? "urgent" : (d <= 21 ? "soon" : "open");
      deadlineBadge = '<span class="detail-pill ' + urg + '">' +
        "Closes " + fmtDate(item.deadline) +
        (d <= 21 ? " &middot; " + d + " day" + (d === 1 ? "" : "s") + " left" : "") +
        "</span>";
    } else {
      deadlineBadge = '<span class="detail-pill open">' + esc(item.status || "Ongoing") + "</span>";
    }
    var meansBlock = item.means
      ? '<div class="detail-means"><div class="detail-means-label">What it means</div>' +
        '<p>' + esc(item.means) + "</p></div>"
      : "";
    var summaryBlock = item.summary
      ? '<div class="detail-section"><h3>Summary</h3><p>' + esc(item.summary) + "</p></div>"
      : "";
    var meta = [];
    // Resolve the source string to a portal slug so "Source:" becomes a link
    // through to the matching how-to-apply guide on the directory page.
    var srcSlug = portalSlugForSource(item.source);
    if (item.source) {
      var srcHtml = srcSlug
        ? '<a class="source-link" href="/directory#portal-' + esc(srcSlug) + '">' + esc(item.source) + "</a>"
        : esc(item.source);
      meta.push("<strong>Source:</strong> " + srcHtml);
    }
    if (item.buyer && item.buyer !== item.source) meta.push("<strong>Buyer:</strong> " + esc(item.buyer));
    if (item.value) meta.push("<strong>Value:</strong> " + esc(item.value));
    if (item.type) meta.push("<strong>Type:</strong> " + esc(item.type));
    if (item.published) meta.push("<strong>Published:</strong> " + fmtDate(item.published));
    if (item.deadline) meta.push("<strong>Deadline:</strong> " + fmtDate(item.deadline));
    var metaBlock = meta.length
      ? '<div class="detail-meta"><dl>' +
        meta.map(function (m) {
          var idx = m.indexOf(":</strong>");
          return "<div><dt>" + m.slice(8, idx) + "</dt><dd>" + m.slice(idx + 11) + "</dd></div>";
        }).join("") +
        "</dl></div>"
      : "";
    var ctaInline = grant
      ? '<p class="detail-inline-cta">If you are looking for an investor backing UK healthtech at this stage, the ' +
        '<a href="/capital">Capital page</a> is the partner-listing route. Coming soon, register interest.</p>'
      : '<p class="detail-inline-cta">Need help writing this bid? Our ' +
        '<a href="/bid-writers">directory of NHS bid writers</a> opens shortly. ' +
        'Looking ahead at the funding rung, see the ' +
        '<a href="/capital">Capital page</a>.</p>';
    // "First time applying here?" callout. Only renders when we have a
    // matching portal guide for this opportunity's source.
    var portalCallout = "";
    if (srcSlug && PORTAL_BY_SLUG[srcSlug]) {
      var p = PORTAL_BY_SLUG[srcSlug];
      portalCallout =
        '<div class="portal-callout">' +
          '<div class="portal-callout-label">Before you click through</div>' +
          '<p>First time applying via <strong>' + esc(p.name) + '</strong>? ' +
          'The portal has its own registration, qualification questionnaires and gotchas. ' +
          '<a href="/directory#portal-' + esc(srcSlug) + '">Read the how-to-apply guide &rarr;</a></p>' +
        "</div>";
    }
    var sourceLink = item.source_url
      ? '<a class="btn btn-primary" href="' + esc(item.source_url) + '" target="_blank" rel="noopener">Read the official notice &rarr;</a>'
      : "";
    var related = relatedItems(item);
    var relatedBlock = related.length
      ? '<div class="detail-section"><h3>Related ' + trackLabel.toLowerCase() + " in " + esc(item.category || "this category") + "</h3>" +
        '<div class="opp-grid">' + related.map(oppCard).join("") + "</div></div>"
      : "";
    var categoryChip = item.category
      ? '<span class="detail-category">' + esc(item.category) + "</span>"
      : "";

    root.innerHTML =
      '<div class="detail-back"><a href="' + backLink + '">&larr; ' + esc(backLabel) + "</a></div>" +
      '<div class="detail-head">' +
        '<div class="detail-track">' + trackLabel + (categoryChip ? " &middot; " + categoryChip : "") + "</div>" +
        "<h1>" + esc(item.title) + "</h1>" +
        '<div class="detail-pills">' + deadlineBadge + "</div>" +
      "</div>" +
      meansBlock +
      metaBlock +
      summaryBlock +
      portalCallout +
      ctaInline +
      '<div class="detail-actions">' + sourceLink + "</div>" +
      relatedBlock;

    // Update the document title and meta description for the loaded item.
    document.title = item.title + " · MyClinical Growth";
  }

  /* ---------- unified search page ---------- */
  // Searches across standing procurement, live procurement, and grants.
  // Each item is tagged with its track so the user can see at-a-glance which
  // pool a hit came from, and filter via the tab chips.
  function renderSearch() {
    var listEl = document.getElementById("search-list");
    var inputEl = document.getElementById("search-input");
    var tabsEl = document.getElementById("search-tabs");
    var countEl = document.getElementById("search-count");
    if (!listEl || !inputEl) return;

    var standing = ((D.opportunities && D.opportunities.opportunities) || [])
      .map(function (o) { return Object.assign({}, o, { _track: "Frameworks" }); });
    var live = ((D.opportunitiesLive && D.opportunitiesLive.opportunities) || [])
      .map(function (o) { return Object.assign({}, o, { _track: "Live tenders" }); });
    var grants = ((D.grants && D.grants.grants) || [])
      .map(function (g) { return Object.assign({}, g, { _track: "Grants" }); });
    var pool = standing.concat(live).concat(grants);

    function textOf(o) {
      return [o.title, o.source, o.buyer, o.summary, o.means, o.category,
              (o.tags || []).join(" ")].join(" ").toLowerCase();
    }

    function trackCard(o) {
      var html = oppCard(o);
      // Inject the track label inside the tags row so it sits with the others.
      var label = '<span class="search-track-label">' + esc(o._track) + "</span>";
      return html.replace('<div class="tags">', '<div class="tags">' + label);
    }

    var state = { q: "", tab: "All" };
    function counts() {
      var q = state.q;
      function n(t) {
        return pool.filter(function (o) {
          var trackOk = t === "All" || o._track === t;
          var qOk = !q || textOf(o).indexOf(q) > -1;
          return trackOk && qOk;
        }).length;
      }
      return { All: n("All"), Frameworks: n("Frameworks"),
               "Live tenders": n("Live tenders"), Grants: n("Grants") };
    }
    function paintTabs() {
      var c = counts();
      tabsEl.innerHTML = ["All", "Frameworks", "Live tenders", "Grants"].map(function (t) {
        var cls = "search-tab" + (state.tab === t ? " active" : "");
        return '<span class="' + cls + '" data-tab="' + esc(t) + '">' +
               esc(t) + '<span class="n">' + c[t] + "</span></span>";
      }).join("");
    }
    function apply() {
      var rows = pool.filter(function (o) {
        var trackOk = state.tab === "All" || o._track === state.tab;
        var qOk = !state.q || textOf(o).indexOf(state.q) > -1;
        return trackOk && qOk;
      });
      // Sort: open first by deadline ascending, then closed at the bottom.
      rows.sort(function (a, b) {
        var ac = isClosed(a) ? 1 : 0, bc = isClosed(b) ? 1 : 0;
        if (ac !== bc) return ac - bc;
        var da = daysUntil(a.deadline), db = daysUntil(b.deadline);
        if (da == null && db == null) return 0;
        if (da == null) return 1;
        if (db == null) return -1;
        return da - db;
      });
      if (!state.q && state.tab === "All") {
        listEl.innerHTML = "";
        countEl.textContent = "";
        return;
      }
      listEl.innerHTML = rows.length
        ? rows.map(trackCard).join("")
        : '<div class="search-empty">Nothing matches that yet. Try a broader keyword, or <a href="/submit">tip us off</a> if we should be tracking it.</div>';
      countEl.textContent = rows.length + " result" + (rows.length === 1 ? "" : "s");
      paintTabs();
    }

    inputEl.addEventListener("input", function () {
      state.q = inputEl.value.trim().toLowerCase();
      // Reflect the query in the URL so it's shareable / bookmarkable.
      var url = new URL(location.href);
      if (state.q) url.searchParams.set("q", inputEl.value.trim());
      else url.searchParams.delete("q");
      history.replaceState(null, "", url.toString());
      apply();
    });
    tabsEl.addEventListener("click", function (e) {
      var t = e.target.closest(".search-tab");
      if (!t) return;
      state.tab = t.dataset.tab;
      apply();
    });

    // Honour ?q= in the URL on initial load.
    var initial = new URL(location.href).searchParams.get("q");
    if (initial) {
      inputEl.value = initial;
      state.q = initial.toLowerCase();
    }
    paintTabs();
    apply();
  }

  function renderDirectory() {
    var proc = ((D.directory && D.directory.procurement) || []).map(function (r) {
      r._group = "Procurement"; return r;
    });
    var grants = ((D.directory && D.directory.grants) || []).map(function (r) {
      r._group = "Grants & funding"; return r;
    });
    renderListPage({
      listId: "dir-list", searchId: "dir-search", filterId: "dir-filter", countId: "dir-count",
      data: proc.concat(grants),
      catOf: function (r) { return r._group; },
      textOf: function (r) {
        return [r["Source"], r["Type"], r["What it covers"], r["What it funds"],
                r["Notes & relevance to digital health"], r["Owner / operator"], r["Owner"]].join(" ");
      },
      render: dirItem,
    });
  }

  /* ---------- Subscribe form ----------
     Form POSTs natively to /.netlify/functions/subscribe (server-side
     Mailchimp API call). The function 302-redirects to /signing-up.html on
     success or /signup-error.html on failure. We intercept only to:
       1. Normalise the two-checkbox frequency picker into DAILY and WEEKLY
          hidden inputs so they POST as Mailchimp merge fields.
       2. Spam-guard the honeypot. */
  function wireSubscribe() {
    var form = document.getElementById("subscribe");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      var btn = form.querySelector("button");
      // Honeypot: bots fill hidden inputs. Block the submit silently.
      var hp = form.querySelector('input[name^="b_"]');
      if (hp && hp.value) { e.preventDefault(); return; }
      // Normalise DAILY and WEEKLY checkboxes into hidden inputs so they
      // POST as merge fields, not as checkbox name attributes. Unchecked
      // boxes still send "No" so the server knows the user actively declined.
      function ensureHidden(name, value) {
        var existing = form.querySelector('input[type="hidden"][name="' + name + '"]');
        if (existing) { existing.value = value; return; }
        var inp = document.createElement("input");
        inp.type = "hidden"; inp.name = name; inp.value = value;
        form.appendChild(inp);
      }
      var dailyBox = form.querySelector('input[data-freq="DAILY"]');
      var weeklyBox = form.querySelector('input[data-freq="WEEKLY"]');
      if (dailyBox || weeklyBox) {
        ensureHidden("DAILY", dailyBox && dailyBox.checked ? "Yes" : "No");
        ensureHidden("WEEKLY", weeklyBox && weeklyBox.checked ? "Yes" : "No");
      }
      // Visual feedback while the redirect lands.
      if (btn) { btn.textContent = "Joining…"; btn.disabled = true; }
      // Do NOT preventDefault: let the form POST natively to the function.
    });
  }

  /* ---------- updated-stamp ---------- */
  function stampUpdated() {
    var el = document.getElementById("updated-stamp");
    if (!el) return;
    var live = D.opportunitiesLive || {};
    if (live.updated) {
      el.textContent = "Live feed updated " + fmtDate(live.updated);
    } else {
      el.textContent = "Live feed activates on deploy";
    }
  }

  /* ---------- flag-banner live counts ----------
     The flag banner at the top of opportunities, grants and directory
     used to hard-code numbers ("18 grant programmes tracked") that drifted
     as we added items. These three spans are now filled at runtime from
     window.GROWTH_DATA so the banner is always honest. "Open" means
     deadline in the future and status not Closed/Completed/Awarded. */
  function fillFlagCounts() {
    var procEl = document.getElementById("flag-count-procurement");
    if (procEl) {
      procEl.textContent = String(allOpportunities()
        .filter(function (o) { return !isClosed(o); }).length);
    }
    var grantsEl = document.getElementById("flag-count-grants");
    if (grantsEl) {
      var g = (D.grants && D.grants.grants) || [];
      grantsEl.textContent = String(g
        .filter(function (x) { return !isClosed(x); }).length);
    }
    var dirEl = document.getElementById("flag-count-directory");
    if (dirEl) {
      var dir = D.directory || {};
      var n = (dir.procurement_count || 0) + (dir.grant_count || 0);
      // Fall back to the raw arrays if the count fields are missing.
      if (!n) {
        n = ((dir.procurement || []).length + (dir.grants || []).length);
      }
      dirEl.textContent = String(n);
    }
  }

  /* ---------- dispatch ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.getAttribute("data-page");
    if (page === "home") renderHome();
    if (page === "opportunities") renderOpportunities();
    if (page === "grants") renderGrants();
    if (page === "directory") renderDirectory();
    if (page === "opportunity") renderOpportunityDetail();
    if (page === "search") renderSearch();
    wireSubscribe();
    stampUpdated();
    fillFlagCounts();
  });
  // Detail page: re-render if the user navigates via hash (e.g. clicking
  // a related-item link on the same page).
  window.addEventListener("hashchange", function () {
    if (document.body.getAttribute("data-page") === "opportunity") {
      renderOpportunityDetail();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });
})();
