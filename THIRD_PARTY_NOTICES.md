# Third-party notices

The licenses below apply to the named third-party components. This file does not select a license for 8BitDo Deck's own code.

## Decky API 1.1.3

The frontend bundle includes code from `@decky/api` 1.1.3, by the SteamDeckHomebrew Team, under the GNU Lesser General Public License, version 2.1. Read the [full license](third_party/licenses/decky-api-LGPL-2.1.txt).

The matching library source, package metadata, and upstream notice are provided in [third_party/decky-api](third_party/decky-api/README.md). The source files are unmodified copies of the published npm package. The compiler configuration comes from the package's published upstream revision, [`d1b7f16070777a0ada939cbacd3606eeba0574f5`](https://github.com/SteamDeckHomebrew/loader-api/tree/d1b7f16070777a0ada939cbacd3606eeba0574f5).

The npm package integrity identifier is `sha512-XsPCZxfxk5I1UtylIUN3qaWQI31siQbKfbLIskkI5innEatY1m4NQqBv/6hwPaO9mKMbdqYpnh5PSJDeMEOOBA==`.

The installation archive also includes the plugin's frontend source and build inputs so the bundled library can be rebuilt or replaced. See [rebuilding the library and frontend](docs/rebuild-library.md). The upstream library's LGPL rights remain applicable to that library.

## TypeScript runtime helpers 2.8.1

`tslib` 2.8.1 is a build dependency, copyright Microsoft Corporation, under the [Zero-Clause BSD license](third_party/licenses/tslib.txt). The current ES2020 frontend does not emit runtime helper code, but its license is supplied for rebuilds that do.

## Components supplied by Decky

React and `@decky/ui` are supplied by Decky at runtime and are external to the distributed frontend bundle. Their implementations are not copied into this archive. Build tools and type declarations are obtained by the package manager under their respective upstream licenses.

The gamepad icon in the frontend is original SVG geometry. This distribution does not include React Icons or Font Awesome artwork.
