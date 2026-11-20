import fs from 'node:fs';
import path from 'node:path';
// Upstream cuts an attached document at fileTokenLimit silently (debug log only), so the model answers as if it read
// everything. Tell the model, which tells the user. Seam: extractFileContext in packages/api (v0.8.8).
export const NEEDLE = '# "${file.filename}"\\n${limitedText}\\n`';
export const NOTICE = '# "${file.filename}"\\n${limitedText}${wasTruncated ? `\\n\\n[Catatan sistem K-AI: dokumen ini terlalu panjang dan dipotong; hanya ±${fileTokenLimit} token pertama yang terbaca. Di awal jawaban, beri tahu pengguna bahwa sisa dokumen tidak ikut dibaca dan sarankan membagi dokumen.]` : ""}\\n`';
export function installFileNotice(root = '/app') {
  const dir = path.join(root, 'packages/api/dist');
  const files = fs.readdirSync(dir, { recursive: true }).filter(f => /\.(c?js|mjs)$/.test(f)).map(f => path.join(dir, f))
    .filter(f => fs.readFileSync(f, 'utf8').includes(NEEDLE));
  if (!files.length) throw new Error('Unexpected native file-context seam');
  for (const file of files) {
    const source = fs.readFileSync(file, 'utf8');
    if (source.split(NEEDLE).length !== 2) throw new Error(`Unexpected native file-context seam: ${file}`);
    fs.writeFileSync(file, source.replace(NEEDLE, NOTICE));
  }
}
