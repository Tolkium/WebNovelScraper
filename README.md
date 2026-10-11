# Web Novel Scraper

An accessibility tool to help an individual blind person convert web novels into EPUB files for reading with text-to-speech (TTS) software, with support for footnotes, chapter notes, cover images, and more.

## Important Note

**Current Compatibility**: This tool has been specifically developed and tested for one certain website where "lite" reading material exists in this "novel world" of ours 😉. While it works flawlessly there, using it elsewhere will in most cases require modifications to the content selection logic.

**Need help?** You have options:

1. Modify the content selectors yourself using the code comments below
2. Open an issue on GitHub - if I have time between reading chapters 📚, I might help adapt it

## For Developers

If you want to adapt this for a different site, you'll need to modify these parts in `WebNovelScraper.py`:

```python
# 1. Content targeting (required) - in get_chapter_content():
325  title_elem = soup.find('span', class_='chapter-title')  # Change this class
332  content_div = soup.find('div', id='chapter-container')  # Change this ID

# 2. Content cleaning (optional) - in _clean_html_content():
202  selectors = [ 'script' ..... 'div[data-mobid]' ]  # Remove or modify list
      
# 3. Content processing (optional) - in _process_content():
265 if text.startswith(('T/L:', 'T/N:'))  # Remove or modify translator notes at start
271 elif text.startswith(tuple(str(i) + '.' for i in range(1, 10))) and ':' in text  # Remove or modify footnotes
277 elif ('T/N:' in text or ..... self._is_note_or_message(text, p))  # Remove or modify end notes and messages
226 ['translator\'s note',  ..... 'note'] # Remove or modify strong text patterns
234 ['t/n:' ..... 'as always'] # Remove or modify regular text patterns

```

**Debug/Modify:**

```bash
python WebNovelScraper.py --debug  # See all processing details
```

## Intended Purpose

The sole purpose of this app is to help an individual blind person create EPUB files and read them using text-to-speech (TTS) software. It is intended exclusively for this personal accessibility purpose and is not meant to be used for any other purpose.

Commercial use and content redistribution are outside this intended purpose. **Support the original content creators** by visiting their sites and, if possible, purchasing official releases.

## Features

- Downloads and converts web novels to EPUB format
- Customizable metadata (author, translator, creator)
- Cover image support with automatic resizing and optimization
- Footnotes support with proper linking
- Chapter notes support
- Simple chapter naming option
- Clean content filtering
- Progress tracking with real-time status
- Error handling and retry mechanism
- Rate limiting to prevent server overload
- Debug logging
- Cross-platform support (Windows, Linux, Mac)
- Native desktop interface with six organized sections
- Background EPUB creation, chapter results, cancellation and downloadable logs
- Saved project settings and automatic draft restoration
- Recent EPUB library, chapter text preview and reading with installed TTS voices
- Command-line interface with both interactive and argument modes

## Requirements

- Python 3.10 or higher
- pip (Python package installer)
- For built-in read-aloud: a system TTS engine and installed voices

## Installation

1. Clone or download this repository
2. Navigate to the project directory
3. Run the installation script:

For Windows users:

```bash
run.bat
```

For Linux/Mac users:

```bash
chmod +x run.sh
./run.sh
```

The launch scripts create a local `webnovel_env` environment, install the dependencies there, and open the desktop app. Use the launch script each time to run with these dependencies. The first launch requires an internet connection to install dependencies.

## Usage

### Desktop workspace

Run `run.bat` on Windows or `./run.sh` on Linux/Mac. After building the standalone executable, you can also open `dist/WebNovelScraper.exe` on Windows without installing Python.

The sidebar separates the workflow into six sections:

| Section | What you can do |
| --- | --- |
| **Novel setup** | Paste a full novel or chapter URL to fill the source, enter a title, choose the chapter range, and select the output folder and filename. |
| **EPUB options** | Include or exclude footnotes and translator/chapter notes, and choose original or simple chapter headings. |
| **Book details** | Set author, translator, creator/publisher, EPUB language, and an optional cover image URL. |
| **Advanced** | Set request rate, timeout, retry attempts, user agent, title/content CSS selectors, minimum chapter length, cover size/ratio, and detailed debug logging. |
| **Activity** | Track chapter progress, inspect downloaded and failed chapters, save the activity log, and open or preview the finished EPUB. |
| **Library & reader** | Open existing EPUBs, revisit recent books, preview chapter text, change text size, and read aloud using installed system voices. |

Click **Review and create EPUB** to check the settings before starting. EPUB creation runs in the background, so the window stays responsive. Existing EPUBs require confirmation before replacement; they are replaced only after the new book is complete. If some chapters fail, the EPUB contains the successful chapters and Activity lists the missing chapter numbers. If every chapter fails, no EPUB is created.

**Cancel job** stops between requests and interrupts retry waits. An active network request must finish or time out before cancellation completes. Cancelling leaves existing EPUBs unchanged and does not save a partial book.

