# CE encodeFunction decoder (7.5 and 7.6+/7.7)

## What changed in CE 7.6+

The outer layer is unchanged: `lua_dump(strip=1)`, then zlib, then CE's custom base85 (`custombase85.pas`).
What changed is CE's `lua53-64.dll`. Its `lua_dump` now writes an obfuscated bytecode format, with the header
`1B 4C 75 61 53 02 ...` instead of `... 53 00`. The constants, integers and instructions are scrambled, so
unluac and luadec reject it.

The 7.7 DLL has one extra export, `lua_compat(int)`, which sets a global flag (that's what
`encodeFunctionCompatibilityMode` uses). When the flag is set, `lua_dump` writes **plain Lua 5.3 bytecode**.

So the decoder:
1. base85-decodes and inflates the string
2. loads the chunk with CE 7.7's own DLL (`lua_load` only, **the code is never run**)
3. calls `lua_compat(1)` and runs `lua_dump` again, which gives standard Lua 5.3 bytecode (`.luac`)
4. decompiles it with unluac (`.lua`) and disassembles it with luadec (`.dis.txt`)

Verified: for a test script, step 3 gives output byte-identical to CE 7.5 compiling the same source.

## Setup

You need 64-bit Python 3, git, a JDK, and Visual Studio with the C++ tools. Build the decompilers once:

```
powershell -ExecutionPolicy Bypass -File build.ps1
```

This clones unluac and luadec into `ref\`, builds `tools\unluac.jar` and `tools\luadec.exe`, and keeps all
intermediates (including TMP/TEMP) in `build\`. Use `-Clean` to rebuild from scratch.

## Usage

```
python cedecode.py mystring.txt          # text file holding the encoded string
python cedecode.py MyTable.CT            # finds every decodeFunction('...') / [[...]] inside
python cedecode.py "c-oWpDNPJ!Qf..."     # the string itself
```

The output goes to `output\` (change it with `-o`): `<name>.luac`, `<name>.lua`, `<name>.dis.txt`, and `<name>.ce.luac`
(the raw 7.7 chunk).

For testing, `python cedecode.py --encode script.lua [--compat]` encodes a script the same way CE 7.7's
`encodeFunction` does.

## Notes / limits
- The CE 7.7 DLL is loaded read-only from `C:\Program Files\Cheat Engine\lua53-64.dll` (change it with `--dll`).
- Needs 64-bit Python. Strings made by 32-bit CE would need 32-bit Python plus `lua53-32.dll`.
- CE strips debug info, so local variable names are lost. Decompiled code uses `L0_0` / `A0_1` style names.
- `tools\unluac.jar` is built from HansWessels/unluac and `tools\luadec.exe` from viruscamp/luadec (Lua 5.3, MSVC).
  luadec's 5.3 decompiler fails on these chunks ("cannot find blockend"), so it's only used for the disassembly
  listing. HansWessels' unluac is an old fork and can drop some assignments (e.g. a `pairs(t)` call shows up
  as `L3_3(L4_4)`). Check the `.dis.txt` listing when the decompiled code looks off.
- Sources are in `ref\`, the test script is in `test\`.

## Credits

- **Cheat Engine** by **Dark Byte**: https://github.com/cheat-engine/cheat-engine
  The `encodeFunction` / `decodeFunction` pipeline and the Lua DLL this tool relies on come from Cheat Engine.
  The custom base85 code here is a port of CE's `custombase85.pas`, written by **mgr.inz.Player**.
- **unluac** by **tehtmi** and contributors: https://sourceforge.net/projects/unluac/
  This project builds it from the fork by **HansWessels**: https://github.com/HansWessels/unluac
- **LuaDec** by **viruscamp**: https://github.com/viruscamp/luadec
  It's based on the original LuaDec (luadec.luaforge.net) and on LuaDec51 by **sztupy** (https://github.com/sztupy/luadec51).
- **CEFunctionDecoder** by **axd1x8a** (FeeeeK): https://github.com/axd1x8a/CEFunctionDecoder
  The original decoder for the pre-7.6 format. This project builds on its approach.
