/* MyClinical Growth — rendering engine. Reads window.GROWTH_DATA (assets/js/data.js). */
(function () {
  "use strict";
  var D = window.GROWTH_DATA || {};

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
  function oppCard(o) {
    var d = daysUntil(o.deadline);
    var deadlineHtml;
    if (o.deadline && d != null) {
      var cls = d <= 21 ? "deadline" : "deadline soft";
      deadlineHtml = '<span class="' + cls + '">Closes ' + fmtDate(o.deadline) + "</span>";
    } else {
      deadlineHtml = '<span class="deadline soft">' + esc(o.status || "Ongoing") + "</span>";
    }
    var meansHtml = o.means
      ? '<p class="means">' + esc(o.means) + "</p>"
      : '<p class="means">' + esc(o.summary || "") + "</p>";
    var link = o.source_url || "#";
    return (
      '<div class="opp-card">' +
        '<div class="tags">' + tagsHtml(o.tags) + "</div>" +
        "<h3>" + esc(o.title) + "</h3>" +
        '<div class="src">' + esc(o.source) +
          (o.value ? " · " + esc(o.value) : "") + "</div>" +
        meansHtml +
        '<div class="foot">' + deadlineHtml +
          '<a class="arrow" href="' + esc(link) + '" target="_blank" rel="noopener">Details &rarr;</a>' +
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
  function dirItem(r) {
    return (
      '<div class="dir-item">' +
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
    if (!listEl) return;
    var data = opts.data;
    var cats = [];
    data.forEach(function (r) {
      var c = opts.catOf(r);
      if (c && cats.indexOf(c) === -1) cats.push(c);
    });
    cats.sort();
    var state = { cat: "All", q: "" };
    function apply() {
      var rows = data.filter(function (r) {
        var catOk = state.cat === "All" || opts.catOf(r) === state.cat;
        var qOk = !state.q || opts.textOf(r).toLowerCase().indexOf(state.q) > -1;
        return catOk && qOk;
      });
      listEl.innerHTML = rows.length
        ? rows.map(opts.render).join("")
        : '<div class="empty">Nothing matches that filter yet.</div>';
      if (countEl) countEl.textContent = rows.length + " of " + data.length + " shown";
    }
    if (filterEl) buildFilter(filterEl, cats, function (c) { state.cat = c; apply(); });
    if (searchEl) searchEl.addEventListener("input", function () {
      state.q = searchEl.value.trim().toLowerCase(); apply();
    });
    apply();
  }

  function renderOpportunities() {
    renderListPage({
      listId: "opp-list", searchId: "opp-search", filterId: "opp-filter", countId: "opp-count",
      data: sortByDeadline(allOpportunities()),
      catOf: function (o) { return o.category; },
      textOf: function (o) { return [o.title, o.source, o.summary, o.means, o.buyer].join(" "); },
      render: oppCard,
    });
  }
  function renderGrants() {
    renderListPage({
      listId: "grant-list", searchId: "grant-search", filterId: "grant-filter", countId: "grant-count",
      data: sortByDeadline((D.grants && D.grants.grants) || []),
      catOf: function (g) { return g.category; },
      textOf: function (g) { return [g.title, g.source, g.summary, g.means].join(" "); },
      render: oppCard,
    });
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

  /* ---------- Mailchimp subscribe (JSONP, mirrors lukekellaway.com) ---------- */
  function wireSubscribe() {
    var form = document.getElementById("subscribe");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var msg = document.getElementById("form-msg");
      var btn = form.querySelector("button");
      var action = form.getAttribute("action") || "";
      if (action.indexOf("YOUR_MAILCHIMP") > -1 || !action) {
        msg.className = "form-msg error";
        msg.textContent = "Mailchimp not connected yet — see the deploy guide (README).";
        return;
      }
      var hp = form.querySelector('input[name^="b_"]');
      if (hp && hp.value) return;
      msg.className = "form-msg"; msg.textContent = "";
      var orig = btn.textContent; btn.textContent = "Sending…";
      var cb = "mc_cb_" + Date.now();
      var url = action.replace("/post?", "/post-json?");
      var params = new URLSearchParams(new FormData(form));
      window[cb] = function (res) {
        delete window[cb];
        document.head.removeChild(script);
        btn.textContent = orig;
        if (res && res.result === "success") {
          msg.className = "form-msg success";
          msg.textContent = "You're in. Your first brief lands tomorrow morning.";
          form.reset();
        } else {
          msg.className = "form-msg error";
          msg.textContent = (res && res.msg ? res.msg : "Something went wrong — try again.")
            .replace(/<[^>]*>/g, "");
        }
      };
      var script = document.createElement("script");
      script.src = url + "&" + params.toString() + "&c=" + cb;
      script.onerror = function () {
        delete window[cb]; btn.textContent = orig;
        msg.className = "form-msg error"; msg.textContent = "Network error — try again.";
      };
      document.head.appendChild(script);
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

  /* ---------- dispatch ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.getAttribute("data-page");
    if (page === "home") renderHome();
    if (page === "opportunities") renderOpportunities();
    if (page === "grants") renderGrants();
    if (page === "directory") renderDirectory();
    wireSubscribe();
    stampUpdated();
  });
})();
