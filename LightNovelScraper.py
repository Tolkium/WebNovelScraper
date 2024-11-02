"""
Light Novel Scraper - A tool for scraping and converting web novels to EPUB format
"""

# Standard library imports
from __future__ import annotations
import os
import re
import time
import logging
import platform
import argparse
from io import BytesIO
from typing import (
    Optional, Tuple, List, Dict, TypedDict, 
    Protocol, NamedTuple, Final, cast, Any
)
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, ParseResult

# Third-party imports
import requests
from bs4 import BeautifulSoup
from PIL import Image
import ebooklib
from ebooklib import epub

# Type definitions
class ChapterData(TypedDict):
    title: str
    content: str
    notes: List[str]
    footnotes: Dict[str, str]

class ImageData(NamedTuple):
    data: bytes
    mime_type: str
    filename: str

@dataclass(frozen=True)
class ScraperConfig:
    """Configuration settings for the scraper."""
    # Network settings
    timeout: Final[int] = 30
    max_retries: Final[int] = 3
    requests_per_second: Final[float] = 20.0
    user_agent: Final[str] = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/91.0.4472.124 Safari/537.36')
    
    # Content validation
    min_content_length: Final[int] = 100
    
    # Image processing
    max_cover_height: Final[int] = 2400
    cover_aspect_ratio: Final[float] = 2/3
    jpeg_quality: Final[int] = 95
    
    # Rate limiting
    max_backoff_time: Final[int] = 300
    retry_delay_multiplier: Final[int] = 5
    rate_limiter_queue_size: Final[int] = 10
    
    # Content markers
    note_markers: Final[List[str]] = field(default_factory=lambda: [
        't/n:', 'tn:', 't/l:', 'tl:',
        'translator\'s note:', 'translator note:',
        'chapter note:', 'chapter note by',
        'thanks for playing', 'as always'
    ])
    
    # Content cleaning
    ad_selectors: Final[List[str]] = field(default_factory=lambda: [
        'script', '.CTGIiSmv', '.L-MHncuP', '.vm-placement',
        'div[data-defid]', 'div[data-mobid]'
    ])
    
    # EPUB styling
    css_style: Final[str] = field(default_factory=lambda: """
        body { 
            font-family: Times New Roman, serif; 
            margin: 5%; 
            text-align: justify; 
        }
        h1 { 
            text-align: center; 
            font-size: 2em;
            margin: 1em 0 1em 0;
        }
        h2 { margin-top: 1em; }
        h3 { margin-top: 1em; }
        p { margin: 1em 0; }
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
    """.strip())

    def validate(self) -> None:
        """Validate configuration settings."""
        if self.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("Max retries cannot be negative")
        if self.requests_per_second <= 0:
            raise ValueError("Requests per second must be positive")
        if self.min_content_length <= 0:
            raise ValueError("Minimum content length must be positive")

@dataclass
class UserConfig:
    """User-specific configuration settings."""
    include_footnotes: bool = False
    include_chapter_notes: bool = False
    simple_chapter_names: bool = False
    debug_mode: bool = False
    extraction_methods: List[str] = field(
        default_factory=lambda: ['known_selectors', 'pattern_matching', 'density', 'custom']
    )

    def validate(self) -> None:
        """Validate user configuration settings."""
        if not all(isinstance(getattr(self, attr), bool) 
                  for attr in ['include_footnotes', 'include_chapter_notes', 
                             'simple_chapter_names', 'debug_mode']):
            raise ValueError("Boolean configuration values must be boolean")
        
        valid_methods = {
            'known_selectors', 'pattern_matching', 
            'density', 'custom'
        }
        if not all(method in valid_methods for method in self.extraction_methods):
            raise ValueError(
                f"Invalid extraction method. Valid methods are: {valid_methods}"
            )

class ConsoleColors:
    """ANSI color codes for console output."""
    RED: Final[str] = '\033[91m'
    GREEN: Final[str] = '\033[92m'
    YELLOW: Final[str] = '\033[93m'
    RESET: Final[str] = '\033[0m'

# Initialize color support on Windows
if platform.system() == 'Windows':
    os.system('color')

# Set up logging
def setup_logging(debug_mode: bool) -> logging.Logger:
    """Configure logging based on debug mode."""
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    
    # Create handlers
    file_handler = logging.FileHandler('scraper_detailed.log')
    file_handler.setLevel(log_level)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level if debug_mode else logging.WARNING)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# Custom exceptions
class ValidationError(Exception):
    """Raised when input validation fails."""
    pass

class ContentExtractionError(Exception):
    """Raised when content extraction fails."""
    pass

class NetworkError(Exception):
    """Raised when network operations fail."""
    pass

class SafeSession:
    """Thread-safe session manager for HTTP requests."""
    
    def __init__(self, config: ScraperConfig):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': config.user_agent})
        self.config = config
        
    def __enter__(self) -> requests.Session:
        return self.session
        
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.session.close()

    def get(self, url: str, timeout: Optional[int] = None) -> requests.Response:
        """Make a GET request with configured timeout."""
        timeout = timeout or self.config.timeout
        return self.session.get(url, timeout=timeout)

