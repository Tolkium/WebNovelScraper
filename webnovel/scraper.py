"""HTTP fetching, rate limiting, cover processing, and saved-chapter loading."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
import logging
from pathlib import Path
import re
import time
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from PIL import Image
import requests

from webnovel.cleaning import DEFAULT_STRIP_SELECTORS, parse_chapter_elements
from webnovel.settings import DEFAULT_SITE_PROFILE, JobSettings, site_profile_for_url


class ValidationError(Exception):
    """Custom exception for validation errors."""


class _RetryableFetchError(Exception):
    """Transient fetch issue (such as an empty response) that should be retried."""


class ScraperCancelled(Exception):
    """A user requested cancellation between requests or during a wait."""


class SiteBlocked(Exception):
    """The website answered with a browser check, so chapters must be collected in the browser."""

    def __init__(self, url: str):
        super().__init__(f'{urlparse(url).netloc} shows a browser check, so the app cannot read its chapters.')


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

    def __init__(
        self,
        requests_per_second: float = JobSettings.requests_per_second,
        cancel_event=None,
        clock=time.monotonic,
    ):
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


class WebNovelScraper:
    def __init__(
        self,
        debug_mode: bool = False,
        settings: Optional[JobSettings] = None,
        cancel_event=None,
        log_callback=None,
        log_path: Optional[Union[str, Path]] = None,
    ):
        self.settings = settings if settings is not None else JobSettings()
        self.debug_mode = bool(debug_mode or self.settings.debug)
        self._initialize_logging(self.debug_mode, log_callback, log_path)
        self.headers = {'User-Agent': self.settings.user_agent}
        self.rate_limiter = RateLimiter(self.settings.requests_per_second, cancel_event)
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._closed = False

    def __enter__(self) -> 'WebNovelScraper':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _initialize_logging(
        self,
        debug_mode: bool,
        log_callback=None,
        log_path: Optional[Union[str, Path]] = None,
    ) -> None:
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
            self.logger.error(f'URL validation failed: {str(e)}')
            return False

    def get_cover_image(self, image_url: str) -> Optional[Tuple[bytes, str, str]]:
        if not self.validate_url(image_url):
            raise ValidationError('Invalid cover image URL')

        try:
            response = self.session.get(image_url, timeout=self.settings.timeout)
            response.raise_for_status()

            image = Image.open(BytesIO(response.content))
            if image.mode != 'RGB':
                image = image.convert('RGB')

            width, height = image.size
            target_ratio = self.settings.cover_aspect_ratio
            current_ratio = width / height

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
            if height > self.settings.max_cover_height:
                new_height = self.settings.max_cover_height
                new_width = int(new_height * target_ratio)
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            img_byte_arr = BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            return img_byte_arr.getvalue(), 'image/jpeg', 'cover.jpg'

        except Exception as e:
            self.logger.error(f'Cover image processing failed: {str(e)}')
            return None

    def get_chapter_content(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[List[str]], Optional[dict]]:
        """Fetch and process chapter content, retrying only transient errors."""
        if not self.validate_url(url):
            raise ValidationError('Invalid chapter URL')

        profile = site_profile_for_url(url) or DEFAULT_SITE_PROFILE

        for attempt in range(self.settings.retries):
            retry_after: Optional[float] = None
            try:
                self.rate_limiter.wait()

                response = self.session.get(url, timeout=self.settings.timeout)
                if is_browser_check(response):
                    raise SiteBlocked(url)
                response.raise_for_status()

                if not response.text.strip():
                    raise _RetryableFetchError('Empty response received')

                soup = BeautifulSoup(response.text, 'html.parser')
                return self.parse_chapter(
                    soup.select_one(self.settings.title_selector),
                    soup.select_one(self.settings.content_selector),
                    url,
                    strip_selectors=profile.strip_selectors,
                )

            except (ScraperCancelled, SiteBlocked, ValidationError):
                raise
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                retryable = status is None or status in (408, 429) or status >= 500
                if e.response is not None and status in (429, 503):
                    retry_after = parse_retry_after(e.response.headers.get('Retry-After'))
                self.logger.error(f'Attempt {attempt + 1} failed for {url}: {str(e)}')
                if not retryable:
                    return None, None, None, None
            except ValueError as e:
                # Missing title/container or too-short text on a 200 OK page will not be fixed by retrying.
                self.logger.error(f'Chapter parsing failed for {url}: {str(e)}')
                return None, None, None, None
            except (_RetryableFetchError, requests.RequestException) as e:
                self.logger.error(f'Attempt {attempt + 1} failed for {url}: {str(e)}')
                if self.debug_mode:
                    import traceback
                    self.logger.error(traceback.format_exc())
            except Exception as e:
                # Never let one malformed chapter's unexpected error abort an entire multi-chapter job.
                self.logger.error(f'Unexpected error processing {url}: {str(e)}')
                if self.debug_mode:
                    import traceback
                    self.logger.error(traceback.format_exc())
                return None, None, None, None

            if attempt < self.settings.retries - 1:
                self.rate_limiter.sleep(self.rate_limiter.retry_delay(attempt, retry_after))
            else:
                return None, None, None, None

        return None, None, None, None

    def parse_chapter(
        self,
        title_elem: Optional[BeautifulSoup],
        content_div: Optional[BeautifulSoup],
        source: str,
        strip_selectors: Tuple[str, ...] = DEFAULT_STRIP_SELECTORS,
    ) -> Tuple[str, str, List[str], Dict[str, str]]:
        """Turn a chapter's title element and content container into EPUB-ready parts."""
        return parse_chapter_elements(
            title_elem,
            content_div,
            source,
            include_notes=self.settings.include_notes,
            include_footnotes=self.settings.include_footnotes,
            simple_chapters=self.settings.simple_chapters,
            min_content_length=self.settings.min_content_length,
            strip_selectors=strip_selectors,
            debug_log=self.logger.debug if self.debug_mode else None,
        )

    def parse_saved_chapter(
        self,
        section: BeautifulSoup,
        number: Union[int, float, str],
    ) -> Tuple[Optional[str], Optional[str], Optional[List[str]], Optional[dict]]:
        """Process one chapter saved by browser/collect-chapters.js; returns Nones when it is unusable."""
        try:
            return self.parse_chapter(
                section.select_one('.chapter-title'),
                section.select_one('.chapter-container'),
                str(number),
            )
        except ValueError as e:
            self.logger.error(f'Saved chapter {number} failed: {e}')
            return None, None, None, None


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
