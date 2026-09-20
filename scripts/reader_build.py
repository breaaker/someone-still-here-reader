#!/usr/bin/env python3
"""把 `正文/` 里的 Markdown 稿源构建成手机端阅读网页（dist/index.html）。

设计要点：
- 唯一稿源是 `正文/`；`作者资料/` 与 `写作记录/` 永不参与构建，也被 .gitignore 排除。
- 不维护额外清单文件：网页每次都从稿源整体重建，`validate` 用“重建结果与已发布网页是否逐字节一致”来判断是否过期。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "正文"
DEFAULT_TEMPLATE = ROOT / "reader" / "template.html"
DEFAULT_CONFIG = ROOT / "reader" / "site.json"
DEFAULT_OUT = ROOT / "dist" / "index.html"
# 不得进入公开仓库的路径：内部资料与写给执笔者的项目说明（都含剧透或结构内幕）
PRIVATE_PATHS = ("作者资料", "写作记录", "AGENTS.md", "README.md")

CHAPTER_FILE_RE = re.compile(r"^第(?P<num>[0-9]+)章[_-]?(?P<name>.*)\.md$")
VOLUME_DIR_RE = re.compile(r"^卷(?P<num>[0-9]+)[_-]?(?P<name>.*)$")
HEADING_RE = re.compile(r"^#\s*(?P<number>第[^\s　]+章)[\s　]+(?P<name>.+?)\s*$")
BREAK_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
MIN_CHAPTER_CHARS = 1200


class BuildError(Exception):
    """稿源或模板不满足发布条件。"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def prose_chars(text: str) -> int:
    """统计正文汉字/字符数（去掉 Markdown 标记与空白）。"""
    plain = re.sub(r"[#>*_`\[\]()!-]", "", text)
    return len(re.sub(r"\s+", "", plain))


def inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(?P<body>[^*]+)\*\*", r"<strong>\g<body></strong>", escaped)
    return escaped


def render_prose(body: str) -> str:
    """把一章的 Markdown 正文转成 <p>/<blockquote>/分隔符。"""
    html_parts: list[str] = []
    paragraph: list[str] = []
    quote_block: list[list[str]] = []
    quote_paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            html_parts.append(f"            <p>{inline(''.join(paragraph))}</p>")
            paragraph.clear()

    def flush_quote_paragraph() -> None:
        if quote_paragraph:
            quote_block.append(list(quote_paragraph))
            quote_paragraph.clear()

    def flush_quote() -> None:
        flush_quote_paragraph()
        if quote_block:
            html_parts.append("            <blockquote>")
            for lines in quote_block:
                joined = "<br>".join(inline(line) for line in lines if line)
                html_parts.append(f"              <p>{joined}</p>")
            html_parts.append("            </blockquote>")
            quote_block.clear()

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_quote()
            continue
        if BREAK_RE.match(stripped):
            flush_paragraph()
            flush_quote()
            html_parts.append('            <div class="scene-break" aria-hidden="true"><span>◆◆◆</span></div>')
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            content = stripped[1:].strip()
            if not content:
                flush_quote_paragraph()
            else:
                quote_paragraph.append(content)
            continue
        flush_quote()
        if stripped.startswith("#"):
            flush_paragraph()
            heading = stripped.lstrip("#").strip()
            html_parts.append(f"            <h2 class='section-heading'>{inline(heading)}</h2>")
            continue
        paragraph.append(stripped)

    flush_paragraph()
    flush_quote()
    return "\n".join(html_parts)


