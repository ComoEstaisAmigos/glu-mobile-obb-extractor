import sys
import os

def wait_for_keypress():
    # Only worth doing when the script was double-clicked in Explorer, because that
    # console window closes the moment the process exits. In a terminal the shell prompt
    # is already waiting, and in a pipe there is nobody to press anything.
    stdin = getattr(sys, 'stdin', None)
    if sys.platform != 'win32' or stdin is None or not stdin.isatty():
        return
    sys.stdout.write('\nPress any key to exit...')
    sys.stdout.flush()
    try:
        import msvcrt
        msvcrt.getch()
        return
    except Exception:
        pass
    try:
        input()
    except EOFError:
        pass


REQUIRED_MODULES = ['struct', 'zlib', 'glob', 'traceback', 'io']
missing = []
for mod in REQUIRED_MODULES:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print('Missing Python modules:', ', '.join(missing))
    print('\nThese normally ship with Python; your installation may be broken.')
    print('\nYou can try: pip install ' + ' '.join(missing))
    wait_for_keypress()
    sys.exit(1)

import struct
import zlib
import glob
import traceback

if sys.platform == 'win32':
    try:
        os.system('chcp 65001 > nul')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

MAGIC_OK = {b'djb2', b's2k1', b'sdbm'}
MAGIC_ALT = {b'crxx'}


def set_title(text):
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(text)
        except Exception:
            pass


def render_progress(text):
    if sys.platform == 'win32':
        sys.stdout.write('\r\x1b[2K' + text)
    else:
        sys.stdout.write('\r' + text.ljust(60))
    sys.stdout.flush()


def clear_progress():
    if sys.platform == 'win32':
        sys.stdout.write('\r\x1b[2K')
    else:
        sys.stdout.write('\r' + ' ' * 60 + '\r')
    sys.stdout.flush()


def read_u32(f):
    return struct.unpack('<I', f.read(4))[0]


def read_u8(f):
    return f.read(1)[0]


class BigFileTOC:
    def __init__(self, path, section_start):
        self.section_start = section_start
        with open(path, 'rb') as f:
            f.seek(section_start)
            magic = f.read(4)
            if magic != b'FGIB':
                raise ValueError('no FGIB signature.')
            version_major = read_u8(f)
            read_u8(f)
            if version_major < 2:
                raise ValueError('old/unsupported bigfile version.')
            read_u8(f)
            read_u8(f)
            scheme = f.read(4)
            if scheme not in MAGIC_OK and scheme not in MAGIC_ALT:
                raise ValueError('unknown scheme %r' % scheme)

            # Twelve u32: five (offset, size) pairs for the blocks, then toc_size and
            # data_size. The offsets are relative to the section start, and 0xFFFFFFFF
            # marks a block that is not present. The blocks do sit contiguously right
            # after this 60-byte header, so reading straight through works on every file
            # seen so far -- but the offsets are what the loader seeks to, so follow them.
            (bucket_off, self.bucket_table_size,
             record_off, self.record_table_size,
             mime_off, self.mime_table_size,
             dict_off, self.dictionary_size,
             string_off, self.string_table_size,
             self.toc_size, self.data_size) = struct.unpack('<12I', f.read(48))

            if self.bucket_table_size == 0 or self.bucket_table_size % 4 != 0 or self.bucket_table_size > 0x10000000:
                raise ValueError('invalid bucket table size')
            if self.dictionary_size > 0xFFFF:
                raise ValueError('invalid dictionary size')
            # Every block has to live inside the TOC. This is what rejects an FGIB
            # signature that happened to occur in random data.
            for label, off, size in (('bucket', bucket_off, self.bucket_table_size),
                                     ('record', record_off, self.record_table_size),
                                     ('mime', mime_off, self.mime_table_size),
                                     ('dictionary', dict_off, self.dictionary_size),
                                     ('string', string_off, self.string_table_size)):
                if size and (off < 60 or off + size > self.toc_size):
                    raise ValueError('%s block (offset %d, size %d) falls outside the'
                                     ' %d-byte TOC' % (label, off, size, self.toc_size))

            def read_block(offset, size):
                if size == 0:
                    return b''
                f.seek(section_start + offset)
                return f.read(size)

            self.block1 = read_block(bucket_off, self.bucket_table_size)
            self.block2 = read_block(record_off, self.record_table_size)
            self.block5 = read_block(string_off, self.string_table_size)

        self.bucket_count = self.bucket_table_size // 4
        self.data_section_abs_start = section_start + self.toc_size


