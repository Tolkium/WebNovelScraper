import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import time
import re
import uuid
from typing import Optional, Tuple, List, Dict, Set, TypedDict, Union
from bs4.element import Comment, NavigableString
import os
from PIL import Image
from io import BytesIO
import argparse
from dataclasses import dataclass, field
from urllib.parse import urlparse
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import sys

class ChapterData(TypedDict):
    title: str
    content: str
    notes: List[str]
    footnotes: Dict[str, str]

@dataclass
class ScraperConstants:
    DEFAULT_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    MIN_CONTENT_LENGTH: int = 100
    DEFAULT_REQUESTS_PER_SECOND: float = 2.0
    MAX_COVER_HEIGHT: int = 2400
    COVER_ASPECT_RATIO: float = 2/3
    TITLE_SELECTOR: str = 'span.chapter-title'
    CONTENT_SELECTOR: str = '#chapter-container'
    USER_AGENT: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

@dataclass
class Config:
    """Configuration settings for the scraper."""
    INCLUDE_FOOTNOTES: bool = False
    INCLUDE_CHAPTER_NOTES: bool = False
    SIMPLE_CHAPTER_NAMES: bool = False
    CHAPTER_HEADINGS: bool = True
    DEBUG_MODE: bool = False

    @classmethod
    def validate(cls) -> None:
        """Validate configuration settings."""
        if not all(isinstance(getattr(cls, attr), bool) for attr in cls.__annotations__):
            raise ValueError("All configuration values must be boolean")

class ConsoleColors:
    """ANSI color codes for console output."""
    RED: str = '\033[91m'
    GREEN: str = '\033[92m'
    YELLOW: str = '\033[93m'
    RESET: str = '\033[0m'

# The start of a translator note: "TL:", "T/N:", "TL Note:".
TL_NOTE_START = r'T/?[LN](?:\s*note)?\s*:'
NOTE_TEXT = re.compile(
    rf'\b{TL_NOTE_START}|\btranslator[\'’]?s? note\b|\bchapter note(?:\s*:|\s+by\b)'
    r'|\bthanks for playing\b|^as always,? (?:thanks|thank you|enjoy)'
    r'|\bglossary of common\b|\bglossary link\b|\bcurrent schedule:|\bcheck out my patreon\b', re.IGNORECASE)
FOOTNOTE_LINE = re.compile(r'^(\d{1,2})\.(?!\d)\s*(.+)$')
LABELED_FOOTNOTE = re.compile(
    rf'^(\d{{1,2}})\.\s*(?:footnote\s*:|{TL_NOTE_START}|translator[\'’]?s?\s*note\s*:)',
    re.IGNORECASE,
)
FOOTNOTE_SUP = re.compile(r'^\[?(\d{1,3})\]?$|^\*+$')

class ValidationError(Exception):
    """Custom exception for validation errors."""
    pass

class ScraperError(Exception):
    """Custom exception for scraper-specific errors."""
    pass

class _RetryableFetchError(Exception):
    """Transient fetch issue (such as an empty response) that should be retried."""
    pass

class ScraperCancelled(Exception):
    """A user requested cancellation between requests or during a wait."""

class SiteBlocked(Exception):
    """The website answered with a browser check, so chapters must be collected in the browser."""

    def __init__(self, url: str):
        super().__init__(f"{urlparse(url).netloc} shows a browser check, so the app cannot read its chapters.")

CHALLENGE_MARKERS = (
    'just a moment',
    'cf-browser-verification',
    '/cdn-cgi/challenge-platform',
    'challenge-running',
    'checking your browser',
    'attention required! | cloudflare',
)
CF_403_MARKERS = CHALLENGE_MARKERS + ('access denied', 'error 1020', 'cloudflare')

def is_browser_check(response: requests.Response) -> bool:
    """True when the response is a Cloudflare check/block page instead of the requested page."""
    if response.status_code not in (403, 503):
        return False
    if response.headers.get('cf-mitigated', '').lower() == 'challenge':
        return True
    if 'cloudflare' not in response.headers.get('server', '').lower():
        return False
    body = (response.text or '').lower()
    markers = CF_403_MARKERS if response.status_code == 403 else CHALLENGE_MARKERS
    return any(marker in body for marker in markers)

def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse an HTTP Retry-After header (seconds or HTTP-date) into non-negative seconds."""
    if not header_value:
        return None
    value = header_value.strip()
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None

class CallbackLogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        self.callback(self.format(record))

class RateLimiter:
    """Monotonic request pacing with bounded retry backoff."""

    def __init__(self, requests_per_second: float = ScraperConstants.DEFAULT_REQUESTS_PER_SECOND,
                 cancel_event=None, clock=time.monotonic):
        self.rate = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self._clock = clock
        self._last_request_at: Optional[float] = None
        self.cancel_event = cancel_event

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise ScraperCancelled()
            return
        if self.cancel_event is not None:
            if self.cancel_event.wait(seconds):
                raise ScraperCancelled()
        else:
            time.sleep(seconds)

    def wait(self) -> None:
        """Wait until the minimum interval since the previous request has elapsed."""
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            self.sleep(self.min_interval - elapsed)
        elif self.cancel_event is not None and self.cancel_event.is_set():
            raise ScraperCancelled()
        self._last_request_at = self._clock()

    def retry_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Return a single bounded backoff delay for a retryable failure."""
        backoff = min(60.0, float(2 ** attempt))
        if retry_after is not None and retry_after > 0:
            return min(300.0, max(backoff, retry_after))
        return backoff

