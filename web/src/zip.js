/**
 * Minimal ZIP reader/writer, enough for .docx.
 *
 * No library: `DecompressionStream`/`CompressionStream` with 'deflate-raw' are
 * exactly the codec ZIP uses, so the whole page stays self-contained with no CDN
 * and no bundler. That matters here beyond tidiness — a manuscript is
 * unpublished work, and a build with no third-party script has nothing that
 * could ship it anywhere.
 *
 * Deliberately not supported, because Word does not produce them for documents
 * of this size: ZIP64 (needed past 4 GB or 65,535 entries), encryption, and
 * multi-disk archives. readZip throws rather than guessing if it meets one.
 *
 * Entry order and the stored/deflated choice are preserved on rewrite, so the
 * output differs from the input only where the document XML was actually edited.
 */

const SIG_LOCAL = 0x04034b50;
const SIG_CENTRAL = 0x02014b50;
const SIG_EOCD = 0x06054b50;
const SIG_EOCD64 = 0x06064b50;

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    t[i] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function deflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * Read a .zip into an ordered list of entries.
 * Returns [{ name, data: Uint8Array, method, mtime, mdate }].
 */
export async function readZip(buffer) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // Locate the end-of-central-directory record by scanning back over the
  // maximum comment length (65535) plus the record itself.
  let eocd = -1;
  const from = Math.max(0, bytes.length - 22 - 0xffff);
  for (let i = bytes.length - 22; i >= from; i--) {
    if (view.getUint32(i, true) === SIG_EOCD) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('Not a .docx file (no ZIP end-of-central-directory record).');

  const count = view.getUint16(eocd + 10, true);
  let cdOffset = view.getUint32(eocd + 16, true);
  if (cdOffset === 0xffffffff || count === 0xffff) {
    // A ZIP64 archive. Word does not emit these for ordinary documents, and
    // silently misreading one would corrupt the output.
    throw new Error('ZIP64 archives are not supported.');
  }
  if (view.getUint32(Math.max(0, eocd - 20), true) === SIG_EOCD64) {
    throw new Error('ZIP64 archives are not supported.');
  }

  const decoder = new TextDecoder('utf-8');
  const entries = [];
  let p = cdOffset;
  for (let i = 0; i < count; i++) {
    if (view.getUint32(p, true) !== SIG_CENTRAL) {
      throw new Error('Corrupt ZIP central directory.');
    }
    const flags = view.getUint16(p + 8, true);
    if (flags & 0x0001) throw new Error('Encrypted .docx files are not supported.');
    const method = view.getUint16(p + 10, true);
    const mtime = view.getUint16(p + 12, true);
    const mdate = view.getUint16(p + 14, true);
    const compSize = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const localOffset = view.getUint32(p + 42, true);
    const name = decoder.decode(bytes.subarray(p + 46, p + 46 + nameLen));

    if (view.getUint32(localOffset, true) !== SIG_LOCAL) {
      throw new Error(`Corrupt ZIP local header for ${name}`);
    }
    // The local header's name and extra lengths can differ from the central
    // directory's, so the data offset must come from the local header.
    const lNameLen = view.getUint16(localOffset + 26, true);
    const lExtraLen = view.getUint16(localOffset + 28, true);
    const dataStart = localOffset + 30 + lNameLen + lExtraLen;
    const raw = bytes.subarray(dataStart, dataStart + compSize);

    let data;
    if (method === 0) data = raw.slice();
    else if (method === 8) data = await inflateRaw(raw);
    else throw new Error(`Unsupported ZIP compression method ${method} for ${name}`);

    entries.push({ name, data, method, mtime, mdate });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

/** Write entries back to a .zip Blob, preserving order and compression method. */
export async function writeZip(entries) {
  const encoder = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const e of entries) {
    const nameBytes = encoder.encode(e.name);
    const crc = crc32(e.data);
    const method = e.method === 0 ? 0 : 8;
    const body = method === 0 ? e.data : await deflateRaw(e.data);

    const local = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, SIG_LOCAL, true);
    lv.setUint16(4, 20, true); // version needed
    lv.setUint16(6, 0, true); // flags
    lv.setUint16(8, method, true);
    lv.setUint16(10, e.mtime ?? 0, true);
    lv.setUint16(12, e.mdate ?? 0x21, true); // 1980-01-01 when unknown
    lv.setUint32(14, crc, true);
    lv.setUint32(18, body.length, true);
    lv.setUint32(22, e.data.length, true);
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);
    local.set(nameBytes, 30);

    chunks.push(local, body);
    central.push({
      nameBytes, crc, method, body, size: e.data.length, offset, mtime: e.mtime, mdate: e.mdate,
    });
    offset += local.length + body.length;
  }

  const cdStart = offset;
  for (const c of central) {
    const rec = new Uint8Array(46 + c.nameBytes.length);
    const rv = new DataView(rec.buffer);
    rv.setUint32(0, SIG_CENTRAL, true);
    rv.setUint16(4, 20, true); // version made by
    rv.setUint16(6, 20, true); // version needed
    rv.setUint16(8, 0, true);
    rv.setUint16(10, c.method, true);
    rv.setUint16(12, c.mtime ?? 0, true);
    rv.setUint16(14, c.mdate ?? 0x21, true);
    rv.setUint32(16, c.crc, true);
    rv.setUint32(20, c.body.length, true);
    rv.setUint32(24, c.size, true);
    rv.setUint16(28, c.nameBytes.length, true);
    rv.setUint32(42, c.offset, true);
    rec.set(c.nameBytes, 46);
    chunks.push(rec);
    offset += rec.length;
  }

  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, SIG_EOCD, true);
  ev.setUint16(8, central.length, true);
  ev.setUint16(10, central.length, true);
  ev.setUint32(12, offset - cdStart, true);
  ev.setUint32(16, cdStart, true);
  chunks.push(eocd);

  return new Blob(chunks, {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  });
}
