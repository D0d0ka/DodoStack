/* Dodo TV — custom HTML5 player */
(() => {
  "use strict";

  const shell = document.getElementById("player");
  const video = document.getElementById("video");
  const configEl = document.getElementById("player-config");
  if (!shell || !video || !configEl) return;

  let config = {};
  try {
    config = JSON.parse(configEl.textContent || "{}");
  } catch (error) {
    console.error("Could not parse player configuration", error);
  }

  const $ = (selector) => shell.querySelector(selector);
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  const playToggle = $("#play-toggle");
  const bigPlay = $("#big-play");
  const seekBar = $("#seek-bar");
  const seekBuffer = $("#seek-buffer");
  const seekPlayed = $("#seek-played");
  const seekThumb = $("#seek-thumb");
  const seekTooltip = $("#seek-tooltip");
  const currentTimeEl = $("#current-time");
  const durationEl = $("#duration");
  const pcTime = $("#pc-time");
  const muteToggle = $("#mute-toggle");
  const volumeInput = $("#volume");
  const fullscreenToggle = $("#fullscreen-toggle");
  const pipToggle = $("#pip-toggle");
  const subtitleToggle = $("#subtitles-toggle");
  const subtitleMenu = $("#subtitle-menu");
  const speedToggle = $("#speed-toggle");
  const speedMenu = $("#speed-menu");
  const resumeToast = $("#resume-toast");
  const resumeTime = $("#resume-time");
  const upNext = $("#up-next");
  const upNextCountdown = $("#up-next-countdown");
  const upNextCancel = $("#up-next-cancel");
  const prevButton = $("#prev-episode");
  const nextButton = $("#next-episode");
  const errorText = $("#player-error-text");
  const retryButton = $("#player-retry");
  const iconPlay = $(".icon-play");
  const iconPause = $(".icon-pause");
  const iconVolume = $(".icon-volume");
  const iconMuted = $(".icon-muted");

  let idleTimer = null;
  let nextTimer = null;
  let nextRemaining = 8;
  let lastSavedPosition = Number(config.progress) || 0;
  let saveInFlight = false;
  let pendingSave = null;
  let isSeeking = false;
  let resumeShown = false;
  let currentSubtitle = -1;

  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) {
      return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    }
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };

  const duration = () => (Number.isFinite(video.duration) ? video.duration : 0);

  /* Playback ------------------------------------------------------------- */
  const updatePlayState = () => {
    const paused = video.paused;
    shell.classList.toggle("paused", paused);
    shell.classList.remove("not-started");
    if (iconPlay) iconPlay.style.display = paused ? "" : "none";
    if (iconPause) iconPause.style.display = paused ? "none" : "";
    if (playToggle) {
      playToggle.setAttribute("aria-label", paused ? "Play" : "Pause");
    }
    if (!paused) {
      shell.classList.remove("error");
      scheduleIdle();
    } else {
      showControls();
    }
  };

  const togglePlay = () => {
    if (video.paused) {
      video.play().catch(() => {
        showError("Playback was blocked by the browser. Press play again to start.");
      });
    } else {
      video.pause();
    }
  };

  playToggle?.addEventListener("click", togglePlay);
  bigPlay?.addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);
  video.addEventListener("dblclick", () => toggleFullscreen());

  video.addEventListener("play", updatePlayState);
  video.addEventListener("pause", () => {
    updatePlayState();
    saveProgress();
  });
  video.addEventListener("playing", () => {
    shell.classList.remove("loading");
    shell.classList.remove("error");
    updatePlayState();
    window.setTimeout(() => {
      if (!video.paused) hideResume();
    }, 5000);
  });
  video.addEventListener("waiting", () => shell.classList.add("loading"));
  video.addEventListener("canplay", () => shell.classList.remove("loading"));
  video.addEventListener("seeking", () => shell.classList.add("loading"));
  video.addEventListener("seeked", () => shell.classList.remove("loading"));
  video.addEventListener("error", () => {
    const code = video.error?.code;
    const detail =
      code === 1 ? "Playback was aborted."
      : code === 2 ? "A network error interrupted the video."
      : code === 3 ? "The video could not be decoded by this browser."
      : code === 4 ? "The video format or file could not be played."
      : "The video could not be loaded.";
    showError(`${detail} Make sure the original file still exists on the server.`);
  });

  const showError = (message) => {
    if (errorText) errorText.textContent = message;
    shell.classList.add("error");
    shell.classList.remove("loading");
  };

  retryButton?.addEventListener("click", () => {
    shell.classList.remove("error");
    video.load();
    video.play().catch(() => {});
  });

  /* Timeline ------------------------------------------------------------- */
  const updateTimeline = () => {
    const total = duration();
    const current = video.currentTime || 0;
    const percent = total ? Math.min(100, Math.max(0, (current / total) * 100)) : 0;
    if (seekPlayed) seekPlayed.style.width = `${percent}%`;
    if (seekThumb) seekThumb.style.left = `${percent}%`;
    if (currentTimeEl) currentTimeEl.textContent = formatTime(current);
    if (durationEl) durationEl.textContent = formatTime(total);
    if (pcTime) pcTime.textContent = `${formatTime(current)} / ${formatTime(total)}`;
    if (seekBar) {
      seekBar.setAttribute("aria-valuenow", String(Math.round(percent)));
      seekBar.setAttribute("aria-valuetext", `${formatTime(current)} of ${formatTime(total)}`);
    }
    updateBuffer();
    maybeSave(current);
  };

  const updateBuffer = () => {
    if (!seekBuffer || !video.buffered.length) return;
    const total = duration();
    if (!total) return;
    let end = 0;
    for (let i = 0; i < video.buffered.length; i += 1) {
      if (video.buffered.start(i) <= video.currentTime + 0.5) {
        end = Math.max(end, video.buffered.end(i));
      }
    }
    seekBuffer.style.width = `${Math.min(100, (end / total) * 100)}%`;
  };

  video.addEventListener("timeupdate", updateTimeline);
  video.addEventListener("progress", updateBuffer);
  video.addEventListener("loadedmetadata", () => {
    updateTimeline();
    setInitialVolume();
    applySpeed(localStorage.getItem("dodo-speed") || "1", false);
    prepareResume();
    setupSubtitleDefault();
  });
  video.addEventListener("durationchange", updateTimeline);

  const seekToPosition = (event) => {
    const total = duration();
    if (!total || !seekBar) return;
    const rect = seekBar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    video.currentTime = ratio * total;
    updateTimeline();
  };

  seekBar?.addEventListener("pointerdown", (event) => {
    isSeeking = true;
    seekBar.setPointerCapture?.(event.pointerId);
    seekToPosition(event);
  });
  seekBar?.addEventListener("pointermove", (event) => {
    if (!seekBar) return;
    const rect = seekBar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    if (seekTooltip) {
      seekTooltip.textContent = formatTime(ratio * duration());
      seekTooltip.style.left = `${Math.min(96, Math.max(4, ratio * 100))}%`;
    }
    if (isSeeking) seekToPosition(event);
  });
  seekBar?.addEventListener("pointerup", (event) => {
    if (isSeeking) {
      seekToPosition(event);
      isSeeking = false;
    }
    saveProgress();
  });
  seekBar?.addEventListener("pointercancel", () => {
    isSeeking = false;
  });
  seekBar?.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "ArrowLeft") video.currentTime = Math.max(0, video.currentTime - 5);
    if (event.key === "ArrowRight") video.currentTime = Math.min(duration(), video.currentTime + 5);
    if (event.key === "Home") video.currentTime = 0;
    if (event.key === "End") video.currentTime = duration();
    updateTimeline();
  });

  /* Volume --------------------------------------------------------------- */
  const setInitialVolume = () => {
    const saved = Number(localStorage.getItem("dodo-volume"));
    video.volume = Number.isFinite(saved) ? Math.min(1, Math.max(0, saved)) : 1;
    video.muted = localStorage.getItem("dodo-muted") === "true";
    if (volumeInput) volumeInput.value = String(video.muted ? 0 : video.volume);
    updateVolumeIcon();
  };

  const updateVolumeIcon = () => {
    if (!iconVolume || !iconMuted) return;
    const muted = video.muted || video.volume === 0;
    iconVolume.style.display = muted ? "none" : "";
    iconMuted.style.display = muted ? "" : "none";
    muteToggle?.classList.toggle("is-active", muted);
    if (muteToggle) muteToggle.setAttribute("aria-label", muted ? "Unmute" : "Mute");
    if (volumeInput) volumeInput.value = muted ? "0" : String(video.volume);
  };

  muteToggle?.addEventListener("click", () => {
    video.muted = !video.muted;
    if (!video.muted && video.volume === 0) video.volume = 0.6;
    localStorage.setItem("dodo-muted", String(video.muted));
    localStorage.setItem("dodo-volume", String(video.volume));
    updateVolumeIcon();
  });
  volumeInput?.addEventListener("input", () => {
    const value = Number(volumeInput.value);
    video.volume = value;
    video.muted = value === 0;
    localStorage.setItem("dodo-volume", String(value));
    localStorage.setItem("dodo-muted", String(video.muted));
    updateVolumeIcon();
  });
  video.addEventListener("volumechange", updateVolumeIcon);

  /* Fullscreen / PiP ----------------------------------------------------- */
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      shell.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  };
  fullscreenToggle?.addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    fullscreenToggle?.classList.toggle("is-active", Boolean(document.fullscreenElement));
  });

  if (pipToggle) {
    if (!document.pictureInPictureEnabled || !video.requestPictureInPicture) {
      pipToggle.style.display = "none";
    } else {
      pipToggle.addEventListener("click", async () => {
        try {
          if (document.pictureInPictureElement) {
            await document.exitPictureInPicture();
          } else {
            await video.requestPictureInPicture();
          }
        } catch (error) {
          console.warn("Picture in picture unavailable", error);
        }
      });
    }
  }

  /* Menus ---------------------------------------------------------------- */
  const closeMenus = (except = null) => {
    [subtitleMenu, speedMenu].forEach((menu) => {
      if (menu && menu !== except) menu.classList.remove("open");
    });
    if (except) {
      const open = except.classList.contains("open");
      subtitleToggle?.setAttribute(
        "aria-expanded",
        String(open && except === subtitleMenu)
      );
      speedToggle?.setAttribute("aria-expanded", String(open && except === speedMenu));
    }
  };

  const toggleMenu = (menu, trigger) => {
    if (!menu) return;
    const willOpen = !menu.classList.contains("open");
    closeMenus(menu);
    menu.classList.toggle("open", willOpen);
    trigger?.setAttribute("aria-expanded", String(willOpen));
  };

  subtitleToggle?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu(subtitleMenu, subtitleToggle);
  });
  speedToggle?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu(speedMenu, speedToggle);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".player-menu") && !event.target.closest("#speed-toggle") && !event.target.closest("#subtitles-toggle")) {
      closeMenus();
    }
  });

  /* Subtitles ------------------------------------------------------------ */
  const setSubtitleMode = (index) => {
    const tracks = video.textTracks;
    for (let i = 0; i < tracks.length; i += 1) {
      tracks[i].mode = i === index ? "showing" : "disabled";
    }
    currentSubtitle = index;
    if (subtitleToggle) subtitleToggle.classList.toggle("is-active", index >= 0);
    subtitleMenu?.querySelectorAll("[data-subtitle]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.subtitle) === index);
    });
    localStorage.setItem("dodo-subtitle", String(index));
  };

  subtitleMenu?.querySelectorAll("[data-subtitle]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setSubtitleMode(Number(button.dataset.subtitle));
      closeMenus();
    });
  });

  const setupSubtitleDefault = () => {
    if (!config.subtitles || !config.subtitles.length) {
      setSubtitleMode(-1);
      return;
    }
    const saved = localStorage.getItem("dodo-subtitle");
    let index = -1;
    if (saved !== null) {
      index = Number(saved);
      if (!Number.isInteger(index) || index >= config.subtitles.length) index = -1;
    } else {
      index = config.subtitles.findIndex((track) => track.default);
    }
    setSubtitleMode(index);
  };

  /* Playback speed ------------------------------------------------------- */
  const applySpeed = (rate, announce = true) => {
    const value = Number(rate) || 1;
    video.playbackRate = value;
    if (speedMenu) {
      speedMenu.querySelectorAll("[data-speed]").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.speed) === value);
      });
    }
    if (announce) localStorage.setItem("dodo-speed", String(value));
  };

  speedMenu?.querySelectorAll("[data-speed]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      applySpeed(button.dataset.speed);
      closeMenus();
      showControls();
    });
  });

  /* Resume --------------------------------------------------------------- */
  const prepareResume = () => {
    const saved = Number(config.progress || 0);
    const total = duration();
    if (!saved || saved < 5 || !total || saved / total >= 0.95) return;
    try {
      video.currentTime = saved;
    } catch (error) {
      return;
    }
    if (resumeTime) resumeTime.textContent = formatTime(saved);
    resumeToast?.classList.add("show");
    resumeShown = true;
  };

  const hideResume = () => {
    resumeToast?.classList.remove("show");
  };

  $("#resume-start-over")?.addEventListener("click", () => {
    video.currentTime = 0;
    hideResume();
    saveProgress(false, true);
    video.play().catch(() => {});
  });
  $("#resume-dismiss")?.addEventListener("click", hideResume);

  /* Progress saving ------------------------------------------------------ */
  const flushProgress = async () => {
    if (saveInFlight || !pendingSave) return;
    const { position, total, keepalive } = pendingSave;
    pendingSave = null;
    if (position === lastSavedPosition) return;
    saveInFlight = true;
    try {
      const response = await fetch(config.saveUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ kind: config.type, id: config.id, position, duration: total }),
        keepalive,
        credentials: "same-origin",
      });
      if (response.ok) lastSavedPosition = position;
    } catch {
      // A subsequent playback event can retry the save.
    } finally {
      saveInFlight = false;
      flushProgress();
    }
  };

  const saveProgress = (keepalive = false, allowStart = false) => {
    if (!config.saveUrl || !config.id) return;
    const total = duration();
    const position = video.currentTime;
    if (!total || (position < 1 && !allowStart)) return;
    pendingSave = { position, total, keepalive: keepalive || Boolean(pendingSave?.keepalive) };
    flushProgress();
  };

  const maybeSave = (current) => {
    if (!saveInFlight && Math.abs(current - lastSavedPosition) >= 12) saveProgress();
  };

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveProgress(true);
  });
  window.addEventListener("pagehide", () => saveProgress(true));

  const handleEnded = () => {
    hideResume();
    saveProgress(false, true);
    shell.classList.add("paused");
    if (resumeToast) resumeToast.classList.remove("show");
    if (config.nextUrl && config.autoNext) {
      startUpNext();
    }
  };

  video.addEventListener("ended", handleEnded);

  /* Up next -------------------------------------------------------------- */
  const startUpNext = () => {
    if (!upNext) return;
    nextRemaining = 8;
    upNext.classList.add("show");
    if (upNextCountdown) upNextCountdown.textContent = `Playing in ${nextRemaining} seconds…`;
    clearInterval(nextTimer);
    nextTimer = window.setInterval(() => {
      nextRemaining -= 1;
      if (nextRemaining <= 0) {
        clearInterval(nextTimer);
        saveProgress(true);
        window.location.href = config.nextUrl;
        return;
      }
      if (upNextCountdown) upNextCountdown.textContent = `Playing in ${nextRemaining} seconds…`;
    }, 1000);
  };

  const stopUpNext = () => {
    clearInterval(nextTimer);
    nextTimer = null;
    upNext?.classList.remove("show");
  };

  upNextCancel?.addEventListener("click", stopUpNext);
  video.addEventListener("play", () => {
    if (video.currentTime < duration()) stopUpNext();
  });

  const navigate = (button) => {
    const href = button?.dataset.href;
    if (!href) return;
    saveProgress(true);
    window.location.href = href;
  };
  prevButton?.addEventListener("click", () => navigate(prevButton));
  nextButton?.addEventListener("click", () => navigate(nextButton));

  /* Controls visibility -------------------------------------------------- */
  const showControls = () => {
    shell.classList.remove("idle");
    scheduleIdle();
  };
  const scheduleIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => {
      if (!video.paused && !document.querySelector(".player-menu.open") && !resumeToast?.classList.contains("show") && !upNext?.classList.contains("show")) {
        shell.classList.add("idle");
      }
    }, 3200);
  };
  shell.addEventListener("pointermove", showControls);
  shell.addEventListener("pointerdown", showControls);
  shell.addEventListener("mouseleave", () => {
    if (!video.paused) shell.classList.add("idle");
  });
  shell.addEventListener("mouseenter", showControls);

  /* Keyboard shortcuts --------------------------------------------------- */
  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === " " || key === "k") {
      event.preventDefault();
      togglePlay();
    } else if (key === "arrowright" || key === "l") {
      event.preventDefault();
      video.currentTime = Math.min(duration(), video.currentTime + 10);
      showControls();
    } else if (key === "arrowleft" || key === "j") {
      event.preventDefault();
      video.currentTime = Math.max(0, video.currentTime - 10);
      showControls();
    } else if (key === "arrowup") {
      event.preventDefault();
      video.volume = Math.min(1, video.volume + 0.08);
      video.muted = false;
      updateVolumeIcon();
      showControls();
    } else if (key === "arrowdown") {
      event.preventDefault();
      video.volume = Math.max(0, video.volume - 0.08);
      updateVolumeIcon();
      showControls();
    } else if (key === "m") {
      video.muted = !video.muted;
      updateVolumeIcon();
      showControls();
    } else if (key === "f") {
      toggleFullscreen();
    }
  });

  updatePlayState();
  updateVolumeIcon();
  scheduleIdle();

  // Track metadata can be available a little after the DOM is parsed.
  window.setTimeout(setupSubtitleDefault, 150);
  window.setTimeout(() => {
    if (resumeShown && !video.paused) hideResume();
  }, 6000);
})();