class ProgressTracker:
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.success = 0
        self.failures = 0
        
    def update(self, success: bool = True) -> None:
        self.current += 1
        if success:
            self.success += 1
        else:
            self.failures += 1
        self._display_progress()
    
    def _display_progress(self) -> None:
        percent = (self.current / self.total) * 100
        bar_length = 50
        filled_length = int(bar_length * self.current // self.total)
        bar = '=' * filled_length + '-' * (bar_length - filled_length)
        
        stats = f"Success: {self.success} Failures: {self.failures}"
        print(f'\rProgress |{bar}| {percent:.1f}% {stats}', end='', flush=True)

class WebNovelScraper:
    def __init__(self, debug_mode: bool = False, settings=None, constants=None,
                 cancel_event=None, log_callback=None, log_path: Optional[Union[str, Path]] = None):
        self.config = settings if settings is not None else Config
        self.constants = constants if constants is not None else ScraperConstants
        self._initialize_logging(debug_mode, log_callback, log_path)
        self.headers = {'User-Agent': self.constants.USER_AGENT}
        self.rate_limiter = RateLimiter(self.constants.DEFAULT_REQUESTS_PER_SECOND, cancel_event)
        self.debug_mode = debug_mode
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._closed = False

    def __enter__(self) -> 'WebNovelScraper':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _initialize_logging(self, debug_mode: bool, log_callback=None,
                            log_path: Optional[Union[str, Path]] = None) -> None:
        log_level = logging.DEBUG if debug_mode else logging.INFO
        self.logger = logging.Logger(__name__, log_level)
        handlers: List[logging.Handler] = []
        if log_path is not None:
            handlers.append(logging.FileHandler(log_path, encoding='utf-8'))
        if log_callback is not None:
            cb_handler = CallbackLogHandler(log_callback)
            # Keep raw debug HTML dumps out of the GUI activity log; full debug stays in the file log.
            cb_handler.setLevel(logging.INFO)
            handlers.append(cb_handler)
        elif debug_mode:
            handlers.append(logging.StreamHandler())
        for handler in handlers:
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(handler)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.session.close()
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

    def validate_url(self, url: str) -> bool:
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception as e:
            self.logger.error(f"URL validation failed: {str(e)}")
            return False

    def get_cover_image(self, image_url: str) -> Optional[Tuple[bytes, str, str]]:
        if not self.validate_url(image_url):
            raise ValidationError("Invalid cover image URL")

        try:
            response = self.session.get(image_url, timeout=self.constants.DEFAULT_TIMEOUT)
            response.raise_for_status()
            
            image = Image.open(BytesIO(response.content))
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            width, height = image.size
            target_ratio = self.constants.COVER_ASPECT_RATIO
            current_ratio = width/height
            
            if current_ratio != target_ratio:
                if current_ratio > target_ratio:
                    new_width = int(height * target_ratio)
                    left = (width - new_width) // 2
                    image = image.crop((left, 0, left + new_width, height))
                else:
                    new_height = int(width / target_ratio)
                    top = (height - new_height) // 2
                    image = image.crop((0, top, width, top + new_height))
            
            width, height = image.size
            if height > self.constants.MAX_COVER_HEIGHT:
                new_height = self.constants.MAX_COVER_HEIGHT
                new_width = int(new_height * target_ratio)
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            img_byte_arr = BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            return img_byte_arr.getvalue(), 'image/jpeg', 'cover.jpg'
            
        except Exception as e:
            self.logger.error(f"Cover image processing failed: {str(e)}")
            return None

    def _clean_html_content(self, content_div: BeautifulSoup) -> BeautifulSoup:
        """Clean HTML content while preserving main content."""
        # Remove SSE comments and ad-related elements first
        content_str = str(content_div)
        content_str = re.sub(r'<!--sse-->.*?<!--/sse-->', '', content_str, flags=re.DOTALL)
        
        # Parse cleaned content back to BeautifulSoup
        content_div = BeautifulSoup(content_str, 'html.parser')
        
        # Remove specific ad-related elements, plus page controls hidden from screen readers
        for selector in [
            'script', '.CTGIiSmv', '.L-MHncuP', '.vm-placement',
            'div[data-defid]', 'div[data-mobid]', '[aria-hidden="true"]:has(button)'
        ]:
            for element in content_div.select(selector):
                element.decompose()
        
        return content_div

    def _is_chapter_number(self, text: str) -> bool:
        text = text.strip()
        patterns = [
            r'^\d+$',
            r'^chapter\s+\d+$',
            r'^\d+\.?$'
        ]
        return any(re.match(pattern, text.lower()) for pattern in patterns)

    def _is_note_or_message(self, text: str, p_element: BeautifulSoup) -> bool:
        """Check if text or element content matches note patterns."""
        # First check strong/b elements combined with following text
        strong_elements = p_element.find_all(['strong', 'b'])
        for strong in strong_elements:
            strong_text = strong.text.strip().lower()
            if re.search(r'\b(?:notes?|t/n)\b', strong_text):
                return True

        # Then check regular text patterns, as whole words so story text such as "was always" stays
        return bool(NOTE_TEXT.search(text))

    def _is_math_exponent(self, sup) -> bool:
        """True when an unbracketed numeric <sup> follows a number or short unit (e.g. 10^2, 5m^2, <em>5m</em>^2)."""
        sup_text = sup.get_text(strip=True)
        if sup_text.startswith('[') and sup_text.endswith(']'):
            return False
        prev = sup.previous_sibling
        while prev is not None:
            if isinstance(prev, Comment):
                prev = prev.previous_sibling
                continue
            prev_text = str(prev) if isinstance(prev, NavigableString) else prev.get_text()
            if prev_text:
                return bool(re.search(r'(?:\d|\d\s*[A-Za-z]{1,3})$', prev_text))
            prev = prev.previous_sibling
        return False

    def _collect_footnote_refs(self, content_div: BeautifulSoup) -> Set[str]:
        """Collect footnote numbers referenced by <sup> tags or [n] markers in the chapter."""
        refs: Set[str] = set()
        for sup in content_div.find_all('sup'):
            match = re.fullmatch(r'\[?(\d{1,2})\]?', sup.get_text(strip=True))
            if match and not self._is_math_exponent(sup):
                refs.add(match.group(1))
        for text in _texts(content_div):
            for match in FOOTNOTE_MARK.finditer(text):
                refs.add(match.group(1) or match.group(2))
        return refs

    def _process_content(self, content_div: BeautifulSoup) -> Tuple[List[str], List[str], Dict[str, str], List[str]]:
        """Process and extract content components."""
        content_div = self._clean_html_content(content_div)
        wrap_loose_text(content_div)
        referenced_footnotes = self._collect_footnote_refs(content_div)
        tidy_for_speech(content_div, keep_notes=self.config.INCLUDE_CHAPTER_NOTES,
                        keep_footnotes=self.config.INCLUDE_FOOTNOTES)

        paragraphs = []
        after_separator = []
        footnotes = {}
        debug_notes = []

        if self.debug_mode:
            self.logger.debug("\nRaw content before processing:")
            self.logger.debug(content_div.prettify())

        for p in content_div.find_all('p'):
            trim_paragraph(p)
            text = p.text.strip()
            if not text:
                continue

            if self.debug_mode:
                self.logger.debug(f"\nProcessing paragraph: {text[:100]}...")

            fn_match = FOOTNOTE_LINE.match(text)
            is_footnote = bool(
                fn_match and (fn_match.group(1) in referenced_footnotes or LABELED_FOOTNOTE.match(text))
            )

            # Always add to main content unless it's clearly a note
            if SEPARATOR_LINE.match(text):
                # A scene break: a line in the book instead of symbols TTS would read out.
                if paragraphs and paragraphs[-1] != '<hr/>':
                    paragraphs.append('<hr/>')
            elif is_watermark(text):
                debug_notes.append(f"Not story: {text}")
            elif text.startswith('T/L:') or text.startswith('T/N:'):
                if self.config.INCLUDE_CHAPTER_NOTES:
                    after_separator.append(str(p))
                debug_notes.append(f"Start note: {str(p)}")
                if self.debug_mode:
                    self.logger.debug("Found start note")
            elif is_footnote:
                num, body = fn_match.group(1), fn_match.group(2).strip()
                if self.config.INCLUDE_FOOTNOTES:
                    footnotes[num] = body
                debug_notes.append(f"Footnote: {text}")
                if self.debug_mode:
                    self.logger.debug("Found footnote")
            elif self._is_note_or_message(text, p):
                if self.config.INCLUDE_CHAPTER_NOTES:
                    after_separator.append(str(p))
                debug_notes.append(f"End note: {str(p)}")
                if self.debug_mode:
                    self.logger.debug("Found end note")
            else:
                # Add to main content if it's not obviously a note
                if not self._is_chapter_number(text):
                    paragraphs.append(str(p))
                    if self.debug_mode:
                        self.logger.debug("Added to main content")

        if self.debug_mode:
            self.logger.debug(f"\nProcessing results:")
            self.logger.debug(f"Main paragraphs: {len(paragraphs)}")
            self.logger.debug(f"After separator: {len(after_separator)}")
            self.logger.debug(f"Footnotes: {len(footnotes)}")
            self.logger.debug(f"Debug notes: {len(debug_notes)}")
            if len(paragraphs) > 0:
                self.logger.debug("\nFirst paragraph:")
                self.logger.debug(paragraphs[0])
                self.logger.debug("\nLast paragraph:")
                self.logger.debug(paragraphs[-1])

        return paragraphs, after_separator, footnotes, debug_notes

    def get_chapter_content(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[List[str]], Optional[dict]]:
        """Fetch and process chapter content, retrying only transient errors."""
        if not self.validate_url(url):
            raise ValidationError("Invalid chapter URL")

        for attempt in range(self.constants.MAX_RETRIES):
            retry_after: Optional[float] = None
            try:
                self.rate_limiter.wait()

                response = self.session.get(url, timeout=self.constants.DEFAULT_TIMEOUT)
                if is_browser_check(response):
                    raise SiteBlocked(url)
                response.raise_for_status()

                if not response.text.strip():
                    raise _RetryableFetchError("Empty response received")

                soup = BeautifulSoup(response.text, 'html.parser')
                chapter = self.parse_chapter(soup.select_one(self.constants.TITLE_SELECTOR),
                                             soup.select_one(self.constants.CONTENT_SELECTOR), url)
                return chapter

            except (ScraperCancelled, SiteBlocked, ValidationError):
                raise
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                retryable = status is None or status in (408, 429) or status >= 500
                if e.response is not None and status in (429, 503):
                    retry_after = parse_retry_after(e.response.headers.get('Retry-After'))
                self.logger.error(f"Attempt {attempt + 1} failed for {url}: {str(e)}")
                if not retryable:
                    return None, None, None, None
            except ValueError as e:
                # Missing title/container or too-short text on a 200 OK page will not be fixed by retrying.
                self.logger.error(f"Chapter parsing failed for {url}: {str(e)}")
                return None, None, None, None
            except (_RetryableFetchError, requests.RequestException) as e:
                self.logger.error(f"Attempt {attempt + 1} failed for {url}: {str(e)}")
                if self.debug_mode:
                    import traceback
                    self.logger.error(traceback.format_exc())
            except Exception as e:
                # Never let one malformed chapter's unexpected error abort an entire multi-chapter job.
                self.logger.error(f"Unexpected error processing {url}: {str(e)}")
                if self.debug_mode:
                    import traceback
                    self.logger.error(traceback.format_exc())
                return None, None, None, None

            if attempt < self.constants.MAX_RETRIES - 1:
                self.rate_limiter.sleep(self.rate_limiter.retry_delay(attempt, retry_after))
            else:
                return None, None, None, None

        return None, None, None, None

    def parse_chapter(self, title_elem: Optional[BeautifulSoup], content_div: Optional[BeautifulSoup],
                      source: str) -> Tuple[str, str, List[str], Dict[str, str]]:
        """Turn a chapter's title element and content container into EPUB-ready parts.

        source is the chapter URL, or the chapter number for saved chapters.
        Raises ValueError when the title or usable content is missing.
        """
        for element in (title_elem, content_div):
            if element is not None:
                repair_split_words(element)
        chapter_title = self._process_chapter_title(title_elem, source)
        if not chapter_title:
            raise ValueError("Chapter title not found")
        if not content_div:
            raise ValueError("Content container not found")

        if self.debug_mode:
            self.logger.debug(f"\nProcessing chapter: {chapter_title}")
            self.logger.debug(f"Source: {source}")

        paragraphs, after_separator, footnotes, debug_notes = self._process_content(content_div)

        if len(paragraphs) == 0:
            if self.debug_mode:
                self.logger.debug("\nNo paragraphs found in content. Raw content:")
                self.logger.debug(content_div.prettify())
            raise ValueError("No valid content found")

        content = self._format_chapter_content(paragraphs, footnotes)
        if len(BeautifulSoup(content, 'html.parser').get_text(strip=True)) < self.constants.MIN_CONTENT_LENGTH:
            raise ValueError('Chapter content is shorter than the configured minimum length')

        if self.debug_mode and debug_notes:
            self.logger.debug(f"\nExcluded notes for {chapter_title}:")
            for note in debug_notes:
                self.logger.debug(f"Note: {note}")

        return chapter_title, content, after_separator, footnotes

    def parse_saved_chapter(self, section: BeautifulSoup, number: Union[int, float, str]) -> Tuple[Optional[str], Optional[str], Optional[List[str]], Optional[dict]]:
        """Process one chapter saved by browser/collect-chapters.js; returns Nones when it is unusable."""
        try:
            return self.parse_chapter(section.select_one('.chapter-title'),
                                      section.select_one('.chapter-container'), str(number))
        except ValueError as e:
            self.logger.error(f"Saved chapter {number} failed: {e}")
            return None, None, None, None

    def _process_chapter_title(self, title_elem: Optional[BeautifulSoup], url: str) -> Optional[str]:
        if not title_elem:
            return None
            
        if self.config.SIMPLE_CHAPTER_NAMES:
            url_match = re.search(r'(\d+(?:\.\d+)?)/?$', urlparse(url).path)
            return f"Chapter {url_match.group(1)}" if url_match else title_elem.text.strip()
        return title_elem.text.strip()

    def _format_chapter_content(self, paragraphs: List[str], footnotes: Dict[str, str]) -> str:
        processed_paragraphs = []
        assigned_ref_ids: Set[str] = set()
        for p in paragraphs:
            if '<sup' not in p.lower():
                processed_paragraphs.append(p)
                continue
            soup_p = BeautifulSoup(p, 'html.parser')
            for sup in soup_p.find_all('sup'):
                ref_text = sup.get_text(strip=True)
                sup_match = FOOTNOTE_SUP.fullmatch(ref_text)
                ref_key = sup_match.group(1) if sup_match and sup_match.group(1) else ref_text
                if self.config.INCLUDE_FOOTNOTES:
                    if ref_key in footnotes and not self._is_math_exponent(sup):
                        if ref_key not in assigned_ref_ids:
                            sup['id'] = f'ref-{ref_key}'
                            assigned_ref_ids.add(ref_key)
                        sup.wrap(soup_p.new_tag('a', href=f'#footnote-{ref_key}'))
                elif sup_match and not self._is_math_exponent(sup):
                    sup.decompose()
            processed_paragraphs.append(str(soup_p))

        return "\n".join(processed_paragraphs)

def build_epub_identifier(title: str, source_key: str, first: object, last: object) -> str:
    """Return a deterministic urn:uuid unique to a book's title, source, and effective chapter bounds."""
    seed = f'webnovelscraper:{title.strip()}|{source_key}|{first}-{last}'
    return f'urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}'

class EpubCreator:
    def __init__(self, novel_title: str, author: str = "Unknown",
                 translator: str = "", created_by: str = "WebNovelScraper",
                 settings=None, language: str = "en", identifier: Optional[str] = None):
        self.config = settings if settings is not None else Config
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
        
        if self.author and self.author != "Unknown":
            self.book.add_author(self.author)
        if self.translator:
            self.book.add_metadata('DC', 'contributor', self.translator, {'role': 'trl'})
        if self.created_by and self.created_by != "WebNovelScraper":
            self.book.add_metadata('DC', 'publisher', self.created_by)

    def add_cover(self, cover_data: Tuple[bytes, str, str]) -> None:
        image_data, content_type, image_name = cover_data
        self.book.set_cover(image_name, image_data)

    def add_chapter(self, chapter_data: ChapterData, index: int) -> epub.EpubHtml:
        chapter = epub.EpubHtml(
            title=chapter_data['title'],
            file_name=f'chap_{index:03d}.xhtml',
            lang=self.language
        )
        
        # Without the heading, text-to-speech goes straight into the story; the title stays in the contents.
        chapter_content = [f'<h1>{escape(chapter_data["title"])}</h1>'] if self.config.CHAPTER_HEADINGS else []
        chapter_content += ['<div class="chapter-content">', chapter_data['content']]

        if self.config.INCLUDE_FOOTNOTES and chapter_data['footnotes']:
            chapter_content.extend(self._format_footnotes(chapter_data['footnotes'], chapter_data['content']))
            
        if self.config.INCLUDE_CHAPTER_NOTES and chapter_data['notes']:
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
            '</div>'
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
            uid="style_nav",
            file_name="style/nav.css",
            media_type="text/css",
            content=style
        )
        self.book.add_item(nav_css)

