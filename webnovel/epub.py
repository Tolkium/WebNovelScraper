"""EPUB assembly and readable text extraction for preview and text-to-speech."""

from html import escape
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple, TypedDict
import uuid

from bs4 import BeautifulSoup
from bs4.element import NavigableString
from ebooklib import ITEM_DOCUMENT, epub

from webnovel.settings import JobSettings


class ChapterData(TypedDict):
    title: str
    content: str
    notes: List[str]
    footnotes: Dict[str, str]


def build_epub_identifier(title: str, source_key: str, first: object, last: object) -> str:
    """Return a deterministic urn:uuid unique to a book's title, source, and effective chapter bounds."""
    seed = f'webnovelscraper:{title.strip()}|{source_key}|{first}-{last}'
    return f'urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}'


class EpubCreator:
    def __init__(
        self,
        novel_title: str,
        author: str = 'Unknown',
        translator: str = '',
        created_by: str = 'WebNovelScraper',
        settings: Optional[JobSettings] = None,
        language: str = 'en',
        identifier: Optional[str] = None,
    ):
        self.settings = settings if settings is not None else JobSettings()
        self.language = language
        self.novel_title = novel_title
        self.author = author
        self.translator = translator
        self.created_by = created_by
        self.identifier = identifier or f'urn:uuid:{uuid.uuid4()}'
        self.book = epub.EpubBook()
        self._initialize_book()

    def _initialize_book(self) -> None:
        self.book.set_identifier(self.identifier)
        self.book.set_title(self.novel_title)
        self.book.set_language(self.language)

        if self.author and self.author != 'Unknown':
            self.book.add_author(self.author)
        if self.translator:
            self.book.add_metadata('DC', 'contributor', self.translator, {'role': 'trl'})
        if self.created_by and self.created_by != 'WebNovelScraper':
            self.book.add_metadata('DC', 'publisher', self.created_by)

    def add_cover(self, cover_data: Tuple[bytes, str, str]) -> None:
        image_data, content_type, image_name = cover_data
        self.book.set_cover(image_name, image_data)

    def add_chapter(self, chapter_data: ChapterData, index: int) -> epub.EpubHtml:
        chapter = epub.EpubHtml(
            title=chapter_data['title'],
            file_name=f'chap_{index:03d}.xhtml',
            lang=self.language,
        )

        # Without the heading, text-to-speech goes straight into the story; the title stays in the contents.
        chapter_content = [f'<h1>{escape(chapter_data["title"])}</h1>'] if self.settings.chapter_headings else []
        chapter_content += ['<div class="chapter-content">', chapter_data['content']]

        if self.settings.include_footnotes and chapter_data['footnotes']:
            chapter_content.extend(self._format_footnotes(chapter_data['footnotes'], chapter_data['content']))

        if self.settings.include_notes and chapter_data['notes']:
            chapter_content.extend(self._format_chapter_notes(chapter_data['notes']))

        chapter_content.append('</div>')
        chapter.content = '\n'.join(chapter_content)

        self.book.add_item(chapter)
        return chapter

    def _format_footnotes(self, footnotes: Dict[str, str], body_html: str = '') -> List[str]:
        items = ['<div class="footnotes">', '<hr/>', '<h3>Footnotes</h3>']
        for num, text in footnotes.items():
            esc_num = escape(num)
            backlink = f' <a href="#ref-{esc_num}">↩</a>' if f'id="ref-{esc_num}"' in body_html else ''
            items.append(
                f'<p id="footnote-{esc_num}" class="footnote"><sup>{esc_num}</sup> {escape(text)}{backlink}</p>'
            )
        items.append('</div>')
        return items

    def _format_chapter_notes(self, notes: List[str]) -> List[str]:
        return [
            '<div class="chapter-notes">',
            '<hr/>',
            '<h3>Chapter Notes</h3>',
            *notes,
            '</div>',
        ]

    def finalize(self, chapters: List[epub.EpubHtml]) -> None:
        self._add_navigation(chapters)
        self._add_styling()
        for chapter in chapters:
            chapter.add_link(href='style/nav.css', rel='stylesheet', type='text/css')
        self.book.spine = ['nav'] + chapters

    def _add_navigation(self, chapters: List[epub.EpubHtml]) -> None:
        self.book.toc = ((epub.Section(self.novel_title), chapters),)
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

    def _add_styling(self) -> None:
        style = """
            body { 
                font-family: Times New Roman, serif; 
                margin: 5%; 
                text-align: justify; 
            }
            h1 { 
                text-align: center; 
                font-size: 2em;
                margin: 1em 0 2em 0;
            }
            h2 { margin-top: 2em; }
            h3 { margin-top: 1.5em; }
            p { margin: 1em 0; text-indent: 0; }
            .chapter-content {
                line-height: 1.6;
            }
            .footnotes {
                margin-top: 3em;
                font-size: 0.9em;
                border-top: 1px solid #cccccc;
                padding-top: 1em;
            }
            .footnote {
                margin: 0.5em 0;
                font-size: 0.9em;
            }
            .chapter-notes {
                margin-top: 3em;
                padding-top: 1em;
                border-top: 1px solid #ccc;
                font-size: 0.9em;
            }
            .note {
                font-style: italic;
                margin: 0.5em 0;
            }
            sup {
                vertical-align: super;
                font-size: 0.8em;
            }
            a {
                color: #000000;
                text-decoration: none;
            }
        """
        nav_css = epub.EpubItem(
            uid='style_nav',
            file_name='style/nav.css',
            media_type='text/css',
            content=style,
        )
        self.book.add_item(nav_css)


