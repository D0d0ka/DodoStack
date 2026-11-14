import {
  clearKey, decryptVault, deriveKey, encryptVault, fromBase64,
  generatePassword, newEntryId, randomBytes, supportsVaultCrypto,
} from "./crypto.js";

const byId = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const status = byId("status");
const unlockForm = byId("unlock-form");
const master = byId("master");
const confirmation = byId("confirmation");
const entryForm = byId("entry-form");
const passwordField = byId("entry-password");
const entriesList = byId("entry-list");
const idleDelay = 5 * 60 * 1000;

let activeVault = null;
let creating = false;
let editingId = null;
let lockGeneration = 0;
let idleTimeout;
let idleDeadline;

function report(message = "", error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}

async function vaultRequest(update) {
  let response;
  try {
    response = await fetch("/api/vault", update ? {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(update),
    } : undefined);
  } catch {
    throw new Error("DodoKey is unreachable. Try again.");
  }
  if (response.status === 401) {
    lockVault("Your session ended. Reload the page to sign in.", true);
    byId("locked-panel").hidden = true;
    throw new Error("Session ended.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || "DodoKey could not complete the request.");
  return data;
}

function setUnlockMode(isNew) {
  creating = isNew;
  byId("unlock-title").textContent = isNew ? "Create your vault" : "Unlock your vault";
  byId("unlock-submit").textContent = isNew ? "Create vault" : "Unlock vault";
  byId("setup-note").hidden = !isNew;
  byId("confirmation-field").hidden = !isNew;
  confirmation.required = isNew;
  master.minLength = isNew ? 12 : 0;
  master.autocomplete = isNew ? "new-password" : "current-password";
  confirmation.autocomplete = "new-password";
}

async function prepareUnlock() {
  const generation = lockGeneration;
  byId("unlock-fields").disabled = true;
  try {
    const data = await vaultRequest();
    if (generation !== lockGeneration) return;
    setUnlockMode(data.vault === null);
    byId("locked-panel").hidden = false;
  } catch (error) {
    if (generation === lockGeneration) report(error.message, true);
  } finally {
    if (generation === lockGeneration) byId("unlock-fields").disabled = false;
  }
}

function closeEditor() {
  editingId = null;
  entryForm.reset();
  setPasswordVisible(false);
  byId("editor").hidden = true;
}

function lockVault(message = "", error = false) {
  lockGeneration += 1;
  clearTimeout(idleTimeout);
  clearKey(activeVault?.key);
  activeVault = null;
  closeEditor();
  byId("entry-fields").disabled = false;
  unlockForm.reset();
  entriesList.replaceChildren();
  byId("open-panel").hidden = true;
  report(message, error);
}

function scheduleLock() {
  if (!activeVault) return;
  clearTimeout(idleTimeout);
  idleDeadline = Date.now() + idleDelay;
  idleTimeout = setTimeout(lockIfIdle, idleDelay);
}

function lockIfIdle() {
  if (activeVault && Date.now() >= idleDeadline) {
    lockVault("Vault locked after five minutes of inactivity.");
    prepareUnlock();
  }
}

async function unlock(event) {
  event.preventDefault();
  if (creating && master.value !== confirmation.value) {
    report("The vault passwords do not match.", true);
    return;
  }
  if (creating && master.value.length < 12) {
    report("Use at least 12 characters for your vault password.", true);
    return;
  }
  const generation = lockGeneration;
  byId("unlock-fields").disabled = true;
  report();
  let key;
  try {
    const data = await vaultRequest();
    if (generation !== lockGeneration) return;
    if (creating !== (data.vault === null)) {
      unlockForm.reset();
      setUnlockMode(data.vault === null);
      report("The vault changed. Enter your vault password again.", true);
      return;
    }
    const salt = creating ? randomBytes(16) : fromBase64(data.vault.salt);
    const keyPromise = deriveKey(master.value, salt);
    unlockForm.reset();
    key = await keyPromise;
    if (generation !== lockGeneration) return;

    let entries = [];
    let revision = data.revision;
    if (creating) {
      const vault = await encryptVault(entries, key, salt);
      if (generation !== lockGeneration) return;
      ({ revision } = await vaultRequest({ revision, vault }));
    } else {
      try {
        entries = await decryptVault(data.vault, key);
      } catch {
        throw new Error("Could not unlock the vault. Check your vault password.");
      }
    }
    if (generation !== lockGeneration) return;
    activeVault = { key, salt, revision, entries };
    key = undefined;
    byId("locked-panel").hidden = true;
    byId("open-panel").hidden = false;
    renderEntries();
    scheduleLock();
    byId("add").focus();
  } catch (error) {
    if (generation === lockGeneration) report(error.message, true);
  } finally {
    clearKey(key);
    if (generation === lockGeneration) byId("unlock-fields").disabled = false;
  }
}

async function copyPassword(password) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(password);
    return;
  }
  const previousFocus = document.activeElement;
  const field = document.createElement("textarea");
  field.className = "clipboard-field";
  field.readOnly = true;
  field.value = password;
  document.body.append(field);
  try {
    field.select();
    if (!document.execCommand("copy")) throw new Error("Clipboard unavailable.");
  } finally {
    field.value = "";
    field.remove();
    previousFocus?.focus();
  }
}