SPLIT_WORD = re.compile(r'\b[A-Za-z]+(?:\.[A-Za-z]+)+\b')

ABBREVIATIONS = ('a.m', 'p.m', 'e.g', 'i.e', 'a.k.a')

def _join_split_word(match: re.Match) -> str:
    word = match.group()
    parts = word.split('.')
    for short in ABBREVIATIONS:
        rest = word[len(short) + 1:]
        if word.lower() == short:
            return word
        # "a.m.The" is an abbreviation before the next word; "a.m.a.z.i.n.g" is a split word.
        if word.lower().startswith(short + '.') and len(rest) > 1 and '.' not in rest:
            return f'{word[:len(short) + 1]} {rest}'
    # "I.went" is a sentence missing a space; "I.t" is a split word.
    if parts[0] == 'I' and len(parts) == 2 and len(parts[1]) > 2:
        return word
    # A capitalised last part starts the next sentence ("G.o.d.You").
    next_sentence = parts.pop() if len(parts) > 2 and parts[-1][0].isupper() else ''
    # Join only when a single letter was split off and the word continues in lowercase,
    # so sentences missing a space ("first.I") and acronyms ("L.T.S") stay as they are.
    if any(len(part) == 1 for part in parts) and all(part[0].islower() for part in parts[1:]):
        return ''.join(parts) + (f'. {next_sentence}' if next_sentence else '')
    return word

