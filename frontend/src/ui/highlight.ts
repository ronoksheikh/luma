import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

for (const [n, l] of Object.entries({ bash, css, ini, javascript, json, markdown, python, typescript, xml, yaml })) hljs.registerLanguage(n, l);

export { hljs };

/** Syntax-highlighted HTML for a code string (escaped fallback). */
export function highlightCode(code: string, language?: string): string {
  try {
    return language && hljs.getLanguage(language) ? hljs.highlight(code || "", { language }).value : hljs.highlightAuto(code || "").value;
  } catch {
    return (code || "").replace(/[&<>]/g, (c: string) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] as string);
  }
}
