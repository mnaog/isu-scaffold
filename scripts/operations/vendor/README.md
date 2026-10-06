# Mermaid runtime

`mermaid.tiny.js` is the unmodified IIFE from `@mermaid-js/tiny@12.1.0`,
`dist/mermaid.tiny.js`, downloaded from the npm registry. MIT license is in
`mermaid.LICENSE`; bundled third-party notices remain in the distributed script.
No CDN is used at runtime. Node/npm is not needed to run the board.

To update: `npm pack @mermaid-js/tiny@<version>`, unpack, replace the script and
license, and run the board's scenario tests and browser checks. Keep the version
pinned in this file.
