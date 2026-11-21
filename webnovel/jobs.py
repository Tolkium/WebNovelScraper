"""Shared job runner and browser script generation for CLI and Desktop."""

from dataclasses import dataclass
from functools import partial
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event
from typing import Callable, Optional

from bs4 import BeautifulSoup
from ebooklib import epub
import requests

from webnovel.epub import EpubCreator, build_epub_identifier
from webnovel.resources import resource
from webnovel.scraper import ScraperCancelled, WebNovelScraper, is_browser_check, load_saved_chapters
from webnovel.settings import JobSettings

MAX_REPORTED_SAVED_GAPS = 500
BROWSER_SCRIPT = resource('browser', 'collect-chapters.js')


def site_blocks_app(url: str, user_agent: str, timeout: int = 15) -> bool:
    """True when url answers with a browser check; network errors are left for the job to report."""
    try:
        response = requests.get(url, headers={'User-Agent': user_agent}, timeout=timeout)
    except requests.RequestException:
        return False
    return is_browser_check(response)


def browser_script(settings: JobSettings) -> str:
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


def run_job(
    settings: JobSettings,
    cancel: Event,
    log: Callable,
    progress: Callable,
    log_path: Optional[Path] = None,
    overwrite: bool = False,
) -> JobResult:
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
    sparse_missing_count = 0
    if settings.saved_file:
        loaded_saved = load_saved_chapters(settings.saved_file)
        saved = [ch for ch in loaded_saved.chapters if settings.start <= ch.major <= settings.end]
        if not saved:
            raise ValueError(
                f'No saved chapters {settings.start} to {settings.end} in {Path(settings.saved_file).name}. '
                'Check the chapter range or choose another file.'
            )
        present_majors = {ch.major for ch in saved}
        missing_count = (settings.end - settings.start + 1) - len(present_majors)
        if missing_count <= MAX_REPORTED_SAVED_GAPS:
            missing_majors = [n for n in range(settings.start, settings.end + 1) if n not in present_majors]
        else:
            sparse_missing_count = missing_count
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    chapters, failed = [], []
    temp_path = None

    def check_cancel() -> None:
        if cancel.is_set():
            raise ScraperCancelled()

    with WebNovelScraper(
        settings.debug,
        settings=settings,
        cancel_event=cancel,
        log_callback=log,
        log_path=log_path,
    ) as scraper:
        if settings.saved_file and loaded_saved is not None:
            if len(loaded_saved.sources) > 1:
                log(
                    f'Warning: saved folder contains chapters from {len(loaded_saved.sources)} different sources: '
                    f'{", ".join(loaded_saved.sources)}.'
                )
            if loaded_saved.duplicates:
                dup_list = ', '.join(loaded_saved.duplicates[:10])
                more = f' (+{len(loaded_saved.duplicates) - 10} more)' if len(loaded_saved.duplicates) > 10 else ''
                log(f'Warning: duplicate saved chapters found ({dup_list}{more}); kept the longest version of each.')
            if loaded_saved.invalid_count:
                log(f'Warning: skipped {loaded_saved.invalid_count} saved chapter section(s) with invalid chapter numbers.')
            if sparse_missing_count:
                log(
                    f'Warning: {sparse_missing_count:,} chapter numbers between {settings.start} and {settings.end} '
                    f'are not in {Path(settings.saved_file).name}; processing the {len(saved):,} saved chapter(s).'
                )
            entries = [
                (
                    ch.key,
                    ch.raw_number,
                    f'saved chapter {ch.raw_number}',
                    partial(scraper.parse_saved_chapter, ch.section, ch.raw_number),
                )
                for ch in saved
            ]
            for missing_num in missing_majors:
                entries.append(((missing_num,), missing_num, f'saved chapter {missing_num}', None))
            entries.sort(key=lambda item: item[0])
            total = len(entries)
            sources = ((num, src, loader) for _, num, src, loader in entries)
        else:
            base_prefix = f'{settings.base_url.rstrip("/")}/{settings.novel_id.strip()}/{settings.chapter_prefix}'
            total = settings.end - settings.start + 1
            sources = (
                (number, f'{base_prefix}{number}', partial(scraper.get_chapter_content, f'{base_prefix}{number}'))
                for number in range(settings.start, settings.end + 1)
            )

        try:
            check_cancel()
            creator = EpubCreator(
                settings.title.strip(),
                author=settings.author.strip(),
                translator=settings.translator.strip(),
                created_by=settings.creator.strip(),
                settings=settings,
                language=settings.language,
                identifier=_epub_identifier(settings, loaded_saved, saved),
            )
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
                progress(
                    {
                        'stage': 'fetching',
                        'number': number,
                        'current': index,
                        'total': total,
                        'success': len(chapters),
                        'failed': len(failed),
                        'title': source,
                    }
                )
                if load is None:
                    failed.append(number)
                    log(f'Warning: saved chapter {number} is missing from {Path(settings.saved_file).name}.')
                    progress(
                        {
                            'stage': 'failed',
                            'number': number,
                            'current': index + 1,
                            'total': total,
                            'success': len(chapters),
                            'failed': len(failed),
                            'title': 'Missing from saved file',
                        }
                    )
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
                progress(
                    {
                        'stage': 'downloaded' if title and content else 'failed',
                        'number': number,
                        'current': index + 1,
                        'total': total,
                        'success': len(chapters),
                        'failed': len(failed),
                        'title': title or 'Could not download',
                    }
                )
            if not chapters:
                raise ValueError(
                    'No chapters could be downloaded. Check the novel URL and Advanced page selectors. See Activity for details.'
                )
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
                        with temp_path.open('rb') as source:
                            with output.open('xb') as target:
                                created = True
                                shutil.copyfileobj(source, target)
                    except BaseException:
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