def _texts(element: BeautifulSoup) -> List[NavigableString]:
    """The element's text nodes, without HTML comments."""
    return [text for text in element.find_all(string=True) if not isinstance(text, Comment)]

def repair_split_words(element: BeautifulSoup) -> None:
    """Rejoin words split with dots, such as s.h.i.+eld or a.s.sa.s.sin, so TTS reads them as words."""
    for text in _texts(element):
        fixed = re.sub(r'\.([!?])\.\+', r'\1', text).replace('.+', '')
        fixed = SPLIT_WORD.sub(_join_split_word, fixed)
        if fixed != text:
            text.replace_with(fixed)

# Text that TTS reads aloud but is not part of the story. Translator notes in brackets may hold
# one level of brackets themselves: (TL: Oppa (older brother) is ...).
NOTE_IN_BRACKETS = re.compile(rf'\s*\({TL_NOTE_START}(?:[^()]|\([^()]*\))*\)', re.IGNORECASE)
# A link in brackets, or on its own without the punctuation after it. Links may hold brackets: /wiki/Foo_(bar).
URL = r'https?://(?:[^\s()]|\([^\s()]*\))*'
LINK = re.compile(rf'\s*\(\s*{URL}\s*\)|\s*{URL}(?<![.,;:!?\'"”’])')
# A footnote mark after a word or a sentence ("collapse. [1] Deals"), but not values such as "Strength [15]" or "Level: [5]".
FOOTNOTE_MARK = re.compile(r'(?<=[A-Za-z])\[(\d{1,2})\]|(?<=[.,;!?”"’)])\s*\[(\d{1,2})\]')
# The "[email protected]" placeholder some sites put in place of censored words, decorative symbols,
# and tildes stretching a word ("Jack~~pot", "Oppa~"); ranges such as "Monday~Friday" stay.
NOISE = re.compile(r'\[email(?:\s|\xa0)protected\]|[★☆■□▲△▼▽◆◇●○►◄▶◀※♥♡❤♪♫]\s*'
                   r'|(?<=[a-z])~+(?=[a-z])|(?<=[A-Za-z])~+(?=[\s.,;:!?”"’]|$)')