Use **File → Save settings** and **File → Load settings** to reuse a project. The app also remembers the current draft, window size, reader text size, and recent books on exit. Diagnostic logs are stored under the app's local data folder; each job shows its full log path in Activity. Detailed debug logs can contain chapter text and source URLs.

The reader shows plain chapter text in EPUB reading order. **Read aloud** uses installed system voices; it reads the selected chapter. **Pause/Resume** is available when the selected TTS engine supports it. You can also open the EPUB in your preferred reading app. TTS availability depends on your operating system and installed speech engines/voices; on Linux this may require installing a supported speech engine separately.

Changing CSS selectors can adapt basic content targeting to another website, but site-specific cleaning rules may still require code changes.

### Command-line interface

The original command-line interface remains available using the local environment:

```powershell
.\webnovel_env\Scripts\python.exe WebNovelScraper.py
# Or use the launcher:
.\run.bat --cli
```

On Linux/Mac, use `webnovel_env/bin/python WebNovelScraper.py` or `./run.sh --cli`. The examples below assume you have activated this environment.

Interactive Mode:

```bash
python WebNovelScraper.py
```

Command-line Mode:

```bash
python WebNovelScraper.py [options]
```

Command-line Options:

- `--url`: Base URL for the web novel website
- `--novel-id`: Novel ID from the URL
- `--start`: First chapter number to download
- `--end`: Last chapter number to download
- `--title`: Novel title for the EPUB file
- `--debug`: Enable debug mode
- `--cover-url`: Direct URL to cover image
- `--author`: Author name
- `--translator`: Translator name
- `--creator`: Creator name
- `--simple-chapters`: Use simple chapter numbering
- `--include-footnotes`: Include footnotes in output
- `--include-notes`: Include chapter notes in output

Example:

```bash
python WebNovelScraper.py --url "https://example.com/novel/" --novel-id "novel-name-123" --start 1 --end 10 --title "My Novel" --cover-url "https://example.com/cover.jpg" --author "Author Name" --simple-chapters
```

## Building Executable

To create a standalone executable:

For Windows:

```bash
build_exe.bat
```

For Linux/Mac:

```bash
chmod +x build_exe.sh
./build_exe.sh
```

## File Structure

```
project/
  ├── WebNovelDesktop.py            # Desktop interface
  ├── desktop_service.py            # Background jobs and EPUB reading
  ├── WebNovelScraper.py            # Scraper engine and original CLI
  ├── WebNovelScraper_Simple.py     # Simplified version of scraper
  ├── requirements.txt              # Python dependencies
  ├── setup.py                      # Package setup file
  ├── build_exe.bat                 # Windows executable build script
  ├── build_exe.sh                  # Linux/Mac executable build script
  ├── build.bat                     # Windows build script
  ├── build.sh                      # Linux/Mac build script
  ├── run.bat                       # Windows run script
  ├── run.sh                        # Linux/Mac run script
  ├── icon.ico                      # Application icon
  ├── WebNovelScraper.spec          # Desktop executable build configuration
  ├── tests/                       # Local website and desktop integration tests
  ├── README.md                     # Documentation
  ├── .gitignore                    # Git ignore file
  ├── build/                        # Build output directory
  └── dist/                         # Distribution output directory
```

## Advanced Features

- **Automatic Cover Processing**: Resizes and optimizes cover images to proper aspect ratio
- **Smart Content Filtering**: Removes ads and unwanted elements
- **Configurable Rate Limiting**: Prevents server overload
- **Progress Tracking**: Real-time progress display with success/failure counts
- **Error Recovery**: Automatic retry mechanism for failed downloads
- **Debug Logging**: Detailed logging for troubleshooting

## Development checks

On Windows, run `.\webnovel_env\Scripts\python.exe -m unittest discover -s tests -v`. On Linux/Mac, use `webnovel_env/bin/python -m unittest discover -s tests -v`.

The tests use a local fixture website to verify the complete desktop job through chapter fetching, content options, cover processing, EPUB writing, preview, progress, saved settings, cancellation and failure handling. They do not download novels from external websites.

## Troubleshooting

If you encounter issues:

1. Check your internet connection
2. Verify the Novel ID and URL are correct
3. Run with --debug flag for detailed logging
4. Check scraper_detailed.log for error messages
5. Ensure all dependencies are installed correctly
6. Try running with different rate limiting settings if experiencing timeouts

## Future Development Roadmap

### Maybe Implement in Future

- **Architecture**
  - Project code modularization

- **Content Processing**
  - Better content cleaning

### Will See

- **Content Targeting**
  - Content boundary detection

- **Content Processing**
  - Better formatting preservation
  - Better metadata handling

### I Don't Think So

- **Architecture**
  - Plugin system for different sites
  - Transformation pipeline
  - Docker container
  - Machine learning features

Note: This roadmap reflects an honest assessment of future development possibilities. The "Maybe Implement in Future" features are those that could realistically be added if they prove necessary. "Will See" features depend on user feedback and project evolution. "I Don't Think So" acknowledges features that, while interesting, are likely beyond the scope of a single-developer hobby project.

## License

This project is intended solely for the personal accessibility purpose described above. Use responsibly and in accordance with the terms of service of the target websites.