INLINE_HTML_TAGS = {
    'a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'code', 'data', 'del', 'dfn', 'em',
    'font', 'i', 'ins', 'kbd', 'mark', 'q', 's', 'samp', 'small', 'span',
    'strong', 'sub', 'sup', 'time', 'u', 'var', 'wbr',
}


def _extract_readable_text(body) -> str:
    """Extract chapter text in document order, preserving paragraphs without breaking inline tags for TTS."""
    paragraphs: List[str] = []
    run: List[str] = []

    def flush() -> None:
        if not run:
            return
        raw = ''.join(run)
        run.clear()
        lines = [re.sub(r'[ \t]+', ' ', part).strip() for part in raw.splitlines()]
        text = '\n'.join(part for part in lines if part)
        if text:
            paragraphs.append(text)

    def walk(node) -> None:
        for child in getattr(node, 'children', ()):
            name = getattr(child, 'name', None)
            if name is None:
                if type(child) is not NavigableString:
                    continue
                # In HTML, source-code newlines inside a paragraph are normal spaces; only <br> breaks lines.
                run.append(re.sub(r'\s+', ' ', str(child)))
            elif name == 'br':
                run.append('\n')
            elif name == 'hr':
                flush()
            elif name in INLINE_HTML_TAGS:
                walk(child)
            else:
                flush()
                walk(child)
                flush()

    walk(body)
    flush()
    return '\n\n'.join(paragraphs)


def read_epub(path) -> Tuple[str, List[Tuple[str, str]]]:
    book = epub.read_epub(str(path), options={'ignore_ncx': True})
    titles = book.get_metadata('DC', 'title')
    title = titles[0][0] if titles else Path(path).stem
    chapters: List[Tuple[str, str]] = []
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if (
            item is None
            or item.get_type() != ITEM_DOCUMENT
            or not item.is_chapter()
            or 'nav' in getattr(item, 'properties', [])
        ):
            continue
        # The stored file, because get_content() rebuilds it without the <title> used when there is no heading.
        soup = BeautifulSoup(item.content, 'html.parser')
        for unwanted in soup.select('script, style'):
            unwanted.decompose()
        heading = soup.find(['h1', 'h2', 'h3']) or soup.title
        name = heading.get_text(' ', strip=True) if heading and heading.get_text(strip=True) else item.get_name()
        body = soup.body or soup
        text = _extract_readable_text(body)
        if text:
            chapters.append((name, text))
    if not chapters:
        raise ValueError('This EPUB has no readable chapters in its reading order.')
    return title, chapters
