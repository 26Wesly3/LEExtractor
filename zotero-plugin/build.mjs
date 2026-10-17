// Packs the plugin folder into dist/LEExtractor.xpi (an .xpi is a zip with files at its root).
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
mkdirSync("dist", { recursive: true });
execFileSync("zip", ["-qr", "dist/LEExtractor.xpi", "manifest.json", "bootstrap.js", "lib"], { stdio: "inherit" });
console.log("built dist/LEExtractor.xpi");
