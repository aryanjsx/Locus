// Locus site: the interactive preview and the download button.
(function () {
  "use strict";

  var REPO = "aryanjsx/Locus";
  var RELEASES_PAGE = "https://github.com/" + REPO + "/releases";
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------------------------------------------------------------------------
  // Download button
  //
  // Looks for the newest published release with a Windows .exe attached. Until
  // there is one, the button explains that the build isn't out yet. On the day a
  // release is published it becomes a direct download, no site change needed.
  // If GitHub can't be reached, the button keeps linking to the releases page.
  // ---------------------------------------------------------------------------

  var CACHE_KEY = "locus-release";
  var CACHE_MS = 10 * 60 * 1000; // GitHub allows 60 unauthenticated API calls/hour

  function findInstaller(releases) {
    for (var i = 0; i < releases.length; i++) {
      var r = releases[i];
      if (r.draft) continue;
      for (var j = 0; j < (r.assets || []).length; j++) {
        var a = r.assets[j];
        if (/\.exe$/i.test(a.name)) {
          return {
            url: a.browser_download_url,
            version: r.tag_name,
            sizeMb: Math.round(a.size / 1e6),
            prerelease: !!r.prerelease
          };
        }
      }
    }
    return null;
  }

  function loadRelease() {
    try {
      var cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
      if (cached && Date.now() - cached.at < CACHE_MS) return Promise.resolve(cached.installer);
    } catch (e) { /* storage unavailable: just fetch */ }

    return fetch("https://api.github.com/repos/" + REPO + "/releases?per_page=10", {
      headers: { Accept: "application/vnd.github+json" }
    }).then(function (res) {
      if (!res.ok) throw new Error("GitHub API " + res.status);
      return res.json();
    }).then(function (releases) {
      var installer = findInstaller(releases);
      try {
        sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), installer: installer }));
      } catch (e) { /* ignore */ }
      return installer;
    });
  }

  function setupDownloads() {
    var buttons = document.querySelectorAll("[data-download]");
    var notes = document.querySelectorAll("[data-download-note]");
    var template = document.querySelector("[data-download-soon]");
    if (!buttons.length) return;

    // Each button gets its own "not out yet" notice right below its note, so clicking
    // the lower button doesn't open something at the top of the page.
    function noticeFor(button) {
      var section = button.closest("section") || document.body;
      var existing = section.querySelector("[data-download-soon]");
      if (existing || !template) return existing;
      var copy = template.cloneNode(true);
      var note = section.querySelector("[data-download-note]");
      (note || button).insertAdjacentElement("afterend", copy);
      return copy;
    }

    loadRelease().then(function (installer) {
      if (installer) {
        var detail = installer.version + " · " + installer.sizeMb + " MB · Windows 10 and 11" +
          (installer.prerelease ? " · beta" : "");
        buttons.forEach(function (b) { b.href = installer.url; b.removeAttribute("data-state"); });
        notes.forEach(function (n) { n.textContent = detail; });
        return;
      }
      // No installer published yet.
      buttons.forEach(function (b) {
        var soon = noticeFor(b);
        b.setAttribute("data-state", "soon");
        b.setAttribute("role", "button");
        b.setAttribute("aria-expanded", "false");
        b.addEventListener("click", function (event) {
          event.preventDefault();
          if (!soon) { window.location.href = RELEASES_PAGE; return; }
          var opening = soon.hidden;
          soon.hidden = !opening;
          b.setAttribute("aria-expanded", String(opening));
          if (opening) {
            soon.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
          }
        });
      });
    }).catch(function () {
      // Offline or rate-limited: leave the plain link to the releases page.
    });
  }

  // ---------------------------------------------------------------------------
  // Interactive preview
  // ---------------------------------------------------------------------------

  var log = document.querySelector("[data-demo-log]");
  var chipBox = document.querySelector("[data-demo-chips]");
  var timer = null;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function reply(text, tag) {
    var li = el("li", "msg msg--locus");
    li.appendChild(el("p", null, text));
    if (tag) li.appendChild(el("span", "msg__tag", tag));
    return li;
  }

  // A confirm card: `question`, buttons [primary, secondary], outcomes, seconds.
  function confirmCard(opts) {
    var li = el("li", "msg msg--locus");
    var card = el("div", "confirm");
    card.appendChild(el("p", null, opts.question));
    var actions = el("div", "confirm__actions");
    var yes = el("button", "is-primary", opts.yes);
    var no = el("button", null, opts.no);
    yes.type = no.type = "button";
    actions.appendChild(yes);
    actions.appendChild(no);
    card.appendChild(actions);
    var countdown = el("p", "confirm__timer");
    card.appendChild(countdown);
    li.appendChild(card);
    li.appendChild(el("span", "msg__tag", opts.tag));

    var left = 15;
    function tick() { countdown.textContent = opts.hint + " · cancels in " + left + " s"; }
    function finish(text) {
      clearInterval(timer);
      timer = null;
      card.replaceChildren(el("p", null, text));
    }
    tick();
    timer = setInterval(function () {
      left -= 1;
      if (left <= 0) { finish(opts.timedOut); return; }
      tick();
    }, 1000);
    yes.addEventListener("click", function () { finish(opts.done); });
    no.addEventListener("click", function () { finish(opts.cancelled); });
    // Focus Confirm so "Press Enter" really works, as it will in the app.
    setTimeout(function () { yes.focus({ preventScroll: true }); }, 0);
    return li;
  }

  var SCENARIOS = [
    {
      say: "what's my CPU at?",
      answer: function () {
        return reply("Your CPU is at 23%. Chrome is using the most right now.",
          "Looks something up · done instantly");
      }
    },
    {
      say: "delete notes.txt from Documents",
      answer: function () {
        return confirmCard({
          question: "Delete notes.txt from Documents? It will go to the Recycle Bin.",
          yes: "Confirm",
          no: "Cancel",
          hint: "Press Enter or click Confirm",
          done: "Moved notes.txt to the Recycle Bin.",
          cancelled: "Cancelled. Nothing was deleted.",
          timedOut: "No answer, so I cancelled. Nothing was deleted.",
          tag: "Deletes a file · needs your click, a spoken “yes” isn't enough"
        });
      }
    },
    {
      say: "how do I delete a branch in git?",
      answer: function () {
        var li = el("li", "msg msg--locus");
        var p = el("p");
        p.append("Run ");
        p.appendChild(el("code", null, "git branch -d <name>"));
        p.append(". Use ");
        p.appendChild(el("code", null, "-D"));
        p.append(" instead to delete a branch that hasn't been merged.");
        li.appendChild(p);
        li.appendChild(el("span", "msg__tag", "A question, so nothing on your PC was touched"));
        return li;
      }
    },
    {
      say: "close Notepad",
      answer: function () {
        return confirmCard({
          question: "Have you saved your work in Notepad?",
          yes: "Yes, close it",
          no: "No",
          hint: "Answer by voice, typing or a click",
          done: "Closed Notepad.",
          cancelled: "Okay, I left Notepad open.",
          timedOut: "No answer, so I left Notepad open.",
          tag: "Could lose unsaved work · asks first"
        });
      }
    },
    {
      say: "what's the weather today?",
      answer: function () {
        return reply("I can't look that up yet. Checking the weather needs the internet, " +
          "and Locus works offline.", "Never guesses live information");
      }
    }
  ];

  function play(index) {
    if (timer) { clearInterval(timer); timer = null; }
    var s = SCENARIOS[index];
    chipBox.querySelectorAll(".chip").forEach(function (c, i) {
      c.setAttribute("aria-pressed", String(i === index));
    });
    log.replaceChildren(el("li", "msg msg--you", s.say));
    var show = function () { log.appendChild(s.answer()); };
    if (reduceMotion) show(); else setTimeout(show, 380);
  }

  function setupDemo() {
    if (!log || !chipBox) return;
    SCENARIOS.forEach(function (s, i) {
      var chip = el("button", "chip", s.say);
      chip.type = "button";
      chip.setAttribute("aria-pressed", "false");
      chip.addEventListener("click", function () { play(i); });
      chipBox.appendChild(chip);
    });
    // Start with a plain example; no timers running until the visitor clicks.
    log.replaceChildren(el("li", "msg msg--you", SCENARIOS[0].say), SCENARIOS[0].answer());
    chipBox.firstChild.setAttribute("aria-pressed", "true");
  }

  setupDemo();
  setupDownloads();
})();