def read_cstr(buf, offset):
    if offset < 0 or offset >= len(buf):
        return b''
    end = buf.find(b'\x00', offset)
    if end == -1:
        return buf[offset:]
    return buf[offset:end]


def enumerate_all(toc):
    entries = []
    for i in range(toc.bucket_count):
        group_off = struct.unpack_from('<i', toc.block1, i * 4)[0]
        if group_off < 0 or group_off + 4 > len(toc.block2):
            continue
        first_word = struct.unpack_from('<I', toc.block2, group_off)[0]
        if first_word & 0x80000000:
            count = first_word & 0x7fffffff
            rec_start = group_off + 4
        else:
            count = 1
            rec_start = group_off
        for k in range(count):
            off = rec_start + k * 16
            if off + 16 > len(toc.block2):
                break
            word0, word1, word2, word3 = struct.unpack_from('<4I', toc.block2, off)
            name_bytes = read_cstr(toc.block5, word0)
            name = name_bytes.decode('ascii', errors='replace')
            entries.append({
                'name': name,
                'data_offset': word1,
                'data_size': word2,
                'is_compressed': bool(word3 & 1),
            })
    return entries


# The zlib statically linked into the game library only ever calls inflateInit_,
# never inflateInit2_. inflateInit_ == inflateInit2_(..., MAX_WBITS), so the stream
# is zlib-wrapped (RFC 1950). Confirmed against the data as well: every compressed
# record starts with 0x78.
ZLIB_WBITS = 15

DECOMP_STATS = {'exact': 0, 'size_mismatch': 0, 'zlib_error': 0}


def read_resource_bytes(f, abs_off, rec):
    f.seek(abs_off)
    if not rec['is_compressed']:
        data = f.read(rec['data_size'])
        if len(data) != rec['data_size']:
            raise ValueError('file ended early: read %d bytes, expected %d'
                             % (len(data), rec['data_size']))
        return data
    compressed_size = read_u32(f)
    raw = f.read(compressed_size)
    try:
        data = zlib.decompress(raw, ZLIB_WBITS)
    except zlib.error as e:
        DECOMP_STATS['zlib_error'] += 1
        raise ValueError('zlib inflate failed (%s)' % e)
    # A size mismatch means the record was read wrong. This used to pick whichever
    # window size came closest and silently write corrupt data; it is now an error.
    if len(data) != rec['data_size']:
        DECOMP_STATS['size_mismatch'] += 1
        raise ValueError('inflated size %d, TOC says %d' % (len(data), rec['data_size']))
    DECOMP_STATS['exact'] += 1
    return data


SIGS = [
    (b'\x89PNG\r\n\x1a\n', 'png'),
    (b'OggS', 'ogg'),
    (b'RIFF', 'wav'),
    (b'PK\x03\x04', 'zip'),
    (b'\x1f\x8b', 'gz'),
    (b'KTX ', 'ktx'),
    (b'PVR\x03', 'pvr'),
    (b'\xab\x4a\x53\x52\x31\x38\x34\xbb\x0d\x0a\x1a\x0a', 'm3g'),
]


def guess_ext(data):
    for sig, ext in SIGS:
        if data[:len(sig)] == sig:
            return ext
    head = data[:256]
    printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127)
    if head and printable / len(head) > 0.9:
        if b'<?xml' in head or (b'<' in head and b'>' in head):
            return 'xml'
        if b'function' in head or b'local ' in head or head.lstrip().startswith(b'--'):
            return 'lua'
        return 'txt'
    return 'bin'


def sanitize_component(name):
    return ''.join(c if c.isalnum() or c in '_-.' else '_' for c in name)


EXT_GROUPS = {
    'png': 'textures',
    'ktx': 'textures',
    'pvr': 'textures',
    'ogg': 'audio',
    'wav': 'audio',
    'm3g': 'models_3d',
    'xml': 'config',
    'lua': 'config',
}

PREFIX_GROUPS = [
    ('IDB_', 'textures'),
    ('SUR_IDB_', 'textures'),
    ('GNS_BIN_IMG', 'textures'),
    ('IDM_', 'audio'),
    ('KEYSET_', 'audio_keysets'),
    ('BIN_M3G', 'models_3d'),
    ('BIN_', 'binary'),
    ('CFG_', 'config'),
    ('SUR_FONT', 'fonts'),
    ('BIN_FONT', 'fonts'),
    ('SUR_', 'ui'),
    ('GNS_', 'ui'),
]


