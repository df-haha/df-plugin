# Third-party code, runtimes and artwork

## Project lineage

This repository extends [Geoffrey Wang's line-desktop-mcp](https://github.com/dtwang/line-desktop-mcp), under the MIT license. The original copyright and permission notice are retained in [LICENSE.md](../LICENSE.md); the community additions retain the same license. LINE Agent MCP is a community project, not a LINE product or an endorsement by LINE.

## SQLite3 Multiple Ciphers — MIT code and format compatibility

The Python wxSQLite3 AES128 interoperability routines follow the MIT implementation in [SQLite3MultipleCiphers](https://github.com/utelle/SQLite3MultipleCiphers), reviewed at commit `7dd52e99ea918c1babf633e30749298c688ce6d7`:

- [cipher_common.c](https://github.com/utelle/SQLite3MultipleCiphers/blob/7dd52e99ea918c1babf633e30749298c688ce6d7/src/cipher_common.c): padding constants.
- [cipher_wxaes128.c](https://github.com/utelle/SQLite3MultipleCiphers/blob/7dd52e99ea918c1babf633e30749298c688ce6d7/src/cipher_wxaes128.c): key derivation and first-page layout.
- [codec_algos.c](https://github.com/utelle/SQLite3MultipleCiphers/blob/7dd52e99ea918c1babf633e30749298c688ce6d7/src/codec_algos.c): page IV, per-page key and AES-CBC interoperability.

The runtime separately requires the [2.5.1 Windows x64 DLL](https://github.com/utelle/SQLite3MultipleCiphers/releases/tag/v2.5.1), which is not bundled here. Its archive and DLL hashes are listed in the installation guide; the reader verifies the DLL hash before loading it.

The following notice is retained for the corresponding code and algorithms, from the [upstream license](https://github.com/utelle/SQLite3MultipleCiphers/blob/7dd52e99ea918c1babf633e30749298c688ce6d7/LICENSE):

```text
MIT License

Copyright (c) 2019-2026 Ulrich Telle
Copyright (c) 2006-2026 Ulrich Telle (cipher_common.c)
Copyright (c) 2006-2024 Ulrich Telle (cipher_wxaes128.c)
Copyright (c) 2006-2020 Ulrich Telle (codec_algos.c)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Historical references and independently expressed code

The earlier [SQLite3-Encryption codec.c](https://github.com/darkman66/SQLite3-Encryption/blob/master/src/codec.c) was consulted as a historical format cross-check. Its repository and embedded components have different GPL/wxWindows/RSA notices. No code from those implementations is copied or translated into this package: Python uses `hashlib` and `cryptography`, and the relevant format rules are covered by the pinned MIT sources above. It is not being represented as MIT-licensed dependency code.

The [line-summary key extractor](https://github.com/yung13yubabie/line-summary/blob/bd3569e80af56cff342cab9690e6a6d0cb2f4f93/key_extractor.py) is acknowledged as prior art for local read-only memory scanning. It is not vendored. The scanner here is independently expressed and adds bounded chunks, process/build identity checks, candidate validation, buffer cleanup and refusal conditions. No license is claimed for that external repository.

Cached-media interoperability references LINE's published [Encryption Overview v2.2](https://www.lycorp.co.jp/en/privacy-security/line-encryption-whitepaper-ver2.2.pdf). A format reference does not grant rights to other users' data or permission to use LINE outside its terms.

## Separately installed dependencies

| Component | License/source | Distribution here |
| --- | --- | --- |
| cryptography | [Apache-2.0 OR BSD-3-Clause](https://github.com/pyca/cryptography/blob/main/LICENSE) | Requirement only; install separately |
| Pillow | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | Requirement only; install separately |
| CUA Driver | [trycua/cua, MIT](https://github.com/trycua/cua/blob/main/LICENSE) | Existing public stdio MCP runtime; binary not bundled |
| AutoHotkey v2 | [AutoHotkey license](https://www.autohotkey.com/docs/v2/license.htm) | Separate application; not bundled |
| Node/MCP dependencies | See the pinned npm lockfile and each installed package's license | Dependencies installed by npm; no node_modules in the release archive |

`requirements.txt` states minimum Python dependency versions rather than a lock. The tested environment used Python 3.12.14, cryptography 50.0.1 and Pillow 12.3.0. The Node lockfile is shipped for reproducible source installs.

## Artwork and platform rights

The cover is a generated concept illustration; workflow and timing diagrams were generated from code. They contain synthetic cards and no real chat screenshots, names or account data. See [asset provenance](assets/README.md) for the tool and prompts. No official LINE logo asset is bundled.

The project MIT license applies to project code; it does not grant LINE trademark, account, service or data rights. Users must independently assess applicable [LINE/LY terms](https://www.lycorp.co.jp/zh-TW/company/terms/) and permissions for their use, particularly commercial deployment. Local decoding does not imply offline AI processing: returned tool content follows the selected client's/model provider's settings.
