/* Dodo TV — shared interactions */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  /* Header scroll state --------------------------------------------------- */
  const header = $("#site-header");
  const setHeader = () => {
    if (!header) return;
    header.classList.toggle("scrolled", window.scrollY > 24);
  };
  setHeader();
  window.addEventListener("scroll", setHeader, { passive: true });

  /* Mobile navigation ----------------------------------------------------- */
  const navToggle = $("[data-nav-toggle]");
  const mainNav = $("#main-nav");
  if (navToggle && mainNav) {
    navToggle.addEventListener("click", () => {
      const open = mainNav.classList.toggle("open");
      navToggle.classList.toggle("open", open);
      navToggle.setAttribute("aria-expanded", String(open));
      document.body.classList.toggle("no-scroll", open);
    });
    $$("a", mainNav).forEach((link) =>
      link.addEventListener("click", () => {
        mainNav.classList.remove("open");
        navToggle.classList.remove("open");
        navToggle.setAttribute("aria-expanded", "false");
        document.body.classList.remove("no-scroll");
      })
    );
    document.addEventListener("click", (event) => {
      if (!mainNav.contains(event.target) && !navToggle.contains(event.target)) {
        mainNav.classList.remove("open");
        navToggle.classList.remove("open");
        document.body.classList.remove("no-scroll");
      }
    });
  }

  /* User dropdown --------------------------------------------------------- */
  $$("[data-dropdown]").forEach((menu) => {
    const toggle = $("[data-dropdown-toggle]", menu);
    if (!toggle) return;
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = menu.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) {
        menu.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  });

  /* Toasts ---------------------------------------------------------------- */
  const dismissToast = (toast) => {
    if (!toast || toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 320);
  };
  $$(".toast").forEach((toast) => {
    $("[data-toast-close]", toast)?.addEventListener("click", () => dismissToast(toast));
    window.setTimeout(() => dismissToast(toast), 7000);
  });

  /* Trailer modal --------------------------------------------------------- */
  const modal = $("#trailer-modal");
  const trailerVideo = $("#trailer-video");
  const trailerTitle = $("#trailer-title");
  let lastTrailerTrigger = null;
  const closeTrailer = () => {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("no-scroll");
    if (trailerVideo) {
      trailerVideo.pause();
      trailerVideo.removeAttribute("src");
      trailerVideo.load();
    }
    lastTrailerTrigger?.focus();
  };
  const openTrailer = (url, title, subtitles, trigger) => {
    if (!modal || !trailerVideo) return;
    lastTrailerTrigger = trigger || null;
    trailerTitle.textContent = title || "Trailer";
    // Remove previous subtitle tracks
    $$("track", trailerVideo).forEach((t) => t.remove());
    trailerVideo.src = url;
    // Inject subtitle tracks
    if (Array.isArray(subtitles)) {
      subtitles.forEach((sub) => {
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.src = sub.url;
        track.label = sub.label;
        track.srclang = sub.language;
        if (sub.default) track.default = true;
        trailerVideo.appendChild(track);
      });
    }
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("no-scroll");
    trailerVideo.play().catch(() => {});
    $("[data-trailer-close]", modal)?.focus();
  };
  $$("[data-trailer]").forEach((button) => {
    button.addEventListener("click", () => {
      let subtitles = null;
      try { subtitles = button.dataset.trailerSubtitles ? JSON.parse(button.dataset.trailerSubtitles) : null; } catch (_) {}
      openTrailer(button.dataset.trailer, button.dataset.trailerTitle, subtitles, button);
    });
  });
  $$("[data-trailer-close]").forEach((button) =>
    button.addEventListener("click", closeTrailer)
  );
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (modal?.classList.contains("open")) closeTrailer();
      if (lightbox.classList.contains("open")) closeLightbox();
      $$(".player-menu.open").forEach((menu) => menu.classList.remove("open"));
    }
  });

  /* Horizontal content rows ---------------------------------------------- */
  $$("[data-scroll]").forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest(".content-row");
      const scroller = row ? $("[data-scroller]", row) : null;
      if (!scroller) return;
      const direction = Number(button.dataset.scroll || 1);
      scroller.scrollBy({ left: direction * scroller.clientWidth * 0.82, behavior: "smooth" });
    });
  });

  /* Reveal on scroll ------------------------------------------------------ */
  const revealItems = $$(".reveal");
  if ("IntersectionObserver" in window && revealItems.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -40px 0px" }
    );
    revealItems.forEach((item) => observer.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add("visible"));
  }

  /* Confirm destructive actions ------------------------------------------ */
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const message = form.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  });
  $$("button[data-confirm]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!window.confirm(button.dataset.confirm)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  /* Upload drop-field feedback ------------------------------------------- */
  $$(".file-drop input[type=file]").forEach((input) => {
    input.addEventListener("change", () => {
      const drop = input.closest(".file-drop");
      const label = $(".file-drop-filename", drop || document);
      const files = Array.from(input.files || []);
      if (drop) drop.classList.toggle("has-file", files.length > 0);
      if (label) {
        if (!files.length) {
          label.textContent = label.dataset.empty || "No file selected";
        } else if (files.length === 1) {
          const file = files[0];
          label.textContent = `${file.name} · ${(file.size / (1024 * 1024)).toFixed(1)} MB`;
        } else {
          const total = files.reduce((sum, file) => sum + file.size, 0);
          label.textContent = `${files.length} files selected · ${(total / (1024 * 1024)).toFixed(1)} MB`;
        }
      }
    });
  });

  /* Hero carousel --------------------------------------------------------- */
  const hero = $("[data-hero-carousel]");
  if (hero) {
    const slides = $$("[data-hero-slide]", hero);
    const dots = $$("[data-hero-dot]", hero);
    if (slides.length > 1) {
      let current = 0;
      let timer = null;
      const show = (index) => {
        current = (index + slides.length) % slides.length;
        slides.forEach((slide, i) => slide.classList.toggle("active", i === current));
        dots.forEach((dot, i) => {
          dot.classList.toggle("active", i === current);
          dot.setAttribute("aria-selected", String(i === current));
        });
      };
      const start = () => {
        stop();
        timer = window.setInterval(() => show(current + 1), 8500);
      };
      const stop = () => {
        if (timer) window.clearInterval(timer);
        timer = null;
      };
      dots.forEach((dot, index) =>
        dot.addEventListener("click", () => {
          show(index);
          start();
        })
      );
      hero.addEventListener("mouseenter", stop);
      hero.addEventListener("mouseleave", start);
      start();
    }
  }

  /* Keyboard shortcut: focus search -------------------------------------- */
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      const search = $(".nav-search input");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    }
  });

  /* Gallery lightbox ------------------------------------------------------ */
  const lightbox = document.createElement("div");
  lightbox.className = "lightbox";
  lightbox.setAttribute("role", "dialog");
  lightbox.setAttribute("aria-modal", "true");
  lightbox.setAttribute("aria-hidden", "true");
  lightbox.setAttribute("aria-label", "Frame viewer");
  lightbox.innerHTML = `
    <div class="lightbox-backdrop" data-lb-close></div>
    <div class="lightbox-wrap">
      <button type="button" class="lightbox-close" data-lb-close aria-label="Close">
        <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>
      </button>
      <img class="lightbox-img" src="" alt="">
      <div class="lightbox-footer" style="display:flex;align-items:center;gap:16px;justify-content:center">
        <button type="button" class="lightbox-prev" aria-label="Previous frame">
          <svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>
        </button>
        <span class="lightbox-counter"></span>
        <button type="button" class="lightbox-next" aria-label="Next frame">
          <svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>
        </button>
      </div>
      <p class="lightbox-caption"></p>
    </div>`;
  document.body.appendChild(lightbox);

  const lbImg = $(".lightbox-img", lightbox);
  const lbCaption = $(".lightbox-caption", lightbox);
  const lbCounter = $(".lightbox-counter", lightbox);
  let lbItems = [];
  let lbIndex = 0;
  let lastLbTrigger = null;

  const showLbFrame = (index) => {
    lbIndex = (index + lbItems.length) % lbItems.length;
    const item = lbItems[lbIndex];
    lbImg.src = item.src;
    lbImg.alt = item.caption || "";
    lbCaption.textContent = item.caption || "";
    lbCounter.textContent = `${lbIndex + 1} / ${lbItems.length}`;
    $(".lightbox-prev", lightbox).style.display = lbItems.length < 2 ? "none" : "";
    $(".lightbox-next", lightbox).style.display = lbItems.length < 2 ? "none" : "";
  };
  const openLightbox = (items, startIndex, trigger) => {
    lbItems = items;
    lastLbTrigger = trigger || null;
    showLbFrame(startIndex);
    lightbox.classList.add("open");
    lightbox.setAttribute("aria-hidden", "false");
    document.body.classList.add("no-scroll");
    $(".lightbox-close", lightbox).focus();
  };
  const closeLightbox = () => {
    lightbox.classList.remove("open");
    lightbox.setAttribute("aria-hidden", "true");
    document.body.classList.remove("no-scroll");
    lastLbTrigger?.focus();
  };

  $$("[data-lb-close]", lightbox).forEach((el) =>
    el.addEventListener("click", closeLightbox)
  );
  $(".lightbox-prev", lightbox).addEventListener("click", () => showLbFrame(lbIndex - 1));
  $(".lightbox-next", lightbox).addEventListener("click", () => showLbFrame(lbIndex + 1));
  lightbox.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") showLbFrame(lbIndex - 1);
    if (event.key === "ArrowRight") showLbFrame(lbIndex + 1);
  });

  $$(".gallery-panel").forEach((panel) => {
    const items = $$(".gallery-item", panel).map((btn) => ({
      src: btn.dataset.gallerySrc,
      caption: btn.dataset.galleryCaption,
      label: btn.dataset.galleryLabel,
    }));
    $$(".gallery-item", panel).forEach((btn, i) => {
      btn.addEventListener("click", () => openLightbox(items, i, btn));
    });
  });

  /* Footer year is server rendered; nothing else to initialize. */
})();