# A sentence that ends without a space before the next one ("neighborhood.Thanks", "Puk!Puk!"),
# but not inside links or addresses ("www.Example.com", "site.com/novel.Chapter").
RUN_ON_SENTENCE = re.compile(r'(?<![/\w])(?!www\.)([A-Za-z][a-z]+[.!?]+)(?=[A-Z][a-z])')

def tidy_for_speech(element: BeautifulSoup, keep_notes: bool = False, keep_footnotes: bool = False) -> None:
    """Remove text TTS should not read and add the missing space between run-on sentences.

    Bracketed translator notes stay when notes are included; links and footnote marks stay when footnotes are.
    """
    removed = [NOISE] + ([] if keep_notes else [NOTE_IN_BRACKETS]) + ([] if keep_footnotes else [LINK, FOOTNOTE_MARK])
    for text in _texts(element):
        fixed = text
        for pattern in removed:
            fixed = pattern.sub('', fixed)
        fixed = RUN_ON_SENTENCE.sub(r'\1 ', fixed)
        if fixed != text:
            text.replace_with(fixed)

# A site's own name, which some sites add to the story as a watermark ("Search Example.com for the original").
SITE_NAME = re.compile(r'\b[A-Z][\w-]*\.(?:com|net|org)\b')
WATERMARK_WORDS = re.compile(r'\b(?:find us|search|the original|original source|on google|stolen|available on'
                             r'|support us|quick look|great content)\b', re.IGNORECASE)
# A site name added after the end of a sentence or a value: '“Are you going?” Example.com', 'Attack: 25,020 Example.com'.
TRAILING_SITE_NAME = re.compile(r'(?<=[.!?…”"’\]\d])\s*' + SITE_NAME.pattern + r'\s*$')
SEPARATOR_LINE = re.compile(r'^[\s*=_~#·•—–-]+$')

def is_watermark(text: str) -> bool:
    """True for a short paragraph that advertises a site, such as 'We are "Example.com", find us on google.'"""
    rest = SITE_NAME.sub('', text)
    return bool(SITE_NAME.search(text)) and len(text.split()) <= 15 and (
        bool(WATERMARK_WORDS.search(rest)) or not re.search(r'[^\W\d_]', rest))

def trim_paragraph(paragraph: BeautifulSoup) -> None:
    """Drop a site name added after a paragraph's last sentence, and the spaces at its start and end."""
    texts = _texts(paragraph)
    for text in reversed(texts):
        fixed = TRAILING_SITE_NAME.sub('', text).rstrip()
        if fixed != text:
            text.replace_with(fixed)
        if fixed:
            break
    for text in _texts(paragraph):
        fixed = text.lstrip()
        if fixed != text:
            text.replace_with(fixed)
        if fixed:
            break