class RateLimiter:
    """Rate limiting implementation with exponential backoff."""
   
    def __init__(self, config: ScraperConfig):
        self._rate = config.requests_per_second
        self._min_interval = 1.0 / config.requests_per_second
        self._last_request_times: deque = deque(
            maxlen=config.rate_limiter_queue_size
        )
        self._failed_attempts = 0
        self._config = config
    
    @property
    def current_delay(self) -> float:
        """Calculate current delay based on failed attempts."""
        if self._failed_attempts == 0:
            return 0
        return min(
            self._config.max_backoff_time,
            (2 ** self._failed_attempts) - 1
        )
    
    def wait(self) -> None:
        """Wait appropriate time between requests with exponential backoff."""
        now = datetime.now()
        
        if self._last_request_times:
            time_since_last = (
                now - self._last_request_times[-1]
            ).total_seconds()
            base_wait = max(0, self._min_interval - time_since_last)
        else:
            base_wait = 0
            
        total_wait = base_wait + self.current_delay
            
        if total_wait > 0:
            time.sleep(total_wait)
            
        self._last_request_times.append(now)
    
    def record_failure(self) -> None:
        """Record a failed attempt and increase backoff."""
        self._failed_attempts += 1
    
    def record_success(self) -> None:
        """Record a successful attempt and reset backoff."""
        self._failed_attempts = 0

