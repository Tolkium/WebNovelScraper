"""Desktop jobs and EPUB previews, independent of the GUI event loop."""

from dataclasses import asdict, dataclass, fields
from functools import partial
from pathlib import Path
from threading import Event
from typing import Callable, Optional
from urllib.parse import urlparse
import json
import os
import re
import sys
import tempfile
import shutil

from bs4 import BeautifulSoup
from bs4.element import NavigableString
from ebooklib import epub, ITEM_DOCUMENT
import requests
import soupsieve

from WebNovelScraper import (
    Config, EpubCreator, ScraperCancelled, ScraperConstants, WebNovelScraper,
    build_epub_identifier, is_browser_check, load_saved_chapters,
)


@dataclass
class JobSettings:
    base_url: str = ''
    novel_id: str = ''
    chapter_prefix: str = 'chapter-'
    saved_file: str = ''
    title: str = ''
    start: int = 1
    end: int = 10
    output_dir: str = ''
    filename: str = ''
    author: str = ''
    translator: str = ''
    creator: str = 'WebNovelScraper'
    language: str = 'en'
    cover_url: str = ''
    simple_chapters: bool = False
    chapter_headings: bool = True
    include_footnotes: bool = False
    include_notes: bool = False
    debug: bool = False
    timeout: int = 30
    retries: int = 3
    requests_per_second: float = 2.0
    min_content_length: int = 100
    max_cover_height: int = 2400
    cover_aspect_ratio: float = 2 / 3
    user_agent: str = ScraperConstants.USER_AGENT
    title_selector: str = ScraperConstants.TITLE_SELECTOR
    content_selector: str = ScraperConstants.CONTENT_SELECTOR

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('The settings file must contain a JSON object.')
        allowed = {field.name: field for field in fields(cls)}
        defaults = cls()
        values = {}
        for key, value in data.items():
            if key not in allowed:
                continue
            expected = type(getattr(defaults, key))
            if expected is float and type(value) in (int, float):
                value = float(value)
            elif type(value) is not expected:
                raise ValueError(f'Invalid value for {key.replace("_", " ")}.')
            values[key] = value
        return cls(**values)

    @property
    def output_path(self):
        name = self.filename.strip() or safe_filename(self.title)
        if not name.lower().endswith('.epub'):
            name += '.epub'
        return Path(self.output_dir).expanduser() / name

    def validate(self):
        errors = {}
        if self.saved_file:
            if not Path(self.saved_file).expanduser().exists():
                errors['saved_file'] = 'The saved chapters file was not found. Choose it again with Browse.'
        else:
            if not http_url(self.base_url):
                errors['base_url'] = 'Enter a full website base URL beginning with https:// or http://.'
            if not self.novel_id.strip() or re.search(r'[/\\?#\s]', self.novel_id):
                errors['novel_id'] = 'Enter the novel ID from the URL, without slashes, spaces or query parameters.'
            if not re.fullmatch(r'[\w.-]*', self.chapter_prefix):
                errors['chapter_prefix'] = 'Enter the chapter link prefix, such as chapter- or og-chapter-, without slashes or spaces.'
        if not self.title.strip():
            errors['title'] = 'Enter the book title.'
        if self.start < 1 or self.end < self.start:
            errors['start'] = 'The first chapter must be at least 1 and no greater than the last chapter.'
        if not self.output_dir.strip():
            errors['output_dir'] = 'Choose where to save the EPUB.'
        filename = self.output_path.name
        stem = Path(filename).stem
        if (re.search(r'[<>:"/\\|?*\x00-\x1f]', self.filename)
                or self.filename.rstrip().endswith('.')
                or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', stem, re.I)):
            errors['filename'] = 'Choose a valid filename without folder paths or reserved Windows names.'
        if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', self.language):
            errors['language'] = 'Use a language code such as en, en-US, sk or de.'
        if self.cover_url.strip() and not http_url(self.cover_url):
            errors['cover_url'] = 'The cover URL must begin with https:// or http://.'
        for name, low, high in [
            ('timeout', 1, 600), ('retries', 1, 20),
            ('requests_per_second', .1, 20), ('min_content_length', 1, 100000),
            ('max_cover_height', 100, 10000), ('cover_aspect_ratio', .1, 3),
        ]:
            if not low <= getattr(self, name) <= high:
                errors[name] = f'{name.replace("_", " ").capitalize()} must be between {low} and {high}.'
        if not self.user_agent.strip():
            errors['user_agent'] = 'Enter a browser user agent.'
        for name in ('title_selector', 'content_selector'):
            try:
                if not getattr(self, name).strip():
                    raise ValueError()
                soupsieve.compile(getattr(self, name))
            except Exception:
                errors[name] = 'Enter a valid CSS selector.'
        return errors

    def config(self):
        return Config(
            INCLUDE_FOOTNOTES=self.include_footnotes,
            INCLUDE_CHAPTER_NOTES=self.include_notes,
            SIMPLE_CHAPTER_NAMES=self.simple_chapters,
            CHAPTER_HEADINGS=self.chapter_headings,
            DEBUG_MODE=self.debug,
        )

    def constants(self):
        return ScraperConstants(
            DEFAULT_TIMEOUT=self.timeout, MAX_RETRIES=self.retries,
            DEFAULT_REQUESTS_PER_SECOND=self.requests_per_second,
            MIN_CONTENT_LENGTH=self.min_content_length,
            MAX_COVER_HEIGHT=self.max_cover_height,
            COVER_ASPECT_RATIO=self.cover_aspect_ratio,
            USER_AGENT=self.user_agent, TITLE_SELECTOR=self.title_selector,
            CONTENT_SELECTOR=self.content_selector,
        )


def http_url(value):
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname)
    except ValueError:
        return False