LOOSE_TAGS = {'em', 'i', 'strong', 'b', 'br', 'span', 'a', 'sup', 'u', 's', 'font'}

def wrap_loose_text(content_div: BeautifulSoup) -> None:
    """Put story text that sits outside any <p> into a paragraph of its own.

    Text after the last paragraph is left out, as it is usually page scraps rather than story.
    """
    root = content_div.find(True)
    if root is None:
        return
    children = [child for child in root.children if not isinstance(child, Comment)]
    last_paragraph = max((i for i, child in enumerate(children) if getattr(child, 'name', None) == 'p'), default=-1)
    run = []
    for child in children[:last_paragraph + 1]:
        if isinstance(child, NavigableString) or child.name in LOOSE_TAGS:
            run.append(child)
            continue
        # Only runs with text of their own; a lone link or label, such as "Next Chapter", is not story.
        if any(isinstance(part, NavigableString) and part.strip() for part in run):
            paragraph = content_div.new_tag('p')
            run[0].insert_before(paragraph)
            for part in run:
                paragraph.append(part.extract())
        run = []

CHAPTER_NUMBER_PATTERN = re.compile(r'^\d+(?:\.\d+)?$')

class _SavedChapterScanner(HTMLParser):
    """Streaming attribute-order-independent scanner for saved chapter HTML files."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources: List[str] = []
        self.numbers: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower()
        if tag_name not in ('meta', 'section'):
            return
        attr_map = {k.lower(): (v or '') for k, v in attrs}
        if tag_name == 'meta' and attr_map.get('name', '').strip().lower() == 'source':
            source = attr_map.get('content', '').strip()
            if source:
                self.sources.append(source)
        elif tag_name == 'section':
            classes = attr_map.get('class', '').split()
            if 'wns-chapter' in classes and 'data-number' in attr_map:
                self.numbers.append(attr_map['data-number'])

@dataclass
class SavedChapter:
    key: Tuple[int, ...]
    raw_number: str
    numeric: Union[int, float]
    major: int
    section: BeautifulSoup
    source: str = ''
    file_path: Optional[Path] = None

@dataclass
class SavedChaptersLoad:
    chapters: List[SavedChapter] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)
    invalid_count: int = 0

def _parse_chapter_number(raw: str) -> Optional[Tuple[Tuple[int, ...], Union[int, float], int]]:
    cleaned = raw.strip()
    if not CHAPTER_NUMBER_PATTERN.fullmatch(cleaned):
        return None
    parts = tuple(int(part) for part in cleaned.split('.'))
    # Normalize "1.0" to (1,) so it matches "1", while keeping "1.1" and "1.10" distinct.
    if len(parts) == 2 and parts[1] == 0:
        parts = (parts[0],)
    major = parts[0]
    if len(parts) == 1:
        numeric: Union[int, float] = major
    else:
        val = float(cleaned)
        numeric = int(val) if val.is_integer() else val
    return parts, numeric, major

def _saved_html_files(location) -> List[Path]:
    path_obj = Path(location).expanduser()
    if not path_obj.exists():
        return []
    return [path_obj] if path_obj.is_file() else sorted(path_obj.glob('*.html'))

def scan_saved_chapters(location) -> Dict[str, object]:
    """Quickly scan saved chapter files for range and diagnostics without full DOM parsing."""
    files = _saved_html_files(location)
    keys: Dict[Tuple[int, ...], int] = {}
    sources_seen: List[str] = []
    duplicates_seen: List[str] = []
    for path in files:
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError as error:
            raise ValueError(f'Could not read {path.name} as UTF-8 text.') from error
        scanner = _SavedChapterScanner()
        scanner.feed(text)
        scanner.close()
        for source in scanner.sources:
            if source not in sources_seen:
                sources_seen.append(source)
        for raw in scanner.numbers:
            parsed = _parse_chapter_number(raw)
            if parsed is None:
                continue
            key, _, major = parsed
            if key in keys:
                if raw.strip() not in duplicates_seen:
                    duplicates_seen.append(raw.strip())
            else:
                keys[key] = major
    if not keys:
        return {'count': 0, 'first': None, 'last': None, 'sources': sources_seen, 'duplicates': duplicates_seen}
    ordered_keys = sorted(keys)
    return {
        'count': len(ordered_keys),
        'first': keys[ordered_keys[0]],
        'last': keys[ordered_keys[-1]],
        'sources': sources_seen,
        'duplicates': duplicates_seen,
    }

def load_saved_chapters(location) -> SavedChaptersLoad:
    """Read chapters saved by browser/collect-chapters.js with source and duplicate diagnostics."""
    files = _saved_html_files(location)
    by_key: Dict[Tuple[int, ...], SavedChapter] = {}
    sources_seen: List[str] = []
    duplicates_seen: List[str] = []
    invalid_count = 0

    for path in files:
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError as error:
            raise ValueError(f'Could not read {path.name} as UTF-8 text.') from error
        soup = BeautifulSoup(text, 'html.parser')
        source_meta = soup.find('meta', attrs={'name': 'source'})
        source = (source_meta.get('content') or '').strip() if source_meta else ''
        if source and source not in sources_seen:
            sources_seen.append(source)
        for section in soup.select('section.wns-chapter[data-number]'):
            raw = str(section.get('data-number', '')).strip()
            parsed = _parse_chapter_number(raw)
            if parsed is None:
                invalid_count += 1
                continue
            key, numeric, major = parsed
            candidate = SavedChapter(
                key=key,
                raw_number=raw,
                numeric=numeric,
                major=major,
                section=section,
                source=source,
                file_path=path,
            )
            if key in by_key:
                if raw not in duplicates_seen:
                    duplicates_seen.append(raw)
                # Prefer the copy with more text rather than letting filename sort win silently.
                if len(section.get_text(strip=True)) >= len(by_key[key].section.get_text(strip=True)):
                    by_key[key] = candidate
            else:
                by_key[key] = candidate

    ordered = [by_key[key] for key in sorted(by_key)]
    return SavedChaptersLoad(
        chapters=ordered,
        sources=sources_seen,
        duplicates=duplicates_seen,
        invalid_count=invalid_count,
    )

def read_saved_chapters(location) -> List[Tuple[Union[int, float], BeautifulSoup]]:
    """Read chapters saved by browser/collect-chapters.js from one .html file, or every one in a folder, in chapter order."""
    return [(chapter.numeric, chapter.section) for chapter in load_saved_chapters(location).chapters]

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Web Novel Scraper')
    
    required = parser.add_argument_group('required arguments')
    required.add_argument('--url', type=str, help='Base URL')
    required.add_argument('--novel-id', type=str, help='Novel ID')
    required.add_argument('--start', type=int, help='Start chapter')
    required.add_argument('--end', type=int, help='End chapter')
    required.add_argument('--title', type=str, help='Novel title')
    parser.add_argument('--chapter-prefix', type=str, default='chapter-',
                        help='Text before the chapter number in chapter URLs (default: chapter-)')
    parser.add_argument('--from-folder', type=str,
                        help='Build from chapter files saved by browser/collect-chapters.js instead of downloading')
    
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--cover-url', type=str, help='Cover image URL')
    parser.add_argument('--author', type=str, help='Author name')
    parser.add_argument('--translator', type=str, help='Translator name')
    parser.add_argument('--creator', type=str, help='Creator name')
    
    parser.add_argument('--simple-chapters', action='store_true', help='Use simple chapter numbering')
    parser.add_argument('--include-footnotes', action='store_true', help='Include footnotes')
    parser.add_argument('--include-notes', action='store_true', help='Include chapter notes')
    parser.add_argument('--no-headings', action='store_true',
                        help='Leave chapter headings out of the text so text-to-speech does not read them')

    return parser.parse_args()

def get_yes_no_input(prompt: str, default: bool = False) -> bool:
    default_text = "Y/n" if default else "y/N"
    response = input(f"{prompt} ({default_text}): ").strip().lower()
    if not response:
        return default
    return response.startswith('y')

def get_optional_input(prompt: str, arg_value: Optional[str] = None, default: str = "") -> str:
    if arg_value is not None:
        return arg_value
    
    response = input(f"{prompt} (Enter to skip): ").strip()
    return response or default

def get_user_input(args: argparse.Namespace) -> Dict[str, str]:
    inputs = {}
    print("\nWeb Novel Scraper")
    print("==================")

    if not args.from_folder:
        inputs['base_url'] = args.url if args.url else input("\nEnter base URL: ").strip()
        if not inputs['base_url']:
            raise ValidationError("Base URL cannot be empty")
        if not inputs['base_url'].endswith('/'):
            inputs['base_url'] += '/'

        inputs['novel_id'] = args.novel_id if args.novel_id else input("Novel ID: ").strip()
        if not inputs['novel_id']:
            raise ValidationError("Novel ID cannot be empty")

    if args.from_folder:
        # Saved chapters need no range; --start and --end only narrow it.
        inputs['start_chapter'] = args.start or 1
        inputs['end_chapter'] = args.end or sys.maxsize
    elif args.start and args.end:
        inputs['start_chapter'] = args.start
        inputs['end_chapter'] = args.end
    else:
        try:
            inputs['start_chapter'] = int(input("Start chapter: "))
            inputs['end_chapter'] = int(input("End chapter: "))
            if inputs['start_chapter'] > inputs['end_chapter']:
                raise ValidationError("Start chapter cannot be greater than end chapter")
        except ValueError:
            raise ValidationError("Please enter valid chapter numbers")

    inputs['novel_title'] = args.title if args.title else input("Novel title: ").strip()
    if not inputs['novel_title']:
        raise ValidationError("Novel title cannot be empty")

    # Handle optional inputs based on whether they were provided as arguments
    if all([args.cover_url, args.author, args.translator, args.creator]):
        # If all optional arguments are provided, use them directly
        inputs['cover_url'] = args.cover_url
        inputs['author'] = args.author
        inputs['translator'] = args.translator
        inputs['created_by'] = args.creator
    else:
        # Only ask for inputs that weren't provided as arguments
        print("\nOptional Settings:")
        
        if args.cover_url is None:
            if get_yes_no_input("Add cover image?"):
                inputs['cover_url'] = get_optional_input("Cover image URL")
            else:
                inputs['cover_url'] = ""
        else:
            inputs['cover_url'] = args.cover_url

        if args.author is None:
            if get_yes_no_input("Add author?"):
                inputs['author'] = get_optional_input("Author name")
            else:
                inputs['author'] = ""
        else:
            inputs['author'] = args.author

        if args.translator is None:
            if get_yes_no_input("Add translator?"):
                inputs['translator'] = get_optional_input("Translator name")
            else:
                inputs['translator'] = ""
        else:
            inputs['translator'] = args.translator

        if args.creator is None:
            if get_yes_no_input("Change creator name?"):
                inputs['created_by'] = get_optional_input("Creator name", default="WebNovelScraper")
            else:
                inputs['created_by'] = "WebNovelScraper"
        else:
            inputs['created_by'] = args.creator

    if not any([args.simple_chapters, args.include_footnotes, args.include_notes, args.no_headings]):
        print("\nConfiguration options:")
        Config.SIMPLE_CHAPTER_NAMES = get_yes_no_input("Use simple chapter numbering?")
        Config.INCLUDE_FOOTNOTES = get_yes_no_input("Include footnotes?")
        Config.INCLUDE_CHAPTER_NOTES = get_yes_no_input("Include chapter notes?")
        Config.CHAPTER_HEADINGS = get_yes_no_input("Read chapter headings aloud?", default=True)
    else:
        Config.SIMPLE_CHAPTER_NAMES = args.simple_chapters
        Config.INCLUDE_FOOTNOTES = args.include_footnotes
        Config.INCLUDE_CHAPTER_NOTES = args.include_notes
        Config.CHAPTER_HEADINGS = not args.no_headings

    return inputs

def main() -> None:
    try:
        args = parse_arguments()
        Config.DEBUG_MODE = args.debug

        inputs = get_user_input(args)

        print("\nFinal Configuration:")
        if args.from_folder:
            last = 'last saved' if inputs['end_chapter'] == sys.maxsize else inputs['end_chapter']
            print(f"Saved chapters folder: {args.from_folder}")
            print(f"Chapters: {inputs['start_chapter']} to {last}")
        else:
            print(f"Base URL: {inputs['base_url']}")
            print(f"Novel ID: {inputs['novel_id']}")
            print(f"Chapters: {inputs['start_chapter']} to {inputs['end_chapter']}")
        print(f"Title: {inputs['novel_title']}")
        print(f"Debug Mode: {'On' if Config.DEBUG_MODE else 'Off'}")
        print(f"Simple Chapter Names: {'On' if Config.SIMPLE_CHAPTER_NAMES else 'Off'}")
        print(f"Include Footnotes: {'On' if Config.INCLUDE_FOOTNOTES else 'Off'}")
        print(f"Include Chapter Notes: {'On' if Config.INCLUDE_CHAPTER_NOTES else 'Off'}")
        print(f"Chapter Headings: {'On' if Config.CHAPTER_HEADINGS else 'Off'}")
        
        if inputs['cover_url']:
            print(f"Cover URL: {inputs['cover_url']}")
        if inputs['author'] != "Unknown":
            print(f"Author: {inputs['author']}")
        if inputs['translator']:
            print(f"Translator: {inputs['translator']}")
        if inputs['created_by'] != "WebNovelScraper":
            print(f"Creator: {inputs['created_by']}")

        if not get_yes_no_input("\nProceed with these settings?", default=True):
            print("Aborted by user.")
            return

        with WebNovelScraper(debug_mode=Config.DEBUG_MODE, log_path='scraper_detailed.log') as scraper:
            first, last = inputs['start_chapter'], inputs['end_chapter']
            if args.from_folder:
                loaded = load_saved_chapters(args.from_folder)
                saved = [ch for ch in loaded.chapters if first <= ch.major <= last]
                if not saved:
                    raise ValidationError(f"No saved chapters found in {args.from_folder}")
                total = len(saved)
                results = ((ch.raw_number, scraper.parse_saved_chapter(ch.section, ch.raw_number)) for ch in saved)
                id_source = (
                    ','.join(loaded.sources)
                    if loaded.sources
                    else str(Path(args.from_folder).expanduser().resolve())
                )
                id_first, id_last = saved[0].raw_number, saved[-1].raw_number
            else:
                numbers = range(first, last + 1)
                total = len(numbers)
                results = ((number, scraper.get_chapter_content(
                    f"{inputs['base_url']}{inputs['novel_id']}/{args.chapter_prefix}{number}")) for number in numbers)
                id_source = f"{inputs['base_url'].rstrip('/')}/{inputs['novel_id']}/{args.chapter_prefix}"
                id_first, id_last = first, last
            progress = ProgressTracker(total)

            chapters_data = []
            cli_identifier = build_epub_identifier(inputs['novel_title'], id_source, id_first, id_last)
            epub_creator = EpubCreator(
                inputs['novel_title'],
                author=inputs['author'],
                translator=inputs['translator'],
                created_by=inputs['created_by'],
                identifier=cli_identifier,
            )

            if inputs['cover_url']:
                cover_data = scraper.get_cover_image(inputs['cover_url'])
                if cover_data:
                    epub_creator.add_cover(cover_data)
                else:
                    print(f"\n{ConsoleColors.YELLOW}Warning: Failed to process cover image{ConsoleColors.RESET}")

            for chapter_num, (chapter_title, content, notes, footnotes) in results:
                if chapter_title and content:
                    chapters_data.append({
                        'title': chapter_title,
                        'content': content,
                        'notes': notes or [],
                        'footnotes': footnotes or {}
                    })
                    progress.update(True)
                else:
                    progress.update(False)
                    print(f"\n{ConsoleColors.YELLOW}Warning: Failed to process chapter {chapter_num}{ConsoleColors.RESET}")

            if chapters_data:
                epub_chapters = []
                for idx, chapter_data in enumerate(chapters_data, 1):
                    chapter = epub_creator.add_chapter(chapter_data, idx)
                    epub_chapters.append(chapter)

                epub_creator.finalize(epub_chapters)
                epub_filename = f"{inputs['novel_title'].lower().replace(' ', '_')}.epub"

                try:
                    epub.write_epub(epub_filename, epub_creator.book, {})
                    print(f"\n{ConsoleColors.GREEN}Successfully created: {epub_filename}{ConsoleColors.RESET}")
                    print(f"Total chapters: {len(chapters_data)}")
                    print(f"Successful: {progress.success}")
                    print(f"Failed: {progress.failures}")
                except Exception as e:
                    print(f"\n{ConsoleColors.RED}Error creating EPUB file: {str(e)}{ConsoleColors.RESET}")
            else:
                print(f"\n{ConsoleColors.RED}No chapters were successfully scraped.{ConsoleColors.RESET}")

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except SiteBlocked as e:
        print(f"\n{ConsoleColors.RED}{e}{ConsoleColors.RESET}")
        print("Collect the chapters in your browser with browser/collect-chapters.js, "
              "then build with --from-folder.")
    except Exception as e:
        print(f"\n{ConsoleColors.RED}Error: {str(e)}{ConsoleColors.RESET}")
        if Config.DEBUG_MODE:
            import traceback
            print("\nFull error trace:")
            print(traceback.format_exc())

if __name__ == "__main__":
    main()