def guess_group_folder(name, ext=None):
    if ext in EXT_GROUPS:
        return EXT_GROUPS[ext]
    for prefix, folder in PREFIX_GROUPS:
        if name.startswith(prefix):
            return folder
    return 'other'


def load_res_map(path):
    res_map = {}
    if not os.path.isfile(path):
        return res_map
    with open(path, encoding='ascii', errors='replace') as f:
        for line in f:
            line = line.strip()
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            res_map[k] = v
    return res_map


def resolve_output_path(out_root, name, res_map, used_paths, unmapped_ext=None, res_map_exists=True):
    relpath = res_map.get(name)
    if relpath:
        parts = [sanitize_component(p) for p in relpath.split('/') if p]
        rel = os.path.join(*parts) if parts else sanitize_component(name)
    else:
        base_name = sanitize_component(name) or 'unnamed'
        ext = unmapped_ext or 'bin'
        group_folder = guess_group_folder(name, ext)
        if res_map_exists:
            rel = os.path.join('_unmapped', group_folder, base_name + '.' + ext)
        else:
            rel = os.path.join(group_folder, base_name + '.' + ext)
    base, ext_existing = os.path.splitext(rel)
    key = rel.lower()
    n = used_paths.get(key, 0)
    used_paths[key] = n + 1
    if n > 0:
        rel = '%s_%d%s' % (base, n, ext_existing)
    return os.path.join(out_root, rel)


def offset_candidates(toc, data_offset):
    return {
        'A': toc.section_start + data_offset,
        'B': toc.data_section_abs_start + data_offset,
    }


def find_offset_base(toc, f, entries):
    scores = {'A': 0, 'B': 0}
    tested = 0
    for rec in entries:
        if tested >= 40 or rec['data_size'] == 0:
            continue
        matched_any = False
        for mode, off in offset_candidates(toc, rec['data_offset']).items():
            try:
                f.seek(off)
                head = f.read(16)
            except Exception:
                head = b''
            if any(head[:len(sig)] == sig for sig, _ in SIGS):
                scores[mode] += 1
                matched_any = True
        if matched_any:
            tested += 1
    if scores['A'] >= scores['B'] and scores['A'] > 0:
        return 'A'
    if scores['B'] > 0:
        return 'B'
    return 'A'


def find_fgib_sections(path):
    positions = []
    chunk_size = 1 << 20
    overlap = 3
    prev_tail = b''
    base = 0
    total_size = os.path.getsize(path)
    last_pct = -1
    print()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            buf = prev_tail + chunk
            start = 0
            while True:
                idx = buf.find(b'FGIB', start)
                if idx == -1:
                    break
                positions.append(base - len(prev_tail) + idx)
                start = idx + 1
            prev_tail = buf[-overlap:]
            base += len(chunk)
            if total_size > 0:
                pct = min(100, int(base * 100 / total_size))
                if pct != last_pct:
                    render_progress('Scanning... %d%%' % pct)
                    last_pct = pct
    clear_progress()
    return positions


def ask_yes_no(question):
    retry_prompt = question.rsplit('\n', 1)[-1]
    prompt = question
    while True:
        answer = input(prompt).strip().lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no', ''):
            return False
        if sys.platform == 'win32':
            sys.stdout.write('\x1b[1A\x1b[2K\r')
        prompt = retry_prompt


def print_error_summary(label, errors):
    if not errors:
        return
    print('! %d %s:' % (len(errors), label))
    for line in errors[:20]:
        print('   ', line)


