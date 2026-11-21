# Rebuild or replace the bundled Decky API

The plugin archive includes the source of the bundled LGPL library, `@decky/api` 1.1.3, along with the frontend source and build configuration. The [third-party notices](../THIRD_PARTY_NOTICES.md) identify its source and license.

Use Node.js 22, the pnpm version declared in `package.json`, and Python 3.11 or later. Extract the plugin archive and run these commands from its `8bitdo-deck` folder, or use the repository checkout.

First install the pinned build dependencies:

```sh
pnpm install --frozen-lockfile
```

Edit the library files under `third_party/decky-api/src/` as needed. Compile the library with the project's TypeScript compiler:

```sh
pnpm exec tsc -p third_party/decky-api/tsconfig.json --skipLibCheck
```

Replace the npm library dependency with the rebuilt local package. This command intentionally updates the local `package.json` and lockfile:

```sh
pnpm add --offline --ignore-scripts @decky/api@file:./third_party/decky-api
```

Build and check the frontend, then create a new installation archive:

```sh
pnpm run typecheck
pnpm run build
python3 scripts/package.py
```

Install the resulting `out/8bitdo-deck.zip` through Decky's ZIP installation option. See [installation](install.md). No signature check in 8BitDo Deck prevents using a rebuilt frontend. Changes to the library's exported API may also require changes to the calling frontend source.

The vendored library source is unmodified in the original distribution. Its `LICENSE`, `package.json`, `README.md`, and `src/` files come from the published npm package; `tsconfig.json` comes from the published upstream revision. The generated `third_party/decky-api/dist/` directory is a local build result and is not part of the source snapshot.