def safe_filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title.strip()).rstrip(' .')
    if not name:
        name = 'My book'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        name = '_' + name
    return name[:180] + '.epub'


# Title and content selectors for websites whose markup differs from the defaults.
SITE_SELECTORS = {
    'wuxiaworld.com': ('h4', 'div.chapter-body'),
}


def site_selectors(url):
    """Return (title_selector, content_selector) for a known website, or None."""
    host = (urlparse(url.strip()).hostname or '').lower()
    for domain, selectors in SITE_SELECTORS.items():
        if host == domain or host.endswith('.' + domain):
            return selectors
    return None


def site_blocks_app(url, user_agent, timeout=15):
    """True when url answers with a browser check; network errors are left for the job to report."""
    try:
        response = requests.get(url, headers={'User-Agent': user_agent}, timeout=timeout)
    except requests.RequestException:
        return False
    return is_browser_check(response)


BROWSER_SCRIPT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'browser' / 'collect-chapters.js'


def browser_script(settings):
    """The browser console script from browser/collect-chapters.js, set up for the book in settings."""
    values = {
        'chapterUrl': f'{settings.base_url.rstrip("/")}/{settings.novel_id.strip()}/{settings.chapter_prefix}{{n}}',
        'name': settings.novel_id.strip(),
        'start': settings.start,
        'end': settings.end,
        'titleSelector': settings.title_selector,
        'contentSelector': settings.content_selector,
    }
    return BROWSER_SCRIPT.read_text(encoding='utf-8').replace('/*SETTINGS*/null', json.dumps(values), 1)


def split_novel_url(value):
    """Split a novel or chapter URL into a website base, novel ID and chapter link prefix."""
    if not http_url(value):
        raise ValueError('Paste a full novel URL beginning with https:// or http://.')
    parsed = urlparse(value.strip())
    parts = parsed.path.strip('/').split('/')
    prefix = 'chapter-'
    chapter = re.fullmatch(r'([\w.-]*?chapter-)\d+.*', parts[-1], re.IGNORECASE) if parts else None
    if chapter:
        prefix = chapter.group(1)
        parts.pop()
    if len(parts) < 2:
        raise ValueError('Paste the novel page URL, including its novel ID.')
    novel_id = parts.pop()
    base = f'{parsed.scheme}://{parsed.netloc}/' + '/'.join(parts) + '/'
    return base, novel_id, prefix


@dataclass
class JobResult:
    path: Optional[Path]
    success: int
    failed: list
    cancelled: bool = False