def collect_sections(obb_path, candidates, f):
    sections = []
    rejected = []
    for sec_start in candidates:
        try:
            toc = BigFileTOC(obb_path, sec_start)
        except Exception as e:
            rejected.append((sec_start, str(e)))
            continue
        entries = enumerate_all(toc)
        if not entries:
            rejected.append((sec_start, 'header is valid but holds no records'))
            continue
        base_mode = find_offset_base(toc, f, entries)
        print('Section @%d -> %d records (data base %s, TOC %d + data %d)'
              % (sec_start, len(entries), base_mode, toc.toc_size, toc.data_size))
        if toc.dictionary_size:
            # The string table can reference the dictionary through 0x1A bytes; in the
            # game library CBigFile_v2::DecompressIntoDestinationBufferIfNeeded expands
            # them. This script does not use the dictionary, so a non-empty one means
            # names would come out wrong.
            print('    ! WARNING: this section carries a %d-byte name dictionary,'
                  ' which this script does not use.' % toc.dictionary_size)
            print('      File names that reference it may come out incomplete.')
        sections.append((toc, entries, base_mode))
    if rejected:
        print()
        print('%d candidates carried an FGIB signature but were REJECTED:' % len(rejected))
        for off, why in rejected:
            print('   @%-12d %s' % (off, why))
        print('   (the signature also occurs in random data, so not all of these are errors)')
    return sections


# Byte ranges left outside the FGIB sections. In this OBB two raw 3GP videos sit at the
# end, but that is not special-cased as 'video extraction': 'a range no section claimed'
# is the general concept, whatever the game.
# When splitting a range we only trust containers that can be validated. Short
# signatures (gzip's two-byte \x1f\x8b, say) occur by chance inside video data and shred
# the file into hundreds of bogus pieces; that is exactly what the first attempt did.
# An MP4/3GP box can be validated: the 4 bytes before 'ftyp' are a big-endian box size.


def unclaimed_regions(path, sections_data):
    total = os.path.getsize(path)
    claimed = sorted((t.section_start, t.section_start + t.toc_size + t.data_size)
                     for t, _, _ in sections_data)
    gaps = []
    cursor = 0
    for start, end in claimed:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append((cursor, total))
    return gaps


def find_mp4_boxes(buf):
    """Offsets of 'ftyp' boxes whose declared size is consistent."""
    out = []
    i = 0
    while True:
        i = buf.find(b'ftyp', i)
        if i < 0:
            break
        box = i - 4
        if box >= 0:
            size = int.from_bytes(buf[box:box + 4], 'big')
            if 8 <= size <= len(buf) - box:
                out.append(box)
        i += 4
    return out


def region_ext(head):
    for sig, ext in SIGS:
        if head.startswith(sig):
            return ext
    if head[4:8] == b'ftyp':
        return 'mp4'
    return 'bin'


def split_region(f, start, end):
    """Separate MP4/3GP containers stored back to back inside one range.

    Nothing else is split on: cutting a range at a signature that cannot be
    validated means tearing a healthy file apart. A range that cannot be split
    comes out as a single piece.
    """
    length = end - start
    f.seek(start)
    buf = f.read(length)
    boxes = find_mp4_boxes(buf)
    if not boxes or boxes[0] != 0:
        return [(start, end, region_ext(buf[:16]))]
    out = []
    for k, off in enumerate(boxes):
        nxt = boxes[k + 1] if k + 1 < len(boxes) else length
        out.append((start + off, start + nxt, 'mp4'))
    return out


def extract_unclaimed(f, obb_path, sections_data, out_root):
    gaps = unclaimed_regions(obb_path, sections_data)
    if not gaps:
        print()
        print('No bytes left outside the sections; the whole OBB was resolved.')
        return 0
    total_bytes = sum(b - a for a, b in gaps)
    print()
    print('%d ranges OUTSIDE the sections, %s bytes in total:'
          % (len(gaps), format(total_bytes, ',')))
    folder = os.path.join(out_root, '_unclaimed')
    written = 0
    for a, b in gaps:
        for start, end, ext in split_region(f, a, b):
            name = 'offset_%d.%s' % (start, ext)
            print('   @%-12d %12s bytes -> _unclaimed/%s'
                  % (start, format(end - start, ','), name))
            os.makedirs(folder, exist_ok=True)
            f.seek(start)
            remaining = end - start
            with open(os.path.join(folder, name), 'wb') as out:
                while remaining > 0:
                    chunk = f.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            written += 1
    return written


def confirm_unmapped_extraction(res_map, total_unmapped):
    if not res_map:
        question = ('\nres_map.dat not found, so the real file paths are unknown.\n'
                    'Extract the files grouped by name/content type instead? (y/N): ')
        return ask_yes_no(question)
    if total_unmapped == 0:
        return False
    question = ('\n%d files have no match in res_map.dat.\n'
                'Extract those into an "_unmapped" folder as well? (y/N): ' % total_unmapped)
    return ask_yes_no(question)


