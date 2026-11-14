"""Build EPUBs from chapter files saved by browser/collect-chapters.js."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from bs4 import BeautifulSoup

from desktop_service import JobSettings, read_epub, run_job
from WebNovelScraper import Config, WebNovelScraper, main, read_saved_chapters, repair_split_words


def saved_file(*chapters):
    sections = ''.join(
        f'<section class="wns-chapter" data-number="{number}">'
        f'<span class="chapter-title">Chapter {number}</span>'
        f'<div class="chapter-container"><p>Chapter {number}</p>{body}</div></section>'
        for number, body in chapters)
    return f'<!doctype html><html><head><meta charset="utf-8"></head><body>{sections}</body></html>'


STORY = '<p>' + 'The s.h.i.+eld of the a.s.sa.s.sin was a G.o.ddess gift. ' * 4 + '</p>'
END_NOTES = ('<p>Glossary of Common Korean Terms.</p><p>OG: Glossary Link.</p>'
             '<p>Current schedule: 20 chapters a week.</p>'
             '<p>Check out my Patreon for early access to a certain number of unedited chapters.</p>')


class SplitWordTests(unittest.TestCase):
    def repaired(self, text):
        soup = BeautifulSoup(f'<p>{text}</p>', 'html.parser')
        repair_split_words(soup)
        return soup.p.get_text()

    def test_rejoins_dotted_words(self):
        for broken, fixed in [
            ('s.h.i.+eld', 'shield'), ('a.s.sa.s.sin', 'assassin'), ('G.o.ddess', 'Goddess'),
            ('b.u.t.ton', 'button'), ('Craftsmans.h.i.+p', 'Craftsmanship'), ('t-s.h.i.+rt', 't-shirt'),
            ('wis.h.!.+', 'wish!'), ('Flas.h.!.+”', 'Flash!”'), ('Kw.a.n.g!Kwaang', 'Kwang!Kwaang'),
            ('“h.e.l.lo?”', '“hello?”'),
        ]:
            self.assertEqual(self.repaired(broken), fixed)

    def test_keeps_sentences_numbers_and_acronyms(self):
        for text in ['first.I', 'late.Come', '1.2 meters', 'L.T.S', 'Des...cend...ant...']:
            self.assertEqual(self.repaired(text), text)


class SavedChapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_all_files_in_chapter_order_without_duplicates(self):
        (self.folder / 'b.html').write_text(saved_file((3, STORY), (2, STORY)), encoding='utf-8')
        (self.folder / 'a.html').write_text(saved_file((1, STORY), (2, STORY)), encoding='utf-8')
        (self.folder / 'notes.txt').write_text('ignored', encoding='utf-8')
        self.assertEqual([number for number, _ in read_saved_chapters(self.folder)], [1, 2, 3])

    def test_in_between_chapters_keep_their_number(self):
        (self.folder / 'novel.html').write_text(saved_file((770, STORY), (769.1, STORY), (769, STORY)), encoding='utf-8')
        chapters = read_saved_chapters(self.folder)
        self.assertEqual([number for number, _ in chapters], [769, 769.1, 770])
        scraper = WebNovelScraper(settings=Config(SIMPLE_CHAPTER_NAMES=True), log_path=self.folder / 'test.log')
        number, section = chapters[1]
        self.assertEqual(scraper.parse_saved_chapter(section, number)[0], 'Chapter 769.1')

    def test_saved_chapter_is_repaired_and_end_notes_removed(self):
        (self.folder / 'novel.html').write_text(saved_file((105, STORY + END_NOTES)), encoding='utf-8')
        [(number, section)] = read_saved_chapters(self.folder)
        title, content, notes, _ = WebNovelScraper(log_path=self.folder / 'test.log').parse_saved_chapter(section, number)
        self.assertEqual(title, 'Chapter 105')
        self.assertIn('The shield of the assassin was a Goddess gift.', content)
        for unwanted in ('Glossary', 'Current schedule', 'Patreon', '<p>Chapter 105</p>'):
            self.assertNotIn(unwanted, content)

    def test_empty_saved_chapter_fails_without_stopping(self):
        (self.folder / 'novel.html').write_text(saved_file((7, '')), encoding='utf-8')
        [(number, section)] = read_saved_chapters(self.folder)
        self.assertEqual(WebNovelScraper(log_path=self.folder / 'test.log').parse_saved_chapter(section, number), (None, None, None, None))

    def saved_job(self, **changes):
        settings = JobSettings(title='Saved Book', start=2, end=3, output_dir=str(self.folder),
                               saved_file=str(self.folder / 'novel.html'), simple_chapters=True, **changes)
        return run_job(settings, Event(), lambda text: None, lambda data: None, self.folder / 'job.log')

    def test_desktop_job_builds_from_saved_file_without_a_website(self):
        (self.folder / 'novel.html').write_text(saved_file((1, STORY), (2, STORY), (3, STORY)), encoding='utf-8')
        self.assertEqual(JobSettings(title='Saved Book', output_dir=str(self.folder),
                                     saved_file=str(self.folder / 'novel.html')).validate(), {})
        result = self.saved_job()
        self.assertEqual((result.success, result.failed), (2, []))
        self.assertEqual([title for title, _ in read_epub(result.path)[1]], ['Chapter 2', 'Chapter 3'])

    def test_saved_file_must_exist_and_hold_the_chosen_chapters(self):
        settings = JobSettings(title='Saved Book', output_dir=str(self.folder), saved_file=str(self.folder / 'missing.html'))
        self.assertIn('saved_file', settings.validate())
        (self.folder / 'novel.html').write_text(saved_file((1, STORY)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'No saved chapters 2 to 3'):
            self.saved_job()

    def test_cli_builds_epub_from_folder_and_chapter_range(self):
        (self.folder / 'novel.html').write_text(saved_file((1, STORY), (2, STORY), (3, STORY)), encoding='utf-8')
        argv = ['WebNovelScraper.py', '--from-folder', str(self.folder), '--title', 'Saved Book',
                '--start', '2', '--end', '3', '--simple-chapters']
        previous = os.getcwd()
        os.chdir(self.folder)
        try:
            with patch('sys.argv', argv), patch('builtins.input', return_value=''):
                main()
        finally:
            os.chdir(previous)
        with ZipFile(self.folder / 'saved_book.epub') as archive:
            chapters = sorted(name for name in archive.namelist() if name.startswith('EPUB/chap_'))
            self.assertEqual(len(chapters), 2)
            self.assertIn('Chapter 2', archive.read(chapters[0]).decode())
            self.assertIn('shield', archive.read(chapters[1]).decode())


if __name__ == '__main__':
    unittest.main()
