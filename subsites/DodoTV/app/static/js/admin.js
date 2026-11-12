/* Dodo TV — admin studio interactions */
(() => {
  "use strict";

  const sidebar = document.getElementById("admin-sidebar");
  const toggle = document.querySelector("[data-admin-toggle]");
  const setSidebarOpen = (open) => {
    sidebar?.classList.toggle("open", open);
    toggle?.setAttribute("aria-expanded", String(open));
  };
  toggle?.addEventListener("click", () => setSidebarOpen(!sidebar?.classList.contains("open")));
  document.addEventListener("click", (event) => {
    if (
      sidebar?.classList.contains("open") &&
      !sidebar.contains(event.target) &&
      !toggle?.contains(event.target)
    ) {
      setSidebarOpen(false);
    }
  });

  /* Bulk metadata apply --------------------------------------------------- */
  const bulkForm = document.querySelector("[data-bulk-form]");
  if (bulkForm) {
    const selectedInput = bulkForm.querySelector("#bulk-episode-ids");
    const checkboxes = Array.from(bulkForm.querySelectorAll("[data-episode-select]"));
    const countLabel = bulkForm.querySelector("[data-selected-count]");
    const selectAll = bulkForm.querySelector("#bulk-select-all");
    const targetRadios = Array.from(bulkForm.querySelectorAll('input[name="target"]'));
    const episodeList = bulkForm.querySelector(".bulk-episode-select-list");

    const targetValue = () =>
      targetRadios.find((radio) => radio.checked)?.value || "all";

    const update = () => {
      const selected = checkboxes.filter((box) => box.checked).length;
      if (countLabel) countLabel.textContent = `${selected} episode${selected === 1 ? "" : "s"} selected`;
      if (selectAll) {
        selectAll.checked = selected > 0 && selected === checkboxes.length;
        selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
      }
      if (episodeList) {
        episodeList.style.display = targetValue() === "selected" ? "" : "none";
      }
    };

    checkboxes.forEach((box) => box.addEventListener("change", update));
    selectAll?.addEventListener("change", () => {
      checkboxes.forEach((box) => {
        box.checked = selectAll.checked;
      });
      update();
    });
    targetRadios.forEach((radio) => radio.addEventListener("change", update));

    bulkForm.addEventListener("submit", (event) => {
      if (targetValue() === "selected") {
        const ids = checkboxes.filter((box) => box.checked).map((box) => box.value);
        if (!ids.length) {
          event.preventDefault();
          window.alert("Select at least one episode, or choose another target.");
          return;
        }
        if (selectedInput) selectedInput.value = ids.join(",");
      } else if (selectedInput) {
        selectedInput.value = "";
      }
    });
    update();
  }

  /* XHR upload progress --------------------------------------------------- */
  const overlay = document.getElementById("upload-overlay");
  const bar = document.getElementById("upload-bar");
  const percentLabel = document.getElementById("upload-percent");
  const statusLabel = document.getElementById("upload-status");
  const titleLabel = document.getElementById("upload-title");
  const noteLabel = document.getElementById("upload-note");

  const openOverlay = () => {
    if (!overlay) return;
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    if (bar) bar.style.width = "0%";
    if (percentLabel) percentLabel.textContent = "0%";
    if (statusLabel) statusLabel.textContent = "Preparing upload…";
    if (titleLabel) titleLabel.textContent = "Uploading media…";
    if (noteLabel) {
      noteLabel.textContent =
        "Please keep this tab open. Videos are validated and preview frames are generated after the upload completes.";
    }
  };
  const closeOverlay = () => {
    if (!overlay) return;
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
  };

  const showError = (message) => {
    const stack = document.querySelector(".toast-stack") || (() => {
      const div = document.createElement("div");
      div.className = "toast-stack";
      document.body.appendChild(div);
      return div;
    })();
    const toast = document.createElement("div");
    toast.className = "toast toast-error";
    toast.setAttribute("role", "status");
    const dot = document.createElement("span");
    dot.className = "toast-dot";
    const text = document.createElement("p");
    text.textContent = message;
    toast.append(dot, text);
    stack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 9000);
  };

  const humanSize = (bytes) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches("[data-upload-form]")) return;
    if (event.defaultPrevented) return;

    const fileInputs = Array.from(form.querySelectorAll('input[type="file"]'));
    const files = fileInputs.flatMap((input) => Array.from(input.files || []));
    if (!files.length) return;

    event.preventDefault();
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    openOverlay();

    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action || window.location.href, true);
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.setRequestHeader("Accept", "text/html, application/json");
    xhr.upload.addEventListener("progress", (progressEvent) => {
      if (!progressEvent.lengthComputable) return;
      const percent = Math.min(100, Math.round((progressEvent.loaded / progressEvent.total) * 100));
      if (bar) bar.style.width = `${percent}%`;
      if (percentLabel) percentLabel.textContent = `${percent}%`;
      if (statusLabel) {
        statusLabel.textContent = `${humanSize(progressEvent.loaded)} of ${humanSize(progressEvent.total)} uploaded`;
      }
      if (percent >= 100) {
        if (titleLabel) titleLabel.textContent = "Processing media…";
        if (statusLabel) statusLabel.textContent = "Upload complete · analysing video and generating frames";
        if (noteLabel) {
          noteLabel.textContent =
            "FFmpeg is analysing the video and generating preview frames. Larger files can take a little while — keep this tab open.";
        }
      }
    });

    xhr.addEventListener("load", () => {
      const contentType = xhr.getResponseHeader("Content-Type") || "";
      const isJson = contentType.includes("application/json");
      if (isJson) {
        let data = {};
        try {
          data = JSON.parse(xhr.responseText);
        } catch (error) {
          data = {};
        }
        closeOverlay();
        showError(data.error || `Upload failed (HTTP ${xhr.status}).`);
        return;
      }

      if (xhr.status === 0 || xhr.status >= 500) {
        closeOverlay();
        showError(`The server could not finish the upload (HTTP ${xhr.status || "network error"}).`);
        return;
      }

      const finalUrl = xhr.responseURL || window.location.href;
      let samePath = false;
      try {
        samePath = new URL(finalUrl).pathname === window.location.pathname;
      } catch (error) {
        samePath = false;
      }

      const doc = new DOMParser().parseFromString(xhr.responseText, "text/html");
      const hasSuccess = Boolean(doc.querySelector(".toast-success"));
      const hasError = Boolean(doc.querySelector(".toast-error, .toast-warning"));

      if (!samePath) {
        window.location.href = finalUrl;
        return;
      }
      if (hasSuccess && !hasError) {
        window.location.reload();
        return;
      }
      // Validation or media errors re-rendered the form. Replace the document so
      // inline field errors and flash messages stay visible.
      closeOverlay();
      document.open();
      document.write(xhr.responseText);
      document.close();
    });

    xhr.addEventListener("error", () => {
      closeOverlay();
      showError("The upload failed because the connection was interrupted.");
    });
    xhr.addEventListener("abort", () => {
      closeOverlay();
      showError("The upload was cancelled.");
    });

    xhr.send(new FormData(form));
  });

  /* Image preview for thumbnails ----------------------------------------- */
  document.querySelectorAll("[data-image-input]").forEach((input) => {
    input.addEventListener("change", () => {
      const target = document.querySelector(input.dataset.imageInput);
      const file = input.files?.[0];
      if (!target || !file) return;
      const url = URL.createObjectURL(file);
      if (target.tagName === "IMG") {
        target.src = url;
      }
    });
  });
})();
