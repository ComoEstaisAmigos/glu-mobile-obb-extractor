# Glu Mobile OBB Extractor

An Android `.obb` is usually a zip with a different extension. Glu Mobile packed their game's
assets into a custom format that no archiver will recognise, and the file's first four bytes
read `FGIB`. This script extracts it.

Almost nothing about the format is public, so most of what follows is the format itself
rather than instructions for the script.

> [!IMPORTANT]
> **Tested against exactly one game:** Contract Killer: Zombies (NR) v3.1.0,
> `com.glu.android.zombsniper`, `main.310.com.glu.android.zombsniper.obb`. Other Glu titles
> *might* ship the same container, and some do not: at least one other Glu game's `.obb` is
> an ordinary zip that any archiver opens, so [check yours first](#is-my-obb-this-format).
> If you run this against another title, an issue saying what it printed is welcome either
> way.

The loader it was reverse engineered from is
`com::glu::platform::components::CBigFile_v2::Load(CInputStream&, unsigned)` in
`libandroidplatformjni.so`. The layout below was read out of that function and then
checked against a real file.

Single file, Python 3 standard library only, no dependencies.

---

## Is my `.obb` this format?

Look at the first 64 bytes. Nothing to install. On Windows:

```powershell
Get-Content .\game.obb -Encoding Byte -TotalCount 64 | Format-Hex
```

(on PowerShell 7, `-Encoding Byte` becomes `-AsByteStream`), or anywhere else:

```bash
xxd -l 64 game.obb
```

One of these files opens like this:

```
           00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F

00000000   46 47 49 42 02 00 00 00 73 32 6B 31 3C 00 00 00  FGIB....s2k1<...
00000010   DC 15 00 00 18 16 00 00 FC 49 00 00 14 60 00 00  Ü.......üI...`..
00000020   18 00 00 00 FF FF FF FF 00 00 00 00 2C 60 00 00  ............,`..
00000030   F6 72 00 00 22 D3 00 00 58 F6 31 18 00 00 00 00  ör.."Ó..Xö1.....
```

`FGIB`, major version `02`, and the hash scheme `s2k1`, all three in plain ASCII.
Everything after that is little-endian `u32`s, and the whole header is readable straight off
this dump once you know the layout: bucket table at `0x3C` for `0x15DC` bytes, record table
at `0x1618` for `0x49FC`, and `FF FF FF FF` where the dictionary offset would be, meaning
there is none. [The format](#the-format) walks through the rest.

If the first bytes are `PK` instead, or `unzip -l` lists files, it is an ordinary zip and
this tool is not what you need.

---

## Usage

Drop `fgib_extractor.py` into the same folder as the `.obb` file and run it:

```bash
python fgib_extractor.py
```

On Windows, double-clicking the script does exactly that, and the console window stays open at
the end so the summary is readable. Everywhere else it exits straight away, so it pipes and
scripts cleanly.

With no arguments it picks up every `*.obb` next to the script. Paths can also be passed
explicitly, from anywhere:

```bash
python fgib_extractor.py main.310.com.glu.android.zombsniper.obb
```

Each input gets its own output folder next to the script, named after the file. If that
folder already exists the script refuses rather than merging into it, so delete or rename it
first.

A run looks like this:

```
res_map.dat found, 1008 original path mappings loaded.

=== Reading: main.310.com.glu.android.zombsniper.obb ===
FGIB candidates: 4
Section @0          -> 1116 records (data base A, TOC 54050 + data 405927512)
Section @405981562  -> 1932 records (data base B, TOC 109546 + data 92850)
Section @406183958  ->  408 records (data base A, TOC 21102 + data 16744181)
Section @422949241  ->  416 records (data base A, TOC 21479 + data 23724367)
Valid sections: 4
Total records: 3872 | matched by res_map.dat: 1662 | unmatched: 2210

2210 files have no match in res_map.dat.
Extract those into an "_unmapped" folder as well? (y/N): y

=== Extracting: main.310.com.glu.android.zombsniper.obb ===
zlib inflated: 511 | size mismatch: 0 | zlib error: 0

1 ranges OUTSIDE the sections, 14,218,936 bytes in total:
   @446695087        7,110,792 bytes -> _unclaimed/offset_446695087.mp4
   @453805879        7,108,144 bytes -> _unclaimed/offset_453805879.mp4
TOTAL files extracted: 3872
```

### Requirements

Nothing to install: no pip, no third-party module. Developed and tested on Python 3.11
under Windows, and nothing in the code needs anything newer than 3.6, though no older
interpreter has actually been run. Everything platform-specific (UTF-8 console setup,
ANSI escapes, the double-click pause) sits behind a `sys.platform` check and the parser
itself is plain Python, so Linux and macOS should be fine, but they have not been tested
either.

### File names, and `res_map.dat`

Records are keyed by engine tokens (`BIN_CARRIER_BIG`, `IDM_MENU_LOOP`), not paths. In
Contract Killer: Zombies the mapping back to real paths ships inside the APK as
`assets/res_map.dat`, in `TOKEN=path/to/file.ext` lines:

```
BIN_AUX_RENDER=res/common/3d/System/AuxiliaryRender.m3g
BIN_CARRIER_BIG=res/zombies/3d/locations/carrier/carrier.m3g
```

Pull it out of your own APK (`unzip -j game.apk assets/res_map.dat`) and drop it beside
the script. It is optional: without it everything still extracts, just under generated
names. It is game data, so it is deliberately **not** shipped here.

Whether other Glu titles carry one at all is **untested**: that path is confirmed only for
this one game. Another title may keep it under a different name, in a different format, or
not ship it at all, in which case you simply extract without it. If you find one elsewhere,
say so in an issue.

The map is usually incomplete. In Contract Killer: Zombies it covers 1,662 of 3,872
records, and the script asks what to do with the rest:

```
2210 files have no match in res_map.dat.
Extract those into an "_unmapped" folder as well? (y/N):
```

### Output layout

```
main.310.com.glu.android.zombsniper/
  res/...                 exact original paths, for records res_map.dat knows
  _unmapped/
    textures/  audio/  models_3d/  config/  fonts/  ui/  binary/  other/
  _unclaimed/
    offset_446695087.mp4  byte ranges no section claimed (see below)
```

Unmapped records are grouped by content signature first, then by token prefix (`IDB_`,
`KEYSET_`, `BIN_M3G`, `SUR_`, and so on), and given an extension guessed from their magic bytes:
`png`, `ogg`, `wav`, `zip`, `gz`, `ktx`, `pvr`, `m3g`, plus `xml` / `lua` / `txt` for
things that look like text. Anything unrecognised stays `.bin`. Colliding paths get a
`_1`, `_2` suffix instead of overwriting.

---

## The format

What follows separates what was actually observed from what was only inferred:

| | status |
|---|---|
| Header, and its five block offset/size pairs | confirmed, in all four sections |
| Bucket, record and string table layout | confirmed |
| Per-section data offset base, A or B | confirmed, both occur in one file |
| zlib with `windowBits = 15` | read from the loader, matched by every compressed record |
| Scheme tag `s2k1` | seen |
| Scheme tags `djb2`, `sdbm`, `crxx` | accepted by the loader, never seen in a file |
| Name dictionary | located, always empty here, not implemented |
| Mime table | not decoded |
| Repacking | not implemented |

### Section header (60 bytes)

| bytes | meaning |
|---|---|
| 4 | `FGIB` |
| 4 | four `u8`; the first is the major version, and the loader rejects anything `<= 1` |
| 4 | scheme tag: `djb2`, `sdbm`, `s2k1` or `crxx` |
| 48 | twelve `u32` |

Those twelve are five `(offset, size)` pairs, one per block, followed by `toc_size` and
`data_size`:

| index | field |
|---|---|
| 0, 1 | bucket table: offset, size |
| 2, 3 | record table: offset, size |
| 4, 5 | mime table: offset, size |
| 6, 7 | dictionary: offset, size |
| 8, 9 | string table: offset, size |
| 10 | `toc_size` |
| 11 | `data_size` |

Offsets are relative to the section start, and **`0xFFFFFFFF` marks a block that is not
present.** That is what the dictionary field holds in every section of the file this was
built against, alongside a size of 0.

The blocks do sit contiguously right after the header, in that order, so each offset is
just the previous offset plus the previous size, and `60 + the five sizes` comes to exactly
`toc_size`. That holds in all four sections here. The extractor still seeks to the declared
offsets rather than reading straight through, because those are what the loader uses.

The mime table is small, 4 to 24 bytes per section, and is not decoded.

A file can hold several sections back to back; this one holds four, tiling it end to end.
There is no index of sections, so the script scans for the `FGIB` magic and validates each
hit: every block must land inside the declared `toc_size`, which is what throws out a
signature that merely occurred in random data.

### Bucket table, record table, string table

A bucket entry is an `i32` offset into the record table. At that offset:

- if the first word has **bit 31 set**, it is a count, and the records start 4 bytes later
- otherwise, exactly one record starts right there

A record is 16 bytes:

| offset | field |
|---|---|
| 0 | name offset into the string table |
| 4 | data offset (see below) |
| 8 | uncompressed size |
| 12 | flags; **bit 0 = compressed** |

Names are NUL-terminated ASCII in the string table.

### Trap 1: the data offset base is not fixed

A record's data offset is relative to either

- **A**: `section_start + offset`, or
- **B**: `section_start + toc_size + offset`

and both occur *in the same file*. In Contract Killer: Zombies three sections use A and one
uses B. There is no flag for it; the script decides per section by trying both bases on up
to 40 records and keeping whichever lands more of them on a known file signature.

Do not hardcode this.

### Trap 2: compressed records are zlib with a header, `windowBits = 15`

A compressed record is a `u32` length followed by the stream. The zlib statically linked
into the game library only ever calls `inflateInit_`, never `inflateInit2_`, and
`inflateInit_` == `inflateInit2_(..., MAX_WBITS)`, so the stream is zlib-wrapped
(RFC 1950), not raw and not gzip. The data agrees: every compressed record starts with
`0x78`.

An earlier version of this script tried `-15`, `15` and `31` in turn and, if none produced
the declared size, wrote whichever came closest. That silent-corruption path never fired,
but it would have been invisible if it had. `windowBits` is now pinned to 15 and a size
mismatch is a counted error, never a written file.

### The hash, and what repacking would take

The scheme tag is a **hash function name**, so the bucket table is a hash table: the engine
resolves `GetStream("KEYSET_MINIGUN_FIRE")` by hashing the name into a bucket. Extraction
never needs the hash, because you can walk every bucket blindly, which is what this script
does.

Writing a container does need it, but only if names change. **Replacing the contents of
existing records requires no hash at all**: leave the bucket table, string table and record
count alone, rewrite the data block, then fix each record's offset, size and compression
flag plus `toc_size` / `data_size` in the header. That covers what asset replacement
actually wants. Adding or renaming a file is the case that forces you to reproduce the
hash; `CBigFile_v2::GetStream(const char*)` is where to read it.

### The dictionary

Names can carry `0x1A` markers referencing the dictionary block, expanded by
`CBigFile_v2::DecompressIntoDestinationBufferIfNeededMore`. Despite the name, that
function is string handling, not zlib.

Every section in the file this was built against has `dictionary_size == 0` and no `0x1A`
in the string table, so this path is **not implemented**. If a non-empty dictionary turns
up the script prints a warning and continues, because names would otherwise come out
silently wrong:

```
! WARNING: this section carries a 812-byte name dictionary, which this script does not use.
  File names that reference it may come out incomplete.
```

### Unclaimed ranges

After the sections are read, whatever bytes they do not cover are written to `_unclaimed/`
as `offset_<n>.<ext>`. A file whose sections cover everything simply reports that nothing
is left over.

In this OBB the last 14,218,936 bytes belong to no section at all: two raw 3GP videos,
800x480 and 1024x576, 45.6 s each, the intro at two qualities. They are *stored*, because
the engine opens them through `AssetFileDescriptor` as an offset and a length; the APK
carries the same pairing for the Glu logo.

Such a range is split only on **validated** MP4 boxes: the four bytes before `ftyp` must be
a plausible box size. An earlier attempt also split on gzip's two-byte `1f 8b`, which occurs
by chance inside video data and shredded both files into hundreds of fragments. Nothing that
cannot be validated is ever used as a cut point.

---

## Limits

- **One game, one file.** What "tested" means for `main.310.com.glu.android.zombsniper.obb`:
  4 sections found and accepted, 3,872 records, 511 compressed and all of them inflating to
  exactly the declared size, 0 read and 0 write errors, and no byte of the 460 MB file left
  unattributed: every one falls either inside an accepted section or inside an `_unclaimed`
  range that was written out. Nothing else has been run through it.
- **`assets/res_map.dat` is confirmed in one APK only**, so on another title you may get no
  real file names at all. Extraction itself does not depend on it.
- **Section discovery is a magic-byte scan**, not an index walk. False hits are validated
  and reported as rejected candidates; the `FGIB` signature does occur in random data.
- **The dictionary path warns instead of working** (see above). It is `0xFFFFFFFF` / size 0
  in every section seen so far, so it has never had to run.
- **The mime table is undecoded**, read past but never parsed.
- **Read-only.** There is no repacker. The notes above are what a repacker would need.
- **Only the `s2k1` scheme tag has actually been seen.** `djb2`, `sdbm` and `crxx` are
  accepted because the loader accepts them, not because a file carrying one has been
  tested. Extraction ignores the hash entirely, so this should not matter, but it is a
  guess, not a result.

## If you want to write a repacker

There is no repacker here, but the container half of that job is the easy half.
[The hash, and what repacking would take](#the-hash-and-what-repacking-would-take) has the
recipe: replacing the contents of records that already exist needs no hash and no
dictionary, only rewritten offsets, sizes and flags.

The hard half is what the records hold, and that is per game, not per format. Everything
below is Contract Killer: Zombies specifically, offered as an example of the shape of the
problem rather than as a description of Glu's engine:

- Level geometry under `res/zombies/3d/locations` is **M3G** (JSR-184), 19 files holding
  1,624 meshes.
- Those same files also hold 603 `Image2D` objects, so the level textures live inside the
  scene files instead of sitting beside them. The 1,063 loose PNGs turned out to be UI and
  2D art.
- Sections inside an M3G can be zlib-compressed and carry their own Adler-32, so editing
  one means rebuilding lengths and checksums. That is a second format nested inside this
  one, and this tool does not go near it.
- The renderer is fixed-function GLES1, so asset quality is the only lever there is, and
  the lighting is baked into `lighting_grad*.m3g` meshes rather than computed at runtime.

The pattern worth taking from that: swapping a record's bytes is easy, swapping what the
player actually sees usually is not, because the interesting content sits one format deeper.
Whether another Glu title is arranged the same way is unknown, since none has been opened.

## Legal

This repository contains a file-format parser and documentation only. **No game assets, no
game code, and no copyrighted data are included.** `res_map.dat` is a game file and is
deliberately left out. Complying with the licence and the law that cover your copy of the
game is on you. Redistributing what comes out is not what this tool is for.

Not affiliated with, endorsed by, or connected to Glu Mobile or EA.

## Credits

The format analysis, the extractor and this write-up were done with help from Claude
(Anthropic).

## License

MIT, see [LICENSE](LICENSE).
