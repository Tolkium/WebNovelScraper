"""Command-line interface for WebNovelScraper."""

import argparse
from pathlib import Path
import sys
from threading import Event
from typing import Optional

from webnovel import (
    JobSettings,
    SiteBlocked,
    ValidationError,
    run_job,
    safe_filename,
    scan_saved_chapters,
    site_profile_for_url,
)


class ConsoleColors:
    """ANSI color codes for console output."""
    RED: str = '\033[91m'
    GREEN: str = '\033[92m'
    YELLOW: str = '\033[93m'
    RESET: str = '\033[0m'


class ProgressTracker:
    def __init__(self, total: int):
        self.total = max(1, total)
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
        stats = f'Success: {self.success} Failures: {self.failures}'
        print(f'\rProgress |{bar}| {percent:.1f}% {stats}', end='', flush=True)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Web Novel Scraper')

    required = parser.add_argument_group('required arguments')
    required.add_argument('--url', type=str, help='Base URL')
    required.add_argument('--novel-id', type=str, help='Novel ID')
    required.add_argument('--start', type=int, help='Start chapter')
    required.add_argument('--end', type=int, help='End chapter')
    required.add_argument('--title', type=str, help='Novel title')
    parser.add_argument(
        '--chapter-prefix',
        type=str,
        default='chapter-',
        help='Text before the chapter number in chapter URLs (default: chapter-)',
    )
    parser.add_argument(
        '--from-folder',
        type=str,
        help='Build from chapter files saved by browser/collect-chapters.js instead of downloading',
    )

    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--cover-url', type=str, help='Cover image URL')
    parser.add_argument('--author', type=str, help='Author name')
    parser.add_argument('--translator', type=str, help='Translator name')
    parser.add_argument('--creator', type=str, help='Creator name')
    parser.add_argument('--language', type=str, default='en', help='EPUB language code (default: en)')

    parser.add_argument('--output-dir', type=str, default='.', help='Directory where the EPUB will be written (default: current directory)')
    parser.add_argument('--output', type=str, help='Output EPUB filename or path')
    parser.add_argument('--overwrite', action='store_true', help='Replace an existing EPUB file if present')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip interactive prompts and confirmation')

    parser.add_argument('--title-selector', type=str, help='CSS selector for the chapter title')
    parser.add_argument('--content-selector', type=str, help='CSS selector for the chapter content container')
    parser.add_argument('--requests-per-second', type=float, default=JobSettings.requests_per_second, help='Max requests per second (default: 2.0)')
    parser.add_argument('--timeout', type=int, default=JobSettings.timeout, help='Request timeout in seconds (default: 30)')
    parser.add_argument('--retries', type=int, default=JobSettings.retries, help='Retry attempts per chapter (default: 3)')

    parser.add_argument('--simple-chapters', action='store_true', help='Use simple chapter numbering')
    parser.add_argument('--include-footnotes', action='store_true', help='Include footnotes')
    parser.add_argument('--include-notes', action='store_true', help='Include chapter notes')
    parser.add_argument(
        '--no-headings',
        action='store_true',
        help='Leave chapter headings out of the text so text-to-speech does not read them',
    )

    return parser.parse_args()


def get_yes_no_input(prompt: str, default: bool = False) -> bool:
    default_text = 'Y/n' if default else 'y/N'
    response = input(f'{prompt} ({default_text}): ').strip().lower()
    if not response:
        return default
    return response.startswith('y')


def get_optional_input(prompt: str, arg_value: Optional[str] = None, default: str = '') -> str:
    if arg_value is not None:
        return arg_value
    response = input(f'{prompt} (Enter to skip): ').strip()
    return response or default