def _epub_identifier(settings: JobSettings, loaded_saved=None, saved=None) -> str:
    """Return a deterministic UUID unique to this book's source and effective chapter bounds."""
    if settings.saved_file:
        if loaded_saved is not None and loaded_saved.sources:
            source = ','.join(loaded_saved.sources)
        else:
            source = str(Path(settings.saved_file).expanduser().resolve())
        first = saved[0].raw_number if saved else settings.start
        last = saved[-1].raw_number if saved else settings.end
    else:
        source = f'{settings.base_url.rstrip("/")}/{settings.novel_id.strip()}/{settings.chapter_prefix}'
        first, last = settings.start, settings.end
    return build_epub_identifier(settings.title, source, first, last)


def run_job(settings: JobSettings, cancel: Event, log: Callable,
            progress: Callable, log_path: Path, overwrite=False):
    errors = settings.validate()
    if errors:
        raise ValueError('\n'.join(errors.values()))
    output = settings.output_path
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        raise FileExistsError(f'{output.name} already exists. Choose another filename.')
    loaded_saved = None
    saved = []
    missing_majors = []
    if settings.saved_file:
        loaded_saved = load_saved_chapters(settings.saved_file)
        saved = [ch for ch in loaded_saved.chapters if settings.start <= ch.major <= settings.end]
        if not saved:
            raise ValueError(f'No saved chapters {settings.start} to {settings.end} in {Path(settings.saved_file).name}. '
                             'Check the chapter range or choose another file.')
        present_majors = {ch.major for ch in saved}
        missing_majors = [n for n in range(settings.start, settings.end + 1) if n not in present_majors]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    config = settings.config()
    chapters, failed = [], []
    temp_path = None

    def check_cancel():
        if cancel.is_set():
            raise ScraperCancelled()

    with WebNovelScraper(settings.debug, settings=config, constants=settings.constants(),
                         cancel_event=cancel, log_callback=log, log_path=log_path) as scraper:
        if settings.saved_file and loaded_saved is not None:
            if len(loaded_saved.sources) > 1:
                log(f'Warning: saved folder contains chapters from {len(loaded_saved.sources)} different sources: '
                    f'{", ".join(loaded_saved.sources)}.')
            if loaded_saved.duplicates:
                dup_list = ', '.join(loaded_saved.duplicates[:10])
                more = f' (+{len(loaded_saved.duplicates) - 10} more)' if len(loaded_saved.duplicates) > 10 else ''
                log(f'Warning: duplicate saved chapters found ({dup_list}{more}); kept the longest version of each.')
            if loaded_saved.invalid_count:
                log(f'Warning: skipped {loaded_saved.invalid_count} saved chapter section(s) with invalid chapter numbers.')
            entries = [
                (ch.key, ch.raw_number, f'saved chapter {ch.raw_number}',
                 partial(scraper.parse_saved_chapter, ch.section, ch.raw_number))
                for ch in saved
            ]
            for missing_num in missing_majors:
                entries.append(((missing_num,), missing_num, f'saved chapter {missing_num}', None))
            entries.sort(key=lambda item: item[0])
            sources = [(num, src, loader) for _, num, src, loader in entries]
        else:
            sources = []
            for number in range(settings.start, settings.end + 1):
                url = f'{settings.base_url.rstrip("/")}/{settings.novel_id.strip()}/{settings.chapter_prefix}{number}'
                sources.append((number, url, partial(scraper.get_chapter_content, url)))
        total = len(sources)

        try:
            check_cancel()
            creator = EpubCreator(settings.title.strip(), author=settings.author.strip(),
                                  translator=settings.translator.strip(), created_by=settings.creator.strip(),
                                  settings=config, language=settings.language,
                                  identifier=_epub_identifier(settings, loaded_saved, saved))
            if settings.cover_url.strip():
                log('Loading and optimizing the cover image…')
                cover = scraper.get_cover_image(settings.cover_url.strip())
                check_cancel()
                if cover:
                    creator.add_cover(cover)
                else:
                    log('Warning: the cover could not be loaded. Continuing without a cover.')
            log(f'Creating “{settings.title}” — {total} chapters requested.')
            for index, (number, source, load) in enumerate(sources):
                check_cancel()
                progress({'stage': 'fetching', 'number': number, 'current': index,
                          'total': total, 'success': len(chapters), 'failed': len(failed), 'title': source})
                if load is None:
                    failed.append(number)
                    log(f'Warning: saved chapter {number} is missing from {Path(settings.saved_file).name}.')
                    progress({'stage': 'failed', 'number': number,
                              'current': index + 1, 'total': total,
                              'success': len(chapters), 'failed': len(failed), 'title': 'Missing from saved file'})
                    continue
                title, content, notes, footnotes = load()
                check_cancel()
                if title and content:
                    chapters.append({'title': title, 'content': content, 'notes': notes or [], 'footnotes': footnotes or {}})
                    # Simple names only repeat the number; the word count shows a chapter that came through short.
                    heading = f'Chapter {number}' if title == f'Chapter {number}' else f'Chapter {number}: {title}'
                    log(f'{heading} — {len(BeautifulSoup(content, "html.parser").get_text().split()):,} words')
                elif settings.saved_file:
                    failed.append(number)
                    log(f'Warning: saved chapter {number} has no usable text.')
                else:
                    failed.append(number)
                    log(f'Warning: chapter {number} failed after {settings.retries} attempts.')
                progress({'stage': 'downloaded' if title and content else 'failed', 'number': number,
                          'current': index + 1, 'total': total,
                          'success': len(chapters), 'failed': len(failed), 'title': title or 'Could not download'})
            if not chapters:
                raise ValueError('No chapters could be downloaded. Check the novel URL and Advanced page selectors. See Activity for details.')
            check_cancel()
            log('Building the EPUB, navigation and reading styles…')
            epub_chapters = []
            for index, chapter in enumerate(chapters, 1):
                check_cancel()
                epub_chapters.append(creator.add_chapter(chapter, index))
            creator.finalize(epub_chapters)
            fd, temporary = tempfile.mkstemp(prefix='.webnovel-', suffix='.epub', dir=output.parent)
            os.close(fd)
            temp_path = Path(temporary)
            if epub.write_epub(str(temp_path), creator.book, {}) is False:
                raise IOError('The EPUB could not be written.')
            check_cancel()
            if overwrite:
                os.replace(temp_path, output)
            else:
                # Exclusive creation also protects against another process creating the file mid-job.
                try:
                    os.link(temp_path, output)
                except FileExistsError:
                    raise
                except OSError:
                    # FAT volumes and some network shares do not support hard links.
                    created = False
                    try:
                        with output.open('xb') as target, temp_path.open('rb') as source:
                            created = True
                            shutil.copyfileobj(source, target)
                    except Exception:
                        if created:
                            output.unlink(missing_ok=True)
                        raise
                temp_path.unlink()
            temp_path = None
            log(f'Saved {output}. {len(chapters)} chapters included; {len(failed)} failed.')
            return JobResult(output, len(chapters), failed)
        except ScraperCancelled:
            log('Cancelled. No EPUB was saved; existing files were left unchanged.')
            return JobResult(None, len(chapters), failed, cancelled=True)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


INLINE_HTML_TAGS = {
    'a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'code', 'data', 'del', 'dfn', 'em',
    'font', 'i', 'ins', 'kbd', 'mark', 'q', 's', 'samp', 'small', 'span',
    'strong', 'sub', 'sup', 'time', 'u', 'var', 'wbr',
}


def _extract_readable_text(body) -> str:
    """Extract chapter text in document order, preserving paragraphs without breaking inline tags for TTS."""
    paragraphs = []
    run = []

    def flush():
        if not run:
            return
        raw = ''.join(run)
        run.clear()
        lines = [re.sub(r'[ \t]+', ' ', part).strip() for part in raw.splitlines()]
        text = '\n'.join(part for part in lines if part)
        if text:
            paragraphs.append(text)

    def walk(node):
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


def read_epub(path):
    book = epub.read_epub(str(path), options={'ignore_ncx': True})
    titles = book.get_metadata('DC', 'title')
    title = titles[0][0] if titles else Path(path).stem
    chapters = []
    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if (item is None or item.get_type() != ITEM_DOCUMENT or not item.is_chapter()
                or 'nav' in getattr(item, 'properties', [])):
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