def extract_records(sections_data, res_map, out_root, f, extract_unmapped):
    total = 0
    used_paths = {}
    read_errors = []
    write_errors = []
    res_map_exists = bool(res_map)
    total_records = sum(
        1 for _, entries, _ in sections_data for rec in entries
        if rec['name'] in res_map or extract_unmapped
    )
    processed = 0
    last_pct = -1
    print()
    for toc, entries, base_mode in sections_data:
        for rec in entries:
            is_mapped = rec['name'] in res_map
            if not is_mapped and not extract_unmapped:
                continue
            processed += 1
            if total_records > 0:
                pct = min(100, int(processed * 100 / total_records))
                if pct != last_pct:
                    render_progress('Extracting... %d%% (%d/%d)' % (pct, processed, total_records))
                    last_pct = pct
            abs_off = offset_candidates(toc, rec['data_offset'])[base_mode]
            try:
                data = read_resource_bytes(f, abs_off, rec)
            except Exception as e:
                read_errors.append('%s -> %s' % (rec['name'], e))
                continue
            ext_guess = None if is_mapped else guess_ext(data)
            out_path = resolve_output_path(out_root, rec['name'], res_map, used_paths, ext_guess, res_map_exists)
            try:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, 'wb') as out:
                    out.write(data)
            except Exception as e:
                write_errors.append('%s -> %s -> %s' % (rec['name'], out_path, e))
                continue
            total += 1
    clear_progress()
    return total, read_errors, write_errors


def process_obb(obb_path, out_root, res_map):
    candidates = find_fgib_sections(obb_path)
    print('FGIB candidates:', len(candidates))
    f = open(obb_path, 'rb')

    sections_data = collect_sections(obb_path, candidates, f)
    if not sections_data:
        f.close()
        print('\n[ERROR]: no section matching the FGIB format was found in this file.')
        print('\nThe file may not be in the expected format:', obb_path)
        return None
    total_entries = sum(len(entries) for _, entries, _ in sections_data)
    total_mapped = sum(sum(1 for e in entries if e['name'] in res_map) for _, entries, _ in sections_data)
    total_unmapped = total_entries - total_mapped

    print('Valid sections:', len(sections_data))
    print('Total records:', total_entries, '| matched by res_map.dat:', total_mapped,
          '| unmatched:', total_unmapped)

    extract_unmapped = confirm_unmapped_extraction(res_map, total_unmapped)

    if not extract_unmapped and total_mapped == 0:
        f.close()
        return None

    print('\n=== Extracting:', os.path.basename(obb_path), '===')
    total, read_errors, write_errors = extract_records(sections_data, res_map, out_root, f, extract_unmapped)

    print('zlib inflated:', DECOMP_STATS['exact'],
          '| size mismatch:', DECOMP_STATS['size_mismatch'],
          '| zlib error:', DECOMP_STATS['zlib_error'])
    extract_unclaimed(f, obb_path, sections_data, out_root)
    f.close()
    print_error_summary('records COULD NOT BE READ', read_errors)
    print_error_summary('files COULD NOT BE WRITTEN', write_errors)
    return total


def main():
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if len(sys.argv) > 1:
        obb_files = sys.argv[1:]
    else:
        obb_files = sorted(glob.glob(os.path.join(script_dir, '*.obb')))
    if not obb_files:
        print('[ERROR]: no .obb file found in this folder:')
        print(script_dir)
        print('\nPut the script in the SAME folder as the .obb file and run it again.')
        return
    res_map_path = os.path.join(script_dir, 'res_map.dat')
    res_map = load_res_map(res_map_path)
    if res_map:
        print('res_map.dat found,', len(res_map), 'original path mappings loaded.')
    else:
        print('res_map.dat not found.')
    for obb in obb_files:
        print('\n=== Reading:', os.path.basename(obb), '===')
        set_title('FGIB Extractor - %s' % os.path.basename(obb))
        out_root = os.path.join(script_dir, os.path.splitext(os.path.basename(obb))[0])
        if os.path.isdir(out_root):
            print('\n[ERROR]: output folder already exists:', out_root)
            print('\nDelete or rename that folder, then run again.')
            continue
        count = process_obb(obb, out_root, res_map)
        if count is not None:
            print('TOTAL files extracted:', count, '->', out_root)
    set_title('FGIB Extractor - done')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
    wait_for_keypress()