def build_settings_from_cli(args: argparse.Namespace) -> JobSettings:
    """Gather CLI arguments (and interactive prompts when not --yes) into a JobSettings instance."""
    print('\nWeb Novel Scraper')
    print('==================')

    base_url = ''
    novel_id = ''
    saved_file = args.from_folder or ''

    if not saved_file:
        base_url = args.url if args.url else ('' if args.yes else input('\nEnter base URL: ').strip())
        if not base_url:
            raise ValidationError('Base URL cannot be empty')
        if not base_url.endswith('/'):
            base_url += '/'

        novel_id = args.novel_id if args.novel_id else ('' if args.yes else input('Novel ID: ').strip())
        if not novel_id:
            raise ValidationError('Novel ID cannot be empty')

    if saved_file:
        scanned = scan_saved_chapters(saved_file)
        if scanned['count'] == 0:
            raise ValidationError(f'No saved chapters found in {saved_file}')
        start_chapter = args.start if args.start is not None else max(1, int(scanned['first']))
        end_chapter = args.end if args.end is not None else int(scanned['last'])
    elif args.start is not None and args.end is not None:
        start_chapter = args.start
        end_chapter = args.end
    elif args.yes:
        raise ValidationError('Both --start and --end are required in non-interactive mode')
    else:
        try:
            start_chapter = args.start if args.start is not None else int(input('Start chapter: '))
            end_chapter = args.end if args.end is not None else int(input('End chapter: '))
            if start_chapter > end_chapter:
                raise ValidationError('Start chapter cannot be greater than end chapter')
        except ValueError:
            raise ValidationError('Please enter valid chapter numbers')

    novel_title = args.title if args.title else ('' if args.yes else input('Novel title: ').strip())
    if not novel_title:
        raise ValidationError('Novel title cannot be empty')

    if args.yes or all([args.cover_url, args.author, args.translator, args.creator]):
        cover_url = args.cover_url or ''
        author = args.author or ''
        translator = args.translator or ''
        created_by = args.creator or 'WebNovelScraper'
    else:
        print('\nOptional Settings:')
        if args.cover_url is None:
            cover_url = get_optional_input('Cover image URL') if get_yes_no_input('Add cover image?') else ''
        else:
            cover_url = args.cover_url

        if args.author is None:
            author = get_optional_input('Author name') if get_yes_no_input('Add author?') else ''
        else:
            author = args.author

        if args.translator is None:
            translator = get_optional_input('Translator name') if get_yes_no_input('Add translator?') else ''
        else:
            translator = args.translator

        if args.creator is None:
            created_by = (
                get_optional_input('Creator name', default='WebNovelScraper')
                if get_yes_no_input('Change creator name?')
                else 'WebNovelScraper'
            )
        else:
            created_by = args.creator

    if not args.yes and not any([args.simple_chapters, args.include_footnotes, args.include_notes, args.no_headings]):
        print('\nConfiguration options:')
        simple_chapters = get_yes_no_input('Use simple chapter numbering?')
        include_footnotes = get_yes_no_input('Include footnotes?')
        include_notes = get_yes_no_input('Include chapter notes?')
        chapter_headings = get_yes_no_input('Read chapter headings aloud?', default=True)
    else:
        simple_chapters = bool(args.simple_chapters)
        include_footnotes = bool(args.include_footnotes)
        include_notes = bool(args.include_notes)
        chapter_headings = not bool(args.no_headings)

    profile = site_profile_for_url(base_url) if base_url else None
    default_title_selector = profile.title_selector if profile is not None else JobSettings.title_selector
    default_content_selector = profile.content_selector if profile is not None else JobSettings.content_selector
    title_selector = args.title_selector if args.title_selector is not None else default_title_selector
    content_selector = args.content_selector if args.content_selector is not None else default_content_selector

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = Path(args.output_dir or '.') / out_path
        output_dir = str(out_path.parent)
        filename = out_path.name
    else:
        output_dir = args.output_dir or '.'
        filename = safe_filename(novel_title.lower().replace(' ', '_'))

    return JobSettings(
        base_url=base_url,
        novel_id=novel_id,
        chapter_prefix=args.chapter_prefix,
        saved_file=saved_file,
        title=novel_title,
        start=start_chapter,
        end=end_chapter,
        output_dir=output_dir,
        filename=filename,
        author=author,
        translator=translator,
        creator=created_by,
        language=args.language,
        cover_url=cover_url,
        simple_chapters=simple_chapters,
        chapter_headings=chapter_headings,
        include_footnotes=include_footnotes,
        include_notes=include_notes,
        debug=bool(args.debug),
        timeout=args.timeout,
        retries=args.retries,
        requests_per_second=args.requests_per_second,
        title_selector=title_selector,
        content_selector=content_selector,
    )


