"""Phase 2 tests: unified CLI via run_job(), SiteProfile resolution, resource(), pure cleaning, and exit codes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from ebooklib import epub

from webnovel.cleaning import parse_chapter_elements
from webnovel.epub import EpubCreator, read_epub
from webnovel.resources import resource
from webnovel.scraper import WebNovelScraper
from webnovel.settings import JobSettings, safe_filename, site_profile_for_url, site_selectors
from WebNovelScraper import build_settings_from_cli, main as cli_main, parse_arguments


STORY = '<p>' + 'The blacksmith hammered the glowing steel into shape. ' * 5 + '</p>'


class CliFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/blocked/'):
            self.send_response(403)
            self.send_header('cf-mitigated', 'challenge')
            body = b'Just a moment...'
        elif self.path.startswith('/custom/'):
            self.send_response(200)
            body = (
                '<html><h2 class="t">Custom Title 1</h2>'
                f'<main class="c">{STORY}</main></html>'
            ).encode()
        else:
            self.send_response(200)
            body = (
                '<html><span class="chapter-title">Default Title 1</span>'
                f'<div id="chapter-container">{STORY}</div></html>'
            ).encode()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Phase2CliAndModulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), CliFixtureHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def _write_saved(self, folder: Path, *numbers):
        sections = ''.join(
            f'<section class="wns-chapter" data-number="{n}">'
            f'<span class="chapter-title">Chapter {n}</span>'
            f'<div class="chapter-container">{STORY}</div></section>'
            for n in numbers
        )
        path = folder / 'chapters.html'
        path.write_text(f'<!doctype html><html><head><meta charset="utf-8"></head><body>{sections}</body></html>', encoding='utf-8')
        return path

    def test_cli_works_non_interactively_and_sanitizes_titles(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_saved(folder, 1, 2)
            argv = [
                'WebNovelScraper.py',
                '--from-folder', str(folder),
                '--title', 'Hero: Rise? *Part 1*',
                '--output-dir', str(folder),
                '--language', 'sk',
                '--simple-chapters',
                '--include-footnotes',
                '--include-notes',
                '--no-headings',
                '--debug',
                '--yes',
            ]
            prev = os.getcwd()
            os.chdir(folder)
            try:
                with patch('sys.argv', argv), patch('builtins.input', side_effect=AssertionError('should not prompt')):
                    code = cli_main()
            finally:
                os.chdir(prev)

            self.assertEqual(code, 0)
            epubs = list(folder.glob('*.epub'))
            self.assertEqual(len(epubs), 1)
            book = epub.read_epub(str(epubs[0]))
            self.assertEqual(book.get_metadata('DC', 'language')[0][0], 'sk')
            _, chapters = read_epub(epubs[0])
            self.assertEqual([t for t, _ in chapters], ['Chapter 1', 'Chapter 2'])

    def test_cli_overwrite_flag_and_exit_codes(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_saved(folder, 1)
            out_file = folder / 'sub' / 'custom.epub'
            argv_base = [
                'WebNovelScraper.py',
                '--from-folder', str(folder),
                '--title', 'Book',
                '--output', str(out_file),
                '--yes',
            ]
            prev = os.getcwd()
            os.chdir(folder)
            try:
                with patch('sys.argv', argv_base):
                    self.assertEqual(cli_main(), 0)
                self.assertTrue(out_file.exists())
                out_file.write_bytes(b'keep-me')

                # Without --overwrite: returns 1 and leaves existing file untouched.
                with patch('sys.argv', argv_base):
                    self.assertEqual(cli_main(), 1)
                self.assertEqual(out_file.read_bytes(), b'keep-me')

                # With --overwrite: returns 0 and replaces file cleanly.
                with patch('sys.argv', argv_base + ['--overwrite']):
                    self.assertEqual(cli_main(), 0)
                self.assertNotEqual(out_file.read_bytes(), b'keep-me')

                # Blocked site returns exit code 1.
                blocked_argv = [
                    'WebNovelScraper.py',
                    '--url', self.base + '/blocked/',
                    '--novel-id', 'demo',
                    '--start', '1',
                    '--end', '1',
                    '--title', 'Blocked',
                    '--yes',
                ]
                with patch('sys.argv', blocked_argv):
                    self.assertEqual(cli_main(), 1)
            finally:
                os.chdir(prev)

    def test_cli_live_http_custom_selectors_and_site_profile_precedence(self):
        # Known site URL fills Wuxiaworld selectors by default, while explicit flags override them.
        with patch('sys.argv', [
            'WebNovelScraper.py',
            '--url', 'https://www.wuxiaworld.com/novel/',
            '--novel-id', 'overgeared',
            '--start', '1',
            '--end', '1',
            '--title', 'Overgeared',
            '--yes',
        ]):
            settings = build_settings_from_cli(parse_arguments())
        self.assertEqual((settings.title_selector, settings.content_selector), ('h4', 'div.chapter-body'))

        with patch('sys.argv', [
            'WebNovelScraper.py',
            '--url', 'https://www.wuxiaworld.com/novel/',
            '--novel-id', 'overgeared',
            '--start', '1',
            '--end', '1',
            '--title', 'Overgeared',
            '--title-selector', 'h1.override',
            '--content-selector', 'article.override',
            '--yes',
        ]):
            overridden = build_settings_from_cli(parse_arguments())
        self.assertEqual((overridden.title_selector, overridden.content_selector), ('h1.override', 'article.override'))

        # End-to-end live HTTP download with custom selectors via CLI.
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            argv = [
                'WebNovelScraper.py',
                '--url', self.base + '/custom/',
                '--novel-id', 'book',
                '--start', '1',
                '--end', '1',
                '--title', 'Live CLI',
                '--title-selector', 'h2.t',
                '--content-selector', 'main.c',
                '--language', 'de',
                '--output-dir', str(folder),
                '--requests-per-second', '20',
                '--yes',
            ]
            prev = os.getcwd()
            os.chdir(folder)
            try:
                with patch('sys.argv', argv):
                    self.assertEqual(cli_main(), 0)
            finally:
                os.chdir(prev)
            epub_path = folder / 'live_cli.epub'
            self.assertTrue(epub_path.exists())
            self.assertEqual(epub.read_epub(str(epub_path)).get_metadata('DC', 'language')[0][0], 'de')
            _, chapters = read_epub(epub_path)
            self.assertEqual(chapters[0][0], 'Custom Title 1')

    def test_site_profile_lookup_and_unified_job_settings_direct_usage(self):
        profile = site_profile_for_url('https://www.wuxiaworld.com/novel/overgeared/og-chapter-1')
        self.assertIsNotNone(profile)
        self.assertEqual(site_selectors('https://www.wuxiaworld.com/novel/x'), ('h4', 'div.chapter-body'))
        self.assertIsNone(site_profile_for_url('https://unknown.example/novel/x'))

        js = JobSettings(title='Direct', simple_chapters=True, chapter_headings=False, include_footnotes=True)
        with WebNovelScraper(settings=js) as scraper:
            self.assertTrue(scraper.settings.simple_chapters)
            self.assertTrue(scraper.settings.include_footnotes)
            self.assertEqual(scraper.settings.title_selector, js.title_selector)

        creator = EpubCreator('Direct', settings=js)
        ch = creator.add_chapter({'title': 'Chapter 1', 'content': STORY, 'notes': [], 'footnotes': {'1': 'Note'}}, 1)
        self.assertNotIn('<h1', ch.content)
        self.assertIn('footnote-1', ch.content)

    def test_pure_cleaning_and_resource_helper(self):
        soup = BeautifulSoup(f'<span class="t">Chapter 1</span><div id="c">{STORY}</div>', 'html.parser')
        title, content, notes, footnotes = parse_chapter_elements(soup.select_one('.t'), soup.select_one('#c'), '1')
        self.assertEqual(title, 'Chapter 1')
        self.assertIn('glowing steel', content)
        self.assertEqual(notes, [])
        self.assertEqual(footnotes, {})

        self.assertTrue(resource('browser', 'collect-chapters.js').is_file())
        self.assertTrue(resource('webnovel_assets', 'check.svg').is_file())
        self.assertTrue(resource('icon.ico').is_file())

    def test_relative_output_respects_output_dir_and_reserved_windows_names_are_caught(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_saved(folder, 1)
            out_dir = folder / 'books'
            argv = [
                'WebNovelScraper.py',
                '--from-folder', str(folder),
                '--title', 'Relative Path Test',
                '--output-dir', str(out_dir),
                '--output', 'part/book.epub',
                '--yes',
            ]
            with patch('sys.argv', argv):
                self.assertEqual(cli_main(), 0)
            self.assertTrue((out_dir / 'part' / 'book.epub').is_file())

        for reserved in ('CONOUT$', 'CONIN$', 'COM¹', 'LPT²', 'COM0', 'NUL'):
            self.assertTrue(safe_filename(reserved).startswith('_'), f'Expected {reserved} to be prefixed')
            settings = JobSettings(title='Valid', output_dir='.', filename=f'{reserved}.epub', saved_file=__file__)
            self.assertIn('filename', settings.validate(), f'Expected {reserved}.epub to fail validation')

    def test_default_instances_use_isolated_job_settings(self):
        with WebNovelScraper() as scraper:
            self.assertEqual(scraper.settings, JobSettings())
        creator = EpubCreator('Fresh Defaults')
        chap = creator.add_chapter({'title': 'Ch 1', 'content': STORY, 'notes': [], 'footnotes': {}}, 1)
        self.assertIn('<h1>Ch 1</h1>', chap.content)

    def test_saved_file_with_chapter_zero_and_sparse_numbers_does_not_fail_or_oom(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_saved(folder, 0, 1, 2)
            argv = [
                'WebNovelScraper.py',
                '--from-folder', str(folder),
                '--title', 'Prologue Book',
                '--output-dir', str(folder),
                '--yes',
            ]
            with patch('sys.argv', argv):
                self.assertEqual(cli_main(), 0)
            _, chapters = read_epub(folder / 'prologue_book.epub')
            self.assertEqual([t for t, _ in chapters], ['Chapter 1', 'Chapter 2'])

            sparse_dir = folder / 'sparse'
            sparse_dir.mkdir()
            self._write_saved(sparse_dir, 1, 100000000)
            argv_sparse = [
                'WebNovelScraper.py',
                '--from-folder', str(sparse_dir),
                '--title', 'Sparse Book',
                '--output-dir', str(sparse_dir),
                '--yes',
            ]
            with patch('sys.argv', argv_sparse):
                self.assertEqual(cli_main(), 0)
            _, sparse_chapters = read_epub(sparse_dir / 'sparse_book.epub')
            self.assertEqual([t for t, _ in sparse_chapters], ['Chapter 1', 'Chapter 100000000'])

    def test_keyboard_interrupt_during_copy_fallback_cleans_destination_and_cancelled_cli_exits_130(self):
        from threading import Event
        from webnovel.jobs import JobResult, run_job

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            saved = self._write_saved(folder, 1)
            settings = JobSettings(title='Interrupt Test', start=1, end=1, output_dir=str(folder), saved_file=str(saved))
            with patch('webnovel.jobs.os.link', side_effect=OSError('no hardlinks')), \
                    patch('webnovel.jobs.shutil.copyfileobj', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run_job(settings, Event(), lambda m: None, lambda p: None)
            self.assertFalse(settings.output_path.exists())
            self.assertEqual(list(folder.glob('.webnovel-*')), [])

            # Cancelled JobResult from run_job causes CLI to exit 130 (not 0).
            argv = ['WebNovelScraper.py', '--from-folder', str(folder), '--title', 'Cancelled', '--output-dir', str(folder), '--yes']
            with patch('sys.argv', argv), patch('WebNovelScraper.run_job', return_value=JobResult(None, 0, [], cancelled=True)):
                self.assertEqual(cli_main(), 130)

    def test_interactive_live_cli_prompts_only_for_missing_bound(self):
        prompts = []

        def fake_input(prompt):
            prompts.append(prompt)
            if 'End chapter' in prompt:
                return '5'
            return ''

        with patch('sys.argv', [
            'WebNovelScraper.py',
            '--url', 'https://example.test/novel/',
            '--novel-id', 'demo',
            '--start', '2',
            '--title', 'Demo',
            '--simple-chapters',
        ]), patch('builtins.input', side_effect=fake_input):
            settings = build_settings_from_cli(parse_arguments())
        self.assertEqual((settings.start, settings.end), (2, 5))
        self.assertFalse(any('Start chapter' in p for p in prompts))
        self.assertTrue(any('End chapter' in p for p in prompts))

    def test_copy_fallback_source_open_failure_leaves_no_empty_epub_and_large_live_range_cancels_lazily(self):
        from threading import Event
        from webnovel.jobs import run_job

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            saved = self._write_saved(folder, 1)
            settings = JobSettings(title='Source Fail', start=1, end=1, output_dir=str(folder), saved_file=str(saved))
            real_open = Path.open

            def fail_temp_read(path_self, mode='r', *args, **kwargs):
                if mode == 'rb' and path_self.name.startswith('.webnovel-'):
                    raise OSError('temp read failed')
                return real_open(path_self, mode, *args, **kwargs)

            with patch('webnovel.jobs.os.link', side_effect=OSError('no hardlinks')), \
                    patch.object(Path, 'open', autospec=True, side_effect=fail_temp_read):
                with self.assertRaises(OSError):
                    run_job(settings, Event(), lambda m: None, lambda p: None)
            self.assertFalse(settings.output_path.exists())
            self.assertEqual(list(folder.glob('.webnovel-*')), [])

            # Huge live range with pre-set cancel exits immediately without allocating millions of URLs.
            huge = JobSettings(
                base_url=self.base + '/novel/',
                novel_id='demo',
                title='Huge',
                start=1,
                end=100_000_000,
                output_dir=str(folder),
            )
            cancel = Event()
            cancel.set()
            result = run_job(huge, cancel, lambda m: None, lambda p: None)
            self.assertTrue(result.cancelled)
            self.assertIsNone(result.path)


if __name__ == '__main__':
    unittest.main()
