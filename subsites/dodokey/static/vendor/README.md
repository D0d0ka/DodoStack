DodoKey loads the browser ES modules below when Web Crypto's SubtleCrypto is unavailable.
The files are from the corresponding npm tarballs and are served alongside DodoKey.
Each directory includes its upstream MIT license.

- [@noble/ciphers 2.4.0](https://github.com/paulmillr/noble-ciphers/releases/tag/2.4.0) provides AES-GCM.
- [@noble/hashes 2.4.0](https://github.com/paulmillr/noble-hashes/releases/tag/2.4.0) provides PBKDF2 and SHA-256.

The selected files are the entry modules used by `crypto.js` and their relative imports.
Source map references have been removed because the maps are omitted.
