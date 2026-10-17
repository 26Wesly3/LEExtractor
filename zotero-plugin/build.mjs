// Packs the plugin folder into dist/LEExtractor.xpi.
// An .xpi is a zip whose root holds manifest.json / bootstrap.js / lib / locale.
//
// 用 Node 自带的 zlib 直接写 zip：不依赖 `zip` 命令，也不依赖 PowerShell 的引号
// 规则（仓库路径里有空格和单引号，Compress-Archive 很容易被引号坑到）。
import { deflateRawSync } from "node:zlib";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve, relative } from "node:path";

const ENTRIES = ["manifest.json", "bootstrap.js", "lib", "locale", "icon.png"];
const DIST = resolve("dist");
const TARGET = resolve(DIST, "LEExtractor.xpi");

function walk(entry, prefix = "") {
  const full = resolve(entry);
  if (statSync(full).isDirectory()) {
    return readdirSync(full).flatMap((child) => walk(join(entry, child), prefix));
  }
  return [{ path: `${prefix}${relative(".", full).replace(/\\/g, "/")}`, full }];
}

function createZip(files) {
  const chunks = [];
  const central = [];
  let offset = 0;
  for (const file of files) {
    const data = readFileSync(file.full);
    const name = Buffer.from(file.path, "utf8");
    const compressed = deflateRawSync(data);
    const crc = (() => {
      let table = createZip.table;
      if (!table) {
        table = createZip.table = Array.from({ length: 256 }, (_, index) => {
          let value = index;
          for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
          return value >>> 0;
        });
      }
      let value = 0xffffffff;
      for (const byte of data) value = table[(value ^ byte) & 0xff] ^ (value >>> 8);
      return (value ^ 0xffffffff) >>> 0;
    })();

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    chunks.push(local, name, compressed);

    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(8, 10);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(compressed.length, 20);
    header.writeUInt32LE(data.length, 24);
    header.writeUInt16LE(name.length, 28);
    header.writeUInt32LE(offset, 42);
    central.push(Buffer.concat([header, name]));

    offset += local.length + name.length + compressed.length;
  }

  const centralBuffer = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralBuffer.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...chunks, centralBuffer, end]);
}

const files = ENTRIES.flatMap((entry) => walk(entry));
mkdirSync(DIST, { recursive: true });
writeFileSync(TARGET, createZip(files));
if (!existsSync(TARGET)) throw new Error("打包失败：没有生成 " + TARGET);
console.log(`built ${TARGET} (${files.length} files)`);
