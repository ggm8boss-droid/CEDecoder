"""
Cheat Engine encodeFunction / decodeFunction decoder (CE 7.5 and 7.6+/7.7 formats).

Pipeline used by CE:  lua_dump(strip=1) -> zlib (clmax) -> CE custom base85
CE 7.6+ additionally ships a modified lua53 dll whose lua_dump writes an obfuscated
bytecode format (header "\\x1bLuaS\\x02..."). That dll exports a hidden `lua_compat(int)`
switch: with it on, lua_dump writes plain Lua 5.3 bytecode again. So for the new format we
let CE 7.7's own dll *load* the chunk (lua_load only, nothing is executed), flip
lua_compat(1), and dump it back out as standard Lua 5.3 bytecode, which unluac decompiles.

Usage:
  python cedecode.py <input> [-o OUTDIR]          decode (input = file with the string, a .CT/.lua
                                                  containing decodeFunction(...), or the string itself)
  python cedecode.py --encode script.lua          encode like CE 7.7's encodeFunction (for testing)
"""
import argparse
import ctypes
import os
import re
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = r"C:\Program Files\Cheat Engine\lua53-64.dll"
UNLUAC_JAR = os.path.join(HERE, "tools", "unluac.jar")
LUADEC_EXE = os.path.join(HERE, "tools", "luadec.exe")

B85 = ("0123456789"
       "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
       "abcdefghijklmnopqrstuvwxyz"
       "!#$%()*+,-./:;=?@[]^_{}")
B85_INDEX = {c: i for i, c in enumerate(B85)}


# ---------------------------------------------------------------- base85 (port of custombase85.pas)

def base85_to_bin(s: str) -> bytes:
    out = bytearray()
    size = len(s)
    i = 0
    a = 0
    while i < size:
        a = 0
        for mul in (85 ** 4, 85 ** 3, 85 ** 2, 85):
            if i < size:
                a += B85_INDEX[s[i]] * mul
                i += 1
        if i < size:
            a += B85_INDEX[s[i]]
            i += 1
            a &= 0xFFFFFFFF
            out += a.to_bytes(4, "big")
    rem = size % 5
    if rem:
        a += {2: 84 * 85 * 85 + 84 * 85 + 84, 3: 84 * 85 + 84, 4: 84}.get(rem, 0)
        a &= 0xFFFFFFFF
        if rem >= 2:
            out += a.to_bytes(4, "big")[:rem - 1]
    return bytes(out)


def bin_to_base85(b: bytes) -> str:
    out = []
    for i in range(0, len(b), 4):
        chunk = b[i:i + 4]
        a = int.from_bytes(chunk.ljust(4, b"\0"), "big")
        digits = []
        for _ in range(5):
            digits.append(B85[a % 85])
            a //= 85
        out.append("".join(reversed(digits)))
    s = "".join(out)
    cut = (4 - len(b) % 4) % 4
    return s[:len(s) - cut] if cut else s


# ---------------------------------------------------------------- CE lua dll wrapper

READER = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t))
WRITER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)