def load_config(path: Path) -> dict:
    if not path.exists():
        raise BuildError(f"缺少站点配置：{path}")
    data = json.loads(read_text(path))
    for key in ("title", "description", "tagline", "notice"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise BuildError(f"站点配置缺少字段：{key}")
    return data


def split_volume(folder: str) -> tuple[str, str]:
    match = VOLUME_DIR_RE.match(folder)
    if match:
        return f"卷{int(match.group('num')):02d}", (match.group("name") or "").strip()
    return "", folder


def iter_chapter_files(src_dir: Path) -> list[tuple[Path, str, str]]:
    """返回按 (卷, 章号) 排好序的 [(文件, 卷标签, 卷名), ...]。"""
    if not src_dir.exists():
        raise BuildError(f"找不到正文目录：{src_dir}")
    files = sorted(path for path in src_dir.rglob("*.md") if path.is_file())
    if not files:
        raise BuildError(f"正文目录里没有 Markdown 章节：{src_dir}")
    entries = []
    for path in files:
        relative = path.relative_to(src_dir)
        folder = relative.parts[0] if len(relative.parts) > 1 else ""
        volume_label, volume_name = split_volume(folder) if folder else ("", "")
        match = CHAPTER_FILE_RE.match(path.name)
        if not match:
            raise BuildError(f"章节文件名不符合「第001章_标题.md」：{relative}")
        entries.append((int(match.group("num")), path, volume_label, volume_name, str(relative)))
    entries.sort(key=lambda item: (item[2], item[0], item[4]))
    seen: set[int] = set()
    for number, _, _, _, relative in entries:
        if number in seen:
            raise BuildError(f"章节编号重复：第{number}章（{relative}）")
        seen.add(number)
    expected = list(range(1, len(entries) + 1))
    if sorted(seen) != expected:
        missing = sorted(set(expected) - seen)
        raise BuildError(f"章节编号不连续，缺少：{missing}")
    return [(path, volume_label, volume_name) for _, path, volume_label, volume_name, _ in entries]


def parse_chapter(path: Path) -> tuple[str, str, str, str]:
    """返回 (显示标题, 章号, 正文 Markdown)。"""
    text = read_text(path).replace("\r\n", "\n")
    lines = text.split("\n")
    heading_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if heading_index is None:
        raise BuildError(f"空文件：{path}")
    match = HEADING_RE.match(lines[heading_index].strip())
    if not match:
        raise BuildError(f"正文第一行必须是「# 第一章　标题」：{path}")
    number = match.group("number").strip()
    name = match.group("name").strip()
    body = "\n".join(lines[heading_index + 1 :]).strip("\n")
    count = prose_chars(body)
    if count < MIN_CHAPTER_CHARS:
        raise BuildError(f"正文过短（{count} 字，至少 {MIN_CHAPTER_CHARS} 字）：{path}")
    return f"{number}　{name}", number, name, body


def build_toc(chapters: list[dict]) -> str:
    rows: list[str] = []
    last_volume = None
    for index, chapter in enumerate(chapters):
        volume_label = chapter["volume_label"]
        if volume_label and volume_label != last_volume:
            volume_name = chapter["volume_name"]
            rows.append("        <li class=\"volume-label\">")
            rows.append(f"          <b>{html.escape(volume_label)}</b><span>{html.escape(volume_name)}</span>")
            rows.append("        </li>")
            last_volume = volume_label
        current = "true" if index == 0 else "false"
        rows.append("        <li>")
        rows.append(f'          <button class="chapter-button" type="button" data-go="{index}" aria-current="{current}">')
        rows.append(f'            <span class="chapter-number">{html.escape(chapter["number"])}</span>')
        rows.append(f'            <span class="chapter-name">{html.escape(chapter["name"])}</span>')
        rows.append("          </button>")
        rows.append("        </li>")
    return "\n".join(rows)


def build_sections(chapters: list[dict]) -> str:
    blocks: list[str] = []
    for index, chapter in enumerate(chapters):
        active = " active" if index == 0 else ""
        blocks.append(f'        <section class="chapter{active}" data-title="{html.escape(chapter["title"], quote=True)}">')
        blocks.append('          <div class="prose">')
        blocks.append(chapter["prose"])
        blocks.append("          </div>")
        blocks.append("        </section>")
    return "\n\n".join(blocks)


def collect_chapters(src_dir: Path) -> list[dict]:
    chapters: list[dict] = []
    for path, volume_label, volume_name in iter_chapter_files(src_dir):
        title, number, name, body = parse_chapter(path)
        chapters.append(
            {
                "title": title,
                "number": number,
                "name": name,
                "volume_label": volume_label,
                "volume_name": volume_name,
                "chars": prose_chars(body),
                "prose": render_prose(body),
                "path": path,
            }
        )
    return chapters


def render_site(src_dir: Path, template_path: Path, config_path: Path) -> tuple[str, list[dict]]:
    config = load_config(config_path)
    if not template_path.exists():
        raise BuildError(f"缺少阅读器模板：{template_path}")
    chapters = collect_chapters(src_dir)
    total_chars = sum(chapter["chars"] for chapter in chapters)

    template = read_text(template_path)
    for token in ("{{BOOK_TITLE}}", "{{DESCRIPTION}}", "{{TAGLINE}}", "{{SITE_FOOT}}", "{{FIRST_TITLE}}", "<!--TOC-->", "<!--CHAPTERS-->"):
        if token not in template:
            raise BuildError(f"模板缺少占位符：{token}")

    site = (
        template.replace("{{BOOK_TITLE}}", html.escape(config["title"]))
        .replace("{{DESCRIPTION}}", html.escape(config["description"], quote=True))
        .replace("{{TAGLINE}}", html.escape(config["tagline"]))
        .replace("{{SITE_FOOT}}", f"已更新 {len(chapters)} 章 · 约 {total_chars:,} 字<br>{html.escape(config['notice'])}")
        .replace("{{FIRST_TITLE}}", html.escape(chapters[0]["title"]))
        .replace("<!--TOC-->", build_toc(chapters))
        .replace("<!--CHAPTERS-->", build_sections(chapters))
    )
    site = site.replace("{{BUILD_DATE}}", date.today().isoformat())
    if "{{" in site:
        leftover = re.findall(r"\{\{[A-Z_]+\}\}", site)
        if leftover:
            raise BuildError("模板里仍有未替换的占位符：" + ", ".join(sorted(set(leftover))))
    return site, chapters


def validate_site(site: str, chapters: list[dict]) -> None:
    titles = re.findall(r'<section class="chapter(?: active)?" data-title="([^"]*)">', site)
    expected = [chapter["title"] for chapter in chapters]
    if titles != expected:
        raise BuildError("网页章节与稿源不一致")
    if site.count('class="chapter active"') != 1:
        raise BuildError("网页必须且只能有一个 active 章节")
    if site.count("<section") != site.count("</section>"):
        raise BuildError("section 标签失衡")
    if site.count("<div") != site.count("</div>"):
        raise BuildError("div 标签失衡")
    buttons = re.findall(r'class="chapter-button" type="button" data-go="(\d+)"', site)
    if buttons != [str(index) for index in range(len(chapters))]:
        raise BuildError("目录索引不连续")
    if "<script" not in site or site.count("<script") != 1:
        raise BuildError("阅读器脚本缺失或重复")


def check_private_paths_untracked(root: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        return
    tracked = subprocess.run(
        ["git", "ls-files", *PRIVATE_PATHS],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    if tracked:
        raise BuildError("内部资料被 Git 跟踪，公开仓库会泄露剧透：" + ", ".join(tracked[:5]))


def command_build(args: argparse.Namespace) -> int:
    site, chapters = render_site(args.src, args.template, args.config)
    validate_site(site, chapters)
    check_private_paths_untracked(ROOT)
    atomic_write(args.out, site)
    total = sum(chapter["chars"] for chapter in chapters)
    print(f"已重建：{args.out}")
    print(f"章节：{len(chapters)} 章 · 约 {total:,} 字")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    site, chapters = render_site(args.src, args.template, args.config)
    validate_site(site, chapters)
    check_private_paths_untracked(ROOT)
    if not args.out.exists():
        raise BuildError(f"尚未生成网页：{args.out}；请先运行 build")
    published = read_text(args.out)
    if published != site:
        raise BuildError("已发布网页与稿源不一致（正文已改但没重建），请运行 build")
    print(f"校验通过：{len(chapters)} 章，网页与稿源一致")
    return 0


def command_status(args: argparse.Namespace) -> int:
    chapters = collect_chapters(args.src)
    total = sum(chapter["chars"] for chapter in chapters)
    for index, chapter in enumerate(chapters, start=1):
        print(f"{index:>3}. {chapter['title']}（{chapter['chars']:,} 字）")
    print(f"合计：{len(chapters)} 章 · {total:,} 字")
    if args.out.exists():
        state = "与稿源一致" if read_text(args.out) == render_site(args.src, args.template, args.config)[0] else "已过期，需要 build"
        print(f"网页：{args.out}（{state}）")
    else:
        print(f"网页：尚未生成（{args.out}）")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="由正文重建 dist/index.html")
    subparsers.add_parser("validate", help="校验网页与稿源是否一致")
    subparsers.add_parser("status", help="显示章节与字数")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    handlers = {"build": command_build, "validate": command_validate, "status": command_status}
    try:
        return handlers[args.command](args)
    except (BuildError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