def _print_configuration(settings: JobSettings) -> None:
    print('\nFinal Configuration:')
    if settings.saved_file:
        print(f'Saved chapters folder: {settings.saved_file}')
        print(f'Chapters: {settings.start} to {settings.end}')
    else:
        print(f'Base URL: {settings.base_url}')
        print(f'Novel ID: {settings.novel_id}')
        print(f'Chapters: {settings.start} to {settings.end}')
    print(f'Title: {settings.title}')
    print(f'Output: {settings.output_path}')
    print(f'Debug Mode: {"On" if settings.debug else "Off"}')
    print(f'Simple Chapter Names: {"On" if settings.simple_chapters else "Off"}')
    print(f'Include Footnotes: {"On" if settings.include_footnotes else "Off"}')
    print(f'Include Chapter Notes: {"On" if settings.include_notes else "Off"}')
    print(f'Chapter Headings: {"On" if settings.chapter_headings else "Off"}')
    if settings.cover_url:
        print(f'Cover URL: {settings.cover_url}')
    if settings.author and settings.author != 'Unknown':
        print(f'Author: {settings.author}')
    if settings.translator:
        print(f'Translator: {settings.translator}')
    if settings.creator and settings.creator != 'WebNovelScraper':
        print(f'Creator: {settings.creator}')


def main() -> int:
    debug_enabled = False
    try:
        args = parse_arguments()
        debug_enabled = bool(args.debug)
        settings = build_settings_from_cli(args)
        _print_configuration(settings)

        if not args.yes and not get_yes_no_input('\nProceed with these settings?', default=True):
            print('Aborted by user.')
            return 0

        tracker: Optional[ProgressTracker] = None

        def on_log(message: str) -> None:
            if message.startswith('Warning:'):
                print(f'\n{ConsoleColors.YELLOW}{message}{ConsoleColors.RESET}')

        def on_progress(data: dict) -> None:
            nonlocal tracker
            if tracker is None:
                tracker = ProgressTracker(data['total'])
            stage = data['stage']
            if stage == 'downloaded':
                tracker.update(True)
            elif stage == 'failed':
                tracker.update(False)

        log_path = (
            Path(settings.output_dir).expanduser() / 'scraper_detailed.log'
            if settings.debug
            else None
        )
        result = run_job(
            settings,
            cancel=Event(),
            log=on_log,
            progress=on_progress,
            log_path=log_path,
            overwrite=bool(args.overwrite),
        )
        if result.cancelled or result.path is None:
            print('\nOperation cancelled.')
            return 130
        print(f'\n{ConsoleColors.GREEN}Successfully created: {result.path}{ConsoleColors.RESET}')
        print(f'Total chapters: {result.success + len(result.failed)}')
        print(f'Successful: {result.success}')
        print(f'Failed: {len(result.failed)}')
        return 0

    except KeyboardInterrupt:
        print('\nOperation cancelled by user.')
        return 130
    except SiteBlocked as e:
        print(f'\n{ConsoleColors.RED}{e}{ConsoleColors.RESET}')
        print(
            'Collect the chapters in your browser with browser/collect-chapters.js, '
            'then build with --from-folder.'
        )
        return 1
    except FileExistsError as e:
        print(f'\n{ConsoleColors.RED}Error: {e} Pass --overwrite to replace it.{ConsoleColors.RESET}')
        return 1
    except Exception as e:
        print(f'\n{ConsoleColors.RED}Error: {str(e)}{ConsoleColors.RESET}')
        if debug_enabled:
            import traceback
            print('\nFull error trace:')
            print(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