class ProgressTracker:
    """Track and display progress of operations."""
    
    def __init__(self, total: int):
        self._total = total
        self._current = 0
        self._success = 0
        self._failures = 0
    
    @property
    def success_count(self) -> int:
        return self._success
    
    @property
    def failure_count(self) -> int:
        return self._failures
    
    @property
    def completion_percentage(self) -> float:
        return (self._current / self._total) * 100
        
    def update(self, success: bool = True) -> None:
        """Update progress with success/failure status."""
        self._current += 1
        if success:
            self._success += 1
        else:
            self._failures += 1
        self._display_progress()
    
    def _display_progress(self) -> None:
        """Display progress bar and statistics."""
        try:
            terminal_width = os.get_terminal_size().columns
            
            # Reserve space for stats
            stats = f"Success: {self._success} Failures: {self._failures}"
            reserved_space = len(f"Progress || 100.0% {stats}") + 2
            
            # Calculate bar length
            bar_length = terminal_width - reserved_space
            filled_length = int(bar_length * self._current // self._total)
            bar = '=' * filled_length + '-' * (bar_length - filled_length)
            
            # Build progress line
            progress_line = (
                f'\rProgress |{bar}| '
                f'{self.completion_percentage:.1f}% {stats}'
            )
            
            # Truncate if needed
            if len(progress_line) > terminal_width:
                progress_line = progress_line[:terminal_width-3] + '...'
                
            print(progress_line, end='', flush=True)
            
        except OSError:
            # Fallback for environments without terminal
            print(
                f'\rProgress: {self.completion_percentage:.1f}% '
                f'(S:{self._success}/F:{self._failures})',
                end='',
                flush=True
            )

class NetworkManager:
    """Manage network operations with retry and rate limiting."""
    
    def __init__(
        self,
        config: ScraperConfig,
        logger: logging.Logger
    ):
        self.config = config
        self.logger = logger
        self.rate_limiter = RateLimiter(config)
        self.session = SafeSession(config)
    
    def get_url(self, url: str) -> requests.Response:
        """
        Get URL with rate limiting and retry logic.
        
        Args:
            url: The URL to fetch
            
        Returns:
            Response object from successful request
            
        Raises:
            NetworkError: If request fails after retries
        """
        if not self._validate_url(url):
            raise ValidationError(f"Invalid URL: {url}")
            
        for attempt in range(self.config.max_retries):
            try:
                self.rate_limiter.wait()
                response = self.session.get(url)
                response.raise_for_status()
                
                self.rate_limiter.record_success()
                return response
                
            except requests.Timeout:
                self.logger.warning(
                    f"Request timed out for {url} "
                    f"(attempt {attempt + 1}/{self.config.max_retries})"
                )
                self.rate_limiter.record_failure()
                
            except requests.HTTPError as e:
                self.logger.warning(
                    f"HTTP error {e.response.status_code} for {url} "
                    f"(attempt {attempt + 1}/{self.config.max_retries})"
                )
                self.rate_limiter.record_failure()
                
            except requests.RequestException as e:
                self.logger.warning(
                    f"Request failed for {url}: {str(e)} "
                    f"(attempt {attempt + 1}/{self.config.max_retries})"
                )
                self.rate_limiter.record_failure()
            
            if attempt < self.config.max_retries - 1:
                time.sleep(
                    (attempt + 1) * self.config.retry_delay_multiplier
                )
                
        raise NetworkError(f"Failed to fetch {url} after "
                          f"{self.config.max_retries} attempts")
    
    @staticmethod
    def _validate_url(url: str) -> bool:
        """Validate URL format."""
        try:
            result: ParseResult = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False
    
    def __enter__(self) -> 'NetworkManager':
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.session.__exit__(exc_type, exc_val, exc_tb)

class ContentCleaner:
    """Clean and sanitize HTML content."""
    
    def __init__(self, config: ScraperConfig):
        self.config = config
    
    def clean_html(self, content: BeautifulSoup) -> BeautifulSoup:
        """
        Clean HTML content while preserving main content structure.
        
        Args:
            content: BeautifulSoup object of the content
            
        Returns:
            Cleaned BeautifulSoup object
        """
        # Create a copy to avoid modifying original
        content = cast(BeautifulSoup, content)
        content_copy = BeautifulSoup(str(content), 'html.parser')
        
        # Remove SSE comments
        content_str = str(content_copy)
        content_str = re.sub(
            r'<!--sse-->.*?<!--/sse-->',
            '',
            content_str,
            flags=re.DOTALL
        )
        
        # Parse cleaned content
        content_copy = BeautifulSoup(content_str, 'html.parser')
        
        # Remove ads and unwanted elements
        for selector in self.config.ad_selectors:
            for element in content_copy.select(selector):
                element.decompose()
        
        return content_copy

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """Normalize whitespace in text."""
        return ' '.join(text.split())

class ContentValidator:
    """Validate extracted content."""
    
    def __init__(self, config: ScraperConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
    
    def is_valid_content(self, content: BeautifulSoup) -> bool:
        """Check if content container is valid."""
        if not content:
            return False
            
        text = content.get_text().strip()
        paragraphs = content.find_all('p')
        
        return (
            len(text) >= self.config.min_content_length and
            len(paragraphs) >= 3 and
            not self._is_navigation_menu(content)
        )
    
    def validate_chapter_content(
        self,
        paragraphs: List[str],
        url: str
    ) -> bool:
        """Validate extracted chapter content."""
        if len(paragraphs) < 1:
            self.logger.error(f"No valid paragraphs found for {url}")
            return False
        
        total_length = sum(len(p) for p in paragraphs)
        if total_length < self.config.min_content_length:
            self.logger.warning(
                f"Content length ({total_length}) below minimum "
                f"threshold for {url}"
            )
            return False
            
        return True
    
    @staticmethod
    def _is_navigation_menu(content: BeautifulSoup) -> bool:
        """Check if content appears to be a navigation menu."""
        links = content.find_all('a')
        if not links:
            return False
            
        text = content.get_text().strip()
        link_text = ''.join(link.get_text() for link in links)
        
        return len(link_text) / len(text) > 0.5 if text else False

class ContentExtractor:
    """Extract and process content from web pages."""
    
    EXTRACTION_METHODS = {
        'known_selectors': 'Try common novel site selectors',
        'pattern_matching': 'Use pattern matching to find content',
        'density': 'Analyze text density to find content',
        'custom': 'Use custom CSS selectors'
    }
    
    def __init__(
        self,
        config: ScraperConfig,
        user_config: UserConfig,
        logger: logging.Logger,
        custom_selectors: Optional[List[str]] = None
    ):
        self.config = config
        self.user_config = user_config
        self.logger = logger
        self.cleaner = ContentCleaner(config)
        self.validator = ContentValidator(config, logger)
        self.custom_selectors = custom_selectors or []
    
    def _find_content_div(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find main content div using selected strategies."""
        strategy_map = {
            'known_selectors': self._try_known_selectors,
            'pattern_matching': self._try_pattern_matching,
            'density': self._try_density_analysis,
            'custom': self._try_custom_selectors
        }
        
        for method in self.user_config.extraction_methods:
            if method not in strategy_map:
                self.logger.warning(f"Unknown extraction method: {method}")
                continue
                
            self.logger.debug(f"Trying extraction method: {method}")
            if content_div := strategy_map[method](soup):
                if self.validator.is_valid_content(content_div):
                    self.logger.info(f"Content found using {method}")
                    return content_div
        
        return None
    
    def _try_known_selectors(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Try common selectors for novel content."""
        selectors = [
            '#chapter-container',
            '.chapter-content',
            'article',
            '.entry-content'
        ]
        
        for selector in selectors:
            content = soup.select_one(selector)
            if content:
                return content
        return None
    
    def _try_pattern_matching(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Try to find content using regex patterns."""
        patterns = [
            {'class_': re.compile(r'chapter|content|text|story', re.I)},
            {'id': re.compile(r'chapter-?\d+', re.I)},
            {'class_': re.compile(r'novel|reading|book', re.I)}
        ]
        
        for pattern in patterns:
            content = soup.find(['div', 'article', 'main'], **pattern)
            if content:
                return content
        return None
    
    def _try_density_analysis(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find content by analyzing text density."""
        candidates = soup.find_all(['div', 'article', 'section'])
        max_score = 0
        best_candidate = None
        
        for div in candidates:
            score = self._calculate_content_score(div)
            if score > max_score:
                max_score = score
                best_candidate = div
                
        return best_candidate
    
    def _try_custom_selectors(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Try user-provided custom CSS selectors."""
        if not self.custom_selectors:
            return None
            
        for selector in self.custom_selectors:
            try:
                if content := soup.select_one(selector):
                    return content
            except Exception as e:
                self.logger.warning(
                    f"Error with custom selector '{selector}': {str(e)}"
                )
        return None
    
    def _calculate_content_score(self, element: BeautifulSoup) -> float:
        """Calculate content score based on various metrics."""
        text = element.get_text().strip()
        if not text:
            return 0.0
            
        # Base metrics
        text_length = len(text)
        html_length = len(str(element))
        paragraphs = len(element.find_all('p'))
        
        if html_length == 0 or paragraphs == 0:
            return 0.0
            
        # Calculate score
        density = text_length / html_length
        score = density * paragraphs
        
        # Apply bonuses and penalties
        if paragraphs > 3 and text_length > 500:
            score *= 1.5
            
        score -= len(element.find_all('a')) * 0.5
        score -= len(element.find_all('img')) * 1.0
        
        return max(0.0, score)
    
    def _extract_title(
        self,
        soup: BeautifulSoup,
        url: str
    ) -> Optional[str]:
        """Extract chapter title from HTML."""
        title_elem = (
            soup.find('span', class_='chapter-title') or
            soup.find('h1') or
            soup.find('title')
        )
        
        if not title_elem:
            # Fallback to URL-based title
            match = re.search(r'chapter-(\d+)', url)
            return f"Chapter {match.group(1)}" if match else None
        
        if self.user_config.simple_chapter_names:
            match = re.search(r'chapter-(\d+)', url)
            return f"Chapter {match.group(1)}" if match else title_elem.text.strip()
            
        return title_elem.text.strip()
    
    def _process_content(
        self,
        content_div: BeautifulSoup
    ) -> Tuple[List[str], List[str], Dict[str, str]]:
        """Process and extract content components."""
        content_div = self.cleaner.clean_html(content_div)
        
        paragraphs = []
        notes = []
        footnotes = {}
        
        for p in content_div.find_all('p'):
            text = p.text.strip()
            if not text:
                continue
            
            if self._is_note(text):
                if self.user_config.include_chapter_notes:
                    notes.append(str(p))
            elif self._is_footnote(text):
                if self.user_config.include_footnotes:
                    num = text.split('.')[0]
                    footnotes[num] = text
            elif not self._is_chapter_number(text):
                paragraphs.append(str(p))
        
        return paragraphs, notes, footnotes
    
    def _is_note(self, text: str) -> bool:
        """Check if text is a translator's note or similar."""
        text_lower = text.lower()
        return any(
            marker in text_lower
            for marker in self.config.note_markers
        )
    
    @staticmethod
    def _is_footnote(text: str) -> bool:
        """Check if text is a footnote."""
        return bool(re.match(r'^\d+\..*:', text))
    
    @staticmethod
    def _is_chapter_number(text: str) -> bool:
        """Check if text is just a chapter number."""
        text = text.strip().lower()
        return bool(re.match(r'^\d+$|^chapter\s+\d+$|^\d+\.?$', text))
    
    def _format_chapter_content(
        self,
        paragraphs: List[str],
        footnotes: Dict[str, str]
    ) -> str:
        """Format chapter content with footnotes if enabled."""
        processed_paragraphs = []
        
        for p in paragraphs:
            # Remove divider characters
            p = p.replace('||', '')
            
            if self.user_config.include_footnotes:
                soup_p = BeautifulSoup(p, 'html.parser')
                for sup in soup_p.find_all('sup'):
                    ref_num = sup.text.strip()
                    if ref_num in footnotes:
                        sup.wrap(soup_p.new_tag(
                            'a',
                            href=f'#footnote-{ref_num}'
                        ))
                processed_paragraphs.append(str(soup_p))
            else:
                p_clean = re.sub(r'<sup>.*?</sup>', '', p)
                processed_paragraphs.append(p_clean)
        
        return "\n".join(processed_paragraphs)
    
class EpubBuilder:
    """Handle EPUB file creation and structure."""
    
    def __init__(
        self,
        title: str,
        author: str = "Unknown",
        translator: str = "",
        creator: str = "LightNovelScraper",
        config: ScraperConfig = ScraperConfig()
    ):
        self.title = title
        self.author = author
        self.translator = translator
        self.creator = creator
        self.config = config
        self.book = epub.EpubBook()
        self.chapters: List[epub.EpubHtml] = []
        self._initialize_book()

    def _initialize_book(self) -> None:
        """Initialize basic EPUB metadata."""
        # Set identifier
        book_id = f'id_{self.title.lower().replace(" ", "_")}'
        self.book.set_identifier(book_id)
        
        # Set basic metadata
        self.book.set_title(self.title)
        self.book.set_language('en')
        
        # Add optional metadata
        if self.author and self.author != "Unknown":
            self.book.add_author(self.author)
            
        if self.translator:
            self.book.add_metadata(
                'DC',
                'contributor',
                self.translator,
                {'role': 'trl'}
            )
            
        if self.creator != "LightNovelScraper":
            self.book.add_metadata('DC', 'publisher', self.creator)
        
        # Add creation date
        self.book.add_metadata(
            'DC',
            'date',
            datetime.now().strftime('%Y-%m-%d'),
            {'event': 'creation'}
        )

    def add_cover(self, cover_data: ImageData) -> None:
        """
        Add cover image to EPUB.
        
        Args:
            cover_data: Named tuple containing image data and metadata
        """
        self.book.set_cover(
            cover_data.filename,
            cover_data.data,
            create_page=True
        )

    def add_chapter(
        self,
        chapter_data: ChapterData,
        index: int
    ) -> epub.EpubHtml:
        """
        Add chapter to EPUB.
        
        Args:
            chapter_data: Dictionary containing chapter content and metadata
            index: Chapter index for file naming
            
        Returns:
            EpubHtml object representing the chapter
            
        Raises:
            ValueError: If required chapter data is missing
        """
        if not chapter_data['title'] or not chapter_data['content']:
            raise ValueError("Chapter title and content are required")
        
        chapter = epub.EpubHtml(
            title=chapter_data['title'],
            file_name=f'chap_{index:03d}.xhtml',
            lang='en'
        )
        
        content_parts = [
            f'<h1>{chapter_data["title"]}</h1>',
            '<div class="chapter-content">',
            chapter_data['content']
        ]
        
        # Add footnotes if present
        if chapter_data['footnotes']:
            content_parts.extend(
                self._format_footnotes(chapter_data['footnotes'])
            )
        
        # Add chapter notes if present
        if chapter_data['notes']:
            content_parts.extend(
                self._format_chapter_notes(chapter_data['notes'])
            )
        
        content_parts.append('</div>')
        chapter.content = '\n'.join(content_parts)
        
        self.book.add_item(chapter)
        self.chapters.append(chapter)
        
        return chapter

    def _format_footnotes(
        self,
        footnotes: Dict[str, str]
    ) -> List[str]:
        """Format footnotes for EPUB."""
        return [
            '<div class="footnotes">',
            '<hr/>',
            '<h3>Footnotes</h3>',
            *[
                f'<p id="footnote-{num}" class="footnote">'
                f'<sup>{num}</sup> {text} '
                f'<a href="#ref-{num}">↩</a></p>'
                for num, text in footnotes.items()
            ],
            '</div>'
        ]

    def _format_chapter_notes(
        self,
        notes: List[str]
    ) -> List[str]:
        """Format chapter notes for EPUB."""
        return [
            '<div class="chapter-notes">',
            '<hr/>',
            '<h3>Chapter Notes</h3>',
            *notes,
            '</div>'
        ]

    def finalize(self) -> None:
        """
        Finalize EPUB structure and add navigation.
        
        This method should be called after all chapters are added
        and before saving the EPUB file.
        """
        self._add_navigation()
        self._add_styling()
        self._set_spine()

    def _add_navigation(self) -> None:
        """Add navigation elements to EPUB."""
        # Create table of contents
        self.book.toc = [
            (
                epub.Section(self.title),
                self.chapters
            )
        ]
        
        # Add required navigation files
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

    def _add_styling(self) -> None:
        """Add CSS styling to EPUB."""
        style = epub.EpubItem(
            uid="style_nav",
            file_name="style/nav.css",
            media_type="text/css",
            content=self.config.css_style
        )
        self.book.add_item(style)

    def _set_spine(self) -> None:
        """Set the reading order of the EPUB."""
        self.book.spine = ['nav'] + self.chapters

    def save(self, filename: str) -> None:
        """
        Save EPUB file.
        
        Args:
            filename: Output filename
            
        Raises:
            IOError: If file cannot be written
        """
        try:
            epub.write_epub(filename, self.book, {})
        except Exception as e:
            raise IOError(f"Failed to save EPUB file: {str(e)}")

class ImageProcessor:
    """Process and optimize images for EPUB."""
    
    def __init__(self, config: ScraperConfig):
        self.config = config
    
    def process_cover_image(
        self,
        image_data: bytes
    ) -> Optional[ImageData]:
        """
        Process and optimize cover image.
        
        Args:
            image_data: Raw image bytes
            
        Returns:
            ImageData named tuple or None if processing fails
        """
        try:
            # Open image
            image = Image.open(BytesIO(image_data))
            
            # Convert to RGB if needed
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Adjust aspect ratio
            image = self._adjust_aspect_ratio(image)
            
            # Resize if needed
            image = self._resize_image(image)
            
            # Save optimized image
            output = BytesIO()
            image.save(
                output,
                format='JPEG',
                quality=self.config.jpeg_quality
            )
            
            return ImageData(
                data=output.getvalue(),
                mime_type='image/jpeg',
                filename='cover.jpg'
            )
            
        except Exception as e:
            logging.error(f"Cover image processing failed: {str(e)}")
            return None

    def _adjust_aspect_ratio(self, image: Image.Image) -> Image.Image:
        """Adjust image aspect ratio to match target ratio."""
        width, height = image.size
        target_ratio = self.config.cover_aspect_ratio
        current_ratio = width/height
        
        if current_ratio == target_ratio:
            return image
            
        if current_ratio > target_ratio:
            # Image is too wide
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            return image.crop((left, 0, left + new_width, height))
        else:
            # Image is too tall
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            return image.crop((0, top, width, top + new_height))

    def _resize_image(self, image: Image.Image) -> Image.Image:
        """Resize image if it exceeds maximum dimensions."""
        width, height = image.size
        
        if height <= self.config.max_cover_height:
            return image
            
        new_height = self.config.max_cover_height
        new_width = int(new_height * self.config.cover_aspect_ratio)
        
        return image.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS
        )
    
class NovelScraper:
    """Main scraper class coordinating the entire scraping process."""
    
    def __init__(
        self,
        config: ScraperConfig,
        user_config: UserConfig,
        logger: logging.Logger
    ):
        self.config = config
        self.user_config = user_config
        self.logger = logger
        self.network = NetworkManager(config, logger)
        self.content_extractor = ContentExtractor(
            config,
            user_config,
            logger
        )
        self.image_processor = ImageProcessor(config)
    
    def scrape_novel(
        self,
        base_url: str,
        novel_id: str,
        start_chapter: int,
        end_chapter: int,
        novel_title: str,
        metadata: Dict[str, str]
    ) -> bool:
        """
        Scrape novel chapters and create EPUB.
        
        Args:
            base_url: Base URL for the novel
            novel_id: Novel identifier
            start_chapter: First chapter to scrape
            end_chapter: Last chapter to scrape
            novel_title: Title of the novel
            metadata: Additional novel metadata
            
        Returns:
            bool indicating success
        """
        try:
            # Initialize progress tracking
            total_chapters = end_chapter - start_chapter + 1
            progress = ProgressTracker(total_chapters)
            
            # Initialize EPUB builder
            epub_builder = EpubBuilder(
                title=novel_title,
                author=metadata.get('author', 'Unknown'),
                translator=metadata.get('translator', ''),
                creator=metadata.get('creator', 'LightNovelScraper'),
                config=self.config
            )
            
            # Add cover if URL provided
            if cover_url := metadata.get('cover_url'):
                self._add_cover_image(cover_url, epub_builder)
            
            # Scrape chapters
            chapters_data = self._scrape_chapters(
                base_url,
                novel_id,
                start_chapter,
                end_chapter,
                progress
            )
            
            if not chapters_data:
                self.logger.error("No chapters were successfully scraped")
                return False
            
            # Add chapters to EPUB
            self._build_epub(chapters_data, epub_builder)
            
            # Save EPUB file
            epub_filename = self._generate_filename(novel_title)
            epub_builder.save(epub_filename)
            
            # Print final stats
            self._print_completion_stats(
                epub_filename,
                len(chapters_data),
                progress
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Novel scraping failed: {str(e)}")
            if self.user_config.debug_mode:
                import traceback
                self.logger.error(traceback.format_exc())
            return False
    
    def _add_cover_image(
        self,
        cover_url: str,
        epub_builder: EpubBuilder
    ) -> None:
        """Process and add cover image to EPUB."""
        try:
            response = self.network.get_url(cover_url)
            if image_data := self.image_processor.process_cover_image(
                response.content
            ):
                epub_builder.add_cover(image_data)
            else:
                self.logger.warning("Failed to process cover image")
        except Exception as e:
            self.logger.warning(f"Failed to add cover image: {str(e)}")

    def _scrape_chapters(
        self,
        base_url: str,
        novel_id: str,
        start_chapter: int,
        end_chapter: int,
        progress: ProgressTracker
    ) -> List[ChapterData]:
        """Scrape individual chapters."""
        chapters_data: List[ChapterData] = []
        
        for chapter_num in range(start_chapter, end_chapter + 1):
            url = self._build_chapter_url(base_url, novel_id, chapter_num)
            
            try:
                response = self.network.get_url(url)
                title, content, notes, footnotes = (
                    self.content_extractor.extract_chapter_content(
                        response.text,
                        url
                    )
                )
                
                if title and content:
                    chapters_data.append({
                        'title': title,
                        'content': content,
                        'notes': notes or [],
                        'footnotes': footnotes or {}
                    })
                    progress.update(True)
                else:
                    progress.update(False)
                    self.logger.warning(
                        f"Failed to process chapter {chapter_num}"
                    )
                    
            except Exception as e:
                progress.update(False)
                self.logger.error(
                    f"Failed to scrape chapter {chapter_num}: {str(e)}"
                )
        
        return chapters_data

    @staticmethod
    def _build_chapter_url(
        base_url: str,
        novel_id: str,
        chapter_num: int
    ) -> str:
        """Build URL for specific chapter."""
        base_url = base_url.rstrip('/')
        return f"{base_url}/{novel_id}/chapter-{chapter_num}"

    def _build_epub(
        self,
        chapters_data: List[ChapterData],
        epub_builder: EpubBuilder
    ) -> None:
        """Add chapters to EPUB and finalize structure."""
        for idx, chapter_data in enumerate(chapters_data, 1):
            epub_builder.add_chapter(chapter_data, idx)
        epub_builder.finalize()

    @staticmethod
    def _generate_filename(title: str) -> str:
        """Generate safe filename for EPUB."""
        safe_title = re.sub(r'[^\w\s-]', '', title).strip()
        safe_title = re.sub(r'[-\s]+', '_', safe_title)
        return f"{safe_title.lower()}.epub"

    def _print_completion_stats(
        self,
        filename: str,
        total_chapters: int,
        progress: ProgressTracker
    ) -> None:
        """Print final statistics."""
        print(f"\n{ConsoleColors.GREEN}"
              f"Successfully created: {filename}"
              f"{ConsoleColors.RESET}")
        print(f"Total chapters: {total_chapters}")
        print(f"Successful: {progress.success_count}")
        print(f"Failed: {progress.failure_count}")

class ScraperCLI:
    """Command-line interface for the novel scraper."""
    
    def __init__(self):
        self.config = ScraperConfig()
        self.user_config = UserConfig()
        self.logger = setup_logging(False)  # Will be updated based on args
    
    def parse_arguments(self) -> argparse.Namespace:
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(
            description='Light Novel Scraper'
        )
        
        # Required arguments
        required = parser.add_argument_group('required arguments')
        required.add_argument('--url', type=str, help='Base URL')
        required.add_argument('--novel-id', type=str, help='Novel ID')
        required.add_argument('--start', type=int, help='Start chapter')
        required.add_argument('--end', type=int, help='End chapter')
        required.add_argument('--title', type=str, help='Novel title')
        
        # Optional arguments
        parser.add_argument('--debug', action='store_true',
                           help='Enable debug mode')
        parser.add_argument('--cover-url', type=str,
                           help='Cover image URL')
        parser.add_argument('--author', type=str,
                           help='Author name')
        parser.add_argument('--translator', type=str,
                           help='Translator name')
        parser.add_argument('--creator', type=str,
                           help='Creator name')
        
        # Configuration options
        parser.add_argument('--simple-chapters',
                           action='store_true',
                           help='Use simple chapter numbering')
        parser.add_argument('--include-footnotes',
                           action='store_true',
                           help='Include footnotes')
        parser.add_argument('--include-notes',
                           action='store_true',
                           help='Include chapter notes')
        parser.add_argument(
            '--extraction-methods',
            type=str,
            nargs='+',
            choices=list(ContentExtractor.EXTRACTION_METHODS.keys()),
            help='Content extraction methods to use'
        )
        parser.add_argument(
            '--custom-selectors',
            type=str,
            nargs='+',
            help='Custom CSS selectors for content extraction'
        )
        
        return parser.parse_args()

    def get_user_input(self, args: argparse.Namespace) -> Dict[str, Any]:
        """Get and validate user input."""
        inputs: Dict[str, Any] = {}
        
        # Get and validate base URL
        base_url = args.url or input("\nEnter base URL: ").strip()
        if not base_url:
            raise ValidationError("Base URL cannot be empty")
        if not urlparse(base_url).scheme:
            raise ValidationError("Invalid URL format")
        inputs['base_url'] = (
            base_url if base_url.endswith('/') else base_url + '/'
        )
        
        # Get novel ID
        inputs['novel_id'] = (
            args.novel_id or input("Novel ID: ").strip()
        )
        if not inputs['novel_id']:
            raise ValidationError("Novel ID cannot be empty")
        
        # Get chapter range
        if args.start and args.end:
            inputs['start_chapter'] = args.start
            inputs['end_chapter'] = args.end
        else:
            try:
                inputs['start_chapter'] = int(input("Start chapter: "))
                inputs['end_chapter'] = int(input("End chapter: "))
                if inputs['start_chapter'] > inputs['end_chapter']:
                    raise ValidationError(
                        "Start chapter cannot be greater than end chapter"
                    )
            except ValueError:
                raise ValidationError("Please enter valid chapter numbers")
        
        # Get novel title
        inputs['novel_title'] = (
            args.title or input("Novel title: ").strip()
        )
        if not inputs['novel_title']:
            raise ValidationError("Novel title cannot be empty")
        
        # Get optional inputs
        inputs.update(self._get_optional_inputs(args))
        
        # Get configuration options
        self._update_user_config(args)
        
        return inputs

    def _get_optional_inputs(
        self,
        args: argparse.Namespace
    ) -> Dict[str, str]:
        """Get optional metadata inputs."""
        inputs: Dict[str, str] = {}
        
        # Handle cover URL
        if args.cover_url is None:
            if self._get_yes_no_input("Add cover image?"):
                inputs['cover_url'] = self._get_optional_input(
                    "Cover image URL"
                )
        else:
            inputs['cover_url'] = args.cover_url
        
        # Handle author
        if args.author is None:
            if self._get_yes_no_input("Add author?"):
                inputs['author'] = self._get_optional_input("Author name")
        else:
            inputs['author'] = args.author
        
        # Handle translator
        if args.translator is None:
            if self._get_yes_no_input("Add translator?"):
                inputs['translator'] = self._get_optional_input(
                    "Translator name"
                )
        else:
            inputs['translator'] = args.translator
        
        # Handle creator
        if args.creator is None:
            if self._get_yes_no_input("Change creator name?"):
                inputs['creator'] = self._get_optional_input(
                    "Creator name",
                    default="LightNovelScraper"
                )
            else:
                inputs['creator'] = "LightNovelScraper"
        else:
            inputs['creator'] = args.creator
        
        return inputs

    def _update_user_config(self, args: argparse.Namespace) -> None:
        """Update user configuration based on arguments."""
        # Handle basic config options
        if not any([
            args.simple_chapters,
            args.include_footnotes,
            args.include_notes
        ]):
            print("\nConfiguration options:")
            self.user_config.simple_chapter_names = (
                self._get_yes_no_input("Use simple chapter numbering?")
            )
            self.user_config.include_footnotes = (
                self._get_yes_no_input("Include footnotes?")
            )
            self.user_config.include_chapter_notes = (
                self._get_yes_no_input("Include chapter notes?")
            )
        else:
            self.user_config.simple_chapter_names = args.simple_chapters
            self.user_config.include_footnotes = args.include_footnotes
            self.user_config.include_chapter_notes = args.include_notes

        # Handle extraction methods
        if not args.extraction_methods:
            print("\nContent Extraction Methods:")
            for method, description in ContentExtractor.EXTRACTION_METHODS.items():
                print(f"  {method}: {description}")
            
            selected_methods = []
            while not selected_methods:
                methods_input = input(
                    "\nEnter extraction methods to use (space-separated),\n"
                    "or press Enter to use all methods in default order\n"
                    "['known_selectors', 'pattern_matching', 'density', 'custom']: "
                ).strip().split()
                
                if not methods_input:
                    # Use default methods if no input
                    selected_methods = ['known_selectors', 'pattern_matching', 'density', 'custom']
                else:
                    selected_methods = [
                        method for method in methods_input
                        if method in ContentExtractor.EXTRACTION_METHODS
                    ]
                    
                    if not selected_methods:
                        print("Please select at least one valid method.")
            
            self.user_config.extraction_methods = selected_methods
        else:
            self.user_config.extraction_methods = args.extraction_methods
        
        # Handle custom selectors if custom method is selected
        if 'custom' in self.user_config.extraction_methods:
            if not args.custom_selectors:
                print("\nCustom CSS Selectors:")
                print("Enter selectors to try (space-separated), examples:")
                print("  .chapter-content")
                print("  #chapter-container")
                print("  article.content")
                self.custom_selectors = input(
                    "Enter custom CSS selectors: "
                ).strip().split()
            else:
                self.custom_selectors = args.custom_selectors
        else:
            self.custom_selectors = []

        # Validate the updated configuration
        try:
            self.user_config.validate()
        except ValueError as e:
            print(f"\n{ConsoleColors.RED}Configuration error: {str(e)}{ConsoleColors.RESET}")
            raise

    @staticmethod
    def _get_yes_no_input(
        prompt: str,
        default: bool = False
    ) -> bool:
        """Get yes/no input from user."""
        default_text = "Y/n" if default else "y/N"
        response = input(
            f"{prompt} ({default_text}): "
        ).strip().lower()
        if not response:
            return default
        return response.startswith('y')

    @staticmethod
    def _get_optional_input(
        prompt: str,
        default: str = ""
    ) -> str:
        """Get optional input from user."""
        response = input(f"{prompt} (Enter to skip): ").strip()
        return response or default

    def print_configuration(
        self,
        inputs: Dict[str, Any]
    ) -> None:
        """Print current configuration."""
        print("\nFinal Configuration:")
        print(f"Base URL: {inputs['base_url']}")
        print(f"Novel ID: {inputs['novel_id']}")
        print(
            f"Chapters: {inputs['start_chapter']} "
            f"to {inputs['end_chapter']}"
        )
        print(f"Title: {inputs['novel_title']}")
        print(f"Debug Mode: {'On' if self.user_config.debug_mode else 'Off'}")
        print(
            f"Simple Chapter Names: "
            f"{'On' if self.user_config.simple_chapter_names else 'Off'}"
        )
        print(
            f"Include Footnotes: "
            f"{'On' if self.user_config.include_footnotes else 'Off'}"
        )
        print(
            f"Include Chapter Notes: "
            f"{'On' if self.user_config.include_chapter_notes else 'Off'}"
        )
        print("\nExtraction Methods:")
        for method in self.user_config.extraction_methods:
            print(f"  - {method}")
        
        if self.custom_selectors:
            print("\nCustom Selectors:")
            for selector in self.custom_selectors:
                print(f"  - {selector}")
        
        if inputs.get('cover_url'):
            print(f"Cover URL: {inputs['cover_url']}")
        if inputs.get('author', "Unknown") != "Unknown":
            print(f"Author: {inputs['author']}")
        if inputs.get('translator'):
            print(f"Translator: {inputs['translator']}")
        if inputs.get('creator') != "LightNovelScraper":
            print(f"Creator: {inputs['creator']}")

    def run(self) -> None:
        """Run the scraper CLI."""
        print(f"\n{ConsoleColors.GREEN}Light Novel Scraper"
              f"{ConsoleColors.RESET}")
        print(f"{ConsoleColors.GREEN}==================="
              f"{ConsoleColors.RESET}")
        
        try:
            # Parse arguments
            args = self.parse_arguments()
            
            # Update configurations
            self.user_config.debug_mode = args.debug
            self.logger = setup_logging(args.debug)
            
            # Get user input
            inputs = self.get_user_input(args)
            
            # Print configuration
            self.print_configuration(inputs)
            
            # Confirm proceeding
            if not self._get_yes_no_input(
                "\nProceed with these settings?",
                default=True
            ):
                print("Aborted by user.")
                return
            
            # Initialize and run scraper
            scraper = NovelScraper(
                self.config,
                self.user_config,
                self.logger,
                custom_selectors=self.custom_selectors
)
            
            # Extract metadata
            metadata = {
                'author': inputs.get('author', 'Unknown'),
                'translator': inputs.get('translator', ''),
                'creator': inputs.get('creator', 'LightNovelScraper'),
                'cover_url': inputs.get('cover_url', '')
            }
            
            # Run scraper
            success = scraper.scrape_novel(
                inputs['base_url'],
                inputs['novel_id'],
                inputs['start_chapter'],
                inputs['end_chapter'],
                inputs['novel_title'],
                metadata
            )
            
            if not success:
                print(f"\n{ConsoleColors.RED}"
                      f"Scraping failed. Check the logs for details."
                      f"{ConsoleColors.RESET}")
                
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
        except Exception as e:
            print(f"\n{ConsoleColors.RED}Error: {str(e)}{ConsoleColors.RESET}")
            if self.user_config.debug_mode:
                import traceback
                print("\nFull error trace:")
                print(traceback.format_exc())

def main() -> None:
    """Main entry point for the scraper."""
    cli = ScraperCLI()
    cli.run()

if __name__ == "__main__":
    main()