class CELua:
    def __init__(self, dll_path):
        if ctypes.sizeof(ctypes.c_void_p) != 8 and "64" in os.path.basename(dll_path):
            raise RuntimeError("64-bit Python is required to load lua53-64.dll")
        self.dll = ctypes.CDLL(dll_path)
        d = self.dll
        d.luaL_newstate.restype = ctypes.c_void_p
        d.lua_close.argtypes = [ctypes.c_void_p]
        d.lua_load.argtypes = [ctypes.c_void_p, READER, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        d.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        d.lua_dump.argtypes = [ctypes.c_void_p, WRITER, ctypes.c_void_p, ctypes.c_int]
        d.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        d.lua_tolstring.restype = ctypes.c_char_p
        self.has_compat = hasattr(d, "lua_compat")
        if self.has_compat:
            d.lua_compat.argtypes = [ctypes.c_int]

    def _dump(self, L, strip, compat):
        if self.has_compat:
            self.dll.lua_compat(1 if compat else 0)
        out = bytearray()

        def w(_L, p, sz, _ud):
            out.extend(ctypes.string_at(p, sz))
            return 0
        cb = WRITER(w)
        self.dll.lua_dump(L, cb, None, strip)
        if self.has_compat:
            self.dll.lua_compat(0)
        return bytes(out)

    def to_standard(self, chunk: bytes) -> bytes:
        """Load (not run) a CE bytecode chunk and re-dump it as standard Lua 5.3 bytecode."""
        L = self.dll.luaL_newstate()
        try:
            buf = ctypes.create_string_buffer(chunk, len(chunk))
            done = [False]

            def r(_L, _ud, size):
                if done[0]:
                    size[0] = 0
                    return None
                done[0] = True
                size[0] = len(chunk)
                return ctypes.addressof(buf)
            cb = READER(r)
            rc = self.dll.lua_load(L, cb, None, b"cechunk", b"b")
            if rc != 0:
                raise RuntimeError("lua_load failed: %s" % self.dll.lua_tolstring(L, -1, None))
            # CE always encodes stripped, so strip=1 reproduces exactly what CE 7.5 would have dumped
            return self._dump(L, 1, compat=True)
        finally:
            self.dll.lua_close(L)

    def compile(self, source: bytes, compat=False) -> bytes:
        L = self.dll.luaL_newstate()
        try:
            if self.dll.luaL_loadstring(L, source) != 0:
                raise RuntimeError("compile error: %s" % self.dll.lua_tolstring(L, -1, None))
            return self._dump(L, 1, compat)
        finally:
            self.dll.lua_close(L)


# ---------------------------------------------------------------- decode pipeline

def unwrap(encoded: str) -> bytes:
    raw = base85_to_bin(encoded)
    if raw.startswith(b"\x1bLua"):
        return raw
    for wbits in (15, -15, 31):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            pass
    # truncated / trailing garbage: salvage what inflates
    d = zlib.decompressobj()
    data = d.decompress(raw)
    if data.startswith(b"\x1bLua"):
        return data
    raise ValueError("data is not zlib compressed Lua bytecode (wrong string or new outer format?)")


def describe(chunk: bytes) -> str:
    if not chunk.startswith(b"\x1bLua"):
        return "not Lua bytecode"
    ver, fmt = chunk[4], chunk[5]
    if fmt == 0:
        hdr = chunk[12:17]
    else:
        hdr = chunk[20:25]
    bits = {8: "64-bit", 4: "32-bit"}.get(hdr[1] if len(hdr) > 1 else 0, "?")
    kind = "standard" if fmt == 0 else "CE 7.6+ obfuscated (format %d)" % fmt
    return "Lua %x.%x, %s, %s" % (ver >> 4, ver & 15, kind, bits)


def decompile(luac_path, lua_path):
    if not os.path.exists(UNLUAC_JAR):
        print("  ! unluac.jar not found at", UNLUAC_JAR)
        return False
    with open(lua_path, "wb") as f:
        p = subprocess.run(["java", "-jar", UNLUAC_JAR, luac_path], stdout=f, stderr=subprocess.PIPE)
    if p.returncode != 0:
        print("  ! unluac failed:", p.stderr.decode(errors="replace").strip())
        return False
    return True


def disassemble(luac_path, dis_path):
    """luadec -dis listing: exact instructions, useful to check the decompiled source against."""
    if not os.path.exists(LUADEC_EXE):
        return False
    with open(dis_path, "wb") as f:
        subprocess.run([LUADEC_EXE, "-dis", luac_path], stdout=f, stderr=subprocess.DEVNULL)
    return os.path.getsize(dis_path) > 0


def extract_strings(text: str):
    """Pull encoded strings out of decodeFunction(...) calls; fall back to the whole text."""
    found = []
    for m in re.finditer(r"decodeFunction\s*\(?\s*(?:\[(=*)\[(.*?)\]\1\]|'([^']*)'|\"([^\"]*)\")", text, re.S):
        s = m.group(2) or m.group(3) or m.group(4) or ""
        found.append(s)
    if not found:
        found = [text]
    return ["".join(ch for ch in s if ch in B85_INDEX) for s in found]


def decode_one(encoded, lua, outbase):
    chunk = unwrap(encoded)
    print("  bytecode:", describe(chunk), "-", len(chunk), "bytes")
    if chunk[5] != 0:
        bits_byte = chunk[21]
        if bits_byte != 8:
            raise RuntimeError("chunk was made by 32-bit CE; run with 32-bit Python and --dll lua53-32.dll")
        orig = outbase + ".ce.luac"
        with open(orig, "wb") as f:
            f.write(chunk)
        chunk = lua.to_standard(chunk)
        print("  converted to standard Lua 5.3 bytecode -", len(chunk), "bytes")
    luac = outbase + ".luac"
    with open(luac, "wb") as f:
        f.write(chunk)
    print("  wrote", luac)
    if decompile(luac, outbase + ".lua"):
        print("  wrote", outbase + ".lua")
    if disassemble(luac, outbase + ".dis.txt"):
        print("  wrote", outbase + ".dis.txt")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="file containing the encoded string / CT / lua, or the string itself")
    ap.add_argument("-o", "--outdir", default=os.path.join(HERE, "output"))
    ap.add_argument("--dll", default=DEFAULT_DLL, help="CE 7.6+ lua53-64.dll (default: %(default)s)")
    ap.add_argument("--encode", action="store_true", help="encode a Lua source file like CE's encodeFunction")
    ap.add_argument("--compat", action="store_true", help="with --encode: old (pre 7.6) compatible format")
    a = ap.parse_args()

    lua = CELua(a.dll)
    if a.encode:
        with open(a.input, "rb") as f:
            bc = lua.compile(f.read(), compat=a.compat)
        print(bin_to_base85(zlib.compress(bc, 9)))
        return

    if os.path.isfile(a.input):
        with open(a.input, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        name = os.path.splitext(os.path.basename(a.input))[0]
    else:
        text, name = a.input, "decoded"
    os.makedirs(a.outdir, exist_ok=True)
    strings = [s for s in extract_strings(text) if s]
    print("found %d encoded string(s)" % len(strings))
    for n, s in enumerate(strings, 1):
        base = os.path.join(a.outdir, name if len(strings) == 1 else "%s_%d" % (name, n))
        print("[%d] %d chars" % (n, len(s)))
        try:
            decode_one(s, lua, base)
        except Exception as e:
            print("  ! failed:", e)


if __name__ == "__main__":
    main()
