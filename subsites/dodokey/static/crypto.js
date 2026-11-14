const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const associatedData = encoder.encode("DodoKey vault v1");
const subtle = globalThis.crypto?.subtle;
const iterations = 600_000;

export const supportsVaultCrypto = typeof globalThis.crypto?.getRandomValues === "function";

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

export function newEntryId() {
  return toBase64(randomBytes(16));
}

export async function deriveKey(password, salt) {
  const bytes = encoder.encode(password);
  try {
    if (!subtle) {
      const [{ pbkdf2Async }, { sha256 }] = await Promise.all([
        import("./vendor/noble-hashes/pbkdf2.js"),
        import("./vendor/noble-hashes/sha2.js"),
      ]);
      return await pbkdf2Async(sha256, bytes, salt, { c: iterations, dkLen: 32 });
    }
    const material = await subtle.importKey("raw", bytes, "PBKDF2", false, ["deriveKey"]);
    return await subtle.deriveKey(
      { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
      material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"],
    );
  } finally {
    bytes.fill(0);
  }
}

export function clearKey(key) {
  if (key instanceof Uint8Array) key.fill(0);
}

async function crypt(operation, bytes, key, nonce) {
  if (subtle) {
    return new Uint8Array(await subtle[operation](
      { name: "AES-GCM", iv: nonce, additionalData: associatedData }, key, bytes,
    ));
  }
  const { gcm } = await import("./vendor/noble-ciphers/aes.js");
  return gcm(key, nonce, associatedData)[operation](bytes);
}

export async function encryptVault(entries, key, salt) {
  const nonce = randomBytes(12);
  const bytes = encoder.encode(JSON.stringify(entries));
  try {
    if (bytes.length + 16 > 512 * 1024) throw new Error("The vault is full.");
    const encrypted = await crypt("encrypt", bytes, key, nonce);
    return {
      version: 1,
      salt: toBase64(salt),
      nonce: toBase64(nonce),
      ciphertext: toBase64(encrypted),
    };
  } finally {
    bytes.fill(0);
  }
}

export async function decryptVault(vault, key) {
  if (vault.version !== 1) throw new Error("Unsupported vault version.");
  const bytes = await crypt("decrypt", fromBase64(vault.ciphertext), key, fromBase64(vault.nonce));
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
