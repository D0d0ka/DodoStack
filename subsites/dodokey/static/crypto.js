const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const associatedData = encoder.encode("DodoKey vault v1");

function toBase64(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function fromBase64(value) {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}

export function randomBytes(length) {
  return crypto.getRandomValues(new Uint8Array(length));
}

export async function deriveKey(password, salt) {
  const bytes = encoder.encode(password);
  try {
    const material = await crypto.subtle.importKey("raw", bytes, "PBKDF2", false, ["deriveKey"]);
    return await crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations: 600_000, hash: "SHA-256" },
      material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"],
    );
  } finally {
    bytes.fill(0);
  }
}

export async function encryptVault(entries, key, salt) {
  const nonce = randomBytes(12);
  const bytes = encoder.encode(JSON.stringify(entries));
  try {
    if (bytes.length + 16 > 512 * 1024) throw new Error("The vault is full.");
    const encrypted = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce, additionalData: associatedData }, key, bytes,
    );
    return {
      version: 1,
      salt: toBase64(salt),
      nonce: toBase64(nonce),
      ciphertext: toBase64(new Uint8Array(encrypted)),
    };
  } finally {
    bytes.fill(0);
  }
}

export async function decryptVault(vault, key) {
  if (vault.version !== 1) throw new Error("Unsupported vault version.");
  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: fromBase64(vault.nonce), additionalData: associatedData },
    key, fromBase64(vault.ciphertext),
  );
  const bytes = new Uint8Array(decrypted);
  try {
    const entries = JSON.parse(decoder.decode(bytes));
    if (!Array.isArray(entries) || !entries.every((entry) =>
      entry && ["id", "name", "website", "username", "password"].every((field) => typeof entry[field] === "string")
    )) throw new Error("Invalid vault contents.");
    return entries;
  } finally {
    bytes.fill(0);
  }
}

export function generatePassword() {
  return toBase64(randomBytes(24)).replaceAll("+", "-").replaceAll("/", "_");
}