function renderEntries() {
  const entries = activeVault.entries;
  byId("empty").hidden = entries.length > 0;
  byId("entry-count").textContent = entries.length === 1 ? "1 saved login" : `${entries.length} saved logins`;
  entriesList.replaceChildren();
  for (const entry of entries) {
    const row = byId("entry-template").content.firstElementChild.cloneNode(true);
    row.querySelector(".entry-name").textContent = entry.name;
    row.querySelector(".entry-detail").textContent = [entry.website, entry.username].filter(Boolean).join(" · ");
    row.querySelector(".copy").addEventListener("click", async () => {
      try {
        await copyPassword(entry.password);
        if (activeVault) report("Password copied.");
      } catch {
        if (activeVault) report("Clipboard unavailable. Open the login to copy its password.", true);
      }
    });
    row.querySelector(".edit").addEventListener("click", () => openEditor(entry));
    entriesList.append(row);
  }
}

function setPasswordVisible(visible) {
  passwordField.type = visible ? "text" : "password";
  byId("show-password").textContent = visible ? "Hide password" : "Show password";
  byId("show-password").setAttribute("aria-pressed", String(visible));
}

function openEditor(entry) {
  if (byId("entry-fields").disabled) return;
  closeEditor();
  editingId = entry?.id ?? null;
  byId("editor-title").textContent = entry ? "Edit login" : "Add login";
  byId("entry-title").value = entry?.name ?? "";
  byId("entry-website").value = entry?.website ?? "";
  byId("entry-username").value = entry?.username ?? "";
  passwordField.value = entry?.password ?? "";
  byId("remove").hidden = !entry;
  byId("editor").hidden = false;
  byId("entry-title").focus();
}

async function persistEntries(entries, message) {
  const current = activeVault;
  const fields = byId("entry-fields");
  fields.disabled = true;
  report();
  try {
    const vault = await encryptVault(entries, current.key, current.salt);
    if (activeVault !== current) return;
    const data = await vaultRequest({ revision: current.revision, vault });
    if (activeVault !== current) return;
    current.revision = data.revision;
    current.entries = entries;
    closeEditor();
    renderEntries();
    byId("add").focus();
    report(message);
  } catch (error) {
    if (activeVault === current) report(error.message, true);
  } finally {
    if (activeVault === current) fields.disabled = false;
  }
}

if (supportsVaultCrypto) {
  prepareUnlock();
} else {
  report("DodoKey needs a browser that can generate secure random values.", true);
}

unlockForm.addEventListener("submit", unlock);
byId("lock").addEventListener("click", () => {
  lockVault();
  prepareUnlock();
});
byId("add").addEventListener("click", () => openEditor());
byId("cancel").addEventListener("click", closeEditor);
byId("show-password").addEventListener("click", () => setPasswordVisible(passwordField.type === "password"));
byId("generate").addEventListener("click", () => {
  passwordField.value = generatePassword();
});
entryForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = byId("entry-title").value.trim();
  if (!activeVault || !name) {
    report("Enter a name for this login.", true);
    return;
  }
  const entry = {
    id: editingId ?? newEntryId(),
    name,
    website: byId("entry-website").value.trim(),
    username: byId("entry-username").value.trim(),
    password: passwordField.value,
  };
  const entries = editingId
    ? activeVault.entries.map((item) => item.id === editingId ? entry : item)
    : [...activeVault.entries, entry];
  persistEntries(entries, "Login saved.");
});
byId("remove").addEventListener("click", () => {
  if (activeVault && editingId && confirm("Delete this login?")) {
    persistEntries(activeVault.entries.filter((entry) => entry.id !== editingId), "Login deleted.");
  }
});
for (const event of ["pointerdown", "keydown"]) {
  document.addEventListener(event, () => {
    lockIfIdle();
    scheduleLock();
  });
}
document.addEventListener("visibilitychange", lockIfIdle);
window.addEventListener("pagehide", () => lockVault());
window.addEventListener("pageshow", (event) => {
  if (event.persisted && supportsVaultCrypto) prepareUnlock();
});
