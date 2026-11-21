"""Unified job settings, site profiles, URL parsing, and Windows-safe filenames."""

from dataclasses import asdict, dataclass, fields
from pathlib import Path
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import soupsieve

from webnovel.cleaning import DEFAULT_STRIP_SELECTORS

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
)

WINDOWS_RESERVED_NAME = re.compile(
    r'^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])(?:\.|$)',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SiteProfile:
    """Default selectors and cleanup rules for a website."""
    domains: Tuple[str, ...]
    title_selector: str
    content_selector: str
    chapter_prefix: str = 'chapter-'
    strip_selectors: Tuple[str, ...] = DEFAULT_STRIP_SELECTORS


DEFAULT_SITE_PROFILE = SiteProfile(
    domains=(),
    title_selector='span.chapter-title',
    content_selector='#chapter-container',
    chapter_prefix='chapter-',
    strip_selectors=DEFAULT_STRIP_SELECTORS,
)

# Websites whose markup differs from the defaults.
SITE_PROFILES: Tuple[SiteProfile, ...] = (
    SiteProfile(
        domains=('wuxiaworld.com',),
        title_selector='h4',
        content_selector='div.chapter-body',
    ),
)


def site_profile_for_url(url: str) -> Optional[SiteProfile]:
    """Return the matching SiteProfile for a known website URL, or None."""
    host = (urlparse(url.strip()).hostname or '').lower()
    for profile in SITE_PROFILES:
        for domain in profile.domains:
            if host == domain or host.endswith('.' + domain):
                return profile
    return None


def site_selectors(url: str) -> Optional[Tuple[str, str]]:
    """Return (title_selector, content_selector) for a known website, or None."""
    profile = site_profile_for_url(url)
    return (profile.title_selector, profile.content_selector) if profile is not None else None


def http_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname)
    except ValueError:
        return False


def safe_filename(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title.strip()).rstrip(' .')
    if not name:
        name = 'My book'
    if WINDOWS_RESERVED_NAME.match(name):
        name = '_' + name
    return name[:180] + '.epub'


def split_novel_url(value: str) -> Tuple[str, str, str]:
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
class JobSettings:
    """Single source of truth for scraping, cleaning, cover processing, and EPUB options."""
    base_url: str = ''
    novel_id: str = ''
    chapter_prefix: str = DEFAULT_SITE_PROFILE.chapter_prefix
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
    user_agent: str = DEFAULT_USER_AGENT
    title_selector: str = DEFAULT_SITE_PROFILE.title_selector
    content_selector: str = DEFAULT_SITE_PROFILE.content_selector

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> 'JobSettings':
        if not isinstance(data, dict):
            raise ValueError('The settings file must contain a JSON object.')
        allowed = {f.name for f in fields(cls)}
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
    def output_path(self) -> Path:
        name = self.filename.strip() or safe_filename(self.title)
        if not name.lower().endswith('.epub'):
            name += '.epub'
        return Path(self.output_dir).expanduser() / name

    def validate(self) -> dict:
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
        if (
            not stem.strip()
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', self.filename)
            or self.filename.rstrip().endswith('.')
            or WINDOWS_RESERVED_NAME.match(stem)
        ):
            errors['filename'] = 'Choose a valid filename without folder paths or reserved Windows names.'
        if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', self.language):
            errors['language'] = 'Use a language code such as en, en-US, sk or de.'
        if self.cover_url.strip() and not http_url(self.cover_url):
            errors['cover_url'] = 'The cover URL must begin with https:// or http://.'
        for name, low, high in [
            ('timeout', 1, 600),
            ('retries', 1, 20),
            ('requests_per_second', 0.1, 20),
            ('min_content_length', 1, 100000),
            ('max_cover_height', 100, 10000),
            ('cover_aspect_ratio', 0.1, 3),
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